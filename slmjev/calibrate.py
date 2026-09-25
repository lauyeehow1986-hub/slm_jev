"""Calibration metrics (P3) — fitting temperature / isotonic calibrators is P4."""

from __future__ import annotations

from collections.abc import Sequence


def ece(probs: Sequence[float], labels: Sequence[bool | int], n_bins: int = 10) -> float:
    """Expected calibration error with ``n_bins`` equal-width bins over [0, 1]."""
    if len(probs) != len(labels):
        raise ValueError("probs and labels differ in length")
    if not probs:
        raise ValueError("no predictions")
    bins: list[list[tuple[float, float]]] = [[] for _ in range(n_bins)]
    for p, y in zip(probs, labels, strict=True):
        if not 0.0 <= p <= 1.0:
            raise ValueError(f"probability out of range: {p}")
        bins[min(int(p * n_bins), n_bins - 1)].append((p, float(y)))
    n = len(probs)
    return sum(len(b) / n * abs(sum(p for p, _ in b) / len(b) - sum(y for _, y in b) / len(b))
               for b in bins if b)


def brier(probs: Sequence[float], labels: Sequence[bool | int]) -> float:
    if len(probs) != len(labels) or not probs:
        raise ValueError("need equal-length, non-empty inputs")
    return sum((p - float(y)) ** 2 for p, y in zip(probs, labels, strict=True)) / len(probs)


def auroc(probs: Sequence[float], labels: Sequence[bool | int]) -> float | None:
    """Area under the ROC curve (Mann-Whitney, ties count half); None if one class is absent."""
    pos = [p for p, y in zip(probs, labels, strict=True) if y]
    neg = [p for p, y in zip(probs, labels, strict=True) if not y]
    if not pos or not neg:
        return None
    wins = sum((a > b) + 0.5 * (a == b) for a in pos for b in neg)
    return wins / (len(pos) * len(neg))
