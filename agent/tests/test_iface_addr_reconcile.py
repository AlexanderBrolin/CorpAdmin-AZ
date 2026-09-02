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


class TestConfAddresses:
    """Every Address value must be collected. Dropping one would put it in
    live-minus-want and get it deleted from the running interface."""

    def _conf(self, tmp_path, body: str) -> str:
        path = tmp_path / "antizapret.conf"
        path.write_text(body)
        return str(path)

    def test_reads_single_address(self, tmp_path):
        conf = self._conf(tmp_path, "[Interface]\nPrivateKey = X\nAddress = 10.29.8.1/21\n")
        assert agent._conf_addresses(conf) == ["10.29.8.1/21"]

    def test_tolerates_extra_whitespace(self, tmp_path):
        conf = self._conf(tmp_path, "[Interface]\n   Address   =    10.29.8.1/21   \n")
        assert agent._conf_addresses(conf) == ["10.29.8.1/21"]

    def test_reads_comma_separated_list(self, tmp_path):
        conf = self._conf(tmp_path, "[Interface]\nAddress = 10.29.8.1/21, 10.29.16.1/24\n")
        assert agent._conf_addresses(conf) == ["10.29.8.1/21", "10.29.16.1/24"]

    def test_reads_several_address_lines(self, tmp_path):
        conf = self._conf(tmp_path, "[Interface]\nAddress = 10.29.8.1/21\nAddress = 10.29.16.1/24\n")
        assert agent._conf_addresses(conf) == ["10.29.8.1/21", "10.29.16.1/24"]

    def test_ipv6_is_ignored(self, tmp_path):
        conf = self._conf(tmp_path, "[Interface]\nAddress = 10.29.8.1/21, fd00::1/64\n")
        assert agent._conf_addresses(conf) == ["10.29.8.1/21"]

    def test_bare_address_is_treated_as_host_route(self, tmp_path):
        conf = self._conf(tmp_path, "[Interface]\nAddress = 10.29.8.1\n")
        assert agent._conf_addresses(conf) == ["10.29.8.1/32"]

    def test_allowedips_in_peer_section_is_not_an_address(self, tmp_path):
        conf = self._conf(
            tmp_path,
            "[Interface]\nAddress = 10.29.8.1/21\n\n[Peer]\nAllowedIPs = 10.29.9.5/32\n",
        )
        assert agent._conf_addresses(conf) == ["10.29.8.1/21"]

    def test_commented_address_is_ignored(self, tmp_path):
        conf = self._conf(tmp_path, "[Interface]\n# Address = 10.29.8.1/24\nAddress = 10.29.8.1/21\n")
        assert agent._conf_addresses(conf) == ["10.29.8.1/21"]

    def test_garbage_value_is_skipped(self, tmp_path):
        conf = self._conf(tmp_path, "[Interface]\nAddress = not-an-address\n")
        assert agent._conf_addresses(conf) == []

    def test_missing_address_line_yields_empty(self, tmp_path):
        conf = self._conf(tmp_path, "[Interface]\nPrivateKey = X\nListenPort = 51443\n")
        assert agent._conf_addresses(conf) == []

    def test_missing_file_yields_empty(self, tmp_path):
        assert agent._conf_addresses(str(tmp_path / "nope.conf")) == []


class TestIpAddr:
    def test_add_invokes_full_argv(self):
        with patch("corpweb_sync_agent.subprocess.run") as m:
            assert agent._ip_addr("add", "10.29.8.1/21", "antizapret") is True
        assert m.call_args[0][0] == [
            "ip", "addr", "add", "10.29.8.1/21", "dev", "antizapret",
        ]

    def test_del_invokes_full_argv(self):
        with patch("corpweb_sync_agent.subprocess.run") as m:
            assert agent._ip_addr("del", "10.29.8.1/24", "antizapret") is True
        assert m.call_args[0][0] == [
            "ip", "addr", "del", "10.29.8.1/24", "dev", "antizapret",
        ]

    def test_returns_false_on_command_failure(self):
        err = subprocess.CalledProcessError(2, ["ip"], stderr="boom")
        with patch("corpweb_sync_agent.subprocess.run", side_effect=err):
            assert agent._ip_addr("add", "10.29.8.1/21", "antizapret") is False

    def test_returns_false_when_ip_binary_missing(self):
        with patch("corpweb_sync_agent.subprocess.run", side_effect=FileNotFoundError):
            assert agent._ip_addr("add", "10.29.8.1/21", "antizapret") is False


class TestReconcileOneIface:
    """The incident case and the ways applying it can go wrong."""

    def test_adds_before_deleting(self):
        """Regression for CorpAdmin-AZ-3f9: live /24, conf /21. The add must
        precede the delete so the interface is never left without an address."""
        calls: list[tuple] = []

        def fake_ip_addr(op, addr, iface):
            calls.append((op, addr, iface))
            return True

        with patch("corpweb_sync_agent._ip_addr", side_effect=fake_ip_addr), \
             patch("corpweb_sync_agent._iface_state", return_value=["10.29.8.1/21"]):
            ok = agent._reconcile_one_iface(
                "antizapret", ["10.29.8.1/24"], ["10.29.8.1/21"],
            )

        assert ok is True
        assert calls == [
            ("add", "10.29.8.1/21", "antizapret"),
            ("del", "10.29.8.1/24", "antizapret"),
        ]

    def test_failed_add_aborts_before_any_delete(self):
        with patch("corpweb_sync_agent._ip_addr", return_value=False) as m, \
             patch("corpweb_sync_agent._iface_state", return_value=["10.29.8.1/24"]):
            ok = agent._reconcile_one_iface(
                "antizapret", ["10.29.8.1/24"], ["10.29.8.1/21"],
            )

        assert ok is False
        assert [c[0][0] for c in m.call_args_list] == ["add"]

    def test_restores_address_lost_to_promote_secondaries(self):
        """With promote_secondaries=0, deleting the primary removes secondaries
        in the same subnet. The wanted address must be put back and further
        deletes abandoned."""
        calls: list[tuple] = []

        def fake_ip_addr(op, addr, iface):
            calls.append((op, addr, iface))
            return True

        # After the delete the kernel reports an interface stripped of both.
        with patch("corpweb_sync_agent._ip_addr", side_effect=fake_ip_addr), \
             patch("corpweb_sync_agent._iface_state", return_value=[]):
            ok = agent._reconcile_one_iface(
                "antizapret", ["10.29.8.1/24", "10.29.8.2/24"], ["10.29.8.1/21"],
            )

        assert ok is False
        assert calls[0] == ("add", "10.29.8.1/21", "antizapret")
        assert calls[1][0] == "del"
        assert ("add", "10.29.8.1/21", "antizapret") in calls[2:]
        # the second delete never happened — only one del in the whole run
        assert [c[0] for c in calls].count("del") == 1

    def test_reports_failure_when_iface_disappears_mid_run(self):
        with patch("corpweb_sync_agent._ip_addr", return_value=True), \
             patch("corpweb_sync_agent._iface_state", return_value=None):
            ok = agent._reconcile_one_iface(
                "antizapret", ["10.29.8.1/24"], ["10.29.8.1/21"],
            )
        assert ok is False

    def test_refuses_an_empty_desired_set(self):
        """Defence in depth for the spec's 'never delete the last address'
        rail: reconcile_iface_addresses already skips an interface whose conf
        yields nothing, but the deleting function must not rely on its caller."""
        with patch("corpweb_sync_agent._ip_addr") as m, \
             patch("corpweb_sync_agent._iface_state", return_value=[]):
            ok = agent._reconcile_one_iface("antizapret", ["10.29.8.1/21"], [])
        assert ok is False
        m.assert_not_called()

    def test_adds_only_when_iface_has_no_addresses(self):
        calls: list[tuple] = []

        def fake_ip_addr(op, addr, iface):
            calls.append((op, addr, iface))
            return True

        with patch("corpweb_sync_agent._ip_addr", side_effect=fake_ip_addr), \
             patch("corpweb_sync_agent._iface_state", return_value=["10.29.8.1/21"]):
            ok = agent._reconcile_one_iface("antizapret", [], ["10.29.8.1/21"])

        assert ok is True
        assert calls == [("add", "10.29.8.1/21", "antizapret")]
