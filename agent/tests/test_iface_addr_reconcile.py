"""Tests for the interface address reconciler.

The agent writes the correct Address into the wg conf but historically never
applied it to a running interface: `wg syncconf` is fed by `wg-quick strip`,
which drops Address by design. wgfi4 therefore served 68% of its peers without
a route for two days (CorpAdmin-AZ-3f9). These tests cover the reconciler that
compares the kernel against the conf and fixes it in place.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from unittest.mock import patch

import pytest

# Make the agent package importable (agent/ is a sibling of tests/)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import corpweb_sync_agent as agent  # noqa: E402


@pytest.fixture(autouse=True)
def reset_reconcile_state():
    """Module-level backoff counters must not leak between tests."""
    agent._addr_reconcile_failures.clear()
    agent._addr_reconcile_applied_total = 0
    yield
    agent._addr_reconcile_failures.clear()
    agent._addr_reconcile_applied_total = 0


def _completed(returncode: int, stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["ip"], returncode=returncode, stdout=stdout, stderr="",
    )


ADDR_JSON = json.dumps([{
    "ifname": "antizapret",
    "mtu": 1420,
    "addr_info": [
        {"family": "inet", "local": "10.29.8.1", "prefixlen": 21, "scope": "global"},
    ],
}])


class TestIfaceState:
    """_iface_state must tell 'no answer' apart from 'no addresses'."""

    def test_parses_addresses_from_ip_json(self):
        with patch("corpweb_sync_agent._ip_addr_show",
                   return_value=_completed(0, ADDR_JSON)):
            assert agent._iface_state("antizapret") == ["10.29.8.1/21"]

    def test_missing_iface_returns_none(self):
        with patch("corpweb_sync_agent._ip_addr_show", return_value=_completed(1)):
            assert agent._iface_state("antizapret") is None

    def test_iface_without_ipv4_returns_empty_list_not_none(self):
        """rc 0 with an empty array means the iface exists but has no IPv4 —
        a state the reconciler fixes, not one it must skip."""
        with patch("corpweb_sync_agent._ip_addr_show",
                   return_value=_completed(0, "[]")):
            assert agent._iface_state("antizapret") == []

    def test_unparsable_output_returns_none(self):
        with patch("corpweb_sync_agent._ip_addr_show",
                   return_value=_completed(0, "not json")):
            assert agent._iface_state("antizapret") is None

    def test_non_global_and_ipv6_addresses_are_ignored(self):
        payload = json.dumps([{
            "ifname": "antizapret",
            "addr_info": [
                {"family": "inet", "local": "10.29.8.1", "prefixlen": 21, "scope": "global"},
                {"family": "inet", "local": "169.254.0.1", "prefixlen": 16, "scope": "link"},
                {"family": "inet6", "local": "fd00::1", "prefixlen": 64, "scope": "global"},
            ],
        }])
        with patch("corpweb_sync_agent._ip_addr_show",
                   return_value=_completed(0, payload)):
            assert agent._iface_state("antizapret") == ["10.29.8.1/21"]

    def test_missing_ip_binary_returns_none(self):
        with patch("corpweb_sync_agent._ip_addr_show", return_value=None):
            assert agent._iface_state("antizapret") is None

    def test_json_object_instead_of_array_returns_none(self):
        """Valid JSON of the wrong shape must not be read as 'no addresses' —
        that would look like an interface to be filled in."""
        with patch("corpweb_sync_agent._ip_addr_show",
                   return_value=_completed(0, '{"ifname": "antizapret"}')):
            assert agent._iface_state("antizapret") is None

    def test_entries_that_are_not_objects_are_skipped(self):
        with patch("corpweb_sync_agent._ip_addr_show",
                   return_value=_completed(0, '["nonsense"]')):
            assert agent._iface_state("antizapret") == []


class TestIfaceIsUpContract:
    """_iface_is_up keys off the return code only. If it ever keyed off the
    JSON, a live interface without an IPv4 address would look absent and
    apply_iface_conf would 'systemctl start' a running unit."""

    def test_returns_true_when_rc_zero(self):
        with patch("corpweb_sync_agent._ip_addr_show",
                   return_value=_completed(0, ADDR_JSON)):
            assert agent._iface_is_up("antizapret") is True

    def test_returns_false_when_rc_nonzero(self):
        with patch("corpweb_sync_agent._ip_addr_show", return_value=_completed(1)):
            assert agent._iface_is_up("antizapret") is False

    def test_returns_true_for_empty_array(self):
        with patch("corpweb_sync_agent._ip_addr_show",
                   return_value=_completed(0, "[]")):
            assert agent._iface_is_up("antizapret") is True

    def test_returns_true_when_json_unparsable(self):
        with patch("corpweb_sync_agent._ip_addr_show",
                   return_value=_completed(0, "not json")):
            assert agent._iface_is_up("antizapret") is True

    def test_returns_false_when_ip_binary_missing(self):
        with patch("corpweb_sync_agent._ip_addr_show", return_value=None):
            assert agent._iface_is_up("antizapret") is False


class TestIpAddrShow:
    def test_invokes_ip_with_json_and_ipv4_flags(self):
        with patch("corpweb_sync_agent.subprocess.run",
                   return_value=_completed(0, ADDR_JSON)) as m:
            agent._ip_addr_show("antizapret")
        assert m.call_args[0][0] == [
            "ip", "-j", "-4", "addr", "show", "dev", "antizapret",
        ]

    def test_returns_none_when_ip_binary_missing(self):
        with patch("corpweb_sync_agent.subprocess.run", side_effect=FileNotFoundError):
            assert agent._ip_addr_show("antizapret") is None
