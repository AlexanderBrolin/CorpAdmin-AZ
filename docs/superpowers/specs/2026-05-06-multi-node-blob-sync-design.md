# Multi-node Blob Sync — Agent Push of Node-Side Ground Truth to CP

**Beads epic:** CorpAdmin-AZ-byc
**Beads child bugs:** CorpAdmin-AZ-9d7 (P0), CorpAdmin-AZ-2xm (P1)
**Date:** 2026-05-06
**Author:** brolin (with Claude)

## Problem

Two defects in production observed on multi-node CP `bb.azfi.ru` belong to one architectural class.

### Defect 1 — `download_config` returns broken AntiZapret `.conf` (CorpAdmin-AZ-9d7, P0)

Скачанный из админки клиентский `.conf` для AntiZapret-режима содержит:

```
[Peer]
...
AllowedIPs = 10.29.8.0/24
```

— только сам VPN-сегмент. На рабочем `wgfi2.p4i.ru` тот же запрос возвращает 8 KiB список из ~250 подсетей. Клиенты `bb.azfi.ru` фактически не получают разблокировки **ни одного** ресурса AntiZapret.

Подтверждено через `psql` на обоих CP:

| Запись `wg_file_state` | bb.azfi.ru | wgfi2.p4i.ru |
|---|---|---|
| `antizapret:allowed_ips` | **отсутствует** | 8083 байта, `updated_by='migrate'` (2026-04-16) |
| `/root/antizapret/config/*.txt` | 0 байт (`bootstrap`) | 800–1500 байт (`migrate`/`admin`) |
| `alembic_version` | 0007 | 0007 |

Цепочка:

1. `GET /api/v1/configs/{id}/download` → [configs.py:209](../../../corpweb/backend/app/api/v1/configs.py#L209) вызывает `vpn_manager.get_antizapret_allowed_ips(db)`.
2. [vpn_manager_new.py:574](../../../corpweb/backend/app/services/vpn_manager_new.py#L574) читает blob `antizapret:allowed_ips`. На bb он отсутствует → возвращается `None`.
3. [wg_templates.py:407](../../../corpweb/backend/app/services/wg_templates.py#L407) активирует fallback: `effective_allowed_ips = "10.29.8.0/24"`.

Единственное место, которое **записывает** этот blob, — [migrate.py:228 `_migrate_allowed_ips`](../../../corpweb/backend/app/migrate.py#L228). Оно ищет локальный файл `/root/antizapret/client/amneziawg/antizapret/antizapret-*-am.conf` на CP. На монолитном wgfi2 (CP+нода на одной машине) файл существовал → blob заполнился. На multi-node bb файла на CP нет → silent skip → blob пустой навсегда.

### Defect 2 — `bootstrap_blob_store` overwrites node-side `WIREGUARD_HOST` with empty default (CorpAdmin-AZ-2xm, P1)

При первом старте e5r-кода на `bb.azfi.ru` [bootstrap_blob_store](../../../corpweb/backend/app/services/antizapret.py#L85) записал в blob `/root/antizapret/setup` дефолтный шаблон с `WIREGUARD_HOST=`. SSE-reconcile разнёс blob на ноду (где раньше стоял реальный `WIREGUARD_HOST=bb.azfi.ru`, выставленный вручную при provision'е) и затёр его. Endpoint в клиентских конфигах перестал работать. Восстановили вручную через `AntizapretService.update_settings`.

Корень такой же, как в Defect 1: на multi-node инсталляции **CP не имеет источника правды** для значений, которые реально живут на ноде, и при bootstrap'е затирает их захардкоженным дефолтом.

### Дополнительно — stale `antizapret:allowed_ips` на wgfi2

На wgfi2 blob был заполнен 2026-04-16 (тогда мигрировали). С тех пор upstream обновил dist-файлы, admin-настройки в setup тоже изменились (`GOOGLE_INCLUDE=n`, `AMAZON_INCLUDE=y`, …), `doall.sh` пересобрал template на ноде — но blob на CP **никто не обновляет**. Сейчас на ноде template содержит 46 подсетей, blob на CP — 250 (старая снимка). Клиенты получают лишние routes, которых нода не обслуживает. Не критично, но stale-проблема существует и на работающей системе.

## Goals

1. На свежей multi-node инсталляции после первого agent boot blob `antizapret:allowed_ips` непустой и совпадает с template-файлом на ноде. Скачивание `.conf` через `download_config` сразу выдаёт корректный `AllowedIPs`.
2. После каждого `doall.sh` на ноде (admin поменял `*_INCLUDE` в UI или upstream обновил dist) — blob на CP синхронизируется автоматически без вмешательства администратора.
3. На multi-node CP с уже зарегистрированной нодой `bootstrap_blob_store` не затирает реальный `setup` — вместо этого ждёт agent push.
4. Все изменения **идемпотентны** — повторный запуск bootstrap'а или повторный agent push не приводят к расхождениям.

## Non-goals (explicit scope-out)

- **Admin UI для прямого редактирования `antizapret:allowed_ips`.** Не требуется: admin влияет на список через переключатели в `setup` (`*_INCLUDE`), которые проходят `setup → SSE → нода → doall.sh → template-conf → agent push → blob`. Поток однонаправленный, конфликтов нет.
- **Pull-механизм CP→agent.** Все ноды могут быть за NAT; всё работает через agent-initiated connections. Push достаточно.
- **Расширение whitelist на дополнительные blob-типы** (DNS-настройки, прочие производные). Расширяется при появлении конкретного use-case.
- **Бэкфил pre-existing stale blob'а на wgfi2** отдельной операцией. После deploy агент при первом startup_reconcile запишет актуальное значение поверх — это и есть бэкфил.
- **Изменение поведения `EDITABLE_FILES` (config/*.txt)**: они остаются admin-управляемыми, bootstrap по-прежнему пишет пустые при отсутствии. Они не источник AllowedIPs — это input для doall на ноде.

## Design

### Поток данных (после фикса)

```
admin UI / Files Editor (CP)
        │
        ▼
   setup blob ─SSE──► /root/antizapret/setup на ноде
                                │
                                ▼ (hook=doall, дебаунс 5 сек)
                          /root/antizapret/doall.sh
                                │
                                ▼
                  /root/antizapret/client/amneziawg/antizapret/
                       antizapret-client-(host)-am.conf  (template)
                                │
                                ▼ (parse AllowedIPs из [Peer])
                  POST /api/v1/agent/seed-blob
                  { path: "antizapret:allowed_ips", content: "..." }
                                │
                                ▼
                          wg_file_state
                                │
                                ▼
                  download_config render → клиент
```

Правило: **нода — единственный источник правды для производных blob'ов**. Admin не редактирует AllowedIPs напрямую, он меняет input (setup); результат возвращается с ноды.

### Part 1 — Backend: новый endpoint `POST /api/v1/agent/seed-blob`

#### Modified: `corpweb/backend/app/api/v1/agent.py`

Новый endpoint рядом с существующими `/agent/heartbeat`, `/agent/applied`, `/agent/events`:

```python
_SEED_BLOB_WHITELIST: set[str] = {
    "antizapret:allowed_ips",
    "/root/antizapret/setup",
}


class SeedBlobRequest(BaseModel):
    path: str
    content: bytes  # bytes-as-base64 в JSON, см. ниже


@router.post("/seed-blob", status_code=204)
def seed_blob(
    req: SeedBlobRequest,
    node: Node = Depends(_require_node),
    db: Session = Depends(get_db),
) -> None:
    if req.path not in _SEED_BLOB_WHITELIST:
        raise HTTPException(400, f"path {req.path!r} not in seed-blob whitelist")
    WgBlobStore(db).put(req.path, req.content, by="agent-sync")
    db.commit()
    logger.info("agent-sync: node=%s path=%s bytes=%d", node.id, req.path, len(req.content))
```

- **Auth.** Та же `_require_node` (Bearer-token), что у `heartbeat` / `applied` / `drain`. Без auth — 401.
- **Whitelist.** Хардкод-set из двух путей — генеричный «прими что угодно» небезопасен. Расширение whitelist'а — отдельная PR-итерация.
- **Семантика записи.** Безусловный overwrite. Не сравниваем с предыдущим значением, не проверяем `updated_by`. По дизайн-решению (см. data-flow): admin не редактирует эти blob'ы напрямую, конфликтовать нечему.
- **Сериализация bytes.** FastAPI/Pydantic для bytes в JSON ожидает base64-кодированную строку. Это совпадает с текущим стилем; `applied_sha` payload использует тот же формат.

#### Whitelist решение «почему именно эти два пути»

| Path | Источник на ноде | Зачем синкать в CP |
|---|---|---|
| `antizapret:allowed_ips` | parsed из template-conf (после `client.sh` / `doall.sh`) | `download_config` рендерит `[Peer] AllowedIPs` из этого blob'а |
| `/root/antizapret/setup` | реальный файл, может содержать manual-set значения от пред-CP-эпохи | `bootstrap_blob_store` deferral — мы откладываем default, ждём agent push |

Прочие `EDITABLE_FILES` (`config/*.txt`) **не входят** в whitelist: они admin-владеемые (через Files Editor), на ноде формируются как side-effect SSE-reconcile, push с ноды только закольцует поток без выгоды.

### Part 2 — Agent: парсеры + push-триггеры

#### Modified: `agent/corpweb_sync_agent.py`

##### Парсеры (на уровне модуля, чистые функции)

```python
_TEMPLATE_CONF_GLOB = "/root/antizapret/client/amneziawg/antizapret/antizapret-*-am.conf"
_SETUP_PATH = "/root/antizapret/setup"
_ALLOWED_IPS_RE = re.compile(r"^\s*AllowedIPs\s*=\s*(.+?)\s*$", re.MULTILINE)


def _parse_allowed_ips_from_template() -> bytes | None:
    matches = sorted(glob.glob(_TEMPLATE_CONF_GLOB))
    if not matches:
        return None
    text = pathlib.Path(matches[0]).read_text()
    m = _ALLOWED_IPS_RE.search(text)
    return m.group(1).encode() if m else None


def _read_setup() -> bytes | None:
    p = pathlib.Path(_SETUP_PATH)
    if not p.exists():
        return None
    return p.read_bytes()
```

- Glob выдаёт несколько файлов (на wgfi2 их сотни — по одному на клиента) — берём `sorted()[0]`. Все клиентские .conf одной ноды содержат идентичный `AllowedIPs` (этот список — общий для интерфейса, см. wgfi2-prod). Стабильность выбора `sorted()[0]` нужна для тестируемости.
- Регекс `re.MULTILINE` чтобы матчить именно строку `AllowedIPs = …` (а не `AllowedIPs` внутри другого ключа).
- Возвращаем `bytes` чтобы согласовать с `WgBlobStore.put(path, content: bytes)`.
- Возврат `None` означает «нечего пушить» — caller просто пропускает push, без ошибок и retry.

##### Push helper

Используем существующий module-level `api_post(path, payload)` ([corpweb_sync_agent.py:588](../../../agent/corpweb_sync_agent.py#L588)), который добавляет Bearer-token и `raise_for_status()`:

```python
def _push_seed_blob(path: str, content: bytes) -> None:
    try:
        api_post(
            "/api/v1/agent/seed-blob",
            {"path": path, "content": base64.b64encode(content).decode()},
        )
        logger.info("seed-blob pushed: path=%s bytes=%d", path, len(content))
    except (requests.HTTPError, requests.ConnectionError, requests.Timeout):
        logger.exception("seed-blob push failed: path=%s", path)
```

Ошибки логируем, но не прерываем reconcile/doall. Следующий триггер (heartbeat-cycle / следующий doall) попробует снова. Узкие исключения — match существующему стилю обработки в [corpweb_sync_agent.py:918](../../../agent/corpweb_sync_agent.py#L918).

##### Триггер 1 — в `startup_reconcile`

Сразу после fetch'а managed_files (в конце функции, см. [corpweb_sync_agent.py:759](../../../agent/corpweb_sync_agent.py#L759)). `startup_reconcile` — module-level функция, не метод:

```python
def startup_reconcile() -> None:
    # existing logic: fetch managed_files, write to disk, etc.
    ...

    # NEW: push node-side ground truth
    for path, parser in (
        ("antizapret:allowed_ips", _parse_allowed_ips_from_template),
        (_SETUP_PATH, _read_setup),
    ):
        content = parser()
        if content is not None:
            _push_seed_blob(path, content)
```

##### Триггер 2 — после успешного `_run_doall()`

В конце `_run_doall()`, в success-path. Текущая реализация ([corpweb_sync_agent.py:364](../../../agent/corpweb_sync_agent.py#L364)) использует `subprocess.run(check=True)` — нет returncode-переменной. Меняем на двухшаговую структуру:

```python
def _run_doall() -> None:
    log.info("Running /root/antizapret/doall.sh")
    try:
        subprocess.run(
            ["/root/antizapret/doall.sh"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        log.error("doall.sh failed (rc=%d): %s", exc.returncode, exc.stderr.strip())
        return
    except FileNotFoundError:
        log.error("/root/antizapret/doall.sh not found")
        return

    # NEW: doall succeeded — template-conf may have changed, push fresh blob
    content = _parse_allowed_ips_from_template()
    if content is not None:
        _push_seed_blob("antizapret:allowed_ips", content)
```

`setup` после doall не пушим — doall его не меняет.

### Part 3 — Backend: defer bootstrap setup-default при наличии активной ноды

#### Modified: `corpweb/backend/app/services/antizapret.py`

Текущая логика `bootstrap_blob_store` ([antizapret.py:85-97](../../../corpweb/backend/app/services/antizapret.py#L85)):

```python
def bootstrap_blob_store(self) -> None:
    if self._store.get(ANTIZAPRET_SETUP_FILE) is None:
        self._store.put(ANTIZAPRET_SETUP_FILE, _load_default_setup(), by="bootstrap")
    for path in EDITABLE_FILES.values():
        if self._store.get(path) is None:
            self._store.put(path, b"", by="bootstrap")
```

Проблема: на свежем CP с уже-зарегистрированной нодой (admin сначала зарегистрировал ноду, потом первый раз поднял backend e5r) — blob `setup` is `None`, bootstrap пишет default, SSE мгновенно отдаёт пустой setup на ноду, нода затирает реальный.

Изменение:

```python
def bootstrap_blob_store(self) -> None:
    if self._store.get(ANTIZAPRET_SETUP_FILE) is None:
        if self._has_registered_active_node():
            logger.info(
                "bootstrap_blob_store: skipping setup default — "
                "registered active node will push it"
            )
        else:
            self._store.put(ANTIZAPRET_SETUP_FILE, _load_default_setup(), by="bootstrap")
            logger.info("Seeded default %s into blob store", ANTIZAPRET_SETUP_FILE)
    for path in EDITABLE_FILES.values():
        if self._store.get(path) is None:
            self._store.put(path, b"", by="bootstrap")


def _has_registered_active_node(self) -> bool:
    from app.db.models import Node
    return self._db.query(Node).filter(
        Node.health.in_(("ok", "degraded"))
    ).first() is not None
```

- **Проверка на `EDITABLE_FILES` НЕ откладывается.** Их заполняет admin через UI (Files Editor), а не agent. Дефолт «пусто» — это валидное стартовое состояние.
- **Health-фильтр.** `unknown` не считается «активной» — это либо никогда-не-видавшая нода, либо потерянная. На свежем CP без нод запрос возвращает empty → bootstrap пишет default, как было.
- **Идемпотентность.** Если admin уже задал setup (через UI), `self._store.get(...) is not None` → bootstrap не делает ничего. Если потом нода появится — agent push'ит свой setup, CP принимает → итог совпадает.

### Race condition анализ

| Сценарий | Поведение |
|---|---|
| Свежий CP, нет нод | bootstrap пишет default. Когда нода добавится — agent push'ит реальный. |
| Свежий CP, нода уже зарегистрирована (bb.azfi.ru) | bootstrap **не** пишет setup; в течение секунд после старта backend agent push'ит реальный. Setup blob: `None → реальный`. |
| Существующий CP, restart backend | Setup blob уже не None → bootstrap noop. Agent при restart всё равно push'ит — CP overwrite'ит тем же значением (idempotent). |
| Admin меняет `*_INCLUDE` в UI | `update_settings()` пишет setup в blob → SSE → нода → doall (5 сек) → template обновляется → agent push'ит обновлённый AllowedIPs. Total RTT ~5–10 сек. |
| Upstream обновил dist (cron) | doall на ноде → новый template → agent push (триггер 2) → blob freshness восстановлена. |

## Test strategy (TDD)

Все тесты — `pytest`, RED → GREEN → REFACTOR, по одному коммиту на цикл.

### Backend: `corpweb/backend/tests/test_seed_blob_endpoint.py` (новый)

1. `test_seed_allowed_ips_writes_blob` — POST с валидным node-token, path=`antizapret:allowed_ips`, content=`b"10.29.8.0/24, 1.2.3.0/24"` → 204; `WgBlobStore.get(...)` возвращает то же; `updated_by='agent-sync'`.
2. `test_seed_setup_writes_blob` — то же для path=`/root/antizapret/setup`.
3. `test_unknown_path_rejected_400` — path=`/etc/passwd` → 400, blob не создан.
4. `test_no_auth_rejected_401` — без Authorization-заголовка → 401.
5. `test_overwrites_existing_blob_unconditionally` — pre-populate blob с `updated_by='admin'`; POST → blob перезаписан, `updated_by='agent-sync'` (валидирует «agent always wins» решение).

### Backend: `corpweb/backend/tests/test_antizapret_bootstrap.py` (расширить)

6. `test_bootstrap_skips_setup_when_active_node_registered` (новый) — pre-create `Node(health='ok')`; вызов `bootstrap_blob_store()`; setup blob = None; config/*.txt blob = b"" (созданы).
7. `test_bootstrap_skips_setup_when_degraded_node_registered` (новый) — то же для `health='degraded'`.
8. `test_bootstrap_writes_setup_when_only_unknown_node` (новый) — `Node(health='unknown')` не считается активной → bootstrap пишет default.
9. `test_bootstrap_writes_setup_when_no_nodes` (existing, проверяет регрессию) — без нод, default пишется.

### Agent: `agent/tests/test_seed_blob_push.py` (новый)

10. `test_parse_allowed_ips_from_template_returns_value` — tmp dir с одним template-conf, `_parse_allowed_ips_from_template()` возвращает байты после `=`.
11. `test_parse_allowed_ips_returns_none_when_no_match` — пустая директория → None.
12. `test_parse_allowed_ips_picks_lexicographically_first` — два template-conf'а с разным AllowedIPs; всегда выбирается первый по `sorted()`.
13. `test_read_setup_returns_bytes` / `test_read_setup_returns_none_when_missing`.
14. `test_startup_reconcile_pushes_both_blobs` — мок _http_post_json; вызов startup_reconcile с валидными парсерами → два POST'а на `/api/v1/agent/seed-blob` (allowed_ips + setup).
15. `test_startup_reconcile_skips_push_when_parser_returns_none` — оба парсера возвращают None → ноль POST'ов.
16. `test_run_doall_success_pushes_allowed_ips` — мок subprocess returncode=0; после `_run_doall()` — один POST с allowed_ips.
17. `test_run_doall_failure_does_not_push` — returncode=1 → ноль POST'ов.

## Migration impact

- **bb.azfi.ru (главный target):** на первом restart агента (через ~30 сек или systemctl restart) blob `antizapret:allowed_ips` заполняется автоматом. Скачивание `.conf` для существующего vpn-az клиента (10.29.8.6/32) сразу возвращает корректный `AllowedIPs` (никаких ручных операций над БД).
- **wgfi2.p4i.ru:** при первом restart агента (после deploy) текущий 8083-байтный stale blob будет перезаписан значением из текущего template на ноде (которое отражает реальный set `*_INCLUDE` в setup + актуальный upstream-baseline). Клиенты, скачавшие свежие `.conf`, увидят набор подсетей, который совпадает с тем, что нода реально обслуживает. Влияние на трафик нейтральное: «лишние» route'ы из stale blob'а и так не работали (нода их не routed). На уже скачанных клиентских .conf изменений нет — пока юзер не перескачает, у него остаётся старое.
- **Свежие установки CP без нод:** ведут себя как сейчас — bootstrap пишет default, admin заполняет через UI, нода добавится позже.

## Acceptance criteria

1. ✅ После merge + deploy на bb.azfi.ru + restart `corpweb-sync-agent` на node-bb01:
   - `psql -c "SELECT octet_length(content), updated_by FROM wg_file_state WHERE path='antizapret:allowed_ips'"` возвращает `>500, agent-sync`.
   - Скачивание `.conf` через админ-панель для vpn-az клиента содержит `AllowedIPs = 10.29.8.0/24, 10.30.0.0/15, ...` (≥40 подсетей).
2. ✅ После того как `doall.sh` отработал на ноде (триггеры: admin-edit `config/*.txt` через Files Editor → SSE → hook=doall на ноде; cron `antizapret-update.timer`; ручной запуск админом) — в течение 30 сек blob `antizapret:allowed_ips` обновлён до значения template на ноде. Замечание: переключатели `*_INCLUDE=y/n` в setup-форме UI не обновляют существующие клиентские template-conf (это upstream-ограничение AntiZapret-VPN: `${IPS}` фиксируется в каждом `antizapret-*-am.conf` при `client.sh add`); чтобы новый набор подсетей попал в blob, нужно либо запустить `doall.sh` вручную, либо пересоздать клиента.
3. ✅ На свежем CP с одной зарегистрированной нодой первый `lifespan()` не вызывает SSE-broadcast пустого setup'а — нода НЕ перезаписывает свой `/root/antizapret/setup` пустым.
4. ✅ `pytest corpweb/backend/tests/ agent/tests/` — все тесты зелёные, в т.ч. 8 новых из стратегии выше.
