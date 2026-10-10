"""Launch the judge's model server: llama-server with the flags fixed in docs/decisions/0002,
or a portable ``ollama serve`` (docs/decisions/0025).

Loopback only. llama-server gets a fresh random API key per launch, no web UI, reasoning off,
CPU by default; the key is returned to the caller and never written to disk or logs by this
module. Ollama has no API key, so it is started on a free port of its own, with its own model
store and cloud models off, and the engine checks the model it serves before trusting it.
"""

from __future__ import annotations

import http.client
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from slmjev.netguard import loopback_request

ENV_BIN = "SLMJEV_LLAMA_SERVER"
ENV_MODEL = "SLMJEV_JUDGE_MODEL"
ENV_OLLAMA = "SLMJEV_OLLAMA"
ENV_OLLAMA_MODELS = "SLMJEV_OLLAMA_MODELS"


def _kill_with_parent(proc: subprocess.Popen) -> object | None:
    """On Windows, put ``proc`` in a job object that kills it when this process exits, even
    on a hard crash, so a dead engine never leaves a 2 GB server behind. Returns the job handle,
    which must stay referenced while ``proc`` runs; None where unsupported (the caller's
    ``stop()`` still applies)."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class _Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class _Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _Basic), ("IoInfo", ctypes.c_uint64 * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                            wintypes.DWORD]
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    job = k32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = _Extended()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = (k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
          and k32.AssignProcessToJobObject(job, int(proc._handle)))
    return job if ok else None


def _kill_job(job: object | None) -> None:
    """End every process left in ``job`` (a server's children)."""
    if job is None or sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.TerminateJobObject(job, 1)


@dataclass
class Server:
    proc: subprocess.Popen
    url: str
    key: str
    job: object | None = field(default=None, repr=False)

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        # ``ollama serve`` runs the model in a child process, which terminate() leaves behind
        _kill_job(self.job)

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
    """HTTP status of a GET (0 if unreachable, or a 200 that is not JSON)."""
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        status, data = loopback_request(url, headers=headers, timeout=5)
        if status == 200:
            json.loads(data.decode("utf-8"))
        return status
    except (OSError, ValueError, http.client.HTTPException):
        return 0


def free_port() -> int:
    """A loopback port nobody is listening on now, so concurrent engines never share one."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start(binary: str | os.PathLike | None = None, model: str | os.PathLike | None = None, *,
          port: int | None = None, log: str | os.PathLike | None = None, wait: float = 180,
          **kw) -> Server:
    """Start llama-server and wait until ``/health`` is ok. ``binary`` and ``model`` default
    to ``$SLMJEV_LLAMA_SERVER`` and ``$SLMJEV_JUDGE_MODEL``; nothing is ever downloaded.
    ``port`` defaults to a free one. The server counts as up only when it accepts this launch's
    key and refuses a request without it, so a foreign server on the same port is never used."""
    port = port or free_port()
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
    srv = Server(proc, f"http://127.0.0.1:{port}", key, job=_kill_with_parent(proc))
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"llama-server exited with code {proc.returncode}")
        if _get(srv.url + "/health", key) == 200 and _get(srv.url + "/v1/models", key) == 200:
            if _get(srv.url + "/v1/models", None) != 401:  # the key must be enforced
                srv.stop()
                raise RuntimeError("llama-server answered without the API key; refusing")
            return srv
        time.sleep(0.5)
    srv.stop()
    raise TimeoutError(f"llama-server not healthy after {wait}s")


def ollama_env(models: str | os.PathLike, port: int) -> dict[str, str]:
    """``ollama serve``'s settings for the judge: loopback only, the kit's own model store
    (``models``), cloud models off, nothing pruned, one model and one request at a time."""
    return {"OLLAMA_HOST": f"127.0.0.1:{port}", "OLLAMA_MODELS": str(models),
            "OLLAMA_NO_CLOUD": "1", "OLLAMA_NOPRUNE": "1", "OLLAMA_NUM_PARALLEL": "1",
            "OLLAMA_MAX_LOADED_MODELS": "1"}


def start_ollama(binary: str | os.PathLike | None = None,
                 models: str | os.PathLike | None = None, *, port: int | None = None,
                 log: str | os.PathLike | None = None, wait: float = 120) -> Server:
    """Start a portable ``ollama serve`` and wait until it answers. ``binary`` and ``models``
    default to ``$SLMJEV_OLLAMA`` and ``$SLMJEV_OLLAMA_MODELS`` (else ``models`` beside the
    binary). Nothing is pulled: the model must already be in ``models``. Ollama has no API key,
    so the caller must check the model it serves (``engine.open_backend`` does)."""
    port = port or free_port()
    binary = binary or os.environ.get(ENV_OLLAMA)
    if not binary or not Path(binary).is_file():
        raise FileNotFoundError(f"ollama binary not found: {binary!r}")
    models = models or os.environ.get(ENV_OLLAMA_MODELS) or Path(binary).parent / "models"
    if not Path(models).is_dir():
        raise FileNotFoundError(f"Ollama model folder not found: {str(models)!r}")
    out = open(log, "ab") if log else subprocess.DEVNULL  # noqa: SIM115 - child inherits it
    try:
        proc = subprocess.Popen([str(binary), "serve"], env=os.environ | ollama_env(models, port),
                                stdout=out, stderr=subprocess.STDOUT)
    finally:
        if log:
            out.close()
    srv = Server(proc, f"http://127.0.0.1:{port}", "", job=_kill_with_parent(proc))
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            srv.stop()
            raise RuntimeError(f"ollama serve exited with code {proc.returncode}")
        if _get(srv.url + "/api/version", None) == 200:
            return srv
        time.sleep(0.5)
    srv.stop()
    raise TimeoutError(f"ollama serve not answering after {wait}s")
