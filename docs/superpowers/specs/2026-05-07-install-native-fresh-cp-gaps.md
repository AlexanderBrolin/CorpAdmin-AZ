# Install-native CP fresh-install gaps

**Beads epic:** CorpAdmin-AZ-6jl
**Beads child bugs:** CorpAdmin-AZ-1oz (P1), CorpAdmin-AZ-lpa (P1), CorpAdmin-AZ-9nq (P1)
**Beads follow-up (out-of-scope):** CorpAdmin-AZ-3pb (P2 — full removal of `Base.metadata.create_all()`)
**Date:** 2026-05-07
**Author:** brolin (with Claude)

## Problem

Свежеустановленный CP через `corpweb/install-native.sh` **не работает** без ручного вмешательства. Все три бага наблюдались на `bb.azfi.ru` при первой установке (e5r-сессия) и были устранены руками; пока install-native.sh не починен, любая новая установка повторит ту же боль.

### Defect 1 — `iptables` не установлен (CorpAdmin-AZ-1oz, P1)

`install-native.sh` ставит `postgresql / python3 / nodejs / nginx / certbot`, но **не** ставит `iptables` и `iptables-persistent`. На Debian 12/13 они не входят в base install по умолчанию (на минимальных образах их может не быть; на cloud-image обычно есть, но это не гарантия).

Цепочка:

1. Backend [services/balancer.py — `ensure_ports_reconciled`](../../../corpweb/backend/app/services/balancer.py) вызывает `iptables -t nat -A PREROUTING …` для DNAT-балансировщика (внешние порты `52443/52080` → внутренние `51443/51080` ифейсов антизапрета на ноде).
2. Если `iptables` бинарь отсутствует — `subprocess.run` ловит `FileNotFoundError`, балансировщик молча no-op'ит.
3. На клиенте при подключении пакеты приходят на `52443/UDP`, не получают DNAT → AntiZapret-туннель не поднимается.

Дополнительно: `netfilter-persistent` нужен для сохранения правил после reboot — без него правила исчезают на перезагрузке.

### Defect 2 — `net.ipv4.ip_forward=1` не выставлен (CorpAdmin-AZ-lpa, P1)

`install-native.sh` не пишет sysctl-файл с `ip_forward=1` и не загружает его. На Debian 12/13 default `net.ipv4.ip_forward=0`. После DNAT в PREROUTING ядро делает routing через FORWARD chain — без `ip_forward` пакет дропается.

Симптом: даже с правильно установленным iptables и DNAT-правилами клиент не пингуется через VPN. Видно в `iptables -t nat -L -nvc PREROUTING` — DNAT counter растёт, но `iptables -L FORWARD -nvc` показывает 0 packets через FORWARD цепочку.

Установка делается через `/etc/sysctl.d/99-corpweb-forwarding.conf` с `net.ipv4.ip_forward=1` + `sysctl --system` (или `sysctl -p <file>`) для применения здесь и сейчас.

### Defect 3 — alembic скрытно глотает ошибки + dual schema source (CorpAdmin-AZ-9nq, P1)

Два связанных issue в обработке БД-схемы:

**3a.** [install-native.sh:363](../../../corpweb/install-native.sh#L363):

```bash
"$INSTALL_DIR/backend/venv/bin/alembic" upgrade head 2>/dev/null || \
    print_warning "Alembic: часть миграций уже применена"
```

`2>/dev/null` подавляет stderr — реальные ошибки миграций (например, синтаксическая ошибка в новой версии, конфликт с уже существующей таблицей не из-за idempotency, потерянная dependency) не видны при установке. На bb.azfi.ru e5r-сессия дебажилась тем, что мы вручную запускали `alembic upgrade head` без `2>/dev/null` и видели `relation … already exists`. Если ошибка реальная — install продолжается, БД остаётся в неконсистентном состоянии, симптомы проявляются позже (отсутствующие триггеры → SSE/sync не работает).

**3b.** [init_db.py:24](../../../corpweb/backend/app/db/init_db.py#L24):

```python
def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    ...
```

`Base.metadata.create_all()` создаёт таблицы из SQLAlchemy моделей — **минуя alembic**. Это второй источник правды для схемы и нарушает invariant "alembic — единственный owner DDL". Последствия:

- На fresh install: даже если alembic-миграция корявая или неполная, `create_all()` "залатывает" таблицы из моделей. Это маскирует баги в миграциях.
- Триггеры, индексы, constraints, default-значения, которые есть в alembic-миграциях но нет в model declarations (например `pg_notify` триггеры из [0002_ha_tables.py](../../../corpweb/backend/alembic/versions/0002_ha_tables.py)), **не воспроизводятся** через `create_all()` — нужно отдельно гонять alembic.
- На existing install с `alembic_version` назад от current: `create_all()` молча создаст недостающие таблицы из current моделей, а alembic потом увидит их и упадёт на migration boundary.

Вариант минимального риска для **этого** эпика — оставить `Base.metadata.create_all()` как safety-net (это reduces probability of breakage on fresh install), но убрать `2>/dev/null` чтобы alembic-ошибки были видны. Полное удаление `create_all()` отслеживается отдельно: **CorpAdmin-AZ-3pb** (P2). Валидация alembic-coverage (модели ↔ миграции, триггеры из 0002_ha_tables.py) — пререквизит для 3pb, вне scope этого эпика.

## Goals

1. После запуска `bash corpweb/install-native.sh` на свежей Debian 12/13 VM CP работает без ручных шагов. Балансировщик (`ensure_ports_reconciled`) пишет DNAT правила без silent failure.
2. После reboot CP правила DNAT восстанавливаются (через `iptables-persistent`).
3. Alembic-ошибки при миграциях видны в выводе install-скрипта; install не "успешно" завершается с silent failure миграций.
4. Изменения **идемпотентны** — повторный запуск install-native.sh на уже установленном CP не падает и не вносит drift.

## Non-goals (explicit scope-out)

- **Удаление `Base.metadata.create_all()` из `init_db()`.** Tracked в **CorpAdmin-AZ-3pb** (P2). Требует валидации alembic-coverage против всех моделей (триггеры, индексы, constraints, defaults). Этот эпик оставляет `create_all()` как safety-net и не трогает `init_db.py` вообще — изменения **только** в `install-native.sh` (несмотря на то, что defect 3 называет два файла).
- **CI/smoke-test для install-native.sh в чистой VM/контейнере.** Не строим infrastructure для одного раз-в-полгода скрипта; verification — manual on a clean Debian 13 VM при следующем provisioning'е CP.
- **Frontend / backend feature work.** Изменения только в `corpweb/install-native.sh` (3 группы правок). Backend и agent не трогаются — их test suites используются как baseline регрессии.
- **Установка `iptables-legacy` vs `iptables-nft` selector.** Debian 13 default — nf_tables, balancer.py использует `iptables` shim который по умолчанию `iptables-nft`; полагаемся на default. Если в будущем понадобится явный legacy — отдельный issue.
- **Idempotency-тесты через actual re-run скрипта.** Идемпотентность доказывается конструкцией (`if ! command -v …` guards и overwrite-safe heredocs), не unit-тестами. Acceptance #5 — manual проверка.

## Architecture

Один PR `feature/6jl-install-native-fixes → CorpAdmin`, три логических группы изменений **только в `install-native.sh`** (init_db.py НЕ трогаем — см. Non-goals):

1. **Системные deps (1oz)**: новый блок установки `iptables iptables-persistent netfilter-persistent` после блока certbot, по тому же паттерну `if ! command -v iptables-save &> /dev/null; then apt-get install -y -qq … fi`. Обоснование `-qq` и `> /dev/null` — соответствие существующему стилю остальных deps. `iptables-save` (а не сам `iptables`) выбран как guard, потому что он гарантированно отсутствует если пакет `iptables-persistent` не поставлен.

2. **Sysctl ip_forward (lpa)**: новый шаг сразу после iptables-блока. Пишет `/etc/sysctl.d/99-corpweb-forwarding.conf` (heredoc с одинарными кавычками — без подстановки переменных), запускает `sysctl --system` для применения здесь и сейчас. Идемпотентно: повторный запуск перезапишет файл тем же содержимым (no-op для kernel state).

3. **Alembic noisy errors (9nq partial)**: убрать `2>/dev/null` из строки 363, оставить остальное (`|| print_warning …`) — если alembic вернул non-zero, причина теперь видна; print_warning продолжает поток (не аварийно exit'ит). Текст warning'а уточняется чтобы отразить что причина может быть не только в idempotency. `Base.metadata.create_all()` не трогаем (см. Non-goals и CorpAdmin-AZ-3pb).

## Testing strategy

Минимум shell-grep тестов через pytest для всех трёх — чтобы (a) RED-этап TDD был, (b) PR-review увидел concrete assertions, (c) accidental rollback в будущем будет flagged тестом. Тесты грубые — проверяют что нужные команды/строки **присутствуют** в `install-native.sh`; они не запускают сам скрипт.

Расположение: новый файл `tests/install/test_install_native.py` в корне репо (не в `corpweb/backend/tests/`, потому что это test о shell-скрипте install, а не о backend code; это уровень repo).

Структура тестов:

```python
# tests/install/test_install_native.py
import pathlib

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "corpweb" / "install-native.sh"
TEXT = SCRIPT.read_text()

def test_installs_iptables_and_persistent():
    assert "apt-get install -y -qq iptables iptables-persistent netfilter-persistent" in TEXT

def test_writes_ip_forward_sysctl_drop_in():
    assert "/etc/sysctl.d/99-corpweb-forwarding.conf" in TEXT
    assert "net.ipv4.ip_forward=1" in TEXT
    assert "sysctl --system" in TEXT  # apply now

def test_alembic_does_not_swallow_stderr():
    # Regression: 2>/dev/null near "alembic upgrade head" hides migration errors
    lines = [l for l in TEXT.splitlines() if "alembic" in l and "upgrade head" in l]
    assert lines, "alembic upgrade head invocation missing"
    for l in lines:
        assert "2>/dev/null" not in l, f"stderr swallowed: {l!r}"
```

Backend регрессионный тест (pytest backend conftest гонит alembic через test fixtures — если 350 продолжает проходить, значит alembic схема полная и совместимая). Никаких изменений в backend кроме `init_db.py:24` мы не делаем; backend тесты должны остаться green как baseline.

Для install-native.sh shell-тесты: НЕ исполняем скрипт в pytest (он требует root, ставит пакеты, конфигурит systemd — это не unit). Acceptance — manual verify на чистой VM (см. Acceptance criterion #4).

## Acceptance criteria

1. ✅ **Automated tests:** `tests/install/test_install_native.py` — три теста пишутся ДО fix'а, фейлятся (RED), проходят после fix'а (GREEN). После merge — часть suite, попадают в `pytest tests/install/`.
2. ✅ **Backend regression:** `pytest corpweb/backend/tests/` — 350 остаются green (no regression). Поскольку backend код мы не трогаем — это sanity baseline, а не функциональный тест fix'а.
3. ✅ **Agent regression:** `pytest agent/` — 112 остаются green (sanity baseline, agent не трогаем).
4. ✅ **Syntax:** `bash -n corpweb/install-native.sh` → exit 0.
5. ⚠️ **Manual verification on a clean Debian 13 VM** (deferred — выполняется разработчиком при следующем provisioning'е CP, не блокирует merge):
   - `which iptables` → `/usr/sbin/iptables` (или `/sbin/iptables`).
   - `sysctl net.ipv4.ip_forward` → `net.ipv4.ip_forward = 1`.
   - `cat /etc/sysctl.d/99-corpweb-forwarding.conf` → содержит `net.ipv4.ip_forward=1`.
   - `systemctl status corpweb-backend` → active.
   - `psql -d corpweb_db -c "\dt"` → все таблицы schema присутствуют (включая ha_tables: nodes, wg_file_state).
   - В выводе install-скрипта warning `Alembic: часть миграций уже применена (или произошла ошибка — см. вывод выше)` появляется **только** если alembic вернул non-zero, и в stderr выше виден конкретный текст ошибки.
6. ⚠️ **Manual idempotency:** Repeated `bash install-native.sh` на уже установленном CP — exit 0, без ошибок (доказывается конструкцией скрипта; manual только в случае подозрений).
