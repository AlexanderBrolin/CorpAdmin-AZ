# Добавление новой ноды в существующий CP

## Prerequisites (нода)

- Чистый Debian 12 (bookworm) или Debian 13 (trixie). Ubuntu 22.04+ может работать,
  но не тестируется в текущем стенде.
- Root-доступ.
- Внешний IP, открытые UDP-порты: **51443**, **51080** (WireGuard base),
  **53443**, **500** (AmneziaWG escape, если `escape_enabled=True` на CP).
- Исходящий TCP 443 к домену CP (для регистрации агента и heartbeat).
- Время синхронизировано (`timedatectl status` — `synchronized: yes`).

## Шаг 1: Установить AntiZapret upstream stack

На ноде, под root:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/GubernievS/AntiZapret-VPN/main/setup.sh)
```

Дождаться завершения (несколько минут). После этого:

- `/root/antizapret/` существует с upstream-скриптами, конфигами и `setup`.
- `antizapret.service` создан, активен и поднял интерфейсы `antizapret` и `vpn`
  (WireGuard, порты 51443 и 51080).
- `doall.sh` уже выполнился — есть `/etc/wireguard/ips`.

Проверить:

```bash
ip -br link show antizapret vpn
systemctl is-active antizapret.service
```

## Шаг 2: Установить AmneziaWG для escape-режима

CorpAdmin-AZ использует AmneziaWG для интерфейсов `az_escape` (UDP 53443) и
`vpn_escape` (UDP 500). На Debian 12/13 пакет ставится через PPA Amnezia
с DKMS-сборкой модуля ядра.

```bash
# Установить зависимости для GPG и DKMS
apt-get install -y gnupg dirmngr curl linux-headers-$(uname -r) dkms

# Добавить GPG-ключ PPA Amnezia
curl -fsSL "https://keyserver.ubuntu.com/pks/lookup?op=get&search=0x75C9DD72C799870E310542E24166F2C257290828" \
    | gpg --dearmor > /usr/share/keyrings/amnezia-ppa.gpg
chmod 644 /usr/share/keyrings/amnezia-ppa.gpg

# Добавить репозиторий (suite=noble — работает на Debian 12/13)
echo "deb [signed-by=/usr/share/keyrings/amnezia-ppa.gpg] https://ppa.launchpadcontent.net/amnezia/ppa/ubuntu noble main" \
    > /etc/apt/sources.list.d/amnezia.list

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y amneziawg-dkms amneziawg-tools
```

Проверить что модуль собрался:

```bash
lsmod | grep amneziawg
# ожидать: amneziawg   <size>   0
awg-quick --version
```

Если `amneziawg-dkms` упал с ошибкой компиляции — убедись что `linux-headers-$(uname -r)`
установлены и `dkms status` не показывает "broken".

## Шаг 3: На CP — создать запись о ноде и получить enroll token

В админ-панели CP:

1. Левое меню → **Ноды** → **Добавить ноду**.
2. Заполни: **Hostname** (имя для UI), **IP** (внешний IP или FQDN ноды).
3. Нажми **Сохранить** — CP сгенерирует **enroll token** (одноразовый).
4. Скопируй token — потребуется в шаге 4.

Альтернатива через API:

```bash
curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"hostname": "wgfi3", "ip": "1.2.3.4"}' \
     https://panel.example.com/api/v1/admin/nodes
```

Endpoint реализован в
[`corpweb/backend/app/api/v1/nodes.py`](../corpweb/backend/app/api/v1/nodes.py).

## Шаг 4: На ноде — установить и запустить sync-agent

CP генерирует установочный скрипт с уже вписанным token и URL. Запусти одну
команду (token получен на шаге 3):

```bash
curl -fsSL "https://panel.example.com/api/v1/agent/install.sh?token=<enroll-token>" | bash
```

Скрипт рендерится на лету бэкендом
([`agent.py:_render_install_script`](../corpweb/backend/app/api/v1/agent.py#L297-L362))
и выполняет следующее:

- Устанавливает `amneziawg-dkms` + `amneziawg-tools` (если ещё нет).
- Включает `awg-quick@az_escape.service` и `awg-quick@vpn_escape.service` в автозапуск.
- Скачивает `corpweb-sync-agent.py` с CP в `/usr/local/bin/`.
- Записывает `/etc/corpweb-sync-agent.env` с `CONTROL_PLANE_URL`, `AGENT_TOKEN`,
  `AGENT_HOSTNAME` (chmod 600).
- Скачивает и устанавливает `corpweb-sync-agent.service`.
- Запускает сервис: `systemctl enable --now corpweb-sync-agent`.

## Шаг 5: Что произойдёт автоматически

В первые 10-30 секунд после старта агента:

1. **Регистрация** — агент делает
   `POST /api/v1/agent/register`
   ([`agent.py:71-109`](../corpweb/backend/app/api/v1/agent.py#L71-L109))
   с hostname + private_ip. CP возвращает server keypair (private/public ключи для
   всех четырёх ifaces) и `wg_config` (адреса, порты, MTU). Агент записывает ключи
   в `/etc/wireguard/*.key`.

2. **Startup reconcile** — агент скачивает все 14 managed файлов из CP и применяет на ноду
   ([`corpweb_sync_agent.py:startup_reconcile`](../agent/corpweb_sync_agent.py#L850-L879)).
   Это перезапишет upstream-файлы (например `/root/antizapret/setup`) если admin
   уже редактировал их в UI. Hook `doall_and_restart_antizapret` запустит
   `doall.sh` + `systemctl restart antizapret.service`.

3. **Blob push** — агент парсит `/etc/wireguard/ips` (output `parse.sh` после `doall.sh`)
   и пушит `antizapret:allowed_ips` blob на CP. Также пушит текущий
   `/root/antizapret/setup`.

4. **Ifaces up** — conf-файлы для escape интерфейсов записаны; `awg-quick@az_escape.service`
   и `awg-quick@vpn_escape.service` запускаются. Все четыре iface подняты.

5. **Heartbeat** — агент начинает слать heartbeat каждые 30 сек.

6. **Balancer reconcile на CP** — backend при следующем heartbeat или явном обновлении
   нод видит новую ноду и `balancer.py` обновляет iptables DNAT правила на CP
   (52443 → нода:51443, 52080 → нода:51080, плюс backup 540/580 и escape 500/53443
   при `escape_enabled=True`).

## Шаг 6: Verify

В панели:

- Ноды → твоя нода → колонка `last_seen` обновляется каждые 30 сек.
- `metrics.escape_drift_detected` = false (escape-правила применены корректно).

На ноде:

```bash
# Все 4 iface подняты
ip -br link show antizapret vpn az_escape vpn_escape

# Агент активен
systemctl status corpweb-sync-agent --no-pager

# Blob успешно запушен
journalctl -u corpweb-sync-agent --since "5 min ago" | grep "seed-blob pushed"

# Escape-модуль загружен
lsmod | grep amneziawg
```

На CP (при наличии доступа к psql):

```sql
-- Blob antizapret:allowed_ips должен быть свежим и записан агентом
SELECT path, octet_length(content), updated_by, updated_at
FROM wg_file_state
WHERE path = 'antizapret:allowed_ips';
-- updated_by = 'agent-sync'
-- octet_length > 0
```

## Шаг 7: Скачать клиентский .conf для проверки

В UI: Конфиги → Создать → AntiZapret → скачать `.conf`. Открой файл и проверь:

- `[Peer] AllowedIPs` содержит актуальный список подсетей (десятки записей, не одну
  `/24`).
- `Endpoint` — IP/FQDN CP с портом **52443** (DNAT через CP на ноду).

Для escape-клиента `[Peer] Endpoint` будет содержать **прямой** IP ноды с портом
53443 или 500 (без DNAT через CP).

## Troubleshooting

### Агент не подключается после установки

```bash
journalctl -u corpweb-sync-agent -n 50 --no-pager
```

Частые причины:
- Неверный URL или enroll token в `/etc/corpweb-sync-agent.env`.
- TCP 443 заблокирован исходящим firewall на ноде.
- Самоподписанный SSL на CP — добавь `REQUESTS_CA_BUNDLE` или исправь сертификат.

### Iface `az_escape` / `vpn_escape` не поднялся

```bash
systemctl status awg-quick@az_escape.service
# Если conf-файл ещё не пришёл с CP, сервис завершится с "Not found"
journalctl -u corpweb-sync-agent | grep "az_escape\|vpn_escape"
```

Conf-файлы `/etc/amnezia/amneziawg/az_escape.conf` и `vpn_escape.conf` приходят
при startup reconcile. Если их нет — проверь что на CP файлы записаны
(admin обязан был создать конфиги через Import или UI).

### AllowedIPs выглядит stale в скачиваемом .conf

Проверь свежесть blob:

```sql
SELECT updated_at, octet_length(content) FROM wg_file_state
WHERE path = 'antizapret:allowed_ips';
```

Если blob свежий, но после снятия галки `GOOGLE_INCLUDE` в setup остались Google-подсети —
это upstream-баг (CorpAdmin-AZ-58u): `update.sh n` не удаляет ранее скачанные файлы
в `download/`. Workaround на ноде:

```bash
rm -f /root/antizapret/download/*google*-ips.txt
/root/antizapret/doall.sh
# Агент автоматически запушит свежий allowed_ips после doall
```

### Heartbeat не приходит (last_seen не обновляется)

- Проверь исходящий TCP 443 с ноды к домену CP.
- Проверь срок действия SSL-сертификата CP.
- `journalctl -u corpweb-sync-agent | grep "Heartbeat failed"`.

### escape_error в метриках

Частые значения:

- `"setup_missing"` — `/root/antizapret/setup` не существует. Запусти
  `bash /root/antizapret/setup.sh` или дождись reconcile с CP.
- `"ALTERNATIVE_CLIENT_IP=y in setup"` — escape-правила рассчитаны на IP-схему `10.26/10.27`,
  которая несовместима с `ALTERNATIVE_CLIENT_IP=y`. Для использования escape-режима
  выключи эту опцию в `/root/antizapret/setup`.

---

См. [HA-SETUP.md](HA-SETUP.md) для архитектурного контекста.
