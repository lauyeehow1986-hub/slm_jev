"""Write the synthetic corpus to data/synthetic/out/ (gitignored).

    uv run python data/synthetic/generate.py [--seed 7] [--notes 2000] [--cells 1500]

Dev and test get a fifth of the train size. Writes <split>.jsonl plus manifest.json with
per-file SHA-256, so an eval run can record exactly which corpus it used.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from slmjev import synth

OUT = Path(__file__).resolve().parent / "out"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--notes", type=int, default=2000, help="train notes")
    ap.add_argument("--cells", type=int, default=1500, help="train cells")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)

    labels = synth.load_labels()
    manifest = {"generator": synth.GENERATOR_VERSION, "labels": labels["version"],
                "seed": args.seed, "files": []}
    for split in synth.SPLITS:
        scale = 1 if split == "train" else 5
        docs = synth.generate(split, args.notes // scale, args.cells // scale, args.seed)
        for d in docs:
            synth.validate_doc(d, labels)
        entry = synth.write_jsonl(docs, args.out / f"{split}.jsonl")
        manifest["files"].append(entry | {"split": split,
                                          "n_spans": sum(len(d["spans"]) for d in docs)})
        print(f"{split:5s} {entry['n_docs']:6d} docs  {entry['sha256'][:12]}")
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                            encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
