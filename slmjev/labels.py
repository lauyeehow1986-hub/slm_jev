"""The versioned label taxonomy (``schemas/labels.v1.json``)."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

LABELS_PATH = Path(__file__).resolve().parents[1] / "schemas" / "labels.v1.json"


@cache
def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_labels(path: Path = LABELS_PATH) -> dict:
    """The label file as a dict (a fresh copy of the top level; nested values are shared)."""
    return dict(_load(str(path)))
