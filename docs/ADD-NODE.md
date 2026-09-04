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

### 1.1. Ответы установщика — не дефолты, а флотовые

> 🚨 **Установщик задаёт ~30 вопросов, и его дефолты НЕ совпадают с нашими.**
> Гнать его вслепую (`printf '\n' x60`, `yes ""`) — значит поставить ноду,
> отличающуюся от остального флота. Именно так wgfi4 получил 31.08.2026 пул
> подменных адресов `198.18.0.0/15` вместо `10.30.0.0/15` и пять суток не
> разблокировал ничего у ~37% клиентов (`CorpAdmin-AZ-wg7`).

Эталон ответов — файл `/root/antizapret/setup` с любой работающей ноды. Снять перед началом:

```bash
ssh -J brolin@wgfi-office.p4i.ru:2201 -p 2201 brolin@<эталонная-нода> \
    'sudo cat /root/antizapret/setup' > /tmp/setup.reference
```

Расхождения дефолтов апстрима с флотом на 2026-09-04 — отвечать **вручную**, не Enter'ом:

| вопрос | дефолт апстрима | флот |
|---|---|---|
| Use alternative range of FAKE IP addresses | **y** | **n** |
| Restrict forwarding in AntiZapret VPN | **y** | **n** |
| Enable network attack and scan protection | **y** | **n** |
| Use UDP ports 80,443,504,508 as backup for OpenVPN | **y** | **n** |
| Allow multiple clients using same profile file | **y** | **n** |
| Amazon / Hetzner / DigitalOcean / OVH / Akamai include | **n** | **y** |
| Google include | n | **n** |

Остальные вопросы совпадают с дефолтами, но сверяться всё равно по `/tmp/setup.reference`.

### 1.2. Запуск

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/GubernievS/AntiZapret-VPN/main/setup.sh)
```

Запускать **только на настоящем терминале** — см. раздел 5 предполётной проверки.
Установщик не умеет читать готовый файл ответов: `/root/antizapret/setup` он в конце
только пишет. Поэтому ответы вводятся руками, а контроль — сверкой результата.

### 1.3. Сразу после установки — сверить ответы

```bash
ssh -J brolin@wgfi-office.p4i.ru:2201 -p 2201 brolin@<новая-нода> \
    'sudo cat /root/antizapret/setup' \
| diff <(grep -vE '^(SETUP_DATE|DEFAULT_INTERFACE|OUT_INTERFACE|OUT_IP)=' /tmp/setup.reference | sort) \
       <(grep -vE '^(SETUP_DATE|DEFAULT_INTERFACE|OUT_INTERFACE|OUT_IP)=' - | sort)
```

Пусто — ответы приняты верно.

**Если есть расхождения — переустановить, а не править файл.** Правка `setup` чинит
только настройки, которые `up.sh`/`parse.sh` пересчитывают при каждом старте.
Настройки из таблицы ниже установщик **запекает один раз** и больше к ним не
возвращается — их не починит ни правка файла, ни агент CP:

| что запекается | куда | чем правится потом |
|---|---|---|
| `ALTERNATIVE_FAKE_IP` | `sed` по `proxy.py` | только переустановка либо явный `--ip-range` в drop-in юнита |
| `ALTERNATIVE_CLIENT_IP` | `sed` по `proxy.py`, `kresd.conf`, конфигам openvpn, шаблонам wg | только переустановка |
| `ANTIZAPRET_DNS` / `VPN_DNS` | `sed` по `kresd.conf` и конфигам openvpn | только переустановка |
| `OPENVPN_DUPLICATE` / `OPENVPN_LOG` | `sed` по конфигам openvpn | правкой конфигов |
| `OPENVPN_PATCH` / `OPENVPN_DCO` | сборка openvpn | переустановкой пакета |

Коварство `ALTERNATIVE_FAKE_IP`: ветка `y` патчит `proxy.py`, ветка `n` **не делает
ничего**. Пока дефолт в апстримовском `proxy.py` был `10.30.0.0/15`, «ничего» означало
нужный нам диапазон. Апстрим сменил дефолт на `198.18.0.0/15` — и тот же ответ `n` стал
означать чужой пул. Ответ не изменился, результат стал противоположным.

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

---

## ⚠️ Обязательная предполётная проверка (уроки инцидента 2026-08-31)

За один день эти три вещи по очереди положили прод. Проверять **до** того, как нода
попадёт в балансировщик.

### 1. Hostname ноды не должен совпадать с DNS-именем CP

У ноды `wgfi2-ssh` системный hostname был `wgfi2.p4i.ru` — то же самое имя, что у
центра управления. При смене IP провижининг SolusVM перегенерировал `/etc/hosts` и
привязал `wgfi2.p4i.ru` к **собственному адресу ноды**. Агент начал ходить сам в себя,
получал локальный протухший сертификат и падал с `certificate has expired`.

```bash
hostnamectl set-hostname wgfi4.p4i.ru     # имя ноды, НЕ имя CP
grep wgfi /etc/hosts                       # должно резолвиться на CP, не на себя
getent hosts wgfi2.p4i.ru                  # ожидаем IP центра управления
```

Требуемое содержимое `/etc/hosts`:

```
<IP ноды>          <hostname ноды>
168.113.209.218    wgfi2.p4i.ru
```

Проверить, что правка переживает перезагрузку — провижининг хостера любит её затирать.

### 2. Проверить UDP-путь CP ↔ нода в обе стороны

Хостер фильтрует трафик **по парам адресов**. За сутки так были перекрыты три пары.
Нода при этом остаётся доступной откуда угодно ещё, поэтому обычные ping/ssh ничего
не покажут. Мерить надо счётчиком на принимающей стороне, до всех фильтров.

На ноде:
```bash
iptables -t raw -I PREROUTING -s <IP CP> -p udp -j ACCEPT
iptables -t raw -Z PREROUTING
```
На CP:
```bash
python3 -c "
import socket,time
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.connect(('<IP ноды>',52443))
for i in range(100): s.send(b'X'*148); time.sleep(0.05)"
```
Обратно на ноде:
```bash
iptables -t raw -L PREROUTING -n -v -x | grep <IP CP>   # должно быть ~100
iptables -t raw -D PREROUTING -s <IP CP> -p udp -j ACCEPT
```

Пакеты слать **растянуто во времени** — залп в сотни пакетов за доли секунды попадает
под rate-limit и даёт ложный ноль.

Обратное направление проверяется симметрично: временный UDP-эхо на CP и отправка с ноды.

Если хоть в одну сторону потери — **в балансировщик не добавлять**, сначала тикет хостеру.
Формулировка, которая сработала: «с A до B не доходит ни один пакет, замер счётчиком
на принимающей стороне; с того же A до соседнего B2 проходит; до B с других адресов
проходит».

### 3. После добавления ноды — переприменить правила и сбросить conntrack

`private_ip` в базе обновляется сам при регистрации агента, **а правила iptables нет**
(см. CorpAdmin-AZ-dpu). И даже после переприменения существующие клиенты останутся на
старой ноде: AmneziaWG шлёт junk 20 пакетов/с, запись conntrack не истекает никогда
(см. CorpAdmin-AZ-zwj).

```bash
# в панели: Ноды → Балансировщик → выставить веса → Сохранить
conntrack -D -p udp --orig-dst <IP CP>
```

Без сброса нода получит только новые подключения, а перераспределения не произойдёт.

### 4. Убедиться, что CP раздаёт актуального агента

`install.sh` отдаёт `corpweb_sync_agent.py` из `/opt/corpweb/agent/` на CP. Этот каталог
живёт отдельно от репозитория и легко отстаёт — на 2026-08-31 там лежала версия от
17 апреля, тогда как на нодах работала майская, а в репозитории ещё более новая.

```bash
# на CP
sha256sum /opt/corpweb/agent/corpweb_sync_agent.py
# сверить с репозиторием: agent/corpweb_sync_agent.py
```

Расходятся — обновить каталог на CP из репозитория, иначе новая нода получит другой
код, чем её соседи.

### 5. Установщик AntiZapret нельзя запускать через пайп

`setup.sh` использует `read -rp '...' -e -i <default>`. Подстановка значения по умолчанию
работает только через readline, то есть **только на настоящем терминале**. Если подать
скрипту пустые строки в канал (`yes "" | bash setup.sh`), переменные останутся пустыми,
валидация уйдёт в бесконечный цикл: процесс будет жечь 90%+ CPU и не поставит ничего.

Нужен псевдотерминал — `ssh -tt`:

```bash
ssh -tt -J brolin@wgfi-office.p4i.ru:2201 -p 2201 brolin@<нода> 'bash /root/az-setup.sh'
```

> 🚨 **Раньше здесь стояло `printf '\n%.0s' $(seq 1 60) | ssh -tt …` — так делать нельзя.**
> Псевдотерминал эту команду действительно чинит, но 60 пустых Enter'ов означают
> «принять все дефолты апстрима не глядя», а часть дефолтов флоту не подходит
> (таблица в шаге 1.1). Именно этой командой 31.08.2026 была введена wgfi4 с чужим
> пулом подменных адресов. Ответы вводить осознанно, по таблице, затем сверить
> результат — шаг 1.3.

### 6. После установки проверить antizapret.service — он может падать из-за dnslib

Установщик ставит Python-модуль `dnslib` через `pip install --user` из git. На Debian 13
это может не сработать, и тогда `proxy.py` падает с `ModuleNotFoundError: No module named
'dnslib'`. Коварство в том, что сервис при этом **успевает выполнить `up.sh`** (правила
появляются), затем падает, срабатывает `ExecStopPost=down.sh` и правила снимает. Внешне
выглядит как «редиректов почему-то нет».

```bash
systemctl is-active antizapret          # ожидаем active, а не failed
iptables -t nat -S PREROUTING | grep -c REDIRECT   # ожидаем 4 и более
```

Если `failed` с ошибкой про dnslib — на Debian 13 ставится системным пакетом:

```bash
apt-get install -y python3-dnslib
systemctl reset-failed antizapret && systemctl start antizapret
```

### 7. Проверить, что ATTACK_PROTECTION выключился после синхронизации

Установщик по умолчанию ставит `ATTACK_PROTECTION=y` — авто-бан источников, делающих более
20 новых соединений в час. После SNAT **весь клиентский трафик приходит на ноду с одного
адреса — с CP**, поэтому включённая защита забанит центр управления и положит всех клиентов
этой ноды. На рабочих нодах стоит `n`.

Агент перезапишет `/root/antizapret/setup` эталоном из CP (где `n`) и через хук
`doall_and_restart_antizapret` применит. Но проверить обязательно:

```bash
grep ATTACK_PROTECTION /root/antizapret/setup   # ожидаем n
```

> ⚠️ Здесь агент действительно спасает — но **только потому**, что `ATTACK_PROTECTION`
> относится к настройкам, которые `up.sh` пересчитывает из `setup` при каждом старте.
> Не переносить этот вывод на остальные ответы установщика: запекаемые один раз агент
> не чинит (таблица в шаге 1.3). Общая проверка — раздел 10.

### 8. Сквозная проверка перед вводом в балансировщик

```bash
# на ноде — снять счётчик
iptables -t nat -L PREROUTING -n -v -x | awk '/dpt:52443/{print $1}'
# на CP — послать 50 пакетов
python3 -c "
import socket,time
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.connect(('<IP ноды>',52443))
for i in range(50): s.send(b'X'*148); time.sleep(0.04)"
# на ноде — счётчик должен вырасти
```

Прирост будет **+1, а не +50** — таблица `nat` проходится только для первого пакета потока.
Это нормально и означает, что путь и редирект работают.

Финальный чек-лист готовности ноды:

```bash
systemctl is-active antizapret corpweb-sync-agent   # оба active
wg show interfaces; awg show interfaces             # antizapret vpn / az_escape vpn_escape
wg show antizapret peers | wc -l                    # число пиров = числу конфигов
iptables -t nat -S PREROUTING | grep -c REDIRECT    # 4 и более
```
В панели — нода с нулевым рассинхроном и свежим `last_seen`.

### 9. Живой префикс интерфейса против конфига

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
  live="$(ip -4 -br addr show dev $i 2>/dev/null | awk '{print $3}')"
  conf="$(grep -m1 '^[[:space:]]*Address' $c 2>/dev/null | sed 's/.*= *//')"
  printf '%-12s live=%-18s conf=%s\n' "$i" "${live:-<ОТСУТСТВУЕТ>}" "${conf:-<ОТСУТСТВУЕТ>}"
done
```

Живое значение и значение из конфига должны совпадать у всех четырёх. Если нет —
посмотреть `journalctl -u corpweb-sync-agent | grep -i 'address drift'`.
Отдельно убедиться, что маршрут до клиента из старшей подсети ведёт в туннель,
а не в шлюз:

```bash
ip route get 10.29.11.10   # ожидается: dev antizapret, НЕ 'via <шлюз> dev eth0'
```

Важно: агент чинит расхождение только на **startup reconcile** — при старте сервиса и
при каждом переподключении к control plane. Heartbeat, уходящий раз в 30 секунд, дрейф
только **обнаруживает** и репортит в метрики (`iface_addr_drift`), сам интерфейс он не
трогает. Значит, расхождение, возникшее пока агент уже работает и подключён, станет
видно в метриках в течение 30 секунд, но не будет исправлено до перезапуска агента или
обрыва его соединения с CP. Если `iface_addr_drift` держится в метриках — правильное
действие: `systemctl restart corpweb-sync-agent` на этой ноде. Так сделано намеренно:
вешать мутатор адреса на 30-секундный путь сочли слишком рискованным для живого
продакшен-интерфейса.

### 10. Паритет с эталонной нодой — последний рубеж перед балансировщиком

Разделы 1–9 ловят по одной известной ошибке каждый. Этот — ловит любую, включая ещё
не встречавшуюся, потому что сравнивает ноду целиком с уже работающей.

```bash
./scripts/node-parity-check.sh diff brolin@<эталонная-нода> brolin@<новая-нода>
```

Скрипт снимает с обеих нод нормализованный отпечаток и печатает unified diff:

- ответы установщика (`/root/antizapret/setup`)
- sha всех `/root/antizapret/*.sh` и `proxy.py`
- эффективный `ExecStart` юнита `antizapret` (с учётом drop-in)
- слушающие и stub-адреса `kresd`
- правила `iptables` во всех четырёх таблицах, с нормализацией собственного адреса ноды
- список `ipset` и пусто/не-пусто по каждому
- адреса интерфейсов, слушающие UDP-порты VPN
- `sysctl`, включая `promote_secondaries` по каждому интерфейсу
- sha `/etc/wireguard/ips`, список упавших юнитов

Выход `0` — паритет. Выход `1` — расхождения; **каждое** либо устранить, либо завести
тикет с обоснованием, **до** ввода ноды в балансировщик. Динамику скрипт отсеивает сам:
маппинги подменных адресов и точные размеры ipset в дифф не попадают.

Почему это отдельный рубеж, а не пункт в списке: `filedrift` в панели сверяет только
14 файлов, которыми управляет CP (четыре wg/awg-конфига, `config/*.txt` и `setup`).
Исполняемый слой AntiZapret — `up.sh`, `down.sh`, `client.sh`, `proxy.py` — не покрыт
ничем, и его расхождение панель показывает как `filedrift = 0` (`CorpAdmin-AZ-w3y`).

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
