# Documentation sweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Each Sub-task A-D is dispatched to a fresh implementer subagent.

**Goal:** Bring all user-facing docs (`README.md`, `corpweb/README.md`, `corpweb/ADMIN_SETTINGS.md`, `docs/HA-SETUP.md`, `docs/ADD-NODE.md`, `agent/install.sh`) and `corpweb/uninstall.sh` in line with current code (epics byc/2yj/nye/6jl/te3).

**Architecture:** Single PR `feature/rce-docs-sweep → CorpAdmin`. Four logical groups → four implementer subagents. No automated regression tests for docs (per user decision); manual review gate before push.

**Tech Stack:** Markdown, bash. No backend/frontend code touched.

**Beads:** epic CorpAdmin-AZ-rce; sub-tasks rce-A/B/C/D to be created in Task 1.

**Spec:** [docs/superpowers/specs/2026-05-07-docs-sweep.md](../specs/2026-05-07-docs-sweep.md)

---

## File Structure

- **Modify:**
  - `README.md` (root) — точечно, lines 169-171
  - `corpweb/README.md` — точечно, lines 52-55, 66-68, 277-289
  - `corpweb/ADMIN_SETTINGS.md` — fact-fix секции trigger / endpoints / schema
  - `docs/HA-SETUP.md` — full rewrite
  - `docs/ADD-NODE.md` — full rewrite
  - `agent/install.sh` — добавить enroll-token инструкцию
  - `corpweb/uninstall.sh` — sysctl drop-in cleanup + iptables WARNING block
  - `corpweb/frontend/src/pages/LoginPage.tsx` — удалить admin/admin hint + Powered-by suffix

No new files. No `.gitignore` changes (we know `__pycache__` exists in repo as legacy artefact — out of scope).

---

## Task 1: Bootstrap — beads sub-tasks + worktree + spec/plan into worktree

**Files:** none modified initially.

- [ ] **Step 1: Create 5 beads sub-tasks** (rce-A through rce-E) under epic rce.

```bash
bd create --title="rce-A: точечные правки README.md (root) + corpweb/README.md + agent/install.sh" --type=task --priority=2
bd create --title="rce-B: fact-correction в corpweb/ADMIN_SETTINGS.md" --type=task --priority=2
bd create --title="rce-C: full rewrite docs/HA-SETUP.md + docs/ADD-NODE.md" --type=task --priority=2
bd create --title="rce-D: corpweb/uninstall.sh — sysctl drop-in cleanup + iptables WARNING" --type=task --priority=2
bd create --title="rce-E: LoginPage cleanup — удалить admin/admin hint + Powered-by suffix" --type=task --priority=2
```

For each created sub-task ID (e.g. CorpAdmin-AZ-XXX), link to epic:

```bash
bd dep add <rce-A-id> CorpAdmin-AZ-rce --type=parent-child
bd dep add <rce-B-id> CorpAdmin-AZ-rce --type=parent-child
bd dep add <rce-C-id> CorpAdmin-AZ-rce --type=parent-child
bd dep add <rce-D-id> CorpAdmin-AZ-rce --type=parent-child
bd dep add <rce-E-id> CorpAdmin-AZ-rce --type=parent-child
```

- [ ] **Step 2: Create worktree**

Use the controller's worktree tool:
```
EnterWorktree(name="feature/rce-docs-sweep")
```

- [ ] **Step 3: Copy spec/plan into worktree** (they were authored in main repo before worktree)

```bash
cp /home/brolin/Documents/ITSS/AdminAZWG/CorpAdmin-AZ/docs/superpowers/specs/2026-05-07-docs-sweep.md docs/superpowers/specs/
cp /home/brolin/Documents/ITSS/AdminAZWG/CorpAdmin-AZ/docs/superpowers/plans/2026-05-07-docs-sweep.md docs/superpowers/plans/
```

- [ ] **Step 4: Commit spec + plan into worktree**

```bash
git add docs/superpowers/specs/2026-05-07-docs-sweep.md docs/superpowers/plans/2026-05-07-docs-sweep.md
git commit -m "docs(rce): spec + plan for documentation sweep epic"
```

---

## Task 2: Sub-task A — точечные правки в README'шках

**Subagent dispatched with:** spec excerpt for sub-task A, current text of three files, target diffs.

**Files:**
- Modify: `README.md` (root), lines 169-171
- Modify: `corpweb/README.md`, lines 52-55, 66-68, 277-289
- Modify: `agent/install.sh`, add comment block + print_info about enroll token

### Step 1: Patch `README.md` lines 169-171

Replace current text (claim about manual AllowedIPs editing) with:

> После обновления списка АнтиЗапрета (через панель → Настройки AZ или через ручной запуск `doall.sh` на ноде) клиенты OpenVPN получают новые маршруты при следующем подключении. Для WireGuard / AmneziaWG список `AllowedIPs` в скачиваемом `.conf` обновляется автоматически: агент на ноде после `doall.sh` пушит свежий `/etc/wireguard/ips` в blob `antizapret:allowed_ips` на CP, и следующий download через панель отдаст актуальный список без ручной правки клиентского конфига.

### Step 2: Patch `corpweb/README.md` lines 52-55

Find the block describing automatic install. Add to the bullet list (or extend existing bullet):

> - устанавливает iptables + iptables-persistent + netfilter-persistent (для DNAT-балансировщика)
> - включает `net.ipv4.ip_forward=1` через `/etc/sysctl.d/99-corpweb-forwarding.conf`

### Step 3: Patch `corpweb/README.md` lines 66-68

In the manual-install apt list, add `iptables iptables-persistent netfilter-persistent`. Right after the apt block, add a separate snippet:

```bash
# Включить IP forwarding (для DNAT-балансировщика)
echo 'net.ipv4.ip_forward=1' > /etc/sysctl.d/99-corpweb-forwarding.conf
sysctl --system
```

### Step 4: Patch `corpweb/README.md` lines 277-289 (managed-files table)

Update the row for `/root/antizapret/setup`. Current cell shows hook = "—". Change to:
- Hook column: `doall_and_restart_antizapret`
- Description column add note: "при изменении blob агент запускает `doall.sh` (rebuild template-conf и push свежего `antizapret:allowed_ips`) + `systemctl restart antizapret.service` (re-apply iptables/sysctl deps from new setup)".

### Step 5: Patch `agent/install.sh`

Add a comment block at the top of the script (after shebang / before first command) and a `print_info` line just before the script asks for `CORPWEB_TOKEN`:

```bash
# Получить enroll token: на CP, в админ-панели:
#   Ноды → Добавить ноду → скопировать enroll-token из созданной строки.
# Либо через API: POST /api/v1/admin/nodes (требует admin-сессию).
```

`print_info` line:
```bash
print_info "Enroll token: получите в админ-панели CP (Ноды → Добавить ноду)."
```

### Step 6: Verify and commit

```bash
git diff --stat README.md corpweb/README.md agent/install.sh
git add README.md corpweb/README.md agent/install.sh
git commit -m "docs(rce-A): точечные правки README + agent/install.sh — auto AllowedIPs, install-native deps, managed-files hook, enroll token (CorpAdmin-AZ-rce)"
```

---

## Task 3: Sub-task B — fact-correction в `corpweb/ADMIN_SETTINGS.md`

**Files:**
- Modify: `corpweb/ADMIN_SETTINGS.md` (3 sections: trigger / endpoints / schema)

### Step 1: Replace PG-trigger section (lines 24-53)

Delete current "PostgreSQL триггер `check_user_config_limit()`" section. Replace with section titled "Application-level enforcement", body:

> Лимит проверяется в Python в [`corpweb/backend/app/api/v1/configs.py:85-94`](backend/app/api/v1/configs.py#L85-L94) перед INSERT нового конфига:
>
> ```python
> active_count = vpn_manager.count_active_by_user(db, current_user.id)
> if active_count >= settings.max_configs_per_user:
>     raise HTTPException(status_code=403, detail=f"Превышен лимит ...")
> ```
>
> При превышении — HTTP 403, INSERT не происходит. Race-condition между двумя одновременными POST-запросами теоретически возможен (count + insert не atomic); на практике не наблюдался, т.к. UI блокирует кнопку "Добавить" при достижении лимита. Если станет проблемой — мигрировать в PG-триггер (отдельный issue).

### Step 2: Update endpoints section ("планируется" → реализованы)

Locate the section about API endpoints. Replace "планируется" notation with concrete implementation references:

> ### `GET /api/v1/admin/settings`
>
> Реализовано в [`corpweb/backend/app/api/v1/admin.py:304-316`](backend/app/api/v1/admin.py#L304-L316). Возвращает текущий объект `SystemSettings`. Требует admin-сессию.
>
> Пример:
> ```bash
> curl -H "Authorization: Bearer $TOKEN" https://panel.example.com/api/v1/admin/settings
> ```
>
> Ответ:
> ```json
> {
>   "id": 1,
>   "max_configs_per_user": 2,
>   "google_play_url": null,
>   "app_store_url": null,
>   "apk_url": null,
>   "windows_url": null,
>   "updated_at": "2026-05-07T12:34:56Z",
>   "updated_by": "admin"
> }
> ```
>
> ### `PATCH /api/v1/admin/settings`
>
> Реализовано в [`corpweb/backend/app/api/v1/admin.py:319-357`](backend/app/api/v1/admin.py#L319-L357). Принимает `SystemSettingsUpdate` (см. [`schemas/settings.py`](backend/app/schemas/settings.py)). `max_configs_per_user` валидируется в диапазоне 1-10.
>
> Пример:
> ```bash
> curl -X PATCH -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
>      -d '{"max_configs_per_user": 5}' \
>      https://panel.example.com/api/v1/admin/settings
> ```

### Step 3: Update DB schema section to include 4 missing columns

Locate the schema section (around lines 11-19). Add 4 columns:

| Column | Type | Default | Назначение |
|---|---|---|---|
| `google_play_url` | VARCHAR(500) | NULL | Ссылка на Google Play в frontend (Установка → Android) |
| `app_store_url` | VARCHAR(500) | NULL | Ссылка на App Store (Установка → iOS) |
| `apk_url` | VARCHAR(500) | NULL | Прямая ссылка на APK |
| `windows_url` | VARCHAR(500) | NULL | Ссылка на Windows-клиент |

Note: эти колонки добавляются в существующую таблицу через `ADD COLUMN IF NOT EXISTS` в [`init_db.py:70-77`](backend/app/db/init_db.py#L70-L77) — это safe migration на уже установленных CP.

### Step 4: Verify and commit

```bash
git add corpweb/ADMIN_SETTINGS.md
git commit -m "docs(rce-B): fact-correction в ADMIN_SETTINGS.md — application-level enforcement, реализованные endpoints, 4 колонки URL (CorpAdmin-AZ-rce)"
```

---

## Task 4: Sub-task C — full rewrite `docs/HA-SETUP.md` + `docs/ADD-NODE.md`

**Files:**
- Rewrite: `docs/HA-SETUP.md` (architecture document, 95 строк → ~150-200 строк)
- Rewrite: `docs/ADD-NODE.md` (runbook, 102 строки → ~150-200 строк)

### Step 1: Rewrite `docs/HA-SETUP.md`

Полная замена. Структура (заголовки):

```
# HA-архитектура CorpAdmin-AZ

## Обзор
Multi-node deployment: один Control-Plane (CP) + N data-plane нод. CP не пропускает VPN-трафик —
только хранит state и через iptables DNAT перенаправляет UDP-handshake'и на ноды. Ноды
работают независимо и автономно, синхронизируясь с CP через push'-стиль агента.

## Компоненты

### Control-Plane (CP)
- PostgreSQL: source of truth для конфигов клиентов, ключей серверов, статуса нод (`wg_file_state`, `nodes`).
- FastAPI backend (`corpweb-backend.service`): admin API (`/api/v1/...`), agent API (`/api/v1/agent/...`).
- nginx: reverse proxy для admin panel + API (HTTPS на 443) и SSE long-poll'инг.
- balancer.py (часть backend): пишет iptables DNAT правила в PREROUTING chain, перенаправляющие
  внешние UDP-порты (52443, 52080, 540, 580) на internal порты нод (51443, 51080).

### Data-plane node
- AntiZapret upstream stack: `antizapret.service`, `proxy.py`, knot-resolver, dnsmasq.
- 4 wireguard-style интерфейса:
  - `antizapret` — split-tunnel WireGuard для AntiZapret-режима (UDP 51443).
  - `vpn` — full-VPN WireGuard (UDP 51080).
  - `az_escape` — AmneziaWG обход для AntiZapret (UDP 53443).
  - `vpn_escape` — AmneziaWG обход для full-VPN (UDP 500).
- `corpweb-sync-agent.service`: Python daemon, реализует:
  - SSE listener для `wg_file_state_changed` notifications (real-time config sync).
  - Reconcile 12 managed файлов (см. `agent/corpweb_sync_agent.py:MANAGED_FILES`).
  - Push node-side ground-truth blobs на CP: `antizapret:allowed_ips` и `/root/antizapret/setup`.
  - Heartbeat каждые 30 сек: метрики (active peers, escape drift, error counters).
  - Custom-up.sh / custom-down.sh hooks для escape iptables правил.

## Поток данных

### Setup blob change (admin → ноды)
1. Admin меняет `*_INCLUDE` toggle в UI Настройки AZ.
2. Backend пишет blob `/root/antizapret/setup` в `wg_file_state`.
3. PostgreSQL trigger `wg_file_state_changed` → SSE event.
4. Каждая нода (через `corpweb-sync-agent`) получает event → applies через `apply_path()`.
5. Hook `doall_and_restart_antizapret` → `doall.sh` (rebuild template-conf + iptables) + restart `antizapret.service`.
6. После doall: agent читает свежий `/etc/wireguard/ips` и пушит `antizapret:allowed_ips` blob на CP.
7. Следующий download `.conf` через панель содержит обновлённый AllowedIPs.

### Client connection (клиент → нода)
1. Клиент шлёт UDP handshake на CP-IP:52443 (или 52080 для full-VPN).
2. iptables PREROUTING (написанный balancer.py) DNAT'ит на одну из нод (round-robin / least-conn).
3. Нода принимает handshake на свой 51443 (или 51080) — `antizapret`/`vpn` iface up.
4. Tunnel установлен; ответный трафик идёт напрямую от ноды (return path не через CP).

### Heartbeat и health
- Каждые 30 сек: agent шлёт `POST /api/v1/agent/heartbeat` с метриками.
- Backend обновляет `nodes.health`: `ok` (всё synced + ifaces up), `degraded` (есть mismatches),
  `down` (heartbeat не пришёл больше 90 сек).
- Dashboard показывает per-node health + escape drift count + active peers per iface.

## Что чем балансируется

| Трафик | Куда летит | Кто балансирует |
|---|---|---|
| HTTPS admin panel + API (443) | nginx на CP | nginx (single CP, не балансируется) |
| WireGuard antizapret (52443/UDP) | iptables PREROUTING DNAT → нода:51443 | balancer.py |
| WireGuard vpn (52080/UDP) | iptables PREROUTING DNAT → нода:51080 | balancer.py |
| AmneziaWG az_escape (53443/UDP) | прямое подключение клиент → нода:53443 | НЕ через CP (клиент знает endpoint) |
| AmneziaWG vpn_escape (500/UDP) | прямое подключение клиент → нода:500 | НЕ через CP |
| Backup ports (540, 580 / UDP) | iptables → нода:51443/51080 | balancer.py |

## Не используется (legacy / не работает)
- nginx stream upstream блоки для UDP-балансировки. **Не настраиваются вручную.** Использовался
  на ранних этапах разработки; заменён balancer.py + iptables DNAT.
- `migrate.py` для bootstrap blob `antizapret:allowed_ips`. Заменён agent push (см. PR #10/#12).

## Health metrics что значат

- `health=ok`: все managed files synced (SHA matches), все 4 ifaces up, escape drift = 0,
  no recent errors.
- `health=degraded`: какой-то managed file mismatched (SHA не совпадает) или escape rules
  drift detected — agent пытается вылечить, но сейчас not in sync.
- `health=down`: heartbeat от ноды не приходил больше 90 сек.

См. ADD-NODE.md для пошаговой регистрации новой ноды.
```

### Step 2: Rewrite `docs/ADD-NODE.md`

Полная замена. Структура:

```
# Добавление новой ноды в существующий CP

## Prerequisites (нода)

- Чистый Debian 12/13 (stable / trixie). Ubuntu 22.04+ может работать, но не тестируется.
- Root доступ.
- Внешний IP, открытые UDP-порты 51443, 51080, 53443, 500.
- Доступ к CP по HTTPS (для регистрации + heartbeat).
- Время синхронизировано (`timedatectl status`).

## Step 1: Установить AntiZapret upstream stack

На ноде, под root:

​```bash
bash <(curl -fsSL https://raw.githubusercontent.com/GubernievS/AntiZapret-VPN/main/setup.sh)
​```

Дождаться завершения. После этого:
- `/root/antizapret/` существует с upstream-скриптами и конфигами.
- `antizapret.service` создан и активен.
- 2 wireguard-интерфейса (`antizapret`, `vpn`) поднялись.

## Step 2: Установить AmneziaWG для escape mode

CorpAdmin-AZ использует AmneziaWG для escape-интерфейсов (`az_escape`, `vpn_escape`).
На Debian 12/13 amneziawg-tools ставится через PPA noble + DKMS-сборку модуля:

​```bash
# Добавить ключ PPA Amnezia (Ubuntu noble)
curl -fsSL "https://keyserver.ubuntu.com/pks/lookup?op=get&search=0xE9E0D8C5BEC85F22FA672BB52FB5A6F3C81B1B1F" | gpg --dearmor -o /usr/share/keyrings/amnezia.gpg

# Добавить repo (с suite=noble)
echo 'deb [signed-by=/usr/share/keyrings/amnezia.gpg] https://ppa.launchpadcontent.net/amnezia/ppa/ubuntu noble main' > /etc/apt/sources.list.d/amnezia.list

apt-get update
apt-get install -y amneziawg-tools amneziawg-dkms
​```

Если `amneziawg-dkms` собирается с ошибками — проверь, что у тебя установлены `linux-headers-$(uname -r)` и `dkms`.

## Step 3: На CP — создать запись о ноде

В админ-панели CP:
1. Левое меню → **Ноды** → **Добавить ноду**.
2. Заполни: hostname (имя для UI), внешний IP/FQDN ноды.
3. Сохрани → CP сгенерирует **enroll token** (одноразовый, действует 1 час).
4. Скопируй token — потребуется на следующем шаге.

Альтернатива через API: `POST /api/v1/admin/nodes` (требует admin Bearer token).

## Step 4: На ноде — установить и запустить sync-agent

​```bash
# Скачать установочный скрипт из репо CP
git clone https://github.com/<your-org>/CorpAdmin-AZ.git /tmp/corpweb-source
cd /tmp/corpweb-source

# Запустить установку с enroll token
CORPWEB_TOKEN="<paste enroll token here>" \
CONTROL_PLANE_URL="https://panel.example.com" \
AGENT_HOSTNAME="$(hostname)" \
bash agent/install.sh
​```

Скрипт:
- Установит зависимости (Python, requests, sseclient).
- Скопирует `corpweb-sync-agent.py` в `/usr/local/bin/`.
- Создаст `/etc/corpweb/agent.env` с конфигом.
- Создаст и запустит `corpweb-sync-agent.service`.

## Step 5: Что произойдёт автоматически

В первые 5-10 секунд после старта агента:

1. **Регистрация**: agent делает `POST /api/v1/agent/register` с enroll token, получает permanent agent token + WG keypair (общий для всех нод одного CP).
2. **Reconcile**: agent скачивает все 12 managed файлов из CP и применяет на ноду. Это перезапишет
   некоторые upstream-файлы (например `/root/antizapret/setup` если admin его уже редактировал в UI).
3. **Blob push**: agent парсит `/etc/wireguard/ips` (свежий output `parse.sh`) и пушит
   `antizapret:allowed_ips` blob на CP. Также пушит текущий `/root/antizapret/setup`.
4. **Ifaces up**: agent поднимает 4 интерфейса (`antizapret`, `vpn`, `az_escape`, `vpn_escape`).
5. **Heartbeat**: agent начинает слать heartbeat каждые 30 сек.
6. **Balancer reconcile**: backend на CP видит новую ноду → balancer.py пишет/обновляет iptables
   DNAT правила (52443→<нода>:51443, 52080→<нода>:51080).

## Step 6: Verify

В админ-панели:
- Ноды → твоя нода → health должен стать `ok` в течение 1 минуты.
- Active peers per iface — должны показываться ненулевые числа после первого подключения клиента.

С ноды:
​```bash
# Все 4 ifaces up
ip -br link show | grep -E "antizapret|vpn|az_escape|vpn_escape"

# Agent active
systemctl status corpweb-sync-agent

# Свежий blob запушен
journalctl -u corpweb-sync-agent | grep "seed-blob pushed"
​```

С CP (если есть psql доступ):
​```sql
SELECT path, octet_length(content), updated_by FROM wg_file_state
WHERE path = 'antizapret:allowed_ips';
-- updated_by должно быть 'agent-sync'
​```

## Step 7: Скачать клиентский .conf для проверки

В UI: Конфиги → Создать → AntiZapret → скачать. В скачанном `.conf`:
- `[Peer] AllowedIPs` должен содержать актуальный список подсетей (десятки записей, не одну `/24`).
- `Endpoint` — IP/FQDN CP с портом 52443 (DNAT на твою ноду).

## Troubleshooting

### health=degraded после регистрации
Проверь `journalctl -u corpweb-sync-agent --since "5 min ago" | grep ERROR`. Частые причины:
- iptables не установлен на ноде (нужен `apt install iptables`) — в данном случае не применимо
  (на ноде нет iptables-через-balancer; balancer работает на CP).
- AmneziaWG не установлен — escape ifaces не поднялись.
- DKMS-модуль не собрался — проверь `lsmod | grep amneziawg`.

### AllowedIPs выглядит stale в скачиваемом .conf
- Проверь что blob свежий: `psql ... WHERE path='antizapret:allowed_ips'`.
- Если blob свежий, но в нём остались Google/Amazon ranges после toggle OFF — это
  upstream-баг (см. CorpAdmin-AZ-58u): `update.sh n` не удаляет ранее скачанные `download/*-ips.txt`.
  Workaround: на ноде `rm /root/antizapret/download/{google,amazon,...}-ips.txt && /root/antizapret/doall.sh`.

### Heartbeat не приходит
Проверь firewall на ноде (исходящий 443 к CP-домену) и SSL (срок сертификата CP).

См. HA-SETUP.md для архитектурного контекста.
```

### Step 3: Verify and commit

```bash
git add docs/HA-SETUP.md docs/ADD-NODE.md
git commit -m "docs(rce-C): full rewrite HA-SETUP.md + ADD-NODE.md — отражают byc/2yj/nye/te3/6jl (CorpAdmin-AZ-rce)"
```

---

## Task 5: Sub-task D — `corpweb/uninstall.sh` cleanup

**Files:**
- Modify: `corpweb/uninstall.sh` — добавить sysctl drop-in cleanup + iptables WARNING block

### Step 1: Read current `corpweb/uninstall.sh`

Identify где сейчас удаляется `/opt/corpweb`, systemd unit'ы, БД. Найти подходящее место для вставки нового cleanup-блока (после удаления systemd, перед или после удаления `/opt/corpweb`).

### Step 2: Add sysctl drop-in removal

Insert block:

```bash
# CorpAdmin-AZ-rce: удалить sysctl drop-in от install-native.sh (6jl).
# Файл наш по имени и содержимому, удалять безопасно.
if [[ -f /etc/sysctl.d/99-corpweb-forwarding.conf ]]; then
    print_info "Удаление /etc/sysctl.d/99-corpweb-forwarding.conf..."
    rm /etc/sysctl.d/99-corpweb-forwarding.conf
    sysctl --system > /dev/null 2>&1 || true
    print_success "sysctl drop-in удалён"
fi
```

### Step 3: Add iptables WARNING block в финале скрипта

Перед заключительным `print_success "..."` (или эквивалентом) добавить:

```bash
print_warning "iptables DNAT правила, написанные balancer.py, НЕ удалены автоматически."
print_warning "Если они больше не нужны — выполните вручную:"
print_warning "    iptables -t nat -F PREROUTING"
print_warning "    netfilter-persistent save"
print_warning "ВНИМАНИЕ: -F PREROUTING удалит ВСЕ DNAT правила в этой цепочке, не только CorpAdmin."
```

### Step 4: Bash syntax check

```bash
bash -n corpweb/uninstall.sh; echo "rc=$?"
```

Expected `rc=0`.

### Step 5: Verify and commit

```bash
git add corpweb/uninstall.sh
git commit -m "fix(uninstall): remove sysctl drop-in + WARNING about iptables DNAT cleanup (CorpAdmin-AZ-rce)"
```

---

## Task 6: Sub-task E — LoginPage.tsx cleanup

**Files:**
- Modify: `corpweb/frontend/src/pages/LoginPage.tsx` (lines 145-147 удалить, line 152 переписать)

### Step 1: Удалить блок "Первый вход: admin / admin"

Use the Edit tool. Match the unique block:

`old_string`:
```tsx
          <div className="mt-6 text-center text-xs text-gray-500">
            Первый вход: admin / admin
          </div>
        </div>
```

`new_string`:
```tsx
        </div>
```

(Удаляем блок полностью с обрамляющим whitespace; закрывающий `</div>` для родительского контейнера остаётся.)

### Step 2: Изменить footer copyright

`old_string`:
```tsx
          <p className="text-xs text-gray-500 mt-1">© 2026 CorpWeb. Powered by AntiZapret VPN</p>
```

`new_string`:
```tsx
          <p className="text-xs text-gray-500 mt-1">© 2026 CorpWeb.</p>
```

### Step 3: Verify TS compiles

Если в репо есть npm scripts:
```bash
cd corpweb/frontend && npm run typecheck 2>&1 | tail -5 && cd ../..
```

Если нет — запустить:
```bash
cd corpweb/frontend && npx tsc --noEmit 2>&1 | tail -5 && cd ../..
```

Expected: 0 errors.

### Step 4: Commit

```bash
git add corpweb/frontend/src/pages/LoginPage.tsx
git commit -m "fix(frontend): remove admin/admin hint + Powered-by suffix from LoginPage (CorpAdmin-AZ-rce)"
```

---

## Task 7: Final verification + push + PR

**Files:** none modified.

### Step 1: Manual review pass

Open every modified file in editor; check:
- Все `[link](path#L123)` — referenced files существуют, lines в окрестности правильные.
- Cross-references between HA-SETUP.md и ADD-NODE.md работают.
- Markdown taxonomy: лестница заголовков последовательная (`#` → `##` → `###`).
- Никаких `XXX TODO TBD ?????`.

### Step 2: Regression baseline

```bash
cd corpweb/backend && python3 -m pytest -q && cd ../..
cd agent && python3 -m pytest -q && cd ..
python3 -m pytest tests/install/ -q
```

Expected: 350 / 112 / 4 passed.

### Step 3: Bash syntax check

```bash
bash -n corpweb/uninstall.sh; echo "rc=$?"
bash -n corpweb/install-native.sh; echo "rc=$?"
bash -n agent/install.sh; echo "rc=$?"
```

All expected `rc=0`.

### Step 4: Diff summary

```bash
git log --oneline 48d8afb7..HEAD
git diff --stat 48d8afb7..HEAD
```

Expected commits (примерно):
- docs(rce): spec + plan
- docs(rce-A): точечные правки README + agent/install.sh
- docs(rce-B): fact-correction в ADMIN_SETTINGS.md
- docs(rce-C): full rewrite HA-SETUP + ADD-NODE
- fix(uninstall): remove sysctl drop-in + WARNING
- fix(frontend): remove admin/admin hint + Powered-by suffix

### Step 5: Push

```bash
git push -u origin worktree-feature+rce-docs-sweep:feature/rce-docs-sweep
```

### Step 6: Create PR

```bash
gh pr create --base CorpAdmin --head feature/rce-docs-sweep \
  --title "docs(rce): sweep — align all README/INSTALL/HA docs with code (CorpAdmin-AZ-rce)" \
  --body "$(cat <<'EOF'
## Summary

Closes the documentation drift epic. Audit identified 3 classes of issues:
1. **Factually wrong** — ADMIN_SETTINGS.md описывает несуществующий PG-триггер; endpoints как "планируется" хотя реализованы; 4 колонки `system_settings` пропущены в schema.
2. **Stale by recent epics** — README'шки не отражают byc (auto blob sync), 2yj (auto doall), nye (fresh /etc/wireguard/ips), 6jl (install-native auto iptables/sysctl).
3. **Deep drift** — HA-SETUP.md и ADD-NODE.md (2026-04-16) не описывают escape mode (4 ifaces, ports 500/53443), AmneziaWG dependency, agent push, heartbeat metrics. Полностью переписаны.

Plus complementary code fix:
- `corpweb/uninstall.sh` теперь удаляет `/etc/sysctl.d/99-corpweb-forwarding.conf` (создаётся install-native.sh с PR #13) и предупреждает админа про iptables DNAT правила.

Spec: docs/superpowers/specs/2026-05-07-docs-sweep.md
Plan: docs/superpowers/plans/2026-05-07-docs-sweep.md

## What changed

- `README.md` (root): auto-AllowedIPs replaces manual editing claim.
- `corpweb/README.md`: install-native deps дополнены iptables/sysctl; managed-files таблица отражает hook `doall_and_restart_antizapret`.
- `corpweb/ADMIN_SETTINGS.md`: PG-триггер заменён на application-level enforcement; endpoints с примерами curl; schema +4 URL колонки.
- `docs/HA-SETUP.md`: full rewrite — текущая архитектура (CP+ноды, balancer.py iptables DNAT, не nginx stream).
- `docs/ADD-NODE.md`: full rewrite — runbook от prerequisites до verify, включая AmneziaWG.
- `agent/install.sh`: enroll token инструкция inline.
- `corpweb/uninstall.sh`: sysctl drop-in cleanup + iptables WARNING.

## Test plan

- [x] No automated tests (per scope decision — docs).
- [x] Backend regression: 350 passed.
- [x] Agent regression: 112 passed.
- [x] Install tests: 4 passed.
- [x] bash -n: all install/uninstall scripts clean.
- [x] Manual review of each rewritten file end-to-end.

## Closes

- CorpAdmin-AZ-rce (epic)
- rce-A / rce-B / rce-C / rce-D (sub-tasks, IDs assigned at Task 1)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review

**Spec coverage:**

- Class 1 (factually wrong) → Task 3 (rce-B).
- Class 2 (stale by epics) → Task 2 (rce-A).
- Class 3 (deep drift HA/ADD-NODE) → Task 4 (rce-C).
- Class 4 (uninstall.sh) → Task 5 (rce-D).
- Goal 1 (factual correctness) — каждое edit ссылается на конкретный file:line кода.
- Goal 2 (coverage всех recent epics) — Task 4 (HA-SETUP) явно перечисляет byc/2yj/nye/te3/6jl.
- Goal 3 (no phantom features) — Task 3 убирает "планируется" и phantom trigger.
- Goal 4 (HA-SETUP/ADD-NODE full rewrite) — Task 4.
- Goal 5 (uninstall removes install state) — Task 5.
- Acceptance #1-3 (ADMIN_SETTINGS) — Task 3.
- Acceptance #4 — Task 2 step 1.
- Acceptance #5-7 (corpweb/README) — Task 2 steps 2-4.
- Acceptance #8 (agent/install.sh enroll token) — Task 2 step 5.
- Acceptance #9 (HA-SETUP rewrite) — Task 4 step 1.
- Acceptance #10 (ADD-NODE rewrite) — Task 4 step 2.
- Acceptance #11 (uninstall) — Task 5.
- Acceptance #12 (manual review gate) — Task 6 step 1.
- Acceptance #13 (bash -n) — Task 6 step 3.
- Acceptance #14 (pytest baseline) — Task 6 step 2.

**Placeholder scan:** все Edit-блоки содержат конкретный текст замены или явные структурные guidance. Где текст слишком длинный (Task 4) — приведён full skeleton, subagent дополняет конкретными формулировками. Это compromise: full text для HA-SETUP/ADD-NODE на 200+ строк раздул бы plan, и формулировки лучше делать с свежим взглядом subagent'а.

**Type/name consistency:**
- Имена hook'ов (`doall_and_restart_antizapret`) согласованы между README и HA-SETUP.
- File:line citations указывают на текущие файлы (admin.py:304-357, configs.py:85-94, models.py:98-101 — verified в audit).
- Beads sub-task IDs создаются в Task 1, потом referenced as `<rce-A-id>` и т.п.; финальный PR body ссылается на `rce-A/B/C/D` без конкретных ID — это OK, real IDs заполняются в commit messages.
