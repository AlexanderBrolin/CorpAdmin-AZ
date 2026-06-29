#!/usr/bin/env python3
"""Bulk-migrate Google-OAuth users to local password auth from the account registry.

Reads the XLSX registry locally, hashes passwords locally (bcrypt, identical to the
backend's passlib config), and writes password_hash + auth_provider='local' to the
prod DB over an SSH tunnel. Idempotent: only rows still auth_provider='google' are
touched. NEVER copies plaintext to the server; NEVER logs passwords.
"""
import argparse
import csv
import sys

import openpyxl
from passlib.context import CryptContext

SHEET = "Аккаунты GSuite @polati.ru"
COL_EMAIL, COL_PASSWORD, COL_FIRED = 2, 3, 5
FIRED_VALUES = {"уволен", "true", "да", "1"}
BCRYPT_MAX_BYTES = 72

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def make_hash(password: str) -> str:
    return pwd_context.hash(password)


def _is_fired(val) -> bool:
    return val is not None and str(val).strip().lower() in FIRED_VALUES


def parse_registry(path: str, sheet_name: str = SHEET) -> dict:
    """email(lower) -> [(password, fired_bool), ...]."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet_name]
    out: dict = {}
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue  # header
        if len(row) <= COL_EMAIL or not row[COL_EMAIL]:
            continue
        email = str(row[COL_EMAIL]).strip().lower()
        pw = row[COL_PASSWORD] if len(row) > COL_PASSWORD else None
        pw = "" if pw is None else str(pw).strip()
        fired = _is_fired(row[COL_FIRED]) if len(row) > COL_FIRED else False
        out.setdefault(email, []).append((pw, fired))
    return out


def resolve(entries: list):
    """(password|None, fired_all_bool, status). status in {ok, empty, conflict}.
    Prefer non-fired entries with a non-empty password."""
    active = [(p, f) for p, f in entries if not f]
    fired_all = len(active) == 0
    pool = active if active else entries
    nonempty = sorted({p for p, _ in pool if p})
    if not nonempty:
        return None, fired_all, "empty"
    if len(nonempty) > 1:
        return None, fired_all, "conflict"
    return nonempty[0], fired_all, "ok"


def classify(db_emails: set, registry: dict):
    cats = {k: [] for k in ("settable", "fired", "empty", "norow", "conflict", "toolong")}
    pwmap: dict = {}
    for email in sorted(db_emails):
        if email not in registry:
            cats["norow"].append(email)
            continue
        pw, fired, status = resolve(registry[email])
        if fired:
            cats["fired"].append(email)
            continue
        if status == "empty":
            cats["empty"].append(email)
            continue
        if status == "conflict":
            cats["conflict"].append(email)
            continue
        if len(pw.encode("utf-8")) > BCRYPT_MAX_BYTES:
            cats["toolong"].append(email)
            continue
        cats["settable"].append(email)
        pwmap[email] = pw
    return cats, pwmap


def _mask(email: str) -> str:
    if "@" not in email:
        return email[:4] + "…"
    local, domain = email.split("@", 1)
    return local[:4] + "…@" + domain


def report(cats: dict) -> None:
    print("=== CLASSIFICATION ===")
    for k in ("settable", "fired", "empty", "norow", "conflict", "toolong"):
        print(f"  {k:9}: {len(cats[k])}")
    for k in ("fired", "empty", "norow", "conflict", "toolong"):
        if cats[k]:
            print(f"\n--- {k} ({len(cats[k])}) ---")
            for e in cats[k]:
                print("   ", _mask(e))


def apply_changes(pwmap: dict, dsn: str, log_path: str) -> int:
    import psycopg2
    conn = psycopg2.connect(dsn)
    updated = 0
    try:
        with conn, conn.cursor() as cur, open(log_path, "w", newline="") as logf:
            w = csv.writer(logf)
            w.writerow(["id", "email", "old_provider"])
            for email, pw in pwmap.items():
                cur.execute(
                    "UPDATE users SET password_hash=%s, auth_provider='local', "
                    "updated_at=now() WHERE lower(email)=%s AND auth_provider='google' "
                    "RETURNING id",
                    (make_hash(pw), email),
                )
                got = cur.fetchone()
                if got:
                    updated += 1
                    w.writerow([got[0], email, "google"])
    finally:
        conn.close()
    return updated


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", required=True)
    ap.add_argument("--emails", required=True, help="file: one lower(email) per line")
    ap.add_argument("--sheet", default=SHEET)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    ap.add_argument("--dsn")
    ap.add_argument("--log", default="apply_log.csv")
    a = ap.parse_args(argv)

    db_emails = {l.strip().lower() for l in open(a.emails) if l.strip()}
    registry = parse_registry(a.registry, a.sheet)
    cats, pwmap = classify(db_emails, registry)
    report(cats)
    print(f"\nsettable to apply: {len(pwmap)}")

    if a.apply:
        if not a.dsn:
            print("ERROR: --apply requires --dsn", file=sys.stderr)
            return 2
        n = apply_changes(pwmap, a.dsn, a.log)
        print(f"APPLIED: {n} rows updated; log -> {a.log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
