"""Build the P5 judge finetune data (docs/decisions/0006). Synthetic data only.

    python finetune/build_data.py            # -> data/sft/{train,val}.jsonl + manifest.json

Sources (train split pools; val from dev):
- the main corpus (``synth.generate``): every identifier, SHI and decoy type, notes and cells;
- hard decoys (``synth.generate_hard``): lab values after a clinic block or an ID keyword;
- lab/bill decoys (``synth.generate_labs``): lab and bill values shaped like postal codes, phones
  and MRNs, beside gold identifiers of the same shapes.

Never trained on: the calibration set (train seed 21) and every eval set (test seeds 31, 41, 51;
dev seed 11). Their documents are regenerated here and any identical text is dropped. Test uses
unseen name, street and lab-word pools, so it stays held out by construction as well.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from slmjev import sft, synth

# (generator, split, size, seed). Seeds are disjoint from every eval and calibration seed.
TRAIN = [("main", "train", (600, 900), 1000), ("hard", "train", 300, 1001),
         ("labs", "train", 600, 1002)]
VAL = [("main", "dev", (40, 60), 1010), ("hard", "dev", 30, 1011), ("labs", "dev", 60, 1012)]
HELD_OUT = [("main", "train", (40, 60), 21), ("main", "test", (40, 60), 31),
            ("main", "dev", (40, 60), 11), ("hard", "test", 400, 41), ("labs", "test", 200, 51)]


def generate(kind: str, split: str, size, seed: int) -> list[dict]:
    if kind == "main":
        return synth.generate(split, n_notes=size[0], n_cells=size[1], seed=seed)
    if kind == "hard":
        return synth.generate_hard(split, size, seed)
    return synth.generate_labs(split, size, seed)


def write(rows: list[dict], path: Path) -> dict:
    data = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8")
    path.write_bytes(data)
    return {"file": path.name, "n": len(rows), "sha256": hashlib.sha256(data).hexdigest()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=Path("data/sft"))
    ap.add_argument("--k", type=int, default=2, help="option rotations per candidate")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    held = {d["text"] for spec in HELD_OUT for d in generate(*spec)}
    args.out.mkdir(parents=True, exist_ok=True)
    manifest = {"format": sft.FORMAT, "generator": synth.GENERATOR_VERSION, "k": args.k,
                "seed": args.seed, "held_out": HELD_OUT, "sets": {}}
    for name, specs in (("train", TRAIN), ("val", VAL)):
        docs = [d for spec in specs for d in generate(*spec)]
        rows, stats = sft.build(docs, seed=args.seed + (name == "val"), k=args.k,
                                exclude_texts=held)
        manifest["sets"][name] = {**write(rows, args.out / f"{name}.jsonl"), "specs": specs,
                                  "docs": len(docs), "stats": dict(sorted(stats.items())),
                                  "answers": _count(rows, "answer"),
                                  "labels": _count(rows, "label"),
                                  "families": _count(rows, "family")}
        s = manifest["sets"][name]
        print(f"{name}: {s['n']} examples from {stats['candidates']} candidates "
              f"({stats['gold']} gold, {stats['decoy']} decoys), {len(docs)} docs; "
              f"skipped {stats['skip_fast_path']} fast-path, "
              f"{stats['skip_excluded_doc']} held-out docs")
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return 0


def _count(rows: list[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[r[key]] = out.get(r[key], 0) + 1
    return dict(sorted(out.items()))


if __name__ == "__main__":
    raise SystemExit(main())
