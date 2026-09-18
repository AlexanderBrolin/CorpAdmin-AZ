#!/bin/bash
#
# Point the whole fleet at a new control-plane address.
#
# Agents reach the CP by name (CONTROL_PLANE_URL=https://wgfi2.p4i.ru), but that name is
# resolved from /etc/hosts on every node — kresd there caches DNS for the record's TTL, and
# on 2026-09-18 wgfi3 spent three hours hammering a dead address because of it. The upshot:
# changing the A record alone does NOT move the nodes. This script does.
#
# Usage:
#   scripts/switch-cp-ip.sh <NEW_CP_IP> [nodes|cp|all]
#
#   nodes  rewrite /etc/hosts on every node and restart its agent
#   cp     set system_settings.cp_ip and rebuild the balancer rules on the new CP
#   all    both (default)
#
# The node list is read from the CP database rather than hardcoded — the fleet changes, and
# a frozen list silently stops covering new nodes. Node IPs come from the DB; the ssh user
# differs per hoster, so it is probed the same way cp-db-backup.sh does it.
#
set -uo pipefail

# iptables wants a dot in --probability; a ru_RU awk would print a comma
export LC_ALL=C

NEW_IP="${1:?usage: switch-cp-ip.sh <NEW_CP_IP> [nodes|cp|all]}"
WHAT="${2:-all}"

SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new -p 2201"
SSH_USERS="${SSH_USERS:-root brolin}"
ENV_FILE=/opt/corpweb/backend/.env
CP_NAME=wgfi2.p4i.ru

log() { printf '%s\n' "$*"; }

cp_psql() {
    ssh $SSH_OPTS "root@${NEW_IP}" "NEWURL=\$(grep -E '^DATABASE_URL=' $ENV_FILE | cut -d= -f2-)
export PGPASSWORD=\$(printf '%s' \"\$NEWURL\" | sed -E 's#.*://[^:]+:([^@]+)@.*#\1#')
psql -h localhost -U corpweb -d corpweb_db $*"
}

node_ips() {
    cp_psql -tA -c "'select private_ip from nodes order by id'" 2>/dev/null | tr -d '\r'
}

# Rewrite the CP line in /etc/hosts and restart the agent. Python, not sed: the file may
# lack a trailing newline, and appending to it blindly glues two records together.
switch_node() {
    local ip="$1" user
    for user in $SSH_USERS; do
        if timeout 60 ssh $SSH_OPTS "${user}@${ip}" "S=''; [ \$(id -u) -ne 0 ] && S=sudo
\$S python3 - <<PY
p = '/etc/hosts'
keep = [l for l in open(p).read().splitlines() if '${CP_NAME}' not in l]
keep.append('${NEW_IP}\t${CP_NAME}')
open(p, 'w').write('\n'.join(keep) + '\n')
PY
\$S systemctl restart corpweb-sync-agent
printf '%s resolves CP to %s\n' '${ip}' \"\$(getent ahostsv4 ${CP_NAME} | head -1 | awk '{print \$1}')\"" 2>/dev/null
        then
            return 0
        fi
    done
    log "$ip: FAILED (no usable ssh user of: $SSH_USERS)"
    return 1
}

IPS=$(node_ips)
if [[ -z "$IPS" ]]; then
    log "cannot read the node list from ${NEW_IP} — is the CP database up?"
    exit 1
fi

if [[ "$WHAT" == "nodes" || "$WHAT" == "all" ]]; then
    log "=== nodes -> ${NEW_IP} ==="
    for ip in $IPS; do switch_node "$ip" & done
    wait
fi

if [[ "$WHAT" == "cp" || "$WHAT" == "all" ]]; then
    log "=== ${NEW_IP}: cp_ip + balancer rules ==="
    # cp_ip feeds the SNAT source; stale value sends node replies into the void
    cp_psql -c "\"update system_settings set cp_ip='${NEW_IP}', updated_at=now() where id=1\"" >/dev/null

    # Equal weights across every node the DB knows. weights_to_probabilities() applies them
    # sequentially: p[i] = w[i] / sum(w[i:]), last rule is the catch-all fallback.
    rules=""
    count=$(printf '%s\n' $IPS | wc -l)
    idx=0
    for ip in $IPS; do
        idx=$((idx + 1))
        remaining=$((count - idx + 1))
        if [[ $idx -eq $count ]]; then
            rules+="iptables -t nat -A PREROUTING -p udp --dport \$p -j DNAT --to-destination ${ip}:\$p; "
        else
            prob=$(LC_ALL=C awk -v r="$remaining" 'BEGIN{printf "%.11f", 1/r}')
            rules+="iptables -t nat -A PREROUTING -p udp --dport \$p -m statistic --mode random --probability ${prob} -j DNAT --to-destination ${ip}:\$p; "
        fi
    done

    # collapse to one line: a multi-line list breaks apart inside the remote command
    snat_ips=$(printf '%s\n' $IPS | sort | tr '\n' ' ')
    ssh $SSH_OPTS "root@${NEW_IP}" "export PATH=/usr/sbin:/sbin:\$PATH
sysctl -w net.ipv4.ip_forward=1 >/dev/null
iptables -t nat -F PREROUTING; iptables -t nat -F POSTROUTING
for p in 51443 51080 52443 52080 540 580 500 53443; do ${rules}done
for ip in ${snat_ips}; do iptables -t nat -A POSTROUTING -d \$ip -j SNAT --to-source ${NEW_IP}; done
netfilter-persistent save >/dev/null 2>&1
conntrack -D -p udp --orig-dst ${NEW_IP} >/dev/null 2>&1 || true
printf 'DNAT/SNAT rules: %s\n' \"\$(iptables -t nat -S | grep -cE 'DNAT|SNAT')\""
fi
