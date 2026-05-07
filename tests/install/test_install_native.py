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
