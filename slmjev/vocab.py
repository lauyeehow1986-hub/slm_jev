"""The judge model's own vocabulary, read from its GGUF file (P25). Stdlib only, no model run.

A byte-level BPE vocabulary of ~150k tokens holds most common English words, in lower case, as one
token after a space (``Ġclinic``). A capitalised word that is *not* such a token is rarer: a
name (``Sharmila``, ``Ramli``), a place, a drug. ``slmjev.propose`` proposes those for judgment
(``shape:token``), so a name that no cue or engine found still reaches the judge.

Only the GGUF header is read: the key-value metadata up to ``tokenizer.ggml.tokens``.
"""

from __future__ import annotations

import struct
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO

_MAGIC = b"GGUF"
_SPACE = "Ġ"  # byte-level BPE marker for a leading space
# GGUF value types -> struct format of a fixed-size scalar
_FIXED = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f", 7: "?", 10: "Q", 11: "q",
          12: "d"}
_STRING, _ARRAY = 8, 9


class GGUFError(ValueError):
    pass


def _read(f: BinaryIO, n: int) -> bytes:
    b = f.read(n)
    if len(b) != n:
        raise GGUFError("truncated GGUF header")
    return b


def _u32(f: BinaryIO) -> int:
    return struct.unpack("<I", _read(f, 4))[0]


def _u64(f: BinaryIO) -> int:
    return struct.unpack("<Q", _read(f, 8))[0]


def _string(f: BinaryIO) -> str:
    return _read(f, _u64(f)).decode("utf-8", "replace")


def _skip(f: BinaryIO, vtype: int) -> None:
    if vtype in _FIXED:
        f.seek(struct.calcsize("<" + _FIXED[vtype]), 1)
    elif vtype == _STRING:
        f.seek(_u64(f), 1)
    elif vtype == _ARRAY:
        etype, n = _u32(f), _u64(f)
        if etype in _FIXED:
            f.seek(n * struct.calcsize("<" + _FIXED[etype]), 1)
        else:
            for _ in range(n):
                _skip(f, etype)
    else:
        raise GGUFError(f"unknown GGUF value type {vtype}")


def tokens(path: str | Path) -> list[str]:
    """The token strings of a GGUF model (``tokenizer.ggml.tokens``)."""
    with open(path, "rb") as f:
        if _read(f, 4) != _MAGIC:
            raise GGUFError(f"{path} is not a GGUF file")
        version = _u32(f)
        if version < 2:
            raise GGUFError(f"GGUF version {version} is not supported")
        _u64(f)  # tensor count
        for _ in range(_u64(f)):
            key, vtype = _string(f), _u32(f)
            if key != "tokenizer.ggml.tokens":
                _skip(f, vtype)
                continue
            if vtype != _ARRAY or _u32(f) != _STRING:
                raise GGUFError("tokenizer.ggml.tokens is not an array of strings")
            return [_string(f) for _ in range(_u64(f))]
    raise GGUFError(f"{path} has no tokenizer.ggml.tokens")


def lower_words(toks: list[str]) -> frozenset[str]:
    """The lower-case ASCII words that are one token after a space."""
    return frozenset(w for t in toks if t.startswith(_SPACE)
                     and (w := t[1:]).isascii() and w.isalpha() and w.islower())


@lru_cache(maxsize=4)
def model_words(path: str | None) -> frozenset[str] | None:
    """:func:`lower_words` of the GGUF model at ``path``; None if it cannot be read."""
    if not path:
        return None
    try:
        return lower_words(tokens(path))
    except (OSError, GGUFError, struct.error):
        return None
