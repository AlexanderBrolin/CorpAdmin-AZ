"""Tests for the interface address reconciler.

The agent writes the correct Address into the wg conf but historically never
applied it to a running interface: `wg syncconf` is fed by `wg-quick strip`,
which drops Address by design. wgfi4 therefore served 68% of its peers without
a route for two days (CorpAdmin-AZ-3f9). These tests cover the reconciler that
compares the kernel against the conf and fixes it in place.
"""
from __future__ import annotations

import base64
import json
import pathlib
import subprocess
import sys
from unittest.mock import MagicMock, patch

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
        # check=True is load-bearing: a failed `ip addr add` must raise
        # CalledProcessError rather than be silently swallowed, or the delete
        # loop would proceed on an address that was never actually added.
        assert m.call_args.kwargs["check"] is True
        assert m.call_args.kwargs["capture_output"] is True
        assert m.call_args.kwargs["text"] is True

    def test_del_invokes_full_argv(self):
        with patch("corpweb_sync_agent.subprocess.run") as m:
            assert agent._ip_addr("del", "10.29.8.1/24", "antizapret") is True
        assert m.call_args[0][0] == [
            "ip", "addr", "del", "10.29.8.1/24", "dev", "antizapret",
        ]
        assert m.call_args.kwargs["check"] is True
        assert m.call_args.kwargs["capture_output"] is True
        assert m.call_args.kwargs["text"] is True

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

    def test_failed_delete_returns_false_without_further_calls(self):
        """The delete-fails branch (as opposed to add-fails) was previously
        unexercised: add succeeds, the single delete fails, and the function
        must stop right there — no kernel re-read, no further `ip addr` call."""
        with patch("corpweb_sync_agent._ip_addr", side_effect=[True, False]) as m, \
             patch("corpweb_sync_agent._iface_state") as state_m:
            ok = agent._reconcile_one_iface(
                "antizapret", ["10.29.8.1/24"], ["10.29.8.1/21"],
            )

        assert ok is False
        assert [c[0][0] for c in m.call_args_list] == ["add", "del"]
        state_m.assert_not_called()

    def test_second_delete_causes_collateral_loss_first_does_not(self):
        """The kernel is re-read after EVERY delete, not just the first: an
        implementation that only checked once (e.g. after the first delete)
        would pass test_restores_address_lost_to_promote_secondaries by
        accident, since that test's collateral loss happens on the very
        first delete. Here the first delete is clean and only the second
        one is fatal, so _iface_state must be consulted both times."""
        calls: list[tuple] = []

        def fake_ip_addr(op, addr, iface):
            calls.append((op, addr, iface))
            return True

        with patch("corpweb_sync_agent._ip_addr", side_effect=fake_ip_addr), \
             patch(
                 "corpweb_sync_agent._iface_state",
                 side_effect=[
                     ["10.29.8.1/21", "10.29.8.2/24"],  # after 1st delete: still fine
                     [],                                  # after 2nd delete: wanted addr gone
                 ],
             ):
            ok = agent._reconcile_one_iface(
                "antizapret",
                ["10.29.8.1/24", "10.29.8.2/24"],
                ["10.29.8.1/21"],
            )

        assert ok is False
        assert calls == [
            ("add", "10.29.8.1/21", "antizapret"),
            ("del", "10.29.8.1/24", "antizapret"),
            ("del", "10.29.8.2/24", "antizapret"),
            ("add", "10.29.8.1/21", "antizapret"),
        ]

    def test_unreadable_kernel_after_delete_readds_every_wanted_address(self):
        """_iface_state returns None for five distinct causes (interface
        gone, ip missing, non-zero rc, unparsable JSON, non-list payload);
        in four of them the interface is alive and may have just lost a
        wanted address to promote_secondaries. Bailing without restoring
        would leave it short, so every address in `want` is re-added blind —
        `want` came from the conf and is trustworthy even when the live
        state is not."""
        calls: list[tuple] = []

        def fake_ip_addr(op, addr, iface):
            calls.append((op, addr, iface))
            return True

        with patch("corpweb_sync_agent._ip_addr", side_effect=fake_ip_addr), \
             patch("corpweb_sync_agent._iface_state", return_value=None):
            ok = agent._reconcile_one_iface(
                "antizapret",
                ["10.29.8.1/24"],
                ["10.29.8.1/21", "10.29.16.1/24"],
            )

        assert ok is False
        dels = [c for c in calls if c[0] == "del"]
        assert dels == [("del", "10.29.8.1/24", "antizapret")]
        readds = calls[calls.index(dels[0]) + 1:]
        assert set(readds) == {
            ("add", "10.29.8.1/21", "antizapret"),
            ("add", "10.29.16.1/24", "antizapret"),
        }

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


ONE_IFACE = {"antizapret": "/etc/wireguard/antizapret.conf"}

TWO_IFACES = {
    "antizapret": "/etc/wireguard/antizapret.conf",
    "vpn": "/etc/wireguard/vpn.conf",
}


class TestReconcileIfaceAddresses:
    def test_no_drift_runs_no_commands(self):
        with patch.dict(agent._IFACE_CONFS, ONE_IFACE, clear=True), \
             patch("corpweb_sync_agent._conf_addresses", return_value=["10.29.8.1/21"]), \
             patch("corpweb_sync_agent._iface_state", return_value=["10.29.8.1/21"]), \
             patch("corpweb_sync_agent._ip_addr") as m:
            metrics = agent.reconcile_iface_addresses(apply=True)
        m.assert_not_called()
        assert metrics == {}

    def test_clean_match_resets_the_failure_counter(self):
        """F2 fix round 1: a prior run left a failure count on this
        interface; once live matches want again, the counter must be
        cleared or the interface would falsely count towards the
        three-strikes backoff on a later, unrelated drift."""
        agent._addr_reconcile_failures["antizapret"] = 2
        with patch.dict(agent._IFACE_CONFS, ONE_IFACE, clear=True), \
             patch("corpweb_sync_agent._conf_addresses", return_value=["10.29.8.1/21"]), \
             patch("corpweb_sync_agent._iface_state", return_value=["10.29.8.1/21"]):
            agent.reconcile_iface_addresses(apply=True)
        assert agent._addr_reconcile_failures.get("antizapret", 0) == 0

    def test_drift_is_fixed_and_counted(self):
        with patch.dict(agent._IFACE_CONFS, ONE_IFACE, clear=True), \
             patch("corpweb_sync_agent._conf_addresses", return_value=["10.29.8.1/21"]), \
             patch("corpweb_sync_agent._iface_state", return_value=["10.29.8.1/24"]), \
             patch("corpweb_sync_agent._reconcile_one_iface", return_value=True) as m:
            metrics = agent.reconcile_iface_addresses(apply=True)
        m.assert_called_once_with("antizapret", ["10.29.8.1/24"], ["10.29.8.1/21"])
        assert metrics["iface_addr_drift_detected"] is True
        assert metrics["iface_addr_drift"] == {
            "antizapret": {"live": ["10.29.8.1/24"], "want": ["10.29.8.1/21"]},
        }
        assert metrics["iface_addr_drift_applied_count"] == 1

    def test_detect_only_never_mutates(self):
        """F3 fix round 1: pin the 'the heartbeat can never mutate an
        interface' guarantee at the subprocess layer rather than by mocking
        _reconcile_one_iface — the old version of this test would still pass
        if some future edit added a mutating call anywhere else on the
        detect-only path. _iface_state runs for real here, fed by a mocked
        `ip -j -4 addr show` that reports drift; every subprocess.run call
        this pass makes must be that same read-only show command."""
        live_json = json.dumps([{
            "ifname": "antizapret",
            "addr_info": [
                {"family": "inet", "local": "10.29.8.1", "prefixlen": 24, "scope": "global"},
            ],
        }])
        with patch.dict(agent._IFACE_CONFS, ONE_IFACE, clear=True), \
             patch("corpweb_sync_agent._conf_addresses", return_value=["10.29.8.1/21"]), \
             patch("corpweb_sync_agent.subprocess.run",
                   return_value=_completed(0, live_json)) as m:
            metrics = agent.reconcile_iface_addresses(apply=False)

        assert metrics["iface_addr_drift_detected"] is True
        assert "iface_addr_drift_applied_count" not in metrics
        assert m.call_args_list, "expected at least one read of kernel state"
        for call in m.call_args_list:
            assert call.args[0] == [
                "ip", "-j", "-4", "addr", "show", "dev", "antizapret",
            ], f"unexpected mutating call: {call.args[0]}"

    def test_absent_iface_is_skipped(self):
        with patch.dict(agent._IFACE_CONFS, ONE_IFACE, clear=True), \
             patch("corpweb_sync_agent._conf_addresses", return_value=["10.29.8.1/21"]), \
             patch("corpweb_sync_agent._iface_state", return_value=None), \
             patch("corpweb_sync_agent._reconcile_one_iface") as m:
            metrics = agent.reconcile_iface_addresses(apply=True)
        m.assert_not_called()
        assert metrics == {}

    def test_unknown_desired_state_is_skipped(self):
        """No Address in the conf means we do not know what the interface
        should hold — never mutate on a guess."""
        with patch.dict(agent._IFACE_CONFS, ONE_IFACE, clear=True), \
             patch("corpweb_sync_agent._conf_addresses", return_value=[]), \
             patch("corpweb_sync_agent._iface_state", return_value=["10.29.8.1/24"]), \
             patch("corpweb_sync_agent._reconcile_one_iface") as m:
            metrics = agent.reconcile_iface_addresses(apply=True)
        m.assert_not_called()
        assert metrics == {}

    def test_backoff_stops_retrying_after_three_failures(self):
        with patch.dict(agent._IFACE_CONFS, ONE_IFACE, clear=True), \
             patch("corpweb_sync_agent._conf_addresses", return_value=["10.29.8.1/21"]), \
             patch("corpweb_sync_agent._iface_state", return_value=["10.29.8.1/24"]), \
             patch("corpweb_sync_agent._reconcile_one_iface", return_value=False) as m:
            for _ in range(3):
                agent.reconcile_iface_addresses(apply=True)
            assert m.call_count == 3
            metrics = agent.reconcile_iface_addresses(apply=True)

        assert m.call_count == 3, "fourth pass must not touch the interface"
        assert metrics["iface_addr_drift_failed"] is True
        assert metrics["iface_addr_drift_detected"] is True

    def test_success_resets_the_failure_counter(self):
        with patch.dict(agent._IFACE_CONFS, ONE_IFACE, clear=True), \
             patch("corpweb_sync_agent._conf_addresses", return_value=["10.29.8.1/21"]), \
             patch("corpweb_sync_agent._iface_state", return_value=["10.29.8.1/24"]), \
             patch("corpweb_sync_agent._reconcile_one_iface", side_effect=[False, True]):
            agent.reconcile_iface_addresses(apply=True)
            assert agent._addr_reconcile_failures["antizapret"] == 1
            agent.reconcile_iface_addresses(apply=True)

        assert agent._addr_reconcile_failures.get("antizapret", 0) == 0

    def test_unexpected_error_is_contained(self):
        with patch.dict(agent._IFACE_CONFS, ONE_IFACE, clear=True), \
             patch("corpweb_sync_agent._conf_addresses", side_effect=RuntimeError("boom")):
            metrics = agent.reconcile_iface_addresses(apply=True)
        assert metrics["iface_addr_drift_failed"] is True

    def test_saturated_failure_is_reported_in_detect_only_mode(self):
        """F1 fix round 1: the CP only ever receives the detect-only pass's
        metrics (task 6 discards the apply pass's return value), so a
        permanently-failing interface must be flagged as failed even when
        apply=False — otherwise the one metric that says 'this needs a
        human' can never reach a human."""
        agent._addr_reconcile_failures["antizapret"] = agent._ADDR_RECONCILE_MAX_FAILURES
        with patch.dict(agent._IFACE_CONFS, ONE_IFACE, clear=True), \
             patch("corpweb_sync_agent._conf_addresses", return_value=["10.29.8.1/21"]), \
             patch("corpweb_sync_agent._iface_state", return_value=["10.29.8.1/24"]), \
             patch("corpweb_sync_agent._reconcile_one_iface") as m:
            metrics = agent.reconcile_iface_addresses(apply=False)
        m.assert_not_called()
        assert metrics["iface_addr_drift_failed"] is True
        assert metrics["iface_addr_drift_detected"] is True

    def test_one_iface_failing_does_not_block_the_others(self):
        """F4 fix round 1: every prior test patched _IFACE_CONFS down to a
        single entry, so two claims went unverified — that one interface's
        unexpected failure does not stop the rest of the loop, and that
        iface_addr_drift carries an entry per drifting interface, not just
        the first."""
        def fake_conf_addresses(conf_path):
            if conf_path == TWO_IFACES["antizapret"]:
                raise RuntimeError("boom")
            return ["10.29.16.1/24"]

        with patch.dict(agent._IFACE_CONFS, TWO_IFACES, clear=True), \
             patch("corpweb_sync_agent._conf_addresses", side_effect=fake_conf_addresses), \
             patch("corpweb_sync_agent._iface_state", return_value=["10.29.16.1/21"]), \
             patch("corpweb_sync_agent._reconcile_one_iface", return_value=True) as m:
            metrics = agent.reconcile_iface_addresses(apply=True)

        m.assert_called_once_with("vpn", ["10.29.16.1/21"], ["10.29.16.1/24"])
        assert "antizapret" not in metrics["iface_addr_drift"]
        assert metrics["iface_addr_drift"]["vpn"] == {
            "live": ["10.29.16.1/21"], "want": ["10.29.16.1/24"],
        }
        assert metrics["iface_addr_drift_failed"] is True


class TestIntegration:
    def test_startup_reconcile_applies_after_every_file_was_applied(self):
        """Order matters: the conf only holds the CP's version once apply_path
        has run for it. Reconciling earlier would enforce a stale local file,
        so the reconcile must come after ALL of them — not after the first."""
        order: list[str] = []

        response = MagicMock()
        response.json.return_value = {"content": base64.b64encode(b"x").decode()}

        with patch("corpweb_sync_agent.api_get", return_value=response), \
             patch("corpweb_sync_agent.apply_path",
                   side_effect=lambda *a, **k: order.append("apply_path")), \
             patch("corpweb_sync_agent._parse_allowed_ips_from_template", return_value=None), \
             patch("corpweb_sync_agent._read_setup", return_value=None), \
             patch("corpweb_sync_agent.reconcile_iface_addresses",
                   side_effect=lambda apply: order.append(f"reconcile(apply={apply})") or {}):
            agent.startup_reconcile()

        assert order.count("apply_path") == len(agent.MANAGED_FILES)
        assert order.count("reconcile(apply=True)") == 1
        assert order[-1] == "reconcile(apply=True)", (
            "the reconcile must run after the whole file loop"
        )

    def test_heartbeat_reports_drift_without_applying(self):
        with patch("corpweb_sync_agent.collect_metrics", return_value={}), \
             patch("corpweb_sync_agent.collect_peers", return_value=[]), \
             patch("corpweb_sync_agent.sync_escape_rules", return_value={}), \
             patch("corpweb_sync_agent._applied_shas", return_value={}), \
             patch("corpweb_sync_agent.reconcile_iface_addresses",
                   return_value={"iface_addr_drift_detected": True}) as rec, \
             patch("corpweb_sync_agent.api_post") as post:
            agent.send_heartbeat()

        rec.assert_called_once_with(apply=False)
        assert post.call_args[0][1]["metrics"]["iface_addr_drift_detected"] is True

    def test_heartbeat_survives_a_raising_reconciler(self):
        with patch("corpweb_sync_agent.collect_metrics", return_value={}), \
             patch("corpweb_sync_agent.collect_peers", return_value=[]), \
             patch("corpweb_sync_agent.sync_escape_rules", return_value={}), \
             patch("corpweb_sync_agent._applied_shas", return_value={}), \
             patch("corpweb_sync_agent.reconcile_iface_addresses",
                   side_effect=RuntimeError("boom")), \
             patch("corpweb_sync_agent.api_post") as post:
            agent.send_heartbeat()

        metrics = post.call_args[0][1]["metrics"]
        assert metrics["iface_addr_error"].startswith("unexpected: RuntimeError")
