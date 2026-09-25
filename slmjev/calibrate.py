"""Calibration: metrics, calibrators fitted on held-out synthetic data, and threshold choice.

A calibrator maps the judge's raw ``p_identifier`` to a probability that matches observed
frequencies. Two are provided:

- ``Temperature``: one parameter, ``sigmoid(logit(p) / T)``. Monotone and smooth; hard to overfit.
- ``Isotonic``: pool-adjacent-violators, a monotone step function. More flexible; needs more data.

``choose_thresholds`` then picks the decision thresholds *in calibrated space* from the recall
and precision targets, and ``save``/``load`` keep both in one JSON file so the judge can be run
with exactly what was fitted. Everything is stdlib and deterministic.
"""

from __future__ import annotations

import json
import math
import os
from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

EPS = 1e-6
FORMAT = "slmjev.calibration.v1"

# --- metrics -------------------------------------------------------------------------------


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


def nll(probs: Sequence[float], labels: Sequence[bool | int]) -> float:
    """Mean negative log-likelihood (log loss), with probabilities clipped to [EPS, 1 - EPS]."""
    if len(probs) != len(labels) or not probs:
        raise ValueError("need equal-length, non-empty inputs")
    tot = 0.0
    for p, y in zip(probs, labels, strict=True):
        p = min(1 - EPS, max(EPS, p))
        tot -= math.log(p) if y else math.log(1 - p)
    return tot / len(probs)


# --- calibrators ---------------------------------------------------------------------------


def _logit(p: float) -> float:
    p = min(1 - EPS, max(EPS, p))
    return math.log(p / (1 - p))


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1 / (1 + math.exp(-z))
    e = math.exp(z)
    return e / (1 + e)


@dataclass(frozen=True)
class Identity:
    kind = "identity"

    def __call__(self, p: float) -> float:
        return p

    def to_dict(self) -> dict:
        return {"kind": self.kind}


@dataclass(frozen=True)
class Temperature:
    """``sigmoid(logit(p) / t)``; ``t > 1`` softens overconfident scores."""

    t: float
    kind = "temperature"

    def __call__(self, p: float) -> float:
        return _sigmoid(_logit(p) / self.t)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "t": self.t}

    @classmethod
    def fit(cls, probs: Sequence[float], labels: Sequence[bool | int],
            lo: float = 0.05, hi: float = 20.0, iters: int = 80) -> Temperature:
        """Minimise log loss over ``log t`` by golden-section search (NLL is unimodal in it)."""
        if not probs:
            raise ValueError("no data to fit")
        z = [_logit(p) for p in probs]

        def loss(log_t: float) -> float:
            t = math.exp(log_t)
            return nll([_sigmoid(v / t) for v in z], labels)

        a, b = math.log(lo), math.log(hi)
        g = (math.sqrt(5) - 1) / 2
        c, d = b - g * (b - a), a + g * (b - a)
        fc, fd = loss(c), loss(d)
        for _ in range(iters):
            if fc < fd:
                b, d, fd = d, c, fc
                c = b - g * (b - a)
                fc = loss(c)
            else:
                a, c, fc = c, d, fd
                d = a + g * (b - a)
                fd = loss(d)
        return cls(round(math.exp((a + b) / 2), 6))


@dataclass(frozen=True)
class Isotonic:
    """A monotone step function fitted by pool-adjacent-violators.

    ``xs`` are the upper edges of the pooled blocks and ``ys`` their fitted rates. Outputs are
    clipped to ``[floor, 1 - floor]`` so no score becomes a hard 0 or 1 from a small block."""

    xs: tuple[float, ...]
    ys: tuple[float, ...]
    floor: float = 0.005
    kind = "isotonic"

    def __call__(self, p: float) -> float:
        # the first block whose upper edge is at or above p (the last block beyond the data)
        i = min(bisect_left(self.xs, p), len(self.ys) - 1)
        return min(1 - self.floor, max(self.floor, self.ys[i]))

    def to_dict(self) -> dict:
        return {"kind": self.kind, "xs": list(self.xs), "ys": list(self.ys), "floor": self.floor}

    @classmethod
    def fit(cls, probs: Sequence[float], labels: Sequence[bool | int],
            floor: float = 0.005) -> Isotonic:
        if not probs:
            raise ValueError("no data to fit")
        pts = sorted(zip(probs, (float(y) for y in labels), strict=True))
        blocks: list[list[float]] = []  # [sum_y, n, max_x]
        for x, y in pts:
            blocks.append([y, 1.0, x])
            while (len(blocks) > 1
                   and blocks[-2][0] / blocks[-2][1] >= blocks[-1][0] / blocks[-1][1]):
                s, n, mx = blocks.pop()
                blocks[-1][0] += s
                blocks[-1][1] += n
                blocks[-1][2] = mx
        return cls(tuple(round(b[2], 6) for b in blocks),
                   tuple(round(b[0] / b[1], 6) for b in blocks), floor)


Calibrator = Identity | Temperature | Isotonic
_KINDS = {"identity": Identity, "temperature": Temperature, "isotonic": Isotonic}


def fit(kind: str, probs: Sequence[float], labels: Sequence[bool | int]) -> Calibrator:
    if kind == "identity":
        return Identity()
    if kind not in _KINDS:
        raise ValueError(f"unknown calibrator {kind!r}")
    return _KINDS[kind].fit(probs, labels)


def from_dict(d: dict) -> Calibrator:
    kind = d.get("kind")
    if kind == "identity":
        return Identity()
    if kind == "temperature":
        return Temperature(float(d["t"]))
    if kind == "isotonic":
        xs, ys = tuple(map(float, d["xs"])), tuple(map(float, d["ys"]))
        if len(xs) != len(ys) or not xs or list(xs) != sorted(xs) or list(ys) != sorted(ys):
            raise ValueError("isotonic calibrator must have matching, sorted xs and ys")
        return Isotonic(xs, ys, float(d.get("floor", 0.005)))
    raise ValueError(f"unknown calibrator kind {kind!r}")


# --- thresholds ----------------------------------------------------------------------------


@dataclass(frozen=True)
class ThresholdChoice:
    drop_below: float
    accept_at: float
    recall_at_drop: float | None  # share of positives with p >= drop_below
    precision_at_accept: float | None  # share of positives among p >= accept_at
    notes: list[str] = field(default_factory=list)


def choose_thresholds(probs: Sequence[float], labels: Sequence[bool | int], *,
                      recall_target: float = 0.98, precision_target: float = 0.98,
                      max_drop: float = 0.5, min_accept: float = 0.5,
                      min_support: int = 20) -> ThresholdChoice:
    """Thresholds from calibrated scores on a *fitting* split.

    - ``drop_below``: the highest cut that still keeps ``recall_target`` of the positives at or
      above it, capped at ``max_drop``. Scores below it are dropped without review.
    - ``accept_at``: the lowest cut whose accepted set (``p >= cut``, at least ``min_support``
      items) reaches ``precision_target``, floored at ``min_accept``; 1.0 (accept nothing) if none.

    Fail closed: with too little data the thresholds fall back to "drop nothing, accept nothing"
    and ``notes`` says why, so everything goes to review."""
    pos = sorted(p for p, y in zip(probs, labels, strict=True) if y)
    notes: list[str] = []
    if len(pos) < min_support:
        notes.append(f"only {len(pos)} positives; thresholds not fitted")
        return ThresholdChoice(0.0, 1.0, None, None, notes)
    # allowed misses k: the k lowest positives may fall below the cut
    k = math.floor(len(pos) * (1 - recall_target) + 1e-9)
    drop = min(pos[k], max_drop) if k < len(pos) else 0.0
    # nudge just below the kept positive so it is not dropped by a tie
    drop = max(0.0, drop - 1e-6)
    recall = sum(p >= drop for p in pos) / len(pos)

    pairs = sorted(zip(probs, (bool(y) for y in labels), strict=True), reverse=True)
    accept, prec_at = 1.0, None
    tp = 0
    for n, (p, y) in enumerate(pairs, 1):
        i = n - 1
        tp += y
        last_of_tie = i + 1 == len(pairs) or pairs[i + 1][0] < p
        if last_of_tie and n >= min_support and p >= min_accept and tp / n >= precision_target:
            accept, prec_at = p, tp / n
    if prec_at is None:
        notes.append("no cut reached the precision target; nothing is auto-accepted")
    if accept <= drop:
        notes.append("accept_at <= drop_below; review band is empty")
    return ThresholdChoice(round(drop, 6), round(accept, 6), round(recall, 4),
                           None if prec_at is None else round(prec_at, 4), notes)


# --- persistence ---------------------------------------------------------------------------


def save(path: str | os.PathLike, calibrator: Calibrator, thresholds: dict, meta: dict) -> None:
    doc = {"format": FORMAT, "calibrator": calibrator.to_dict(), "thresholds": thresholds,
           "meta": meta}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")


def load(path: str | os.PathLike) -> tuple[Calibrator, dict, dict]:
    """``(calibrator, thresholds, meta)`` from a file written by ``save``."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("format") != FORMAT:
        raise ValueError(f"not a {FORMAT} file: {path}")
    th = doc["thresholds"]
    if not 0.0 <= th["drop_below"] <= th["accept_at"] <= 1.0:
        raise ValueError("thresholds must satisfy 0 <= drop_below <= accept_at <= 1")
    return from_dict(doc["calibrator"]), th, doc.get("meta", {})
