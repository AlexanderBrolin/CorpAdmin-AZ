"""Grep-based regression tests for corpweb/install-native.sh.

These do NOT execute the script; they only assert that required commands
are present in the script text. Manual verification on a clean Debian 13
VM is the real acceptance gate (see spec Acceptance #4).
"""
from __future__ import annotations

import pathlib

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "corpweb" / "install-native.sh"
TEXT = SCRIPT.read_text()


def test_script_exists_and_is_bash():
    assert SCRIPT.exists(), f"missing {SCRIPT}"
    assert TEXT.startswith("#!/"), "install-native.sh must start with shebang"


def test_installs_iptables_and_persistent():
    # CorpAdmin-AZ-1oz: balancer.py needs iptables binary; iptables-persistent
    # keeps DNAT rules across reboot; netfilter-persistent provides the save/restore.
    assert (
        "apt-get install -y -qq iptables iptables-persistent netfilter-persistent"
        in TEXT
    ), "iptables/iptables-persistent/netfilter-persistent install line missing"


def test_writes_ip_forward_sysctl_drop_in():
    # CorpAdmin-AZ-lpa: without net.ipv4.ip_forward=1 the kernel drops packets
    # on the FORWARD chain after DNAT, so the balancer rules look fine
    # (counters tick) but no traffic reaches the WG ifaces.
    assert "/etc/sysctl.d/99-corpweb-forwarding.conf" in TEXT, \
        "expected drop-in file path /etc/sysctl.d/99-corpweb-forwarding.conf"
    assert "net.ipv4.ip_forward=1" in TEXT, \
        "expected net.ipv4.ip_forward=1 directive"
    assert "sysctl --system" in TEXT, \
        "expected `sysctl --system` to apply the drop-in immediately"


def test_alembic_does_not_swallow_stderr():
    # CorpAdmin-AZ-9nq partial: hiding alembic stderr behind 2>/dev/null
    # masks real migration failures; install reports success while DB schema
    # is partially broken (missing pg_notify triggers → SSE/sync silently dies).
    lines = [l for l in TEXT.splitlines() if "alembic" in l and "upgrade head" in l]
    assert lines, "alembic upgrade head invocation missing from install-native.sh"
    for line in lines:
        assert "2>/dev/null" not in line, \
            f"alembic stderr is being swallowed: {line.strip()!r}"
