"""
Tests for PUT /api/v1/nodes/balancer — escape ports must survive a weight save.

apply_rules() flushes nat PREROUTING and rebuilds it from
get_active_ports(escape_enabled), then persists the result. If the endpoint
does not forward escape_enabled, every weight save silently deletes the DNAT
rules for UDP 500 and 53443 and writes that loss to disk — which is what
happened on the live CP on 2026-09-01 09:58 (CorpAdmin-AZ-c1l).
"""
from unittest.mock import patch

import pytest

from tests.conftest import auth_header


@pytest.fixture
def two_nodes(db):
    """Two enabled nodes so the balancer has something to apply."""
    from app.db.models import Node

    for i, ip in enumerate(("10.0.0.1", "10.0.0.2"), start=1):
        db.add(
            Node(
                hostname=f"node{i}",
                private_ip=ip,
                enroll_token=f"tok-{i}",
                health="ok",
            )
        )
    db.commit()


def _payload():
    return {
        "nodes": [
            {"ip": "10.0.0.1", "weight": 50, "enabled": True},
            {"ip": "10.0.0.2", "weight": 50, "enabled": True},
        ]
    }


class TestBalancerUpdateForwardsEscapeEnabled:
    def test_escape_enabled_true_is_forwarded(
        self, client, db, admin_user, admin_token, system_settings, two_nodes
    ):
        """With escape on, the save must keep ports 500/53443 in the ruleset."""
        system_settings.cp_ip = "10.0.0.100"
        system_settings.escape_enabled = True
        db.commit()

        with patch(
            "app.api.v1.balancer.apply_rules", return_value={}
        ) as spy:
            resp = client.put(
                "/api/v1/nodes/balancer",
                json=_payload(),
                headers=auth_header(admin_token),
            )

        assert resp.status_code == 200
        spy.assert_called_once()
        kwargs = spy.call_args.kwargs
        assert kwargs.get("escape_enabled") is True, (
            f"weight save dropped the escape ports; kwargs={kwargs}, "
            f"args={spy.call_args.args}"
        )

    def test_escape_enabled_false_is_forwarded(
        self, client, db, admin_user, admin_token, system_settings, two_nodes
    ):
        """With escape off, the save must not add the escape ports either."""
        system_settings.cp_ip = "10.0.0.100"
        system_settings.escape_enabled = False
        db.commit()

        with patch(
            "app.api.v1.balancer.apply_rules", return_value={}
        ) as spy:
            resp = client.put(
                "/api/v1/nodes/balancer",
                json=_payload(),
                headers=auth_header(admin_token),
            )

        assert resp.status_code == 200
        spy.assert_called_once()
        assert spy.call_args.kwargs.get("escape_enabled") is False


class TestApplyRulesRequiresAnExplicitDecision:
    def test_escape_enabled_has_no_default(self):
        """
        apply_rules must not default escape_enabled.

        A default of False is what let this bug exist: a caller that simply
        forgets the argument silently tears down escape. Every call site has
        a SystemSettings row available, so each must state its intent.
        """
        import inspect

        from app.services.balancer import apply_rules

        param = inspect.signature(apply_rules).parameters["escape_enabled"]
        assert param.default is inspect.Parameter.empty, (
            "escape_enabled must be a required argument so a forgetful "
            "caller fails loudly instead of disabling escape"
        )
