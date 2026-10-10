import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from slmjev import calibrate, netguard, server
from slmjev.labels import load_labels


def test_ece_and_brier():
    assert calibrate.ece([0.0, 1.0], [0, 1]) == 0.0
    # one bin at 0.8 with half positives: |0.8 - 0.5| = 0.3
    assert calibrate.ece([0.8, 0.8], [1, 0]) == pytest.approx(0.3)
    assert calibrate.brier([1.0, 0.0], [1, 0]) == 0.0
    with pytest.raises(ValueError):
        calibrate.ece([1.2], [1])


def test_auroc():
    assert calibrate.auroc([0.9, 0.8, 0.1], [1, 1, 0]) == 1.0
    assert calibrate.auroc([0.5, 0.5], [1, 0]) == 0.5
    assert calibrate.auroc([0.5], [1]) is None


def test_forbid_network_allows_only_loopback():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    netguard.forbid_network()
    try:
        with pytest.raises(netguard.NetworkForbidden):
            socket.create_connection(("203.0.113.9", 80), timeout=1)
        s = socket.socket()
        with pytest.raises(netguard.NetworkForbidden):
            s.connect(("example.com", 443))
        s.connect(listener.getsockname())  # loopback still works
        s.close()
    finally:
        netguard.allow_network()
        listener.close()


def test_server_command_is_loopback_cpu_and_keeps_key_off_argv():
    cmd = server.command("llama-server", "m.gguf", port=8123)
    assert cmd[cmd.index("--host") + 1] == "127.0.0.1"
    assert cmd[cmd.index("-ngl") + 1] == "0"
    assert "--no-webui" in cmd and cmd[cmd.index("--reasoning") + 1] == "off"
    assert "--api-key" not in cmd
    assert int(cmd[cmd.index("--cache-ram") + 1]) <= 1024  # host prompt cache stays bounded


def test_server_start_needs_existing_files(tmp_path):
    with pytest.raises(FileNotFoundError):
        server.start(tmp_path / "nope.exe", tmp_path / "nope.gguf")
    with pytest.raises(FileNotFoundError):
        server.start(sys.executable, tmp_path / "nope.gguf")


class _Foreign(BaseHTTPRequestHandler):
    """Someone else's llama-server: healthy, but it enforces a key we do not have."""

    def do_GET(self):  # noqa: N802
        status = 200 if self.path == "/health" else 401
        data = b'{"status": "ok"}' if status == 200 else b'{"error": "key"}'
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def foreign():
    srv = HTTPServer(("127.0.0.1", 0), _Foreign)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_port
    srv.shutdown()


def test_loopback_request_is_plain_http_to_this_machine(foreign):
    status, body = netguard.loopback_request(f"http://127.0.0.1:{foreign}/health")
    assert status == 200 and b"ok" in body
    assert netguard.loopback_request(f"http://127.0.0.1:{foreign}/v1/models")[0] == 401
    with pytest.raises(netguard.NetworkForbidden):
        netguard.loopback_request("http://203.0.113.9/health")
    with pytest.raises(netguard.NetworkForbidden):
        netguard.loopback_request(f"https://127.0.0.1:{foreign}/health")


def test_server_start_never_adopts_a_foreign_server(foreign, tmp_path):
    # our "llama-server" (python given llama flags) dies at once; the healthy server on the port
    # rejects our key, so start() must fail instead of returning a handle to it
    model = tmp_path / "m.gguf"
    model.write_bytes(b"")
    with pytest.raises(RuntimeError, match="exited"):
        server.start(sys.executable, model, port=foreign, wait=20)


@pytest.mark.skipif(sys.platform != "win32", reason="job objects are Windows-only")
def test_server_child_dies_with_a_crashed_parent(tmp_path):
    # the parent starts a long-lived child the way start() does, then dies without cleanup
    pidfile = tmp_path / "pid"
    parent = (
        "import os, subprocess, sys\n"
        "from slmjev import server\n"
        "D = subprocess.DEVNULL\n"  # detached stdio, as start() runs it: it outlives a parent
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],"
        " stdin=D, stdout=D, stderr=D)\n"
        "job = server._kill_with_parent(p)\n"
        f"open({str(pidfile)!r}, 'w').write(str(p.pid) if job else '')\n"
        "os._exit(1)\n")
    root = str(Path(server.__file__).resolve().parents[1])
    subprocess.run([sys.executable, "-c", parent], env=os.environ | {"PYTHONPATH": root},
                   timeout=30, check=False)
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 10
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not _alive(pid)


def _alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True,
                         text=True, check=False).stdout
    return str(pid) in out


def test_free_port_is_bindable():
    p = server.free_port()
    with socket.socket() as s:
        s.bind(("127.0.0.1", p))


def test_labels_load_and_cache_copy():
    a = load_labels()
    a["version"] = "tampered"
    assert load_labels()["version"] == "labels.v1"


def test_ollama_runs_on_loopback_with_its_own_store_and_no_cloud():
    env = server.ollama_env("kit/ollama/models", 50123)
    assert env["OLLAMA_HOST"] == "127.0.0.1:50123"
    assert env["OLLAMA_MODELS"] == "kit/ollama/models"
    assert env["OLLAMA_NO_CLOUD"] == "1" and env["OLLAMA_NOPRUNE"] == "1"


def test_start_ollama_needs_the_binary_and_the_model_folder(tmp_path, monkeypatch):
    monkeypatch.delenv(server.ENV_OLLAMA, raising=False)
    with pytest.raises(FileNotFoundError, match="ollama binary"):
        server.start_ollama()
    exe = tmp_path / "ollama.exe"
    exe.write_bytes(b"")
    with pytest.raises(FileNotFoundError, match="model folder"):
        server.start_ollama(exe, tmp_path / "no-models")


class _Proc:
    def __init__(self, code=None):
        self.returncode, self._handle, self.args = code, 0, None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 1

    def wait(self, timeout=None):
        return self.returncode


def test_start_ollama_waits_for_it_to_answer(tmp_path, monkeypatch):
    exe = tmp_path / "ollama.exe"
    exe.write_bytes(b"")
    (tmp_path / "models").mkdir()
    seen = {}

    def popen(cmd, env, **kw):
        seen.update(cmd=cmd, env=env)
        return _Proc()

    monkeypatch.setattr(server.subprocess, "Popen", popen)
    monkeypatch.setattr(server, "_kill_with_parent", lambda p: None)
    answers = iter([0, 200])
    monkeypatch.setattr(server, "_get", lambda url, key: next(answers))
    monkeypatch.setattr(server.time, "sleep", lambda s: None)
    srv = server.start_ollama(exe, port=50124)  # the model folder defaults to one beside it
    assert seen["cmd"] == [str(exe), "serve"] and srv.url == "http://127.0.0.1:50124"
    assert seen["env"]["OLLAMA_MODELS"] == str(tmp_path / "models")
    assert seen["env"]["OLLAMA_NO_CLOUD"] == "1"


def test_start_ollama_reports_an_exit(tmp_path, monkeypatch):
    exe = tmp_path / "ollama.exe"
    exe.write_bytes(b"")
    (tmp_path / "models").mkdir()
    monkeypatch.setattr(server.subprocess, "Popen", lambda cmd, env, **kw: _Proc(code=1))
    monkeypatch.setattr(server, "_kill_with_parent", lambda p: None)
    with pytest.raises(RuntimeError, match="exited"):
        server.start_ollama(exe)
