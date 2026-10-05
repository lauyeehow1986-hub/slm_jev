import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_EVAL = Path(__file__).resolve().parents[1] / "eval"
sys.path.insert(0, str(_EVAL))
_spec = importlib.util.spec_from_file_location("realval", _EVAL / "realval.py")
realval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(realval)

# synthetic only: invented people and numbers
SET = """# synthetic test set
=== t_n001 | discharge ===
Pt {{name|Tan Ah Kow}}, NRIC {{national_id|S1234567D}}, seen for {{mental_health|depression}}.
Call {{phone|9123 4567}}.
=== t_n002 | letter ===
No identifiers in this note.
=== t_n003 | ed ===
{{name|Siti Nurhaliza}} reviewed.
"""


def test_hash_then_verify_catches_changed_and_missing_files(tmp_path):
    (tmp_path / "d").mkdir()
    a, b = tmp_path / "d" / "a.txt", tmp_path / "b.txt"
    a.write_text("one", encoding="utf-8")
    b.write_text("two", encoding="utf-8")
    m = tmp_path / "freeze.sha256"
    assert realval.write_manifest(m, [tmp_path / "d", b]) == 2
    assert "*d/a.txt" in m.read_text(encoding="utf-8")
    assert realval.verify_manifest(m) == []
    a.write_text("changed", encoding="utf-8")
    b.unlink()
    assert sorted(realval.verify_manifest(m)) == ["changed: d/a.txt", "missing: b.txt"]


def test_a_manifest_never_lists_itself(tmp_path):
    (tmp_path / "x.txt").write_text("x", encoding="utf-8")
    m = tmp_path / realval.MANIFEST
    m.write_text("stale", encoding="utf-8")
    assert realval.write_manifest(m, [tmp_path]) == 1
    assert realval.MANIFEST not in m.read_text(encoding="utf-8")


def test_git_root_finds_the_enclosing_working_tree(tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    assert realval.git_root(tmp_path / "repo" / "not" / "yet" / "made.json") == \
        (tmp_path / "repo").resolve()


def test_guard_refuses_git_trees_and_synced_folders(tmp_path, monkeypatch):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    assert "git working tree" in realval.unsafe_reason(tmp_path / "repo" / "r.json")
    with pytest.raises(SystemExit):
        realval.guard(tmp_path / "repo" / "r.json")
    monkeypatch.setattr(realval, "git_root", lambda p: None)
    assert "synced" in realval.unsafe_reason(tmp_path / "OneDrive - Org" / "r.json")
    assert realval.unsafe_reason(tmp_path / "realval" / "r.json") is None


def test_check_counts_a_set_and_prints_no_text(tmp_path, capsys):
    s = tmp_path / "part_B1.txt"
    s.write_text(SET, encoding="utf-8")
    c = realval.check_set(s)
    assert (c["notes"], c["direct_gold"], c["identifier_gold"], c["shi_gold"]) == (3, 4, 4, 1)
    assert c["negative_notes"] == 1 and c["duplicate_ids"] == []
    assert c["by_label"] == {"mental_health": 1, "name": 2, "national_id": 1, "phone": 1}
    realval.print_check(c)
    out = capsys.readouterr().out
    for text in ("Tan Ah Kow", "S1234567D", "9123", "Siti", "depression"):
        assert text not in out


def test_check_errors_name_the_note_not_its_text(tmp_path):
    s = tmp_path / "bad.txt"
    s.write_text("=== t_n009 | ed ===\nPt {{nmae|Tan Ah Kow}} seen.\n", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        realval.check_set(s)
    assert "t_n009" in str(e.value) and "Tan" not in str(e.value)


def test_check_warns_about_lines_that_would_be_dropped_as_comments(tmp_path, capsys):
    s = tmp_path / "c.txt"
    s.write_text("# header\n=== t_n001 | ward ===\nProblems:\n# fracture L wrist\n#\nok\n",
                 encoding="utf-8")
    c = realval.check_set(s)
    assert c["skipped_comment_lines"] == 2
    realval.print_check(c)
    assert "WARNING 2 line(s)" in capsys.readouterr().out


def test_check_flags_duplicate_note_ids(tmp_path):
    s = tmp_path / "d.txt"
    s.write_text("=== t_n001 | a ===\nx\n=== t_n001 | b ===\ny\n", encoding="utf-8")
    assert realval.check_set(s)["duplicate_ids"] == ["t_n001"]


def test_smoke_set_keeps_the_header_and_the_first_notes():
    out = realval.smoke_set(SET, 2)
    assert out.startswith("# synthetic test set") and "t_n002" in out and "t_n003" not in out


def test_launchers_only_use_kit_relative_paths():
    bats = realval.launchers("judge.gguf")
    assert {"env.bat", "1_verify_kit.bat", "5_run_set.bat", "6_pool.bat"} <= set(bats)
    for name, text in bats.items():
        assert ":\\" not in text and "C:/" not in text, name
        assert "\r\n" in text
    env = bats["env.bat"]
    assert r'set "KIT=%~dp0"' in env and r"%KIT%models\judge.gguf" in env
    assert "set SLMJEV_TOKEN_SWEEP=\r\n" in env  # the sweep stays off
    assert "jev+pf+ner.person" in bats["5_run_set.bat"]
    assert "guard" in bats["5_run_set.bat"] and "guard" in bats["6_pool.bat"]


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c",
                    "user.email=t@example.com", *args], check=True, capture_output=True)


def test_pack_builds_a_kit_that_verifies(tmp_path, monkeypatch):
    monkeypatch.setattr(realval, "unsafe_reason", lambda p: None)  # tmp is inside a home repo
    repo = tmp_path / "repo"
    (repo / "eval" / "bench").mkdir(parents=True)
    (repo / realval.SMOKE_SET).write_text(SET, encoding="utf-8")
    (repo / "uncommitted.txt").parent.mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "x")
    (repo / "uncommitted.txt").write_text("not in the kit", encoding="utf-8")
    src = tmp_path / "src"
    for d, files in {"llama": ["llama-server.exe", "ggml.dll", "ggml-cuda.dll"],
                     "python": ["python.exe"], "pf": ["config.json"]}.items():
        (src / d).mkdir(parents=True)
        for f in files:
            (src / d / f).write_text(f, encoding="utf-8")
    (src / "judge.gguf").write_text("gguf", encoding="utf-8")
    (src / "cal.json").write_text("{}", encoding="utf-8")
    out = tmp_path / "kit"
    realval.main(["pack", "--out", str(out), "--repo", str(repo), "--sd-repo", str(repo),
                  "--judge", str(src / "judge.gguf"), "--calibration", str(src / "cal.json"),
                  "--llama", str(src / "llama"), "--python", str(src / "python"),
                  "--pf-model", str(src / "pf")])
    assert (out / "slm_jev" / realval.SMOKE_SET).is_file()
    assert not (out / "slm_jev" / "uncommitted.txt").exists()
    assert (out / "models" / "judge.gguf").is_file()
    assert (out / "models" / "calibration.json").is_file()
    assert (out / "llama" / "llama-server.exe").is_file()
    assert not (out / "llama" / "ggml-cuda.dll").exists()
    assert "t_n001" in (out / "smoke" / "smoke_notes.txt").read_text(encoding="utf-8")
    assert (out / "5_run_set.bat").is_file() and (out / "README_KIT.txt").is_file()
    assert realval.verify_manifest(out / realval.MANIFEST) == []
    with pytest.raises(SystemExit):  # never into a non-empty folder
        realval.main(["pack", "--out", str(out), "--repo", str(repo), "--sd-repo", str(repo),
                      "--judge", str(src / "judge.gguf"), "--calibration",
                      str(src / "cal.json"), "--llama", str(src / "llama"), "--python",
                      str(src / "python"), "--pf-model", str(src / "pf")])
