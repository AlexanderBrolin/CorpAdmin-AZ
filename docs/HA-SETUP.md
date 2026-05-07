# HA-архитектура CorpAdmin-AZ

## Обзор

Multi-node deployment: один Control-Plane (CP) + N data-plane нод. CP не пропускает VPN-трафик —
только хранит state и через iptables DNAT перенаправляет входящие UDP-handshake'и на ноды. Ноды
работают независимо и автономно, синхронизируясь с CP через push-стиль агента.

```text
          Admin browser
               |  HTTPS 443
          nginx (CP)
               |  :8000
          corpweb-backend (FastAPI)
         /          \
   PostgreSQL     balancer.py
                      |  iptables DNAT
                  data-plane ноды
                 /antizapret /vpn /az_escape /vpn_escape
```

## Компоненты

### Control-Plane (CP)

- **PostgreSQL**: source of truth для конфигов клиентов, ключей серверов, статуса нод
  (таблицы `wg_file_state`, `nodes`, `wg_server_keys`, `vpn_configs`).
- **FastAPI backend** (`corpweb-backend.service`): admin API (`/api/v1/...`),
  agent API (`/api/v1/agent/...`), SSE endpoint (`/api/v1/agent/events`).
- **nginx**: reverse proxy для admin panel + API (HTTPS 443) и SSE long-poll.
- **balancer.py** (часть backend,
  [`corpweb/backend/app/services/balancer.py`](../corpweb/backend/app/services/balancer.py)):
  пишет iptables DNAT правила в PREROUTING/POSTROUTING chain, перенаправляющие
  внешние UDP-порты на internal порты нод.
  - `BASE_PORTS = [51443, 51080, 52443, 52080, 540, 580]` — активны всегда.
  - `ESCAPE_PORTS = [500, 53443]` — добавляются когда `escape_enabled=True`.

### Data-plane нода

- **AntiZapret upstream stack**: `antizapret.service` (ExecStartPre=`up.sh`, ExecStart=`proxy.py`),
  knot-resolver, dnsmasq, ipset.
  Юнит: [`setup/etc/systemd/system/antizapret.service`](../setup/etc/systemd/system/antizapret.service).
- **Четыре WireGuard/AmneziaWG интерфейса**:

  | Интерфейс    | Протокол    | UDP-порт на ноде | Режим               |
  |---|---|---|---|
  | `antizapret` | WireGuard   | 51443            | split-tunnel AZ     |
  | `vpn`        | WireGuard   | 51080            | full-VPN            |
  | `az_escape`  | AmneziaWG   | 53443            | AZ обход (bypass)   |
  | `vpn_escape` | AmneziaWG   | 500              | full-VPN обход      |

  Escape-интерфейсы управляются `awg-quick@az_escape.service` /
  `awg-quick@vpn_escape.service`. Iptables правила для них генерирует агент через
  `custom-up.sh` / `custom-down.sh` hooks
  ([`agent/corpweb_sync_agent.py:render_custom_up_sh`](../agent/corpweb_sync_agent.py#L110)).

- **`corpweb-sync-agent.service`**: Python daemon, реализует:
  - SSE listener на `/api/v1/agent/events` — real-time config sync через
    PostgreSQL NOTIFY `wg_file_state_changed`.
  - Reconcile 14 managed файлов (см.
    [`agent/corpweb_sync_agent.py:MANAGED_FILES`](../agent/corpweb_sync_agent.py#L79-L94)).
  - Push node-side ground-truth blobs на CP: `antizapret:allowed_ips`
    и `/root/antizapret/setup` — через `POST /api/v1/agent/seed-blob`.
  - Heartbeat каждые 30 сек: `POST /api/v1/agent/heartbeat` с метриками
    (active peers per iface, escape drift, rx/tx bytes/sec).
  - Reconcile escape-rules (`custom-up.sh` / `custom-down.sh`) на каждый heartbeat.

## Управляемые файлы (14 штук)

Полный список [`MANAGED_FILES`](../agent/corpweb_sync_agent.py#L79-L94):

| Путь | Hook при изменении |
|---|---|
| `/etc/wireguard/antizapret.conf` | `wg_antizapret` — syncconf/restart iface |
| `/etc/wireguard/vpn.conf` | `wg_vpn` |
| `/etc/amnezia/amneziawg/az_escape.conf` | `awg_az_escape` |
| `/etc/amnezia/amneziawg/vpn_escape.conf` | `awg_vpn_escape` |
| `/root/antizapret/setup` | `doall_and_restart_antizapret` |
| `/root/antizapret/config/include-hosts.txt` | `doall` |
| `/root/antizapret/config/exclude-hosts.txt` | `doall` |
| `/root/antizapret/config/include-ips.txt` | `doall` |
| `/root/antizapret/config/exclude-ips.txt` | `doall` |
| `/root/antizapret/config/allow-ips.txt` | `doall` |
| `/root/antizapret/config/forward-ips.txt` | `doall` |
| `/root/antizapret/config/include-adblock-hosts.txt` | `doall` |
| `/root/antizapret/config/exclude-adblock-hosts.txt` | `doall` |
| `/root/antizapret/config/remove-hosts.txt` | `doall` |

## Поток данных

### Setup blob change (admin → ноды)

1. Admin меняет `*_INCLUDE` toggle в UI (Настройки AZ).
2. Backend записывает blob `/root/antizapret/setup` в таблицу `wg_file_state`.
3. PostgreSQL trigger `wg_file_state_changed` → NOTIFY → SSE event на все
   подключённые ноды (`GET /api/v1/agent/events`).
4. Каждая нода получает event → agent вызывает `apply_path()` c hook
   `doall_and_restart_antizapret`
   ([`agent/corpweb_sync_agent.py:654-659`](../agent/corpweb_sync_agent.py#L654-L659)):
   - `schedule_doall()` — debounce 5 сек, затем `/root/antizapret/doall.sh`
     (rebuild template-conf + обновить `/etc/wireguard/ips`).
   - `schedule_restart_antizapret()` — debounce 5 сек, затем
     `systemctl restart antizapret.service` (re-apply iptables/sysctl из нового `setup`
     через `up.sh`).
5. После успешного `doall.sh` агент читает `/etc/wireguard/ips` и пушит
   `antizapret:allowed_ips` blob на CP
   ([`agent/corpweb_sync_agent.py:458-461`](../agent/corpweb_sync_agent.py#L458-L461)).
6. Следующий download `.conf` через панель содержит обновлённый AllowedIPs.

### Client connection (клиент → нода)

1. Клиент шлёт UDP handshake на CP-IP. Конкретный порт зависит от типа клиента:
   - WireGuard antizapret → `51443`
   - AmneziaWG antizapret → `52443`
   - WireGuard vpn → `51080`
   - AmneziaWG vpn → `52080`
   - Backup ports антизапрета и vpn (`540` / `580`) — fallback при блокировке primary.
2. iptables PREROUTING (написанный `balancer.py`) DNAT'ит на одну из нод
   по вероятностному round-robin. Source-port = target-port (`balancer.py` пишет
   `--dport X --to-destination IP:X`, [`balancer.py:106`](../corpweb/backend/app/services/balancer.py#L106)).
3. Нода принимает handshake на тот же порт — tunnel установлен.
4. Ответный трафик идёт напрямую от ноды (return path не через CP; SNAT через
   [`balancer.py:apply_rules`](../corpweb/backend/app/services/balancer.py#L217-L272)
   обеспечивает корректный src IP).
5. Escape-клиенты (`az_escape`, `vpn_escape`): когда `escape_enabled=True` в настройках CP,
   их порты (`53443` / `500`) тоже включаются в DNAT-балансировку через CP
   ([`balancer.py:get_active_ports`](../corpweb/backend/app/services/balancer.py#L31-L37)).

### Heartbeat и health

- Агент шлёт `POST /api/v1/agent/heartbeat` каждые 30 сек
  ([`HEARTBEAT_INTERVAL = 30`](../agent/corpweb_sync_agent.py#L984)).
- Payload содержит: `applied_sha` (sha256 всех 14 managed файлов), `health: "ok"`,
  `metrics` (active_peers per iface, escape_drift_detected, rx/tx bytes/sec), `peers`.
- Backend обновляет `nodes.health`, `nodes.last_seen`, `nodes.metrics`,
  `nodes.peers_snapshot`
  ([`agent.py:136-149`](../corpweb/backend/app/api/v1/agent.py#L136-L149)).
- Dashboard отображает `last_seen` timestamp и per-node метрики.
- Нода, от которой не пришёл heartbeat, остаётся в последнем записанном состоянии;
  `last_seen` в dashboard показывает когда был последний контакт.

## Что чем балансируется

DNAT правила одинаковы по портам (source = target). Каждый порт обслуживает
свой тип трафика:

| Iface | Flavor | Порт UDP | Когда активен | Балансировщик |
|---|---|---|---|---|
| HTTPS admin + API | — | 443/TCP | всегда | nginx → backend :8000 (один CP) |
| antizapret | WireGuard | 51443 | всегда | iptables DNAT (`balancer.py`) |
| antizapret | AmneziaWG | 52443 | всегда | iptables DNAT |
| vpn | WireGuard | 51080 | всегда | iptables DNAT |
| vpn | AmneziaWG | 52080 | всегда | iptables DNAT |
| antizapret backup | — | 540 | всегда | iptables DNAT |
| vpn backup | — | 580 | всегда | iptables DNAT |
| az_escape | AmneziaWG | 53443 | `escape_enabled=True` | iptables DNAT |
| vpn_escape | AmneziaWG | 500 | `escape_enabled=True` | iptables DNAT |

`balancer.py:BASE_PORTS = [51443, 51080, 52443, 52080, 540, 580]`
([`balancer.py:19`](../corpweb/backend/app/services/balancer.py#L19)) — активны всегда.
`ESCAPE_PORTS = [500, 53443]` ([`balancer.py:23-25`](../corpweb/backend/app/services/balancer.py#L23-L25))
добавляются в DNAT-таблицу только когда `escape_enabled=True`.

Port-mapping для конкретной комбинации (iface, flavor) определён в
[`wg_templates.py:_PORT_MAP`](../corpweb/backend/app/services/wg_templates.py#L31-L39).

## Не используется (legacy)

- **nginx `stream {}` upstream блоки для UDP-балансировки.** Не настраиваются вручную.
  Использовались на ранних этапах разработки; полностью заменены `balancer.py` +
  iptables DNAT. Старый `docs/HA-SETUP.md` (до 2026-05-07) описывал эти блоки — это
  была ошибка документации.
- **`migrate.py` для bootstrap blob `antizapret:allowed_ips`**. Заменён agent push
  при `startup_reconcile` (PR #10, PR #12).

## Health metrics: что значат

CP хранит поле `nodes.health`, которое агент выставляет в каждом heartbeat.
В текущей реализации агент всегда шлёт `health: "ok"` (escape_drift и ошибки
уходят в поле `metrics.escape_error`). Dashboard CP показывает:

- **Нода видна, `last_seen` свежий** — агент жив, sync работает.
- **`last_seen` устарел** — агент не посылал heartbeat. Проверь
  `systemctl status corpweb-sync-agent` на ноде.
- **`metrics.escape_drift_detected: true`** — `custom-up.sh` / `custom-down.sh`
  содержали устаревшие escape-правила; агент исправил их и перезапустил
  `antizapret.service`. Это нормальное самовосстановление при обновлении агента.
- **`metrics.escape_error`** — агент не смог применить escape-правила. Причина
  указана в значении поля (например, `"ALTERNATIVE_CLIENT_IP=y in setup"` или
  `"setup_missing"`).

## Blob auto-sync (byc)

При `startup_reconcile` агент после скачивания всех managed-файлов пушит на CP:
- `antizapret:allowed_ips` — разобранный AllowedIPs из `/etc/wireguard/ips`
  (обновляется `parse.sh` после каждого `doall.sh`).
- `/root/antizapret/setup` — текущий файл настроек ноды.

Это позволяет CP иметь актуальные данные даже если ноду перезапустили с другим
upstream-state, а также обеспечивает актуальный `AllowedIPs` в скачиваемых `.conf`.

Whitelist путей для `seed-blob`:
[`agent.py:_SEED_BLOB_WHITELIST`](../corpweb/backend/app/api/v1/agent.py#L55-L58).

---

См. [ADD-NODE.md](ADD-NODE.md) для пошаговой регистрации новой ноды.
