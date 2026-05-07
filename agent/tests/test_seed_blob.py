"""Tests for seed-blob parsers and push integration (CorpAdmin-AZ-byc)."""
from __future__ import annotations

import base64
import pathlib
import sys
from unittest.mock import MagicMock, patch

import pytest

# Make the agent package importable (agent/ is a sibling of tests/)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import corpweb_sync_agent as agent  # noqa: E402


# ---------------------------------------------------------------------------
# Parser: _parse_allowed_ips_from_template
# ---------------------------------------------------------------------------

CONF_FIXTURE = """\
[Interface]
PrivateKey = abc=
Address = 10.29.8.6/32
DNS = 10.29.8.1

[Peer]
PublicKey = xyz=
Endpoint = bb.azfi.ru:52443
AllowedIPs = 10.29.8.0/24, 1.2.3.0/24, 4.5.6.0/24
PersistentKeepalive = 15
"""


@pytest.fixture(autouse=True)
def isolate_result_ips_path(tmp_path, monkeypatch):
    """Default to a non-existent _RESULT_IPS_PATH so legacy tests fall back to
    the *-am.conf parser. Tests that exercise the fresh-source code path
    explicitly write to this path via monkeypatch."""
    monkeypatch.setattr(agent, "_RESULT_IPS_PATH", str(tmp_path / "no-fresh-ips"))


def test_parse_allowed_ips_returns_value(tmp_path, monkeypatch):
    target = tmp_path / "client" / "amneziawg" / "antizapret"
    target.mkdir(parents=True)
    (target / "antizapret-foo-am.conf").write_text(CONF_FIXTURE)

    monkeypatch.setattr(
        agent, "_TEMPLATE_CONF_GLOB",
        str(target / "antizapret-*-am.conf"),
    )
    assert agent._parse_allowed_ips_from_template() == \
        b"10.29.8.0/24, 1.2.3.0/24, 4.5.6.0/24"


def test_parse_allowed_ips_returns_none_when_no_match(tmp_path, monkeypatch):
    monkeypatch.setattr(
        agent, "_TEMPLATE_CONF_GLOB",
        str(tmp_path / "no-such-pattern-*.conf"),
    )
    assert agent._parse_allowed_ips_from_template() is None


def test_parse_allowed_ips_picks_lexicographically_first(tmp_path, monkeypatch):
    target = tmp_path
    (target / "antizapret-bbb-am.conf").write_text(
        CONF_FIXTURE.replace(
            "AllowedIPs = 10.29.8.0/24, 1.2.3.0/24, 4.5.6.0/24",
            "AllowedIPs = 9.9.9.0/24",
        )
    )
    (target / "antizapret-aaa-am.conf").write_text(CONF_FIXTURE)
    monkeypatch.setattr(
        agent, "_TEMPLATE_CONF_GLOB",
        str(target / "antizapret-*-am.conf"),
    )
    # aaa sorts first → returns CONF_FIXTURE's AllowedIPs
    assert agent._parse_allowed_ips_from_template() == \
        b"10.29.8.0/24, 1.2.3.0/24, 4.5.6.0/24"


def test_parse_allowed_ips_uses_fresh_ips_when_available(tmp_path, monkeypatch):
    # *-am.conf has stale baked AllowedIPs (snapshot from client.sh add time).
    target = tmp_path / "client" / "amneziawg" / "antizapret"
    target.mkdir(parents=True)
    (target / "antizapret-foo-am.conf").write_text(CONF_FIXTURE)
    monkeypatch.setattr(
        agent, "_TEMPLATE_CONF_GLOB",
        str(target / "antizapret-*-am.conf"),
    )

    # /etc/wireguard/ips is rebuilt by parse.sh on every doall; format starts
    # with ", " followed by comma-separated subnets (no /24 prefix).
    fresh = tmp_path / "wg" / "ips"
    fresh.parent.mkdir(parents=True)
    fresh.write_text(", 10.30.0.0/15, 7.8.9.0/24")
    monkeypatch.setattr(agent, "_RESULT_IPS_PATH", str(fresh))

    # Result combines the antizapret network prefix from *-am.conf
    # (10.29.8.0/24, doesn't change with toggles) with the fresh subnet list.
    assert agent._parse_allowed_ips_from_template() == \
        b"10.29.8.0/24, 10.30.0.0/15, 7.8.9.0/24"


def test_parse_allowed_ips_handles_empty_fresh_ips(tmp_path, monkeypatch):
    target = tmp_path / "client" / "amneziawg" / "antizapret"
    target.mkdir(parents=True)
    (target / "antizapret-foo-am.conf").write_text(CONF_FIXTURE)
    monkeypatch.setattr(
        agent, "_TEMPLATE_CONF_GLOB",
        str(target / "antizapret-*-am.conf"),
    )

    fresh = tmp_path / "ips"
    fresh.write_text("")  # parse.sh produced an empty list
    monkeypatch.setattr(agent, "_RESULT_IPS_PATH", str(fresh))

    # Only the prefix remains
    assert agent._parse_allowed_ips_from_template() == b"10.29.8.0/24"


def test_parse_allowed_ips_falls_back_when_no_fresh_ips(tmp_path, monkeypatch):
    # Early-bootstrap node: *-am.conf exists but parse.sh has not produced
    # /etc/wireguard/ips yet. Fall back to the legacy *-am.conf parser.
    target = tmp_path / "client" / "amneziawg" / "antizapret"
    target.mkdir(parents=True)
    (target / "antizapret-foo-am.conf").write_text(CONF_FIXTURE)
    monkeypatch.setattr(
        agent, "_TEMPLATE_CONF_GLOB",
        str(target / "antizapret-*-am.conf"),
    )
    # _RESULT_IPS_PATH is already a non-existent path via the autouse fixture
    assert agent._parse_allowed_ips_from_template() == \
        b"10.29.8.0/24, 1.2.3.0/24, 4.5.6.0/24"


# ---------------------------------------------------------------------------
# Parser: _read_setup
# ---------------------------------------------------------------------------

def test_read_setup_returns_bytes(tmp_path, monkeypatch):
    setup = tmp_path / "setup"
    setup.write_bytes(b"WIREGUARD_HOST=foo\n")
    monkeypatch.setattr(agent, "_SETUP_PATH", str(setup))
    assert agent._read_setup() == b"WIREGUARD_HOST=foo\n"


def test_read_setup_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "_SETUP_PATH", str(tmp_path / "nope"))
    assert agent._read_setup() is None


# ---------------------------------------------------------------------------
# Push integration: startup_reconcile
# ---------------------------------------------------------------------------

def test_startup_reconcile_pushes_both_blobs(tmp_path, monkeypatch):
    # Set up template-conf and setup so parsers return real values
    target = tmp_path / "client" / "amneziawg" / "antizapret"
    target.mkdir(parents=True)
    (target / "antizapret-foo-am.conf").write_text(CONF_FIXTURE)
    setup = tmp_path / "setup"
    setup.write_bytes(b"WIREGUARD_HOST=foo\n")

    monkeypatch.setattr(agent, "_TEMPLATE_CONF_GLOB", str(target / "antizapret-*-am.conf"))
    monkeypatch.setattr(agent, "_SETUP_PATH", str(setup))
    # Stub out the existing managed-files fetch so startup_reconcile only
    # exercises the new seed-blob block (and does not hit the network).
    monkeypatch.setattr(agent, "MANAGED_FILES", [])

    with patch.object(agent, "api_post") as mock_post:
        agent.startup_reconcile()

    paths_pushed = sorted(call.args[0] for call in mock_post.call_args_list)
    assert paths_pushed == ["/api/v1/agent/seed-blob", "/api/v1/agent/seed-blob"]

    decoded = {}
    for call in mock_post.call_args_list:
        body = call.args[1]
        decoded[body["path"]] = base64.b64decode(body["content"])
    assert decoded["antizapret:allowed_ips"] == \
        b"10.29.8.0/24, 1.2.3.0/24, 4.5.6.0/24"
    assert decoded["/root/antizapret/setup"] == b"WIREGUARD_HOST=foo\n"


def test_startup_reconcile_skips_push_when_parsers_return_none(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "_TEMPLATE_CONF_GLOB", str(tmp_path / "no-match-*.conf"))
    monkeypatch.setattr(agent, "_SETUP_PATH", str(tmp_path / "no-such-setup"))
    monkeypatch.setattr(agent, "MANAGED_FILES", [])

    with patch.object(agent, "api_post") as mock_post:
        agent.startup_reconcile()

    assert mock_post.call_args_list == []


# ---------------------------------------------------------------------------
# Push integration: _run_doall
# ---------------------------------------------------------------------------

def test_run_doall_success_pushes_allowed_ips(tmp_path, monkeypatch):
    target = tmp_path / "client" / "amneziawg" / "antizapret"
    target.mkdir(parents=True)
    (target / "antizapret-foo-am.conf").write_text(CONF_FIXTURE)
    monkeypatch.setattr(agent, "_TEMPLATE_CONF_GLOB", str(target / "antizapret-*-am.conf"))

    fake_run = MagicMock(return_value=MagicMock(returncode=0, stderr=""))
    with patch.object(agent.subprocess, "run", fake_run), \
         patch.object(agent, "api_post") as mock_post:
        agent._run_doall()

    assert mock_post.call_count == 1
    body = mock_post.call_args.args[1]
    assert body["path"] == "antizapret:allowed_ips"
    assert base64.b64decode(body["content"]) == \
        b"10.29.8.0/24, 1.2.3.0/24, 4.5.6.0/24"


def test_run_doall_failure_does_not_push(tmp_path, monkeypatch):
    import subprocess as sp
    target = tmp_path / "client" / "amneziawg" / "antizapret"
    target.mkdir(parents=True)
    (target / "antizapret-foo-am.conf").write_text(CONF_FIXTURE)
    monkeypatch.setattr(agent, "_TEMPLATE_CONF_GLOB", str(target / "antizapret-*-am.conf"))

    def boom(*args, **kwargs):
        raise sp.CalledProcessError(returncode=1, cmd=args[0], stderr="oops")

    with patch.object(agent.subprocess, "run", side_effect=boom), \
         patch.object(agent, "api_post") as mock_post:
        agent._run_doall()

    assert mock_post.call_args_list == []
