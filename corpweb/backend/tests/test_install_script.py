"""
Tests for the install-script renderer in app.api.v1.agent.

The script is generated per-node by _render_install_script(cp_url, token,
hostname). It must include an amneziawg-tools install block so escape ifaces
work out of the box on freshly enrolled nodes.
"""
from app.api.v1.agent import _render_install_script


def _render() -> str:
    return _render_install_script(
        cp_url="https://example.org",
        token="t-1234567890",
        hostname="node-test01",
    )


def test_install_script_contains_amneziawg_block():
    script = _render()
    assert "command -v awg-quick" in script
    assert "75C9DD72C799870E310542E24166F2C257290828" in script
    assert "amneziawg-dkms" in script
    assert "amneziawg-tools" in script
    assert "signed-by=/usr/share/keyrings/amnezia-ppa.gpg" in script


def test_install_script_uses_noble_repo():
    script = _render()
    assert "ppa.launchpadcontent.net/amnezia/ppa/ubuntu noble main" in script


def test_install_script_enables_awg_quick_units():
    script = _render()
    assert "systemctl enable awg-quick@az_escape awg-quick@vpn_escape" in script
