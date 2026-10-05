"""Tools for the governed real-data validation (``docs/real_data_validation.md``). Stdlib only.

The real data never passes through this repository. These commands read it only to hash it or to
count it. They print counts, hashes and note IDs, never note text, and they refuse to write into
a git working tree or a synced folder::

    python eval/realval.py pack --out C:/slmjev_kit --judge models/p5/<judge>.gguf \\
        --calibration models/calibration_p22.json --llama <llama.cpp bin dir> \\
        --python <portable python dir> --pf-model <privacy filter dir> --sd-repo <SD checkout>
    python eval/realval.py check D:/realval/sets/part_B1.txt
    python eval/realval.py hash D:/realval/freeze.sha256 <file or folder> ...
    python eval/realval.py verify D:/realval/freeze.sha256
    python eval/realval.py guard D:/realval/reports/B1.json

``pack`` builds a portable kit: one folder to copy onto the approved machine and run with no
admin install. It holds the code at a commit, the judge, the calibration, llama.cpp (CPU only,
with its Visual C++ runtime DLLs beside it), a portable Python with Privacy Filter and Presidio,
batch launchers, and a ``MANIFEST.sha256`` that ``verify`` checks on arrival.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from collections import Counter
from pathlib import Path

_EVAL = Path(__file__).resolve().parent
sys.path[:0] = [str(_EVAL), str(_EVAL.parent)]

import bench  # noqa: E402
import pool  # noqa: E402

MANIFEST = "MANIFEST.sha256"
SECS_PER_1K = 64  # P25 blind mean on this laptop's CPU (0022)
SYNC_MARKERS = ("onedrive", "dropbox", "google drive", "googledrive", "icloud")
VC_RUNTIME = ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll", "vcomp140.dll")
SKIP_LLAMA = ("ggml-cuda.dll",)  # the target is CPU only; this one is 220 MB
SMOKE_SET = "eval/bench/notes_v27.txt"
SMOKE_NOTES = 4


# --- where output may go -------------------------------------------------------------------

def git_root(path: Path) -> Path | None:
    """The git working tree that contains ``path`` (which need not exist yet), or None."""
    p = path.resolve()
    for q in (p, *p.parents):
        if (q / ".git").exists():
            return q
    return None


def unsafe_reason(path: Path) -> str | None:
    """Why ``path`` must not receive validation output, or None if it may."""
    if root := git_root(path):
        return f"it is inside the git working tree {root}"
    low = str(path.resolve()).lower()
    if hit := next((m for m in SYNC_MARKERS if m in low), None):
        return f"it looks like a synced folder ({hit})"
    return None


def guard(path: Path) -> None:
    if why := unsafe_reason(path):
        raise SystemExit(f"refusing to write {path}: {why}. Use a folder outside any git "
                         "repository and any synced folder.")


# --- hashing --------------------------------------------------------------------------------

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def files_under(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        if p.is_dir():
            out += sorted(q for q in p.rglob("*") if q.is_file())
        elif p.is_file():
            out.append(p)
        else:
            raise SystemExit(f"no such file or folder: {p}")
    return out


def _name(path: Path, base: Path) -> str:
    """``path`` relative to ``base`` when it is under it, else absolute; always with '/'."""
    p = path.resolve()
    try:
        return p.relative_to(base.resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def write_manifest(manifest: Path, paths: list[Path]) -> int:
    """Hash every file under ``paths`` into ``manifest`` (sha256sum format); returns the count."""
    base = manifest.parent
    files = [f for f in files_under(paths) if f.resolve() != manifest.resolve()]
    lines = [f"{sha256(f)} *{_name(f, base)}" for f in files]
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)


def verify_manifest(manifest: Path) -> list[str]:
    """Problems found (missing or changed files); empty if everything matches."""
    base, problems = manifest.parent, []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, name = line.split(" ", 1)
        f = Path(name.lstrip("*"))
        f = f if f.is_absolute() else base / f
        if not f.is_file():
            problems.append(f"missing: {name.lstrip('*')}")
        elif sha256(f) != digest:
            problems.append(f"changed: {name.lstrip('*')}")
    return problems


# --- checking an annotated set (counts only) ------------------------------------------------

def _comment_lines(src: str) -> int:
    """Lines inside notes that ``bench.parse_markup`` skips as comments ("#" or "# ...")."""
    inside, n = False, 0
    for line in src.splitlines():
        if re.match(r"=== (\S+) \| (.+) ===$", line):
            inside = True
        elif inside and (line == "#" or line.startswith("# ")):
            n += 1
    return n


def check_set(path: Path) -> dict:
    """Counts for one annotated set. Errors name the note and offsets, never the text."""
    src = path.read_text(encoding="utf-8-sig")
    try:
        docs = bench.parse_markup(src)
    except ValueError as e:
        raise SystemExit(f"{path.name}: {str(e).split(':')[0]}: markup error (unknown label or "
                         "unbalanced {{ }})") from None
    for d in docs:
        for g in d["spans"]:
            if d["text"][g["start"] - 1:g["end"]] != g["match"]:
                raise SystemExit(f"{path.name}: {d['id']}: gold span at {g['start']}-{g['end']} "
                                 "does not match the text")
    ids = Counter(d["id"] for d in docs)
    labels = Counter(g["label"] for d in docs for g in d["spans"])
    identifiers = set(bench.IDENTIFIERS)
    chars = sum(len(d["text"]) for d in docs)
    return {
        "set": path.name, "notes": len(docs), "chars": chars,
        "duplicate_ids": sorted(i for i, n in ids.items() if n > 1),
        "negative_notes": sum(not any(g["label"] in identifiers for g in d["spans"])
                              for d in docs),
        "direct_gold": sum(n for lab, n in labels.items() if lab in pool.DIRECT),
        "identifier_gold": sum(n for lab, n in labels.items() if lab in identifiers),
        "shi_gold": sum(n for lab, n in labels.items() if lab in bench.SHI),
        "by_label": dict(sorted(labels.items())),
        "kinds": dict(Counter(d["kind"] for d in docs)),
        "skipped_comment_lines": _comment_lines(src),
        "est_hours": round(chars / 1000 * SECS_PER_1K / 3600, 1),
    }


def print_check(c: dict) -> None:
    print(f"{c['set']}: {c['notes']} notes, {c['chars']:,} chars, est. {c['est_hours']} h to run")
    print(f"  gold: {c['direct_gold']} direct, {c['identifier_gold']} identifier, "
          f"{c['shi_gold']} SHI; {c['negative_notes']} notes with no identifier")
    print("  by label: " + ", ".join(f"{k} {v}" for k, v in c["by_label"].items()))
    print("  by kind: " + ", ".join(f"{k} {v}" for k, v in c["kinds"].items()))
    if c["duplicate_ids"]:
        print("  WARNING duplicate note IDs: " + ", ".join(c["duplicate_ids"]))
    if c["skipped_comment_lines"]:
        print(f"  WARNING {c['skipped_comment_lines']} line(s) inside notes start with '#' and "
              "will be dropped as comments; indent them by one space or remove the '# '")


# --- the portable kit -----------------------------------------------------------------------

def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True).stdout


def export_commit(repo: Path, commit: str, dest: Path) -> str:
    """Extract ``commit`` of ``repo`` (committed files only) into ``dest``; returns the sha."""
    sha = _git(repo, "rev-parse", "--verify", f"{commit}^{{commit}}").decode().strip()
    with tarfile.open(fileobj=io.BytesIO(_git(repo, "archive", "--format=tar", sha))) as t:
        t.extractall(dest, filter="data")
    return sha


def smoke_set(src: str, n: int = SMOKE_NOTES) -> str:
    """The header comments and the first ``n`` notes of a synthetic set file."""
    out, notes = [], 0
    for line in src.splitlines():
        if line.startswith("=== "):
            notes += 1
            if notes > n:
                break
        out.append(line)
    return "\n".join(out).rstrip("\n") + "\n"


def _bat(lines: list[str]) -> str:
    return "\r\n".join(["@echo off", "setlocal", *lines]) + "\r\n"


def launchers(judge: str) -> dict[str, str]:
    """The kit's batch files. Every path is relative to the kit folder (%~dp0)."""
    run = (r'"%PY%" "%KIT%slm_jev\eval\bench.py" --systems rules,ner,pf,jev+pf+ner.person '
           r'--pf-model "%KIT%pf" --calibration "%SLMJEV_CALIBRATION%"')
    rv = r'"%PY%" "%KIT%slm_jev\eval\realval.py"'
    return {
        "env.bat": "\r\n".join([
            "@echo off",
            "rem Paths are relative to this folder, so the kit runs wherever it is copied.",
            r'set "KIT=%~dp0"',
            r'set "PY=%KIT%python\python.exe"',
            r'set "PYTHONPATH=%KIT%slm_jev"',
            "set PYTHONIOENCODING=utf-8",
            "set PYTHONNOUSERSITE=1",
            "set PYTHONDONTWRITEBYTECODE=1",
            r'set "SE_PYTHON=%PY%"',
            r'set "SE_PF_DIR=%KIT%pf"',
            r'set "SLMJEV_SD_ROOT=%KIT%sd"',
            r'set "SLMJEV_ROOT=%KIT%slm_jev"',
            r'set "SLMJEV_LLAMA_SERVER=%KIT%llama\llama-server.exe"',
            rf'set "SLMJEV_JUDGE_MODEL=%KIT%models\{judge}"',
            r'set "SLMJEV_CALIBRATION=%KIT%models\calibration.json"',
            "set SLMJEV_TOKEN_SWEEP=",
            ""]),
        "1_verify_kit.bat": _bat([r'call "%~dp0env.bat"',
                                  rf'{rv} verify "%KIT%{MANIFEST}" || exit /b 1',
                                  r'"%PY%" -m slmjev.engine --probe']),
        "2_smoke.bat": _bat([
            r'call "%~dp0env.bat"',
            r'if "%~1"=="" (echo usage: 2_smoke.bat OUTPUT_FOLDER & exit /b 2)',
            rf'{rv} guard "%~1\smoke.json" || exit /b 2',
            rf'{run} --set "%KIT%smoke\smoke_notes.txt" --out "%~1\smoke.json"']),
        "3_check_set.bat": _bat([r'call "%~dp0env.bat"',
                                 r'if "%~1"=="" (echo usage: 3_check_set.bat SET.txt ... & '
                                 r'exit /b 2)',
                                 rf'{rv} check %*']),
        "4_freeze.bat": _bat([r'call "%~dp0env.bat"',
                              r'if "%~2"=="" (echo usage: 4_freeze.bat FREEZE.sha256 '
                              r'FILE_OR_FOLDER ... & exit /b 2)',
                              rf'{rv} hash %*']),
        "5_run_set.bat": _bat([
            r'call "%~dp0env.bat"',
            r'if "%~2"=="" (echo usage: 5_run_set.bat SET.txt REPORT.json & exit /b 2)',
            rf'{rv} guard "%~2" || exit /b 2',
            rf'{run} --set "%~1" --out "%~2"']),
        "6_pool.bat": _bat([
            r'call "%~dp0env.bat"',
            r'if "%~2"=="" (echo usage: 6_pool.bat POOLED.json REPORT.json ... & exit /b 2)',
            rf'{rv} guard "%~1" || exit /b 2',
            r'set "OUT=%~1"',
            "shift",
            "set REPORTS=",
            ":more",
            r'if "%~1"=="" goto run',
            r'set REPORTS=%REPORTS% "%~1"',
            "shift",
            "goto more",
            ":run",
            r'"%PY%" "%KIT%slm_jev\eval\pool.py" %REPORTS% --out "%OUT%"']),
        "7_verify_freeze.bat": _bat([r'call "%~dp0env.bat"',
                                     r'if "%~1"=="" (echo usage: 7_verify_freeze.bat '
                                     r'FREEZE.sha256 & exit /b 2)',
                                     rf'{rv} verify "%~1"']),
    }


KIT_README = """slm_jev real-data validation kit
================================
Built {date} from slm_jev {sha} and structured_deidentification {sd_sha}.
The procedure is slm_jev\\docs\\real_data_validation.md. Nothing here needs an install or admin
rights: copy this folder onto the approved machine and run the numbered .bat files from a
Command Prompt. No step uses the network.

  1_verify_kit.bat                         check every file against MANIFEST.sha256
  2_smoke.bat D:\\realval\\smoke             {smoke_n} synthetic notes end to end (a few minutes)
  3_check_set.bat D:\\realval\\sets\\x.txt    validate an annotated set; counts only
  4_freeze.bat D:\\realval\\freeze.sha256 <files/folders>   hash before the run
  5_run_set.bat <set.txt> <report.json>    run one set (about {secs} s per 1k characters)
  6_pool.bat <pooled.json> <report.json> ...   the gate verdict over the held-out reports
  7_verify_freeze.bat D:\\realval\\freeze.sha256   check nothing changed after the run

Reports (step 5) contain note text: keep them on this machine. Only pooled.json (counts) and
your cause counts leave it, after the data controller has checked them.

Contents: slm_jev\\ (code), sd\\ (structured_deidentification's engines), python\\ (portable
Python {pyver} with Privacy Filter, Presidio and spaCy), pf\\ (Privacy Filter model), llama\\
(llama.cpp, CPU, with the Visual C++ runtime DLLs beside it), models\\ (the judge and its
calibration), smoke\\ (synthetic notes only).
Antivirus: if llama\\llama-server.exe is quarantined, or a run stops with "judge backend
down", ask IT to allow this folder in the endpoint protection. On the development laptop the
security software froze a copied llama-server.exe (every thread suspended) 10-30 s into its
first run; the judge then stops the run after 3 failed calls instead of crawling for hours.
"""


def pack(a: argparse.Namespace) -> None:
    out = a.out
    guard(out)
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} is not empty")
    for p in (a.judge, a.calibration):
        if not p.is_file():
            raise SystemExit(f"missing file: {p}")
    server = next((a.llama / n for n in ("llama-server.exe", "llama-server")
                   if (a.llama / n).is_file()), None)
    if not server:
        raise SystemExit(f"no llama-server in {a.llama}")
    py = a.python / ("python.exe" if os.name == "nt" else "bin/python3")
    if not py.is_file():
        raise SystemExit(f"no Python at {py}")
    if not (a.pf_model / "config.json").is_file():
        raise SystemExit(f"{a.pf_model} does not look like the Privacy Filter model")
    out.mkdir(parents=True, exist_ok=True)

    step = lambda s: print(s, file=sys.stderr, flush=True)  # noqa: E731
    step("code ...")
    sha = export_commit(a.repo, a.commit, out / "slm_jev")
    sd_sha = export_commit(a.sd_repo, a.sd_commit, out / "sd")
    step("models ...")
    (out / "models").mkdir()
    shutil.copy2(a.judge, out / "models" / a.judge.name)
    shutil.copy2(a.calibration, out / "models" / "calibration.json")
    step("llama.cpp ...")
    shutil.copytree(a.llama, out / "llama",
                    ignore=shutil.ignore_patterns(*SKIP_LLAMA, "*.pdb", "*.lib", "*.exp"))
    sysdir = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32"
    for dll in VC_RUNTIME:
        if (sysdir / dll).is_file() and not (out / "llama" / dll).exists():
            shutil.copy2(sysdir / dll, out / "llama" / dll)
    step("python ...")
    shutil.copytree(a.python, out / "python",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    step("privacy filter model ...")
    shutil.copytree(a.pf_model, out / "pf")
    (out / "smoke").mkdir()
    src = (out / "slm_jev" / SMOKE_SET).read_text(encoding="utf-8")
    (out / "smoke" / "smoke_notes.txt").write_text(smoke_set(src), encoding="utf-8")
    for name, text in launchers(a.judge.name).items():
        (out / name).write_bytes(text.encode("utf-8"))
    try:
        pyver = subprocess.run([str(py), "-c", "import sys; print(sys.version.split()[0])"],
                               capture_output=True, text=True, timeout=60).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pyver = ""
    (out / "README_KIT.txt").write_bytes(KIT_README.format(
        date=time.strftime("%Y-%m-%d"), sha=sha[:7], sd_sha=sd_sha[:7], smoke_n=SMOKE_NOTES,
        secs=SECS_PER_1K, pyver=pyver or "3.x").replace("\n", "\r\n").encode("utf-8"))
    step("manifest ...")
    n = write_manifest(out / MANIFEST, [out])
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"kit: {out} ({n} files, {size / 1e9:.2f} GB); slm_jev {sha[:7]}, SD {sd_sha[:7]}")


# --- CLI ------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pack", help="build the portable validation kit")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--repo", type=Path, default=_EVAL.parent)
    p.add_argument("--commit", default="HEAD")
    p.add_argument("--sd-repo", type=Path, required=True)
    p.add_argument("--sd-commit", default="HEAD")
    p.add_argument("--judge", type=Path, required=True)
    p.add_argument("--calibration", type=Path, required=True)
    p.add_argument("--llama", type=Path, required=True, help="folder with llama-server")
    p.add_argument("--python", type=Path, required=True, help="portable Python folder")
    p.add_argument("--pf-model", type=Path, required=True)
    p = sub.add_parser("check", help="validate annotated sets; print counts only")
    p.add_argument("sets", nargs="+", type=Path)
    p = sub.add_parser("hash", help="hash files or folders into a manifest")
    p.add_argument("manifest", type=Path)
    p.add_argument("paths", nargs="+", type=Path)
    p = sub.add_parser("verify", help="check files against a manifest")
    p.add_argument("manifest", type=Path)
    p = sub.add_parser("guard", help="fail if a path is in a git tree or a synced folder")
    p.add_argument("path", type=Path)
    a = ap.parse_args(argv)

    if a.cmd == "pack":
        pack(a)
    elif a.cmd == "check":
        for s in a.sets:
            print_check(check_set(s))
    elif a.cmd == "hash":
        guard(a.manifest)
        print(f"{write_manifest(a.manifest, a.paths)} files hashed into {a.manifest}")
    elif a.cmd == "verify":
        problems = verify_manifest(a.manifest)
        for line in problems:
            print(line)
        n = sum(1 for line in a.manifest.read_text(encoding="utf-8").splitlines()
                if line.strip())
        print(f"{n - len(problems)} of {n} files match {a.manifest.name}")
        return 1 if problems else 0
    elif a.cmd == "guard":
        guard(a.path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
