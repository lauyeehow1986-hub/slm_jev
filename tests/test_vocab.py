import struct

import pytest

from slmjev import vocab

G = "Ġ"


def _str(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack("<Q", len(b)) + b


def gguf(path, toks, magic=b"GGUF", version=3, before=True):
    """A minimal GGUF header: a few metadata pairs of other types, then the token list."""
    kv = []
    if before:
        kv += [_str("general.name") + struct.pack("<I", 8) + _str("tiny"),
               _str("general.file_type") + struct.pack("<I", 4) + struct.pack("<I", 15),
               _str("tokenizer.ggml.token_type") + struct.pack("<IIQ", 9, 5, 3)
               + struct.pack("<3i", 1, 1, 1),
               _str("tokenizer.ggml.merges") + struct.pack("<IIQ", 9, 8, 2)
               + _str("a b") + _str("c d"),
               _str("nested") + struct.pack("<IIQ", 9, 9, 1) + struct.pack("<IQ", 6, 2)
               + struct.pack("<2f", 0.5, 1.5)]
    if toks is not None:
        kv.append(_str("tokenizer.ggml.tokens") + struct.pack("<IIQ", 9, 8, len(toks))
                  + b"".join(_str(t) for t in toks))
    path.write_bytes(magic + struct.pack("<IQQ", version, 0, len(kv)) + b"".join(kv)
                     + b"\x00" * 16)
    return path


TOKS = [f"{G}clinic", f"{G}Clinic", "clinic", f"{G}naïve", f"{G}ward", f"{G}x1", f"{G}the", "<s>"]


def test_tokens_skip_other_metadata(tmp_path):
    p = gguf(tmp_path / "m.gguf", TOKS)
    assert vocab.tokens(p) == TOKS
    assert vocab.lower_words(TOKS) == {"clinic", "ward", "the"}


@pytest.mark.parametrize("kw", [{"magic": b"GGML"}, {"version": 1}, {"toks": None}])
def test_bad_files_raise_and_read_as_no_vocabulary(tmp_path, kw):
    p = gguf(tmp_path / "bad.gguf", **{"toks": TOKS, **kw})
    with pytest.raises(vocab.GGUFError):
        vocab.tokens(p)
    assert vocab.model_words(str(p)) is None


def test_truncated_and_missing_files(tmp_path):
    p = gguf(tmp_path / "t.gguf", TOKS)
    p.write_bytes(p.read_bytes()[:60])
    assert vocab.model_words(str(p)) is None
    assert vocab.model_words(str(tmp_path / "none.gguf")) is None
    assert vocab.model_words(None) is None
    assert vocab.model_words(str(gguf(tmp_path / "ok.gguf", TOKS))) == {"clinic", "ward", "the"}
