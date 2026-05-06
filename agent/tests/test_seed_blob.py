"""Tests for seed-blob parsers and push integration (CorpAdmin-AZ-byc)."""
from __future__ import annotations

import base64
import pathlib
import sys
from unittest.mock import patch

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
