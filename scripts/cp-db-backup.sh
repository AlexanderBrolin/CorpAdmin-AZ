#!/bin/bash
#
# Hourly off-site backup of the control-plane database.
#
# The 2026-09-18 outage left the only copy of the CP database on the CP itself, which
# was unreachable for hours. This hands a fresh dump to every node that answers, so a
# lost control plane can be rebuilt from any surviving node.
#
# The node list comes from the database, not from a constant: the fleet changes, and a
# hardcoded list silently stops covering new nodes.
#
# Install: /usr/local/bin/cp-db-backup.sh, run hourly from cron.
#
set -uo pipefail

ENV_FILE=/opt/corpweb/backend/.env
LOCAL_DIR=/root/backup
KEEP_DAYS=3
KEY=/root/.ssh/id_ed25519_backup
SSH_PORT=2201
SSH_USERS="root brolin"          # nodes differ: some allow root, some only brolin

log() { logger -t cp-db-backup -- "$*"; echo "$*"; }

DB_URL=$(grep -E '^DATABASE_URL=' "$ENV_FILE" | cut -d= -f2-)
PGPASSWORD=$(printf '%s' "$DB_URL" | sed -E 's#.*://[^:]+:([^@]+)@.*#\1#')
export PGPASSWORD

umask 077
mkdir -p "$LOCAL_DIR"
stamp=$(date -u +%Y%m%d-%H%M)
dump="$LOCAL_DIR/corpweb_db-$stamp.sql.gz"

if ! pg_dump -h localhost -U corpweb -d corpweb_db --no-owner --no-acl \
             --exclude-table-data=connection_logs | gzip -9 > "$dump"; then
    rm -f "$dump"
    log "FAILED: pg_dump"
    exit 1
fi

# a truncated dump that looks like a backup is the worst outcome
if ! gzip -t "$dump" 2>/dev/null; then
    rm -f "$dump"
    log "FAILED: dump is corrupt"
    exit 1
fi

find "$LOCAL_DIR" -maxdepth 1 -name 'corpweb_db-*.sql.gz' -mtime +"$KEEP_DAYS" -delete

nodes=$(psql -h localhost -U corpweb -d corpweb_db -tA \
             -c 'select private_ip from nodes order by id' 2>/dev/null)
if [[ -z "$nodes" ]]; then
    log "WARNING: no nodes in DB, dump kept locally only ($(stat -c%s "$dump") bytes)"
    exit 0
fi

sent=0 failed=""
for ip in $nodes; do
    delivered=0
    for user in $SSH_USERS; do
        if timeout 180 ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=8 \
               -o StrictHostKeyChecking=accept-new -p "$SSH_PORT" \
               "$user@$ip" < "$dump" >/dev/null 2>&1; then
            delivered=1; sent=$((sent+1)); break
        fi
    done
    [[ $delivered -eq 0 ]] && failed="$failed $ip"
done

if [[ -n "$failed" ]]; then
    log "delivered to $sent node(s), unreachable:$failed"
else
    log "delivered to $sent node(s), all reachable"
fi
