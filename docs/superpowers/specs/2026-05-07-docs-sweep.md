# Documentation sweep — align all user-facing docs with code

**Beads epic:** CorpAdmin-AZ-rce
**Beads child bugs:** to be created (5 sub-tasks — see Architecture)
**Date:** 2026-05-07
**Author:** brolin (with Claude)

## Problem

Документация драфтилась от кода за последние 6 недель. Аудит выявил три класса проблем:

### Class 1 — Factually wrong claims (severe)

`corpweb/ADMIN_SETTINGS.md` (329 строк, 2026-02-17):

- **Lines 24-53** заявляют о PostgreSQL триггере `check_user_config_limit()` на таблице `configs`. **Триггера нет в коде**. Проверка лимита реализована в Python в [corpweb/backend/app/api/v1/configs.py:85-94](../../../corpweb/backend/app/api/v1/configs.py#L85-L94) (count active configs before INSERT). Это серьёзная ошибка документа: читатель будет искать триггер в БД, не найдёт, не поймёт где enforcement.
- Endpoints `GET/PATCH /api/v1/admin/settings` помечены как "**планируется**". Они **реализованы** в [admin.py:304-357](../../../corpweb/backend/app/api/v1/admin.py#L304-L357).
- В описании схемы `system_settings` отсутствуют 4 реальные колонки: `google_play_url`, `app_store_url`, `apk_url`, `windows_url` (определены в [models.py:98-101](../../../corpweb/backend/app/db/models.py#L98-L101), редактируются через UI `AdminSettingsPage.tsx`).

### Class 2 — Stale claims (по эпикам последних 6 недель)

`README.md` (корень, 317 строк, 2026-04-22):

- **Lines 169-171** — "клиентам WireGuard/AmneziaWG нужно вручную добавить новые IP в AllowedIPs". Устарело по **nye** (PR #12): agent читает свежий `/etc/wireguard/ips`, blob `antizapret:allowed_ips` обновляется автоматически на каждый `doall.sh`, скачиваемый `.conf` всегда актуален.

`corpweb/README.md` (460 строк, 2026-04-22):

- **Lines 52-55** — описание install-native.sh не упоминает что теперь iptables/sysctl настраиваются автоматически. Устарело по **6jl** (PR #13).
- **Lines 66-68** — секция "ручная установка" не упоминает `iptables iptables-persistent netfilter-persistent`. Устарело по **6jl**.
- **Lines 277-289** — таблица "Управляемые файлы" показывает `/root/antizapret/setup` с hook = "—". Устарело по **2yj** (PR #11): hook теперь `doall_and_restart_antizapret`, который запускает doall + restart antizapret.service.

`agent/install.sh` (162 строки, 2026-04-22):

- Не указано **где админ берёт enroll token** для нового CORPWEB_TOKEN. Это блокер для нового пользователя при первой установке агента. Нужно: ссылка на UI/API endpoint для генерации токена.

### Class 3 — Deep drift (HA-SETUP / ADD-NODE)

`docs/HA-SETUP.md` (95 строк, 2026-04-16) и `docs/ADD-NODE.md` (102 строки, 2026-04-16) — не отражают:

- **byc**: agent push of antizapret:allowed_ips и /root/antizapret/setup blobs; bootstrap defer на CP при наличии зарегистрированной active ноды.
- **2yj**: setup change → auto doall + restart на ноде.
- **nye**: blob pulls fresh `/etc/wireguard/ips`, не stale per-client conf.
- **te3 / escape**: 4 интерфейса (antizapret, vpn, az_escape, vpn_escape), порты 500 (vpn_escape) / 53443 (az_escape), AmneziaWG dependency на ноде, custom-up.sh / custom-down.sh hooks для escape rules.
- **6jl**: install-native.sh теперь сам ставит iptables/ip_forward; manual инструкции "после установки выполните apt install iptables" устарели.
- **Архитектурно**: nginx upstream блоки в HA-SETUP противоречат текущему [balancer.py](../../../corpweb/backend/app/services/balancer.py) который использует iptables DNAT. Нужно явно расписать что чем балансируется (HTTP vs UDP WG/AWG vs escape ports).
- **Heartbeat metrics**: не упомянуты — admin не понимает что значит health=ok/degraded и где видеть detail.

### Class 4 — Code drift (uninstall.sh)

`corpweb/uninstall.sh` (83 строки, 2026-02-17):

- Не удаляет `/etc/sysctl.d/99-corpweb-forwarding.conf` (создаётся install-native.sh после **6jl** PR #13). Persistent state остаётся на хосте после uninstall.
- Не зачищает iptables правила DNAT, которые писал [balancer.py:ensure_ports_reconciled](../../../corpweb/backend/app/services/balancer.py). После uninstall эти правила висят пока не reboot.

### Class 5 — UI cleanup в LoginPage

`corpweb/frontend/src/pages/LoginPage.tsx`:

- **Line 145-147**: подсказка "Первый вход: admin / admin" должна быть удалена. На production-серверах эта подсказка раскрывает default-credentials до того как admin сменил пароль; даже после смены — она вводит в заблуждение пользователя в авторизованном окружении.
- **Line 152**: "© 2026 CorpWeb. Powered by AntiZapret VPN" → "© 2026 CorpWeb." Удалить упоминание upstream-проекта в footer (брендинг CorpWeb должен стоять отдельно).

## Goals

1. **Factual correctness**: каждый claim в каждом затронутом файле верифицируется против текущего кода (file:line citations) или удаляется.
2. **Coverage**: все user-visible behaviour changes из последних эпиков (byc/2yj/nye/6jl/te3) отражены минимум в одном document.
3. **No phantom features**: документация не описывает то, чего нет в коде (никаких "будет реализовано", "планируется" — либо реализовано и описано как факт, либо удалить).
4. **HA-SETUP и ADD-NODE — полный rewrite** (по решению пользователя). Цель — runbook'и, которые admin может выполнить без guesswork.
5. **uninstall.sh** теперь removes всё что install-native.sh писал (persistent state cleanup).

## Non-goals (explicit scope-out)

- **TP-Link.md, Keenetic.md** — конфигурация роутеров, upstream-стиль; не трогаем.
- **corpweb/BRANDING.md** — стабильная feature, контент актуален.
- **SESSION_HANDOFF.md** — session-specific, обновляется руками per-session.
- **corpweb/install-docker.sh** — Docker deploy не используется (bb/wgfi2 — native). Отдельный issue если понадобится.
- **corpweb/install.sh** — простой wrapper, не требует правок.
- **Regression тесты для docs** (например grep-based assertions) — по решению пользователя не делаем; manual review каждого файла достаточен.
- **Полный rewrite `corpweb/README.md`** — точечные правки (3 места). Полный rewrite — отдельный issue если понадобится.
- **Полный rewrite `corpweb/ADMIN_SETTINGS.md`** — точечные правки (3 секции). Архитектура файла корректна.

## Architecture

Один PR `feature/rce-docs-sweep → CorpAdmin`. Логически 5 групп изменений → 5 sub-tasks для subagent-driven execution:

### Sub-task A: точечные правки в README'шках (rce-A, P2)

Files: `README.md` (корень), `corpweb/README.md`, `agent/install.sh`.

Правки:
- `README.md` lines 169-171: переписать на "клиенты WG/AWG получают AllowedIPs автоматически из blob `antizapret:allowed_ips`, который агент обновляет после каждого `doall.sh`".
- `corpweb/README.md` lines 52-55: расширить описание install-native.sh — "также установит iptables/iptables-persistent/netfilter-persistent и включит net.ipv4.ip_forward через /etc/sysctl.d drop-in".
- `corpweb/README.md` lines 66-68: добавить `iptables iptables-persistent netfilter-persistent` в список manual deps + sysctl-команда.
- `corpweb/README.md` lines 277-289: обновить таблицу managed files — добавить hook `doall_and_restart_antizapret` для `/root/antizapret/setup` с пояснением.
- `agent/install.sh`: добавить блок-комментарий или print_info с инструкцией где получить enroll token (UI: Ноды → Добавить ноду → копировать token; либо API endpoint).

### Sub-task B: fact-correction в `corpweb/ADMIN_SETTINGS.md` (rce-B, P2)

- Lines 24-53: удалить секцию о PG-триггере `check_user_config_limit()`. Заменить секцией "Application-level enforcement" с описанием реальной проверки в `configs.py:85-94` (Python check `count_active_by_user(user_id) >= max_configs_per_user` перед INSERT).
- Удалить пометки "планируется" с endpoints `GET/PATCH /api/v1/admin/settings` — описать как реализованные с примерами curl/JSON.
- Расширить раздел DB schema: добавить 4 колонки (`google_play_url`, `app_store_url`, `apk_url`, `windows_url`) с типами и описанием назначения. Обновить ALTER TABLE-пример или указать что эти колонки добавляются через `init_db.py:70-77` `ADD COLUMN IF NOT EXISTS`.

### Sub-task C: full rewrite `docs/HA-SETUP.md` + `docs/ADD-NODE.md` (rce-C, P2)

Полный rewrite **обоих** файлов как единый набор runbook'ов:

- `docs/HA-SETUP.md`: архитектура multi-node CorpAdmin-AZ. Секции:
  - Архитектура (text + ASCII-схема): CP с PostgreSQL + nginx + balancer.py + sync-agent push API; ноды с antizapret + sync-agent + 4 ifaces (antizapret, vpn, az_escape, vpn_escape); порты UDP 51443/51080 (base), 500/53443 (escape), 52443/52080 (внешние backup для DNAT).
  - Communication: SSE stream для realtime config sync, agent heartbeat, blob auto-sync (byc/2yj/nye).
  - Какой балансировщик что делает: balancer.py пишет iptables DNAT для UDP-портов нод; nginx HTTP для admin panel + API. **Никаких manual nginx upstream блоков** — это устарело.
  - Health metrics: что значит health=ok / degraded; escape drift detection.

- `docs/ADD-NODE.md`: пошаговый runbook добавления **новой** ноды в существующий CP. Шаги:
  1. Prerequisites на ноде: clean Debian 12/13, root, доступ к интернету.
  2. Установить AntiZapret upstream (`bash <(curl ... setup.sh)`) + AmneziaWG prerequisite (для escape mode): inline-инструкция по PPA noble + DKMS на Debian 12/13, с командами для копипаста.
  3. На CP: создать node entry в UI Ноды → Добавить → получить enroll token.
  4. На ноде: `bash agent/install.sh` с CORPWEB_TOKEN + CONTROL_PLANE_URL.
  5. Что произойдёт автоматически после регистрации (agent reconcile + blob push + ifaces up + balancer DNAT update).
  6. Verify: какие команды/endpoints проверить (health=ok, peers list, скачать клиентский .conf).
  7. Troubleshooting: что делать если health=degraded, что делать если AllowedIPs выглядит stale (теперь редко — но есть upstream caveat 58u про download-cache).

Cross-references между HA-SETUP и ADD-NODE: HA-SETUP — архитектура, ADD-NODE — runbook. Не дублируем содержание; ADD-NODE ссылается на HA-SETUP для архитектурных вопросов.

### Sub-task D: `corpweb/uninstall.sh` cleanup (rce-D, P2)

- **Удалить `/etc/sysctl.d/99-corpweb-forwarding.conf`** если он существует. Это safe — файл наш по имени и содержимому, других пользователей быть не может.
- **Не трогать iptables автоматически.** balancer.py не помечает свои правила маркером, поэтому selective cleanup потребует правки balancer.py (вне scope этого PR — отдельный issue если понадобится). Вместо этого добавить в uninstall.sh **WARNING-блок** в финальном выводе: "iptables DNAT правила не удалены автоматически. Если они больше не нужны — `iptables -t nat -F PREROUTING` (предупреждение: удалит ВСЕ DNAT правила, не только CorpAdmin)".
- **Не удалять packages** (`iptables-persistent`, `netfilter-persistent`) — могут быть нужны другим приложениям.

### Sub-task E: LoginPage cleanup (rce-E, P2)

Файл: [corpweb/frontend/src/pages/LoginPage.tsx](../../../corpweb/frontend/src/pages/LoginPage.tsx).

- **Lines 145-147**: удалить блок `<div className="mt-6 text-center text-xs text-gray-500">Первый вход: admin / admin</div>`.
- **Line 152**: изменить `© 2026 CorpWeb. Powered by AntiZapret VPN` на `© 2026 CorpWeb.`.

Других страниц с этими строками нет (verified `grep`-ом по `corpweb/frontend/src/`).

## Testing strategy

**No automated tests** (по решению пользователя — docs не имеют грубых regression-tests которые приносили бы ценность).

**Manual verification gate** перед merge:

1. Каждый файл прочитан end-to-end после правок — нет битых ссылок (file:line, anchor, MD link).
2. Cross-references between HA-SETUP и ADD-NODE working (relative links).
3. Все `file:line` citations в текстах указывают на реальные строки текущего кода.
4. `bash -n corpweb/uninstall.sh` → exit 0.
5. Backend регрессия: `pytest corpweb/backend/` — 350 passed (no regressions, мы не трогаем backend код).
6. Agent регрессия: `pytest agent/` — 112 passed (no regressions).
7. Install-tests регрессия: `pytest tests/install/` — 4 passed (no regressions, мы не трогаем install-native.sh).

## Acceptance criteria

1. ✅ ADMIN_SETTINGS.md больше не упоминает несуществующий PG-триггер; описывает реальную application-level проверку с file:line на configs.py.
2. ✅ ADMIN_SETTINGS.md описывает `/api/v1/admin/settings` endpoints как реализованные (с примерами curl), не "планируется".
3. ✅ ADMIN_SETTINGS.md schema-секция содержит все 6 колонок `system_settings` (id, max_configs_per_user, updated_at, updated_by + 4 URL колонки).
4. ✅ README.md (root) lines 169-171 переписаны на текущее auto-AllowedIPs поведение.
5. ✅ corpweb/README.md upgrade-описание install-native.sh упоминает iptables + sysctl auto-config.
6. ✅ corpweb/README.md manual install список содержит iptables + iptables-persistent + netfilter-persistent + sysctl команду.
7. ✅ corpweb/README.md managed-files таблица содержит hook `doall_and_restart_antizapret` для setup.
8. ✅ agent/install.sh содержит ясную инструкцию где получить enroll token (комментарий в скрипте + print_info при run).
9. ✅ docs/HA-SETUP.md полностью переписан, отражает byc/2yj/nye/te3/6jl, упоминает escape ports / 4 ifaces / SSE / heartbeat / blob sync, не содержит manual nginx upstream блоков.
10. ✅ docs/ADD-NODE.md полностью переписан как пошаговый runbook, использует agent/install.sh, упоминает amneziawg prerequisite, blob auto-fill после регистрации.
11. ✅ corpweb/uninstall.sh удаляет `/etc/sysctl.d/99-corpweb-forwarding.conf` и документирует/реализует iptables cleanup.
12. ✅ LoginPage.tsx больше не содержит "Первый вход: admin / admin" и не содержит "Powered by AntiZapret VPN" в footer.
13. ✅ Manual verification gate (testing strategy выше) пройдена.
14. ✅ `bash -n corpweb/uninstall.sh` → exit 0.
15. ✅ pytest baseline (backend 350 + agent 112 + install 4) green.
16. ✅ Frontend builds without TypeScript errors после правки LoginPage.tsx.
