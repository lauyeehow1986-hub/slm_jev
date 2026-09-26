"""Merge the P5 adapter into the base model and export a Q4_K_M GGUF (docs/decisions/0006).

    set PYTHONPATH=.
    <cuda python> finetune/export_gguf.py --base models/base/Qwen3-1.7B \\
        --adapter models/p5/adapter --out models/p5

Steps, all local: merge the LoRA into the bf16 weights (peft ``merge_and_unload``), convert with
llama.cpp's ``convert_hf_to_gguf.py`` to a bf16 GGUF, quantize with ``llama-quantize`` to Q4_K_M
(the tier of the zero-shot model, so latency and memory stay comparable). The llama.cpp folder
comes from ``--llama-cpp`` or ``$SLMJEV_LLAMA_CPP``. Writes ``export.json`` with SHA-256 sums.

The adapter was trained on bf16 weights, not 4-bit ones, so merging adds no quantization mismatch
beyond the final Q4_K_M step, which the eval measures.
"""

from __future__ import annotations

import os

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

import argparse  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

from slmjev import netguard  # noqa: E402

ENV_LLAMA_CPP = "SLMJEV_LLAMA_CPP"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def find_quantize(llama_cpp: Path) -> Path:
    for p in (llama_cpp / "llama-quantize.exe", llama_cpp / "build/bin/Release/llama-quantize.exe",
              llama_cpp / "build/bin/llama-quantize", llama_cpp / "llama-quantize"):
        if p.exists():
            return p
    raise SystemExit(f"llama-quantize not found under {llama_cpp}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", type=Path, required=True)
    ap.add_argument("--adapter", type=Path, default=Path("models/p5/adapter"))
    ap.add_argument("--out", type=Path, default=Path("models/p5"))
    ap.add_argument("--name", default="slmjev-judge-p5-qwen3-1.7b")
    ap.add_argument("--quant", default="Q4_K_M")
    ap.add_argument("--llama-cpp", type=Path, default=os.environ.get(ENV_LLAMA_CPP))
    args = ap.parse_args(argv)
    if not args.llama_cpp:
        raise SystemExit(f"--llama-cpp or ${ENV_LLAMA_CPP} is required")
    convert = args.llama_cpp / "convert_hf_to_gguf.py"
    quantize = find_quantize(args.llama_cpp)
    if not convert.exists():
        raise SystemExit(f"{convert} not found")

    netguard.forbid_network()
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    merged = args.out / "merged"
    tok = AutoTokenizer.from_pretrained(args.base, local_files_only=True)
    model = AutoModelForCausalLM.from_pretrained(args.base, local_files_only=True,
                                                 dtype=torch.bfloat16)
    model = PeftModel.from_pretrained(model, args.adapter).merge_and_unload()
    model.save_pretrained(merged, safe_serialization=True)
    tok.save_pretrained(merged)
    print(f"merged -> {merged}", flush=True)

    bf16 = args.out / f"{args.name}-BF16.gguf"
    final = args.out / f"{args.name}-{args.quant}.gguf"
    subprocess.run([sys.executable, str(convert), str(merged), "--outtype", "bf16",
                    "--outfile", str(bf16)], check=True)
    subprocess.run([str(quantize), str(bf16), str(final), args.quant], check=True)
    meta = {"base": str(args.base), "adapter": str(args.adapter), "quant": args.quant,
            "gguf": str(final), "sha256": sha256(final), "bytes": final.stat().st_size,
            "bf16_gguf": str(bf16), "bf16_sha256": sha256(bf16)}
    (args.out / "export.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(json.dumps(meta, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
