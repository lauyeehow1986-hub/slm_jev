import socket
import sys

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


def test_labels_load_and_cache_copy():
    a = load_labels()
    a["version"] = "tampered"
    assert load_labels()["version"] == "labels.v1"
