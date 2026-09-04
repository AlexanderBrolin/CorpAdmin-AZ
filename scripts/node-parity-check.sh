#!/bin/bash
#
# Сверка новой ноды с эталонной перед вводом в балансировщик.
#
# Снимает с ноды нормализованный отпечаток (правила iptables, ipset, sha
# исполняемых файлов AntiZapret, адреса интерфейсов, порты, sysctl, ответы
# установщика) и сравнивает две ноды между собой.
#
# Зачем: часть настроек установщик AntiZapret запекает ОДИН РАЗ (sed-патчи в
# proxy.py, kresd.conf, конфигах openvpn, шаблонах wg) и больше к ним не
# возвращается. Агент CP перезаписывает /root/antizapret/setup, что чинит
# только те настройки, которые up.sh пересчитывает при каждом старте.
# Запечённые расхождения не чинит никто и не видит никто — так wgfi4 пять
# суток раздавал подменные адреса из чужого пула (CorpAdmin-AZ-wg7).
#
# Использование:
#   scripts/node-parity-check.sh collect <user@host>
#   scripts/node-parity-check.sh diff <user@host-эталон> <user@host-кандидат>
#
# Доступ к нодам идёт через jump-host, переопределяется через NODE_SSH_OPTS.
#
set -uo pipefail

NODE_SSH_OPTS="${NODE_SSH_OPTS:--J brolin@wgfi-office.p4i.ru:2201 -p 2201}"
SSH_TIMEOUT="${SSH_TIMEOUT:-30}"

usage() {
	cat >&2 <<'EOF'
Использование:
  node-parity-check.sh collect <user@host>
  node-parity-check.sh diff <user@host-эталон> <user@host-кандидат>

Переменные окружения:
  NODE_SSH_OPTS   опции ssh до ноды (по умолчанию — через wgfi-office)
  SSH_TIMEOUT     таймаут подключения, секунд (по умолчанию 30)
EOF
	exit 2
}

# Снимает отпечаток с одной ноды. Всё, что зависит от конкретной машины
# (собственный адрес, hostname), заменяется плейсхолдером, иначе две
# исправные ноды всегда будут различаться.
collect() {
	local target="$1"
	# shellcheck disable=SC2086
	ssh -o BatchMode=yes -o ConnectTimeout="$SSH_TIMEOUT" $NODE_SSH_OPTS "$target" 'bash -s' <<'REMOTE'
export PATH=/usr/sbin:/sbin:/usr/bin:$PATH
# На части нод brolin имеет uid 0 и sudo не установлен.
if [[ "$EUID" -eq 0 ]]; then SUDO=""; else SUDO="sudo -n"; fi

SELF_IP="$(ip route get 1.2.3.4 2>/dev/null | grep -oP 'src \K\S+')"
SELF_HOST="$(hostname -s)"
norm() { sed -e "s/${SELF_IP//./\\.}/<NODE_IP>/g" -e "s/${SELF_HOST}/<NODE_HOST>/g"; }

echo "## setup (ответы установщика, эталон приходит с CP)"
$SUDO grep -vE '^(DEFAULT_INTERFACE|OUT_INTERFACE|OUT_IP|SETUP_DATE)=' /root/antizapret/setup 2>/dev/null | sort

echo "## sha исполняемых файлов AntiZapret"
# Глоб раскрывается ВНУТРИ sudo: там, где brolin не root, каталог /root
# ему не читается и глоб молча схлопнулся бы в пустой список.
$SUDO sh -c 'sha256sum /root/antizapret/*.sh /root/antizapret/proxy.py' 2>/dev/null | sort -k2

echo "## ExecStart antizapret.service (drop-in учитывается)"
systemctl show antizapret -p ExecStart --value 2>/dev/null | tr ';' '\n' | grep -oP 'argv\[\]=\K.*'

echo "## kresd: слушающие адреса и stub-адреса"
$SUDO grep -ohP "net\.listen\('\K[0-9.]+|STUB\('\K[0-9.]+" /etc/knot-resolver/kresd.conf 2>/dev/null | sort

echo "## iptables filter"
$SUDO iptables -w -S 2>/dev/null | norm | sort
echo "## iptables nat (без динамических маппингов подменных адресов)"
$SUDO iptables -w -t nat -S 2>/dev/null | grep -v -- '-A ANTIZAPRET-MAPPING -d ' | norm | sort
echo "## iptables mangle"
$SUDO iptables -w -t mangle -S 2>/dev/null | grep -v -- '-A ANTIZAPRET-WARP -d ' | norm | sort
echo "## iptables raw"
$SUDO iptables -w -t raw -S 2>/dev/null | norm | sort

echo "## ipset: имена и наполнение"
for s in $($SUDO ipset list -n 2>/dev/null | sort); do
	n=$($SUDO ipset list "$s" 2>/dev/null | awk '/Number of entries/{print $4}')
	# Точное число записей плавает (списки обновляются ночью), поэтому
	# сверяем только «пусто / не пусто» — этого хватает, чтобы поймать
	# набор, включённый лишним ответом установщика.
	if [[ "${n:-0}" -eq 0 ]]; then echo "$s: empty"; else echo "$s: non-empty"; fi
done

echo "## адреса интерфейсов"
ip -4 -br addr show 2>/dev/null | awk '$1 ~ /^(antizapret|vpn|az_escape|vpn_escape)/ {print $1, $3}' | sort

echo "## слушающие UDP-порты VPN"
$SUDO ss -lnup 2>/dev/null | grep -oP ':\K(500|540|580|51080|51443|52080|52443|53443)\b' | sort -u

echo "## sysctl"
for k in net.ipv4.ip_forward net.ipv4.conf.all.promote_secondaries \
         net.ipv4.conf.default.promote_secondaries; do
	echo "$k = $(sysctl -n "$k" 2>/dev/null)"
done
for d in antizapret vpn az_escape vpn_escape; do
	echo "net.ipv4.conf.$d.promote_secondaries = $(sysctl -n "net.ipv4.conf.$d.promote_secondaries" 2>/dev/null)"
done

echo "## sha /etc/wireguard/ips"
$SUDO sha256sum /etc/wireguard/ips 2>/dev/null | awk '{print $1}'

echo "## упавшие юниты"
systemctl list-units --state=failed --no-pager --no-legend 2>/dev/null | awk '{print $1}' | sort
REMOTE
}

cmd="${1:-}"
case "$cmd" in
	collect)
		[[ $# -eq 2 ]] || usage
		collect "$2"
		;;
	diff)
		[[ $# -eq 3 ]] || usage
		ref_out="$(mktemp)"; cand_out="$(mktemp)"
		trap 'rm -f "$ref_out" "$cand_out"' EXIT
		collect "$2" > "$ref_out"
		collect "$3" > "$cand_out"
		if [[ ! -s "$ref_out" || ! -s "$cand_out" ]]; then
			echo "ОШИБКА: не удалось снять отпечаток (эталон: $(wc -l < "$ref_out") строк, кандидат: $(wc -l < "$cand_out") строк)" >&2
			exit 3
		fi
		if diff -u --label "эталон $2" --label "кандидат $3" "$ref_out" "$cand_out"; then
			echo "Паритет подтверждён: расхождений нет."
			exit 0
		fi
		echo >&2
		echo "Расхождения выше. Каждое либо устранить, либо явно обосновать в тикете," >&2
		echo "ДО ввода ноды в балансировщик." >&2
		exit 1
		;;
	*)
		usage
		;;
esac
