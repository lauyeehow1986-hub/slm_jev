"""Launch a llama-server for the judge with the flags fixed in docs/decisions/0002.

Loopback only, a fresh random API key per launch, no web UI, reasoning off, CPU by default. The
key is returned to the caller and never written to disk or logs by this module.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ENV_BIN = "SLMJEV_LLAMA_SERVER"
ENV_MODEL = "SLMJEV_JUDGE_MODEL"


@dataclass
class Server:
    proc: subprocess.Popen
    url: str
    key: str

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def __enter__(self) -> Server:
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


def command(binary: str | os.PathLike, model: str | os.PathLike, *, port: int = 8089,
            ctx: int = 4096, parallel: int = 1, gpu_layers: int = 0,
            threads: int | None = None, cache_ram: int = 256) -> list[str]:
    """The launch command. The API key is not in it: it goes in ``LLAMA_API_KEY`` so it does not
    show on the process command line.

    ``cache_ram`` (MiB) caps llama-server's host-RAM prompt cache. Its default of 8 GiB took a
    1.7B server to 6.5 GB private memory in P3, over the 4 GB model share. The judge only needs
    the live slot's KV for prefix reuse (docs/decisions/0003)."""
    cmd = [str(binary), "-m", str(model), "--host", "127.0.0.1", "--port", str(port),
           "-c", str(ctx), "-np", str(parallel), "-ngl", str(gpu_layers), "--no-webui",
           "--reasoning", "off", "--cache-ram", str(cache_ram)]
    if threads:
        cmd += ["-t", str(threads)]
    return cmd


def _get(url: str, key: str | None) -> int:
    """HTTP status of a GET (0 if unreachable)."""
    req = urllib.request.Request(url)
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            json.loads(r.read().decode("utf-8"))
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return 0


def start(binary: str | os.PathLike | None = None, model: str | os.PathLike | None = None, *,
          port: int = 8089, log: str | os.PathLike | None = None, wait: float = 180,
          **kw) -> Server:
    """Start llama-server and wait until ``/health`` is ok. ``binary`` and ``model`` default
    to ``$SLMJEV_LLAMA_SERVER`` and ``$SLMJEV_JUDGE_MODEL``; nothing is ever downloaded."""
    binary = binary or os.environ.get(ENV_BIN)
    model = model or os.environ.get(ENV_MODEL)
    for what, p in (("llama-server binary", binary), ("model", model)):
        if not p or not Path(p).is_file():
            raise FileNotFoundError(f"{what} not found: {p!r}")
    key = secrets.token_urlsafe(24)
    out = open(log, "ab") if log else subprocess.DEVNULL  # noqa: SIM115 - child inherits it
    try:
        proc = subprocess.Popen(command(binary, model, port=port, **kw),
                                env=os.environ | {"LLAMA_API_KEY": key},
                                stdout=out, stderr=subprocess.STDOUT)
    finally:
        if log:
            out.close()
    srv = Server(proc, f"http://127.0.0.1:{port}", key)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"llama-server exited with code {proc.returncode}")
        if _get(srv.url + "/health", key) == 200:
            if _get(srv.url + "/v1/models", None) != 401:  # the key must be enforced
                srv.stop()
                raise RuntimeError("llama-server answered without the API key; refusing")
            return srv
        time.sleep(0.5)
    srv.stop()
    raise TimeoutError(f"llama-server not healthy after {wait}s")
