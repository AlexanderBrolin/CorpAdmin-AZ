# Agent Iface Address Reconcile — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Дать агенту способность привести IPv4-адреса управляемых интерфейсов к тому, что записано в managed-конфиге, не перезапуская интерфейс, и показывать расхождение в метриках на каждом heartbeat.

**Architecture:** Независимый сверщик в `agent/corpweb_sync_agent.py`. Живое состояние читается одним `ip -j -4 addr show dev <iface>`, желаемое — из строк `Address =` конфига. Правка выполняется множествами: сначала `ip addr add` недостающих, затем `ip addr del` лишних, с перечитыванием ядра после каждого удаления. Применение — только из `startup_reconcile()` (после цикла применения файлов), из `send_heartbeat()` — только детект. Общий путь `apply_path()` / `apply_iface_conf()` не изменяется.

**Tech Stack:** Python 3.11+ (Debian 12/13), stdlib `subprocess` / `json` / `ipaddress`, iproute2 ≥ 4.13 (`-j`), pytest + `unittest.mock`.

**Spec:** [docs/superpowers/specs/2026-09-02-iface-address-reconcile-design.md](../specs/2026-09-02-iface-address-reconcile-design.md)

**Статус проверки:** весь код из задач 1–6 (реализация и тесты) прогнан на временной копии
`agent/` вне репозитория — 160 тестов зелёные, включая все 112 существующих. То есть план
содержит проверенно работающий код, а не намерение. Копия удалена; реализация выполняется
заново по TDD, тест за тестом.

## Global Constraints

- **Не трогать `apply_path()` и `apply_iface_conf()`.** Это общий путь всех 14 managed-файлов; его изменение вне объёма (спека, Non-goals).
- **Никакой работы с MTU** — ни правки, ни детекта. Отдельная задача `CorpAdmin-AZ-3up`.
- **Сверщик не поднимает интерфейсы.** Отсутствующий интерфейс пропускается.
- **Сверщик никогда не выпускает исключение наружу.** Любая ошибка → `log.error` + метрика.
- **Порядок в `startup_reconcile()` фиксирован:** сверщик вызывается **после** цикла `apply_path()`. Раньше — приведёт интерфейс к устаревшему локальному файлу.
- **Тесты — pytest-классы без `unittest.TestCase`**, ассерты на полный `argv`.
- **Осознанное отступление от конвенции тестов.** Существующие файлы подделывают результат
  `subprocess.run` через `MagicMock(returncode=0, stderr="")`. Здесь используется настоящий
  `subprocess.CompletedProcess` (хелпер `_completed`). Причина: `MagicMock` возвращает
  объект на **любой** атрибут, поэтому опечатка в реализации (`.stdou` вместо `.stdout`)
  дала бы зелёный тест на неработающем коде. Для изменения, которое трогает сетевые
  интерфейсы прод-нод, ложно-зелёный недопустим. Файл и без того смешивает стили —
  в `test_seed_blob.py` используется настоящий `CalledProcessError`, в
  `test_sync_agent.py` — вручную собранные объекты результата.
- **Язык:** код, комментарии, docstring'и и сообщения коммитов — английский.
- **TDD обязателен:** тест пишется первым, запускается и падает по правильной причине, только затем реализация.
- **Деплой на прод — только после merge в `CorpAdmin`.** Task 8 выполняется отдельно от Task 1–7.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `agent/corpweb_sync_agent.py` | Modify | `_IFACE_CONFS`, `_IFACES` из неё, рефактор `_apply_wg_config`, `_ip_addr_show` / `_iface_state` / `_iface_is_up`, `_conf_addresses`, `_ip_addr`, `_reconcile_one_iface`, `reconcile_iface_addresses`, две точки вызова |
| `agent/tests/test_iface_addr_reconcile.py` | Create | Все тесты сверщика (конвенция «одна фича — свой файл») |
| `agent/tests/test_sync_agent.py` | Modify | Один тест на `_IFACES == tuple(_IFACE_CONFS)` — рядом с существующими тестами констант |
| `docs/ADD-NODE.md` | Modify | Предполётная проверка «живой префикс против конфига» |

---

## Task 1: Single source of truth for the managed interface set

Сейчас набор из четырёх интерфейсов задан в файле **дважды**: как локальный `iface_map` внутри `_apply_wg_config()` и как модульный `_IFACES`. Сверщику нужен путь конфига по интерфейсу — третьей копии быть не должно.

**Files:**
- Modify: `agent/corpweb_sync_agent.py` (константы рядом с `MANAGED_FILES`; `_IFACES` ~L890; `_apply_wg_config` ~L776-843)
- Test: `agent/tests/test_sync_agent.py` (дописать в существующий класс констант)

**Interfaces:**
- Produces: `_IFACE_CONFS: dict[str, str]` — ключи `antizapret`, `vpn`, `az_escape`, `vpn_escape` в этом порядке; значения — абсолютные пути конфигов. `_IFACES: tuple[str, ...] == tuple(_IFACE_CONFS)`.

**Beads:** `CorpAdmin-AZ-c9w`

- [ ] **Step 1: Прочитать существующие тесты, которые прикрывают рефакторинг**

```bash
sed -n '194,295p' agent/tests/test_sync_agent.py
grep -n "class TestConstants" -A 20 agent/tests/test_escape_rules.py
```

Это `TestApplyWgConfigEscapeIfaces` (три теста, все четыре интерфейса, включая отсутствующий конфиг). Они — страховочная сетка: рефакторинг ничего не должен в них сломать. Класс констант в `test_escape_rules.py` показывает принятый стиль проверки модульных констант.

- [ ] **Step 2: Написать падающий тест на единый источник правды**

Дописать в `agent/tests/test_sync_agent.py` (в конец файла):

```python
class TestIfaceConfsSingleSource:
    """_IFACES must be derived from _IFACE_CONFS — one definition of the
    managed interface set, not two that can drift apart."""

    def test_iface_confs_maps_all_four_ifaces_to_their_conf_paths(self):
        assert agent._IFACE_CONFS == {
            "antizapret": "/etc/wireguard/antizapret.conf",
            "vpn": "/etc/wireguard/vpn.conf",
            "az_escape": "/etc/amnezia/amneziawg/az_escape.conf",
            "vpn_escape": "/etc/amnezia/amneziawg/vpn_escape.conf",
        }

    def test_ifaces_is_derived_from_iface_confs(self):
        assert agent._IFACES == tuple(agent._IFACE_CONFS)
```

- [ ] **Step 3: Убедиться, что тест падает по правильной причине**

Run: `cd agent && python3 -m pytest tests/test_sync_agent.py::TestIfaceConfsSingleSource -v`
Expected: FAIL — `AttributeError: module 'corpweb_sync_agent' has no attribute '_IFACE_CONFS'`.

- [ ] **Step 4: Добавить константу рядом с `MANAGED_FILES`**

Вставить сразу после блока `MANAGED_PATHS` (~L96):

```python
# Interface → wg-quick / awg-quick conf path. Single definition of the set of
# interfaces the agent manages: _IFACES is derived from it, _apply_wg_config()
# patches these files, and reconcile_iface_addresses() compares them against
# the kernel.
_IFACE_CONFS: dict[str, str] = {
    "antizapret": "/etc/wireguard/antizapret.conf",
    "vpn": "/etc/wireguard/vpn.conf",
    "az_escape": "/etc/amnezia/amneziawg/az_escape.conf",
    "vpn_escape": "/etc/amnezia/amneziawg/vpn_escape.conf",
}
```

- [ ] **Step 5: Вывести `_IFACES` из неё**

Заменить литерал (~L890):

```python
# Ordered tuple of WireGuard / AmneziaWG ifaces the agent monitors for peer
# activity. Derived from _IFACE_CONFS so the managed interface set has exactly
# one definition. The first two are the baseline; the last two host escape-mode
# (bypass) tunnels. collect_metrics() emits one active_peers_<iface> key per
# entry; collect_peers() iterates this tuple when dumping peer state.
_IFACES: tuple[str, ...] = tuple(_IFACE_CONFS)
```

- [ ] **Step 6: Перевести `_apply_wg_config()` на ту же константу**

Заменить тело функции целиком (~L776-843). Ключи, которые присылает CP, единообразны (`<iface>_address`, `<iface>_listen_port`), поэтому четыре литеральных блока сворачиваются в цикл. Локальный `import re` убирается — модуль уже импортирует `re` на верхнем уровне:

```python
def _apply_wg_config(cfg: dict) -> None:
    """Patch [Interface] section in WG conf files with CP-provided config."""
    mtu = cfg.get("mtu")

    for iface, conf_path in _IFACE_CONFS.items():
        if not os.path.exists(conf_path):
            continue

        content = open(conf_path).read()
        changed = False

        for key, value in (
            ("Address", cfg.get(f"{iface}_address")),
            ("ListenPort", cfg.get(f"{iface}_listen_port")),
            ("MTU", mtu),
        ):
            if not value:
                continue
            new_content = re.sub(
                rf"^{key}\s*=\s*.*$",
                f"{key} = {value}",
                content, flags=re.MULTILINE,
            )
            if new_content != content:
                content = new_content
                changed = True

        if changed:
            write_atomic(conf_path, content.encode())
            log.info("Patched %s with wg_config from CP", conf_path)
```

- [ ] **Step 7: Прогнать новый тест и всю страховочную сетку**

Run: `cd agent && python3 -m pytest tests/ -v -k "IfaceConfs or ApplyWgConfig or collect_metrics or collect_peers"`
Expected: PASS, ни одного FAIL. Затем полный набор: `python3 -m pytest tests/ -q` → `112 passed` плюс 2 новых = `114 passed`.

- [ ] **Step 8: Commit**

```bash
git add agent/corpweb_sync_agent.py agent/tests/test_sync_agent.py
git commit -m "refactor(agent): single source of truth for the managed iface set (CorpAdmin-AZ-c9w)

_IFACE_CONFS replaces the iface_map literal inside _apply_wg_config and now
defines _IFACES, so the four managed interfaces are declared once instead of
twice. _apply_wg_config loops over it — the CP's wg_config keys are uniform
(<iface>_address, <iface>_listen_port) — which also drops the redundant
function-local 'import re'. Behaviour is unchanged and covered by the existing
TestApplyWgConfigEscapeIfaces and the four-iface collect_metrics/collect_peers
tests."
```

---

## Task 2: Read live interface state

**Files:**
- Modify: `agent/corpweb_sync_agent.py` (`_iface_is_up` ~L573)
- Test: `agent/tests/test_iface_addr_reconcile.py` (создать)

**Interfaces:**
- Consumes: ничего из Task 1.
- Produces:
  - `_ip_addr_show(iface: str) -> subprocess.CompletedProcess | None`
  - `_iface_is_up(iface: str) -> bool` — контракт прежний: интерфейс существует в ядре
  - `_iface_state(iface: str) -> list[str] | None` — адреса вида `"10.29.8.1/21"`; `None` = ответа нет (интерфейса нет / нет `ip` / вывод не разобрался); `[]` = интерфейс есть, IPv4-адресов нет

**Beads:** `CorpAdmin-AZ-c9w`

- [ ] **Step 1: Создать файл тестов с шапкой по конвенции**

Создать `agent/tests/test_iface_addr_reconcile.py`:

```python
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
```

- [ ] **Step 2: Написать падающие тесты на чтение живого состояния**

Дописать в тот же файл:

```python
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
```

- [ ] **Step 3: Убедиться, что тесты падают по правильной причине**

Run: `cd agent && python3 -m pytest tests/test_iface_addr_reconcile.py -v`
Expected: ERROR на setup autouse-фикстуры — `AttributeError: module 'corpweb_sync_agent' has no attribute '_addr_reconcile_failures'`. Это правильный красный: ни состояния, ни функций ещё нет.

- [ ] **Step 4: Добавить состояние сверщика и функции чтения**

Добавить `import ipaddress` в блок импортов (алфавитно, между `hashlib` и `json`).

Заменить существующий `_iface_is_up` (~L573) на блок:

```python
# Consecutive failed reconcile attempts per interface, and the number of
# successful reconciles this process has performed. startup_reconcile() runs on
# every SSE reconnect, so a fix that refuses to stick must stop being retried
# rather than hammer a production interface in a loop.
_ADDR_RECONCILE_MAX_FAILURES = 3
_addr_reconcile_failures: dict[str, int] = {}
_addr_reconcile_applied_total = 0


def _ip_addr_show(iface: str) -> subprocess.CompletedProcess | None:
    """
    Run ``ip -j -4 addr show dev <iface>``.

    Returns the completed process, or None if the ip binary is missing.
    Callers distinguish three outcomes: a non-zero return code (the interface
    does not exist), rc 0 with an empty JSON array (it exists but carries no
    IPv4 address), and rc 0 with an entry.
    """
    try:
        return subprocess.run(
            ["ip", "-j", "-4", "addr", "show", "dev", iface],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None


def _iface_is_up(iface: str) -> bool:
    """Return True if the network iface currently exists in the kernel."""
    result = _ip_addr_show(iface)
    return result is not None and result.returncode == 0


def _iface_state(iface: str) -> list[str] | None:
    """
    Return the iface's global IPv4 addresses as ``<ip>/<prefixlen>`` strings.

    None means there is no usable answer — the interface does not exist, the ip
    binary is missing, or the output did not parse. An empty list means the
    interface exists but has no IPv4 address, which is a state the reconciler
    corrects rather than skips.
    """
    result = _ip_addr_show(iface)
    if result is None or result.returncode != 0:
        return None
    try:
        entries = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        log.error("Could not parse `ip -j -4 addr show dev %s` output", iface)
        return None
    if not isinstance(entries, list):
        log.error("Unexpected `ip -j` payload for %s: %r", iface, type(entries))
        return None

    addresses: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for info in entry.get("addr_info", []):
            if info.get("family") != "inet" or info.get("scope") != "global":
                continue
            local, prefixlen = info.get("local"), info.get("prefixlen")
            if local is None or prefixlen is None:
                continue
            addresses.append(f"{local}/{prefixlen}")
    return addresses
```

- [ ] **Step 5: Прогнать тесты**

Run: `cd agent && python3 -m pytest tests/test_iface_addr_reconcile.py -v`
Expected: 15 passed.

Затем регрессия на существующем контракте:
Run: `cd agent && python3 -m pytest tests/test_sync_agent.py -q`
Expected: без FAIL — `TestApplyIfaceConfBranching` продолжает работать, потому что `_iface_is_up` сохранил сигнатуру и семантику.

- [ ] **Step 6: Commit**

```bash
git add agent/corpweb_sync_agent.py agent/tests/test_iface_addr_reconcile.py
git commit -m "feat(agent): read live iface IPv4 state via ip -j (CorpAdmin-AZ-c9w)

_ip_addr_show runs 'ip -j -4 addr show dev <iface>' once and backs both
_iface_is_up (return code only, contract unchanged) and the new _iface_state,
which parses the addresses. Keeping _iface_is_up off the JSON matters: an
interface that exists without an IPv4 address returns rc 0 with an empty array,
and treating that as 'absent' would make apply_iface_conf start a running unit."
```

---

## Task 3: Read the desired state from the conf

**Files:**
- Modify: `agent/corpweb_sync_agent.py` (после `_iface_state`)
- Test: `agent/tests/test_iface_addr_reconcile.py`

**Interfaces:**
- Produces: `_conf_addresses(conf_path: str) -> list[str]` — нормализованные IPv4 вида `"10.29.8.1/21"`; пустой список, если файла нет или ни одного валидного адреса не нашлось.

**Beads:** `CorpAdmin-AZ-c9w`

- [ ] **Step 1: Написать падающие тесты**

Дописать в `agent/tests/test_iface_addr_reconcile.py`:

```python
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
```

- [ ] **Step 2: Убедиться, что тесты падают по правильной причине**

Run: `cd agent && python3 -m pytest tests/test_iface_addr_reconcile.py::TestConfAddresses -v`
Expected: FAIL — `AttributeError: module 'corpweb_sync_agent' has no attribute '_conf_addresses'`.

- [ ] **Step 3: Реализовать**

Добавить после `_iface_state`:

```python
def _conf_addresses(conf_path: str) -> list[str]:
    """
    Return the IPv4 addresses from the ``Address =`` lines of a wg-quick conf,
    normalised to ``<ip>/<prefixlen>`` so they compare byte-for-byte with what
    _iface_state() reports.

    wg-quick allows several Address lines and a comma-separated list on each,
    and every value is collected on purpose: an address the parser missed would
    land in live-minus-want and be deleted from the running interface. IPv6 is
    ignored — the reconciler only ever looks at IPv4.

    An empty list means "desired state unknown"; callers must skip the
    interface rather than assume it should have no addresses.
    """
    try:
        with open(conf_path) as fh:
            content = fh.read()
    except OSError:
        return []

    addresses: list[str] = []
    for line in content.splitlines():
        key, sep, value = line.strip().partition("=")
        if not sep or key.strip().lower() != "address":
            continue
        for item in value.split(","):
            item = item.strip()
            if not item:
                continue
            try:
                parsed = ipaddress.ip_interface(item)
            except ValueError:
                log.warning("Ignoring unparsable Address %r in %s", item, conf_path)
                continue
            if parsed.version == 4:
                addresses.append(str(parsed))
    return addresses
```

- [ ] **Step 4: Прогнать тесты**

Run: `cd agent && python3 -m pytest tests/test_iface_addr_reconcile.py -v`
Expected: 26 passed.

- [ ] **Step 5: Commit**

```bash
git add agent/corpweb_sync_agent.py agent/tests/test_iface_addr_reconcile.py
git commit -m "feat(agent): parse desired iface addresses from the wg conf (CorpAdmin-AZ-c9w)

_conf_addresses collects every value from every Address line, including
comma-separated lists, and normalises through ipaddress.ip_interface so the
result compares directly with the kernel's. Collecting all of them is a
correctness requirement, not thoroughness: a missed address would be seen as
unwanted and deleted from the live interface."
```

---

## Task 4: Apply the address set to one interface

**Files:**
- Modify: `agent/corpweb_sync_agent.py` (после `_conf_addresses`)
- Test: `agent/tests/test_iface_addr_reconcile.py`

**Interfaces:**
- Consumes: `_iface_state` (Task 2).
- Produces:
  - `_ip_addr(op: str, addr: str, iface: str) -> bool` — `op` это `"add"` или `"del"`
  - `_reconcile_one_iface(iface: str, live: list[str], want: list[str]) -> bool`

**Beads:** `CorpAdmin-AZ-c9w`

- [ ] **Step 1: Написать падающие тесты**

Дописать в `agent/tests/test_iface_addr_reconcile.py`:

```python
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
```

- [ ] **Step 2: Убедиться, что тесты падают по правильной причине**

Run: `cd agent && python3 -m pytest tests/test_iface_addr_reconcile.py::TestIpAddr tests/test_iface_addr_reconcile.py::TestReconcileOneIface -v`
Expected: FAIL — `AttributeError: ... has no attribute '_ip_addr'`.

- [ ] **Step 3: Реализовать**

Добавить после `_conf_addresses`:

```python
def _ip_addr(op: str, addr: str, iface: str) -> bool:
    """Run ``ip addr add|del <addr> dev <iface>``. Returns True on success."""
    try:
        subprocess.run(
            ["ip", "addr", op, addr, "dev", iface],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        log.error("ip addr %s %s dev %s failed (rc=%d): %s",
                  op, addr, iface, exc.returncode, (exc.stderr or "").strip())
        return False
    except FileNotFoundError:
        log.error("ip binary not found — cannot run 'ip addr %s'", op)
        return False
    log.info("ip addr %s %s dev %s", op, addr, iface)
    return True


def _reconcile_one_iface(iface: str, live: list[str], want: list[str]) -> bool:
    """
    Bring one interface's address set to ``want``. Returns True if the kernel
    ends up matching exactly.

    Adds before deleting, so the interface is never momentarily without an
    address, and re-reads the kernel after every delete: with
    promote_secondaries off, removing a primary address also removes the
    secondaries in its subnet, so anything wanted that disappears is restored
    at once and the remaining deletes are abandoned.
    """
    live_set, want_set = set(live), set(want)

    for addr in sorted(want_set - live_set):
        if not _ip_addr("add", addr, iface):
            return False

    for addr in sorted(live_set - want_set):
        if not _ip_addr("del", addr, iface):
            return False
        current = _iface_state(iface)
        if current is None:
            log.error("Lost sight of %s while reconciling its addresses", iface)
            return False
        missing = want_set - set(current)
        if missing:
            log.error("Removing %s from %s also dropped %s — restoring, no further deletes",
                      addr, iface, sorted(missing))
            for lost in sorted(missing):
                _ip_addr("add", lost, iface)
            return False

    final = _iface_state(iface)
    return final is not None and set(final) == want_set
```

- [ ] **Step 4: Прогнать тесты**

Run: `cd agent && python3 -m pytest tests/test_iface_addr_reconcile.py -v`
Expected: 35 passed.

- [ ] **Step 5: Commit**

```bash
git add agent/corpweb_sync_agent.py agent/tests/test_iface_addr_reconcile.py
git commit -m "feat(agent): apply an address set to one iface, add before delete (CorpAdmin-AZ-c9w)

_reconcile_one_iface works in sets rather than 'changing the prefix', because
'ip addr change' was observed on wgfi4 to add a second address instead of
replacing the existing one. Adds run first so the interface always holds an
address, and the kernel is re-read after each delete to catch the
promote_secondaries=0 case where deleting a primary drops its secondaries."
```

---

## Task 5: The reconciler itself

**Files:**
- Modify: `agent/corpweb_sync_agent.py` (после `_reconcile_one_iface`)
- Test: `agent/tests/test_iface_addr_reconcile.py`

**Interfaces:**
- Consumes: `_IFACE_CONFS` (Task 1), `_conf_addresses` (Task 3), `_iface_state` (Task 2), `_reconcile_one_iface` (Task 4).
- Produces: `reconcile_iface_addresses(apply: bool) -> dict` — метрики: `iface_addr_drift_detected`, `iface_addr_drift`, `iface_addr_drift_applied_count`, `iface_addr_drift_failed`. Пустые ключи не отдаются.

**Beads:** `CorpAdmin-AZ-c9w`

- [ ] **Step 1: Написать падающие тесты**

Дописать в `agent/tests/test_iface_addr_reconcile.py`:

```python
ONE_IFACE = {"antizapret": "/etc/wireguard/antizapret.conf"}


class TestReconcileIfaceAddresses:
    def test_no_drift_runs_no_commands(self):
        with patch.dict(agent._IFACE_CONFS, ONE_IFACE, clear=True), \
             patch("corpweb_sync_agent._conf_addresses", return_value=["10.29.8.1/21"]), \
             patch("corpweb_sync_agent._iface_state", return_value=["10.29.8.1/21"]), \
             patch("corpweb_sync_agent._ip_addr") as m:
            metrics = agent.reconcile_iface_addresses(apply=True)
        m.assert_not_called()
        assert metrics == {}

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
        with patch.dict(agent._IFACE_CONFS, ONE_IFACE, clear=True), \
             patch("corpweb_sync_agent._conf_addresses", return_value=["10.29.8.1/21"]), \
             patch("corpweb_sync_agent._iface_state", return_value=["10.29.8.1/24"]), \
             patch("corpweb_sync_agent._reconcile_one_iface") as m:
            metrics = agent.reconcile_iface_addresses(apply=False)
        m.assert_not_called()
        assert metrics["iface_addr_drift_detected"] is True
        assert "iface_addr_drift_applied_count" not in metrics

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
```

- [ ] **Step 2: Убедиться, что тесты падают по правильной причине**

Run: `cd agent && python3 -m pytest tests/test_iface_addr_reconcile.py::TestReconcileIfaceAddresses -v`
Expected: FAIL — `AttributeError: ... has no attribute 'reconcile_iface_addresses'`.

- [ ] **Step 3: Реализовать**

Добавить после `_reconcile_one_iface`:

```python
def reconcile_iface_addresses(apply: bool) -> dict:
    """
    Compare each managed interface's live IPv4 addresses against its conf and,
    when ``apply`` is set, bring the kernel into line. Returns heartbeat
    metrics describing what was found.

    An interface is only touched when it exists and its conf yields at least
    one address: without a trustworthy desired state we never mutate a live
    interface. ``iface_addr_drift`` reports what the pass found, whether or not
    the pass then fixed it; ``iface_addr_drift_applied_count`` says how many
    fixes this process has made.

    Never raises — the heartbeat must survive any failure here.
    """
    global _addr_reconcile_applied_total

    drift: dict = {}
    failed = False

    for iface, conf_path in _IFACE_CONFS.items():
        try:
            want = _conf_addresses(conf_path)
            if not want:
                continue
            live = _iface_state(iface)
            if live is None:
                continue
            if set(live) == set(want):
                _addr_reconcile_failures.pop(iface, None)
                continue

            drift[iface] = {"live": sorted(live), "want": sorted(want)}
            if not apply:
                continue

            if _addr_reconcile_failures.get(iface, 0) >= _ADDR_RECONCILE_MAX_FAILURES:
                log.error("Address drift on %s persists after %d attempts — not retrying",
                          iface, _ADDR_RECONCILE_MAX_FAILURES)
                failed = True
                continue

            log.warning("Address drift on %s: live=%s want=%s — reconciling",
                        iface, sorted(live), sorted(want))
            if _reconcile_one_iface(iface, live, want):
                _addr_reconcile_applied_total += 1
                _addr_reconcile_failures.pop(iface, None)
            else:
                _addr_reconcile_failures[iface] = _addr_reconcile_failures.get(iface, 0) + 1
                if _addr_reconcile_failures[iface] >= _ADDR_RECONCILE_MAX_FAILURES:
                    failed = True
        except Exception as exc:  # defensive — the heartbeat must not break
            log.error("Address reconcile failed for %s: %s", iface, exc)
            failed = True

    metrics: dict = {}
    if drift:
        metrics["iface_addr_drift_detected"] = True
        metrics["iface_addr_drift"] = drift
    if _addr_reconcile_applied_total:
        metrics["iface_addr_drift_applied_count"] = _addr_reconcile_applied_total
    if failed:
        metrics["iface_addr_drift_failed"] = True
    return metrics
```

- [ ] **Step 4: Прогнать тесты**

Run: `cd agent && python3 -m pytest tests/test_iface_addr_reconcile.py -v`
Expected: 43 passed.

- [ ] **Step 5: Commit**

```bash
git add agent/corpweb_sync_agent.py agent/tests/test_iface_addr_reconcile.py
git commit -m "feat(agent): reconcile iface addresses against the managed conf (CorpAdmin-AZ-c9w)

reconcile_iface_addresses compares the kernel with the conf for every managed
interface and, with apply set, fixes the difference. An interface whose conf
yields no address is skipped rather than emptied, and a fix that fails three
times in a row stops being retried — startup_reconcile runs on every SSE
reconnect, so an unstickable fix must not hammer a production interface."
```

---

## Task 6: Wire the reconciler into the agent's two loops

**Files:**
- Modify: `agent/corpweb_sync_agent.py` (`startup_reconcile` ~L850-880, `send_heartbeat` ~L997)
- Test: `agent/tests/test_iface_addr_reconcile.py`

**Interfaces:**
- Consumes: `reconcile_iface_addresses` (Task 5).
- Produces: метрики сверщика в payload heartbeat'а.

**Beads:** `CorpAdmin-AZ-c9w`

- [ ] **Step 1: Написать падающие тесты**

Дописать в `agent/tests/test_iface_addr_reconcile.py`:

```python
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
```

В блок импортов тестового файла добавить то, чем пользуются эти тесты:
`import base64` (рядом с `import json`) и `MagicMock` в строку
`from unittest.mock import patch` → `from unittest.mock import MagicMock, patch`.

- [ ] **Step 2: Убедиться, что тесты падают по правильной причине**

Run: `cd agent && python3 -m pytest tests/test_iface_addr_reconcile.py::TestIntegration -v`
Expected: FAIL — `reconcile_iface_addresses` не вызывается ни из `startup_reconcile`, ни из `send_heartbeat` (`AssertionError` на `order[-1]` и `rec.assert_called_once_with`).

- [ ] **Step 3: Вызвать сверщик из `startup_reconcile()`**

Вставить сразу после цикла `for path, hook in MANAGED_FILES:` и **до** блока push'а seed-блобов:

```python
    # The confs on disk are now the CP's version, which is the only reason the
    # conf can be trusted as the desired interface state. Reconciling before
    # the loop above would enforce a stale local file.
    reconcile_iface_addresses(apply=True)
```

- [ ] **Step 4: Слить метрики в heartbeat**

В `send_heartbeat()`, сразу после блока `sync_escape_rules()`:

```python
    try:
        addr_metrics = reconcile_iface_addresses(apply=False)
    except Exception as exc:  # defensive — mirrors the sync_escape_rules guard
        log.error("reconcile_iface_addresses unexpectedly raised: %s", exc)
        addr_metrics = {"iface_addr_error": f"unexpected: {exc.__class__.__name__}"}
    metrics.update(addr_metrics)
```

- [ ] **Step 5: Прогнать весь набор агента**

Run: `cd agent && python3 -m pytest tests/ -q`
Expected: `160 passed` — 112 исходных + 2 из Task 1 + 46 в `test_iface_addr_reconcile.py`
(15 + 11 + 9 + 8 + 3 по задачам 2–6). Ни одного FAIL и ни одного ERROR.
Число проверено прогоном на временной копии агента до написания плана.

- [ ] **Step 6: Commit**

```bash
git add agent/corpweb_sync_agent.py agent/tests/test_iface_addr_reconcile.py
git commit -m "feat(agent): run the address reconciler from startup and heartbeat (CorpAdmin-AZ-c9w)

startup_reconcile applies, after the file loop so the conf is already the CP's
version; send_heartbeat only reports, every 30s. Deliberately unlike
sync_escape_rules, which does apply from the heartbeat path: a live network
interface on a production node is not a shell hook, and a fix loop running
twice a minute is not worth the visibility it buys."
```

---

## Task 7: Preflight check in the node runbook

Задача `CorpAdmin-AZ-3f9` требует, чтобы предполётная проверка ноды ловила это расхождение. Сверщик закрывает проблему автоматически, но runbook читают до того, как агент вообще установлен.

**Files:**
- Modify: `docs/ADD-NODE.md` (раздел обязательной предполётной проверки)

**Beads:** `CorpAdmin-AZ-3f9`

- [ ] **Step 1: Найти нужный раздел**

```bash
grep -n "^#\|^##" docs/ADD-NODE.md | head -30
```

- [ ] **Step 2: Дописать проверку в конец предполётного раздела**

```markdown
### Живой префикс интерфейса против конфига

Установщик AntiZapret поднимает `antizapret` и `vpn` из шаблона
`/etc/wireguard/templates/*.conf`, где подсеть **`/24`**. Агент затем записывает
в конфиг правильную `/21`, но `wg syncconf` не умеет применять `Address`
к работающему интерфейсу — расхождение остаётся невидимым, `drift` в панели равен нулю,
а клиенты с адресами вне `/24` подключаются и не получают трафика.

Агент версии CorpAdmin-AZ-c9w и новее чинит это сам при старте. Проверить результат:

```bash
for i in antizapret vpn az_escape vpn_escape; do
  case $i in
    az_escape|vpn_escape) c=/etc/amnezia/amneziawg/$i.conf ;;
    *)                    c=/etc/wireguard/$i.conf ;;
  esac
  printf '%-12s live=%-18s conf=%s\n' "$i" \
    "$(ip -4 -br addr show dev $i 2>/dev/null | awk '{print $3}')" \
    "$(grep -m1 '^Address' $c 2>/dev/null | sed 's/.*= *//')"
done
```

Живое значение и значение из конфига должны совпадать у всех четырёх. Если нет —
посмотреть `journalctl -u corpweb-sync-agent | grep -i 'address drift'`.
Отдельно убедиться, что маршрут до клиента из старшей подсети ведёт в туннель,
а не в шлюз:

```bash
ip route get 10.29.11.10   # ожидается: dev antizapret, НЕ 'via <шлюз> dev eth0'
```
```

- [ ] **Step 3: Проверить рендер и ссылки**

```bash
grep -n "Живой префикс" docs/ADD-NODE.md
```

- [ ] **Step 4: Commit**

```bash
git add docs/ADD-NODE.md
git commit -m "docs(add-node): preflight check for live iface prefix vs conf (CorpAdmin-AZ-3f9)

The AntiZapret installer brings antizapret/vpn up from a /24 template while the
agent writes /21 into the conf, and wg syncconf cannot apply Address to a
running interface. Nothing in the panel shows it — file drift stays at zero —
so the runbook now compares the live prefix with the conf and checks that a
peer from a higher subnet routes into the tunnel."
```

---

## Task 8: Verification and production rollout

Выполняется **после merge в `CorpAdmin`** — деплой из feature-ветки запрещён.

**Files:** нет изменений в коде.

**Beads:** `CorpAdmin-AZ-c9w`, `CorpAdmin-AZ-3f9`

- [ ] **Step 1: Полная верификация локально**

```bash
cd agent && python3 -m pytest tests/ -q
cd ../corpweb/backend && python3 -m pytest -q
```

Expected: агент — все зелёные; backend — `354 passed` (не затрагивался, но проверяется на всякий случай).

- [ ] **Step 2: Убедиться, что запрещённые файлы не изменены**

```bash
git diff CorpAdmin...HEAD -- agent/corpweb_sync_agent.py | grep -E "^[-+].*def (apply_path|apply_iface_conf)" || echo "OK: apply_path/apply_iface_conf untouched"
```

Expected: `OK: ...`. Если строки нашлись — Global Constraints нарушены, откатить.

- [ ] **Step 3: Code review и merge**

Использовать `superpowers:requesting-code-review`, затем `superpowers:finishing-a-development-branch`. Push только через 443:
`git push ssh://git@ssh.github.com:443/AlexanderBrolin/CorpAdmin-AZ.git <branch>`

- [ ] **Step 4: Выкатить агента на wgfi3 (одну ноду), в непиковое время**

Сначала убедиться, что мы **не** в feature-ветке — деплой из неё запрещён:

```bash
git rev-parse --abbrev-ref HEAD    # должно быть CorpAdmin
```

Обновить дистрибутив на CP (`docs/ADD-NODE.md`, раздел «Убедиться, что CP раздаёт
актуального агента» — этот каталог живёт отдельно от репозитория и легко отстаёт):

```bash
sha256sum agent/corpweb_sync_agent.py
ssh -p 2201 brolin@168.113.209.218 'sha256sum /opt/corpweb/agent/corpweb_sync_agent.py'
# расходятся — обновить:
scp -P 2201 agent/corpweb_sync_agent.py \
    brolin@168.113.209.218:/opt/corpweb/agent/corpweb_sync_agent.py
```

Затем обновить одну ноду — wgfi3, в непиковое время. Юнит запускает
`/usr/local/bin/corpweb-sync-agent`, который исполняет
`/usr/local/bin/corpweb-sync-agent.py`:

```bash
ssh -J brolin@wgfi-office.p4i.ru:2201 -p 2201 brolin@89.125.26.93 \
  'sudo wg show antizapret | grep -c "latest handshake"'      # снять до

scp -o "ProxyJump=brolin@wgfi-office.p4i.ru:2201" -P 2201 agent/corpweb_sync_agent.py \
    brolin@89.125.26.93:/tmp/corpweb_sync_agent.py
ssh -J brolin@wgfi-office.p4i.ru:2201 -p 2201 brolin@89.125.26.93 \
  'sudo install -m 0755 /tmp/corpweb_sync_agent.py /usr/local/bin/corpweb-sync-agent.py \
   && sudo systemctl restart corpweb-sync-agent \
   && sleep 5 \
   && sudo journalctl -u corpweb-sync-agent -n 50 --no-pager | grep -iE "address|drift" \
   && sudo wg show antizapret | grep -c "latest handshake"'   # снять после
```

Expected: строк `Address drift` нет — обе ноды уже приведены хотфиксом 2026-09-02.
Число рукопожатий до и после отличается не больше, чем на естественные колебания
(единицы), а не в разы.

- [ ] **Step 5: Контролируемая проверка на wgfi4, что сверщик действительно работает**

```bash
ssh -J brolin@wgfi-office.p4i.ru:2201 -p 2201 brolin@78.17.39.244
# внести заведомо лишний адрес, не пересекающийся ни с одной клиентской подсетью
ip addr add 10.29.99.1/32 dev antizapret
ip -4 -br addr show antizapret          # видно два адреса
# дождаться heartbeat (30 с) и проверить метрику на CP:
#   psql ... -c "SELECT hostname, metrics->'iface_addr_drift' FROM nodes;"
systemctl restart corpweb-sync-agent
sleep 10
ip -4 -br addr show antizapret          # ожидается только 10.29.8.1/21
```

Expected: до рестарта метрика `iface_addr_drift` показывает лишний адрес; после — интерфейс чист, `iface_addr_drift_applied_count = 1`, число пиров с рукопожатием не просело.

- [ ] **Step 6: Закрыть задачи**

```bash
bd close CorpAdmin-AZ-c9w --reason "Reconciler shipped and verified on wgfi3/wgfi4"
bd close CorpAdmin-AZ-3f9 --reason "Root cause fixed in the agent; runbook preflight added"
```

---

## Self-Review

**Покрытие спеки.** Пройдено по разделам:

| требование спеки | задача |
|---|---|
| `_IFACE_CONFS` без дублирования, `_IFACES` из неё, упрощение `_apply_wg_config` | Task 1 |
| `_ip_addr_show` / `_iface_is_up` на returncode / `_iface_state` с тремя состояниями | Task 2 |
| `_conf_addresses`: список через запятую, несколько строк, IPv6, мусор, отсутствие файла | Task 3 |
| add-before-delete, откат при `promote_secondaries=0`, не удалять последний адрес | Task 4 |
| пропуск при `want` пустом и при отсутствующем интерфейсе, backoff, метрики | Task 5 |
| точки вызова и инвариант порядка, изоляция ошибок в heartbeat | Task 6 |
| предполётная проверка в runbook (acceptance `CorpAdmin-AZ-3f9`) | Task 7 |
| проверка на проде, включая контролируемый тест | Task 8 |

Не покрытого не осталось. MTU в плане отсутствует сознательно (Non-goals + `CorpAdmin-AZ-3up`).

**Согласованность имён.** `_ip_addr_show`, `_iface_is_up`, `_iface_state`, `_conf_addresses`, `_ip_addr`, `_reconcile_one_iface`, `reconcile_iface_addresses`, `_IFACE_CONFS`, `_IFACES`, `_ADDR_RECONCILE_MAX_FAILURES`, `_addr_reconcile_failures`, `_addr_reconcile_applied_total` — во всех задачах и тестах написаны одинаково. Ключи метрик: `iface_addr_drift_detected`, `iface_addr_drift`, `iface_addr_drift_applied_count`, `iface_addr_drift_failed`, `iface_addr_error`.

**Плейсхолдеров нет.** Каждый шаг содержит исполнимый код или команду с ожидаемым
результатом. Второй проход убрал последний размытый шаг — «обновить `/opt/corpweb/agent`
из репозитория» в Task 8 заменён на конкретные команды с реальными путями
(`/opt/corpweb/agent/corpweb_sync_agent.py` на CP, `/usr/local/bin/corpweb-sync-agent.py`
на ноде — оба проверены на живых машинах).

**Что ещё исправил второй проход:**

- добавлены два теста на битую форму вывода `ip -j` (валидный JSON, но не массив;
  элементы не-объекты) — ветки `isinstance` в `_iface_state` были реализованы, но не покрыты;
- ожидаемые числа тестов пересчитаны по фактическому прогону: 15 / 26 / 35 / 43 / 160;
- страховочная проверка в Task 8 сравнивала с `main`, хотя работа ведётся от `CorpAdmin` —
  на `main` диапазон дал бы посторонние изменения.
