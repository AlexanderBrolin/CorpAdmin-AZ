"""Tests for uplink throughput metrics.

collect_metrics() used to look for the uplink by name — any(x in parts[0] for x in
("eth0", "ens")). Hosters name interfaces differently: wgfi5 (Aeza) calls it net0, so
on 2026-09-18 that node reported no rx/tx at all and the panel drew a flat zero while
the node was actually pushing 15 Mbit/s. The uplink is now taken from the default
route, which holds regardless of naming (CorpAdmin-AZ-zcq).
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import corpweb_sync_agent as agent  # noqa: E402


ROUTE_NET0 = (
    "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
    "net0\t0000000A\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0\n"
    "net0\t00000000\t0100000A\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
)
ROUTE_NO_DEFAULT = (
    "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
    "net0\t0000000A\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0\n"
)


def _dev(iface: str, rx: int, tx: int) -> str:
    return (
        "Inter-|   Receive                                                |  Transmit\n"
        " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets\n"
        "    lo: 1000 10 0 0 0 0 0 0 2000 20 0 0 0 0 0 0\n"
        f"  {iface}: {rx} 10 0 0 0 0 0 0 {tx} 20 0 0 0 0 0 0\n"
    )


@pytest.fixture(autouse=True)
def _reset_counter():
    agent._prev_net = {"rx": 0, "tx": 0, "ts": 0.0}
    yield
    agent._prev_net = {"rx": 0, "tx": 0, "ts": 0.0}


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def test_uplink_iface_is_taken_from_default_route(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "_PROC_ROUTE", _write(tmp_path, "route", ROUTE_NET0))
    assert agent._uplink_iface() == "net0", "имя аплинка берётся из маршрута по умолчанию"


def test_uplink_iface_none_without_default_route(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "_PROC_ROUTE", _write(tmp_path, "route", ROUTE_NO_DEFAULT))
    assert agent._uplink_iface() is None


def test_metrics_collected_on_node_named_net0(tmp_path, monkeypatch):
    """Регрессия wgfi5: имя не eth0/ens*, метрики обязаны считаться."""
    monkeypatch.setattr(agent, "_PROC_ROUTE", _write(tmp_path, "route", ROUTE_NET0))
    monkeypatch.setattr(agent, "_PROC_NET_DEV", _write(tmp_path, "dev1", _dev("net0", 1000, 5000)))
    monkeypatch.setattr(agent, "_active_peers", lambda iface: 0)

    first = agent.collect_metrics()
    assert "rx_bytes_per_sec" not in first, "первый замер задаёт базу, скорости ещё нет"

    monkeypatch.setattr(agent, "_PROC_NET_DEV", _write(tmp_path, "dev2", _dev("net0", 3000, 9000)))
    second = agent.collect_metrics()
    assert second["rx_bytes_per_sec"] > 0, "дельта 2000 байт обязана превратиться в скорость"
    assert second["tx_bytes_per_sec"] > 0


def test_metrics_survive_missing_proc_files(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "_PROC_ROUTE", str(tmp_path / "nope"))
    monkeypatch.setattr(agent, "_PROC_NET_DEV", str(tmp_path / "nope2"))
    monkeypatch.setattr(agent, "_active_peers", lambda iface: 0)
    assert agent.collect_metrics()["active_peers_antizapret"] == 0
