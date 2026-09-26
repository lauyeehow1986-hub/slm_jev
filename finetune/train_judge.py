"""LoRA-finetune the judge on the Choice questions (P5, docs/decisions/0006). Synthetic data only.

    set PYTHONPATH=.
    <cuda python> finetune/train_judge.py --base models/base/Qwen3-1.7B --out models/p5/adapter

Runs offline: the base model is read from a local folder (``local_files_only``), the Hugging Face
hub is forced offline and ``netguard`` closes every non-loopback socket before anything loads.

The judge reads probabilities from the *first answer token*, so the loss is cross-entropy on that
one position only: the prompt is the chat template exactly as llama-server renders it (Qwen3,
``enable_thinking=False``), and the target is the gold option's letter. Batches are left-padded so
the answer position is always the last one (``logits_to_keep=1``: no full-vocabulary logits for
the prompt).

Needs torch with CUDA, transformers and peft (the Unsloth Studio venv has them); not a project
dependency. Validation loss and accuracy are logged every ``--eval-every`` steps; the adapter with
the best validation loss is kept.
"""

from __future__ import annotations

import os

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

import argparse  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import random  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

from slmjev import netguard  # noqa: E402
from slmjev.judge import LETTERS  # noqa: E402

TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]


def load_rows(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def encode(tok, rows: list[dict], max_len: int) -> list[dict]:
    """Prompt ids (chat template, generation prompt, no thinking) and the answer's token id."""
    letter_ids = {}
    for c in LETTERS:
        ids = tok.encode(c, add_special_tokens=False)
        if len(ids) != 1:
            raise SystemExit(f"letter {c!r} is not one token: {ids}")
        letter_ids[c] = ids[0]
    out = []
    for r in rows:
        prompt = tok.apply_chat_template(r["messages"][:2], tokenize=False,
                                         add_generation_prompt=True, enable_thinking=False)
        ids = tok(prompt, add_special_tokens=False)["input_ids"]
        if len(ids) > max_len:
            raise SystemExit(f"{r['doc']}: prompt of {len(ids)} tokens > --max-len {max_len}")
        out.append({"ids": ids, "y": letter_ids[r["answer"]], "label": r["label"],
                    "family": r["family"],
                    "opts": [letter_ids[c] for c in LETTERS[:r["n_options"]]]})
    return out


def batches(data: list[dict], size: int, rng: random.Random | None):
    """Length-bucketed batches (shuffled when ``rng`` is given)."""
    idx = sorted(range(len(data)), key=lambda i: len(data[i]["ids"]))
    chunks = [idx[i:i + size] for i in range(0, len(idx), size)]
    if rng:
        rng.shuffle(chunks)
    return chunks


def collate(torch, data: list[dict], chunk: list[int], pad_id: int, device):
    n = max(len(data[i]["ids"]) for i in chunk)
    ids = torch.full((len(chunk), n), pad_id, dtype=torch.long)
    mask = torch.zeros((len(chunk), n), dtype=torch.long)
    for row, i in enumerate(chunk):
        x = data[i]["ids"]
        ids[row, n - len(x):] = torch.tensor(x)
        mask[row, n - len(x):] = 1
    pos = (mask.cumsum(-1) - 1).clamp(min=0)
    y = torch.tensor([data[i]["y"] for i in chunk])
    return ids.to(device), mask.to(device), pos.to(device), y.to(device)


def last_logits(model, ids, mask, pos):
    out = model(input_ids=ids, attention_mask=mask, position_ids=pos, logits_to_keep=1,
                use_cache=False)
    return out.logits[:, -1, :].float()


def evaluate(torch, model, data, pad_id, device, size: int) -> dict:
    model.eval()
    loss = n = ok = 0.0
    per: dict[str, list[int]] = {}
    with torch.no_grad():
        for chunk in batches(data, size, None):
            ids, mask, pos, y = collate(torch, data, chunk, pad_id, device)
            logits = last_logits(model, ids, mask, pos)
            loss += torch.nn.functional.cross_entropy(logits, y, reduction="sum").item()
            for row, i in enumerate(chunk):
                opts = data[i]["opts"]
                pick = opts[int(torch.argmax(logits[row, opts]))]
                hit = int(pick == data[i]["y"])
                ok += hit
                c = per.setdefault(data[i]["label"], [0, 0])
                c[0] += 1
                c[1] += hit
            n += len(chunk)
    model.train()
    return {"loss": loss / n, "acc": ok / n,
            "acc_by_label": {k: round(v[1] / v[0], 4) for k, v in sorted(per.items())}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", type=Path, required=True, help="local HF folder (Qwen3-1.7B)")
    ap.add_argument("--data", type=Path, default=Path("data/sft"))
    ap.add_argument("--out", type=Path, default=Path("models/p5/adapter"))
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--alpha", type=int, default=32)
    ap.add_argument("--dropout", type=float, default=0.05)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--epochs", type=float, default=1.0)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--accum", type=int, default=2)
    ap.add_argument("--warmup", type=float, default=0.03)
    ap.add_argument("--max-len", type=int, default=1024)
    ap.add_argument("--eval-every", type=int, default=100, help="optimizer steps")
    ap.add_argument("--max-steps", type=int, default=None, help="smoke test")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    netguard.forbid_network()
    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    random.seed(args.seed)
    torch.manual_seed(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type != "cuda":
        raise SystemExit("no CUDA device: training on CPU is not supported")

    tok = AutoTokenizer.from_pretrained(args.base, local_files_only=True)
    pad_id = tok.pad_token_id if tok.pad_token_id is not None else tok.eos_token_id
    train = encode(tok, load_rows(args.data / "train.jsonl"), args.max_len)
    val = encode(tok, load_rows(args.data / "val.jsonl"), args.max_len)
    print(f"train {len(train)}, val {len(val)}; max prompt "
          f"{max(len(d['ids']) for d in train)} tokens", flush=True)

    model = AutoModelForCausalLM.from_pretrained(args.base, local_files_only=True,
                                                 dtype=torch.bfloat16,
                                                 attn_implementation="sdpa").to(device)
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.enable_input_require_grads()
    model = get_peft_model(model, LoraConfig(r=args.rank, lora_alpha=args.alpha,
                                             lora_dropout=args.dropout, target_modules=TARGETS,
                                             task_type="CAUSAL_LM"))
    model.print_trainable_parameters()

    steps_per_epoch = math.ceil(len(batches(train, args.batch, None)) / args.accum)
    total = args.max_steps or math.ceil(steps_per_epoch * args.epochs)
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=args.lr,
                            weight_decay=0.0)
    warm = max(1, int(total * args.warmup))
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1.0, (s + 1) / warm) * 0.5 * (
        1 + math.cos(math.pi * min(1.0, max(0, s - warm) / max(1, total - warm)))))

    args.out.mkdir(parents=True, exist_ok=True)
    log_path = args.out / "train_log.jsonl"
    log_path.write_text("", encoding="utf-8")

    def record(entry: dict) -> None:
        with open(log_path, "a", encoding="utf-8") as log:
            log.write(json.dumps(entry) + "\n")
        print(json.dumps({k: v for k, v in entry.items() if k != "acc_by_label"}), flush=True)

    base_eval = evaluate(torch, model, val, pad_id, device, args.batch * 2)
    record({"step": 0, "val": True, **base_eval})
    best = base_eval["loss"]
    rng = random.Random(args.seed)
    step = micro = 0
    t0 = time.perf_counter()
    running = []
    model.train()
    while step < total:
        for chunk in batches(train, args.batch, rng):
            ids, mask, pos, y = collate(torch, train, chunk, pad_id, device)
            loss = torch.nn.functional.cross_entropy(last_logits(model, ids, mask, pos), y)
            (loss / args.accum).backward()
            running.append(loss.item())
            micro += 1
            if micro % args.accum:
                continue
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            opt.zero_grad(set_to_none=True)
            step += 1
            if step % 10 == 0:
                record({"step": step, "loss": sum(running) / len(running),
                        "lr": sched.get_last_lr()[0], "secs": round(time.perf_counter() - t0)})
                running = []
            if step % args.eval_every == 0 or step == total:
                ev = evaluate(torch, model, val, pad_id, device, args.batch * 2)
                record({"step": step, "val": True, **ev})
                if ev["loss"] < best:
                    best = ev["loss"]
                    model.save_pretrained(args.out)
                    record({"step": step, "saved": True, "best_val_loss": best})
            if step >= total:
                break
    if not (args.out / "adapter_config.json").exists():
        raise SystemExit("validation loss never improved on the base model; no adapter saved")
    meta = {"base": str(args.base), "data": json.loads((args.data / "manifest.json").read_text(
        encoding="utf-8"))["sets"], "args": {k: str(v) for k, v in vars(args).items()},
        "best_val_loss": best, "base_val": base_eval, "steps": step,
        "minutes": round((time.perf_counter() - t0) / 60, 1),
        "peak_vram_gib": round(torch.cuda.max_memory_allocated() / 2**30, 2)}
    (args.out / "train_meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(f"done: best val loss {best:.4f} (base {base_eval['loss']:.4f}), {meta['minutes']} min, "
          f"peak VRAM {meta['peak_vram_gib']} GiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
