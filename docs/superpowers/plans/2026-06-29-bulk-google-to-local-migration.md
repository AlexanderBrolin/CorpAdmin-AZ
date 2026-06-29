# Bulk Google→local migration — Implementation Plan

> REQUIRED: superpowers:test-driven-development (Task 1). Prod write (Task 4) — STOP for explicit approval. Checkbox steps.

**Goal:** Проставить пароли 414 google-юзерам `@polati.ru` из реестра, `auth_provider google→local`, разгрузив саппорт. Безопасно: локальный bcrypt + SSH-туннель, идемпотентно, с бэкапом.

**Architecture:** standalone-скрипт `scripts/migrate_google_to_local.py` (чистые функции parse/resolve/classify + apply через psycopg2-туннель) + тесты `scripts/test_migrate_google_to_local.py`. Запуск — `/usr/bin/python3` (есть openpyxl/passlib/psycopg2 --user). Не трогает backend-venv.

**Spec:** `docs/superpowers/specs/2026-06-29-bulk-google-to-local-migration-design.md`
**Epic:** CorpAdmin-AZ-lmu
**Working tree:** worktree `feature/bulk-google-migration` в `.worktrees/bulk-migration`.

## Конвенции (подставляются буквально)

```bash
# Путь scratchpad этой сессии (вне git):
SCRATCH="/tmp/claude-1000/-home-brolin-Documents-ITSS-AdminAZWG-CorpAdmin-AZ/591980de-e902-4126-8fec-d5b828ba5d11/scratchpad"
# Пароль БД corpweb (взять из /opt/corpweb/backend/.env, НЕ коммитить, экспортнуть в шелл):
export PGPW='<DATABASE_URL password из прод .env>'
REG="docs/Реестр аккаунтов.xlsx"
WT="/home/brolin/Documents/ITSS/AdminAZWG/CorpAdmin-AZ/.worktrees/bulk-migration"
```

---

## File Structure

| File | Action |
|---|---|
| `scripts/migrate_google_to_local.py` | New — parse/resolve/classify + apply/dry-run CLI |
| `scripts/test_migrate_google_to_local.py` | New — unit-тесты чистых функций |

Реестр и снимок email'ов — в `$SCRATCH` (вне git). Скрипт секретов не содержит.

---

## Task 0: Worktree

- [ ] **0.1**
```bash
cd /home/brolin/Documents/ITSS/AdminAZWG/CorpAdmin-AZ
git worktree add -b feature/bulk-google-migration .worktrees/bulk-migration CorpAdmin
mkdir -p "$WT/scripts"
```
- [ ] **0.2** `bd update CorpAdmin-AZ-lmu --claim`

---

## Task 1: TDD чистых функций

### Step 1.1 — RED: написать `scripts/test_migrate_google_to_local.py`

```python
import openpyxl
import importlib.util, pathlib
spec = importlib.util.spec_from_file_location(
    "mig", pathlib.Path(__file__).parent / "migrate_google_to_local.py")
mig = importlib.util.module_from_spec(spec); spec.loader.exec_module(mig)


def _wb(tmp_path, rows):
    """rows: list of (email, password, fired) → xlsx with header on the right sheet."""
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = mig.SHEET
    ws.append(["ФИО", "Должность", "Аккаунт GSUite", "Пароль GSUite",
               "Пароль itscrm", "Уволен", "Объект", "Рюкзак"])
    for email, pw, fired in rows:
        ws.append(["fio", "pos", email, pw, "", fired, "obj", "False"])
    p = tmp_path / "reg.xlsx"; wb.save(p); return str(p)


def test_parse_maps_columns_and_lowercases_email(tmp_path):
    reg = mig.parse_registry(_wb(tmp_path, [("User@Polati.ru", "Pass1234", "")]))
    assert reg == {"user@polati.ru": [("Pass1234", False)]}

def test_parse_skips_rows_without_email(tmp_path):
    reg = mig.parse_registry(_wb(tmp_path, [(None, "x", ""), ("a@polati.ru", "y", "")]))
    assert list(reg) == ["a@polati.ru"]

def test_resolve_single(tmp_path):
    assert mig.resolve([("Pass1234", False)]) == ("Pass1234", False, "ok")

def test_resolve_duplicate_same_password_ok():
    assert mig.resolve([("Same1", False), ("Same1", False)]) == ("Same1", False, "ok")

def test_resolve_conflict():
    pw, fired, st = mig.resolve([("A1", False), ("B2", False)])
    assert pw is None and st == "conflict"

def test_resolve_prefers_active_over_fired():
    assert mig.resolve([("Old1", True), ("New2", False)]) == ("New2", False, "ok")

def test_resolve_all_fired_marks_fired():
    pw, fired, st = mig.resolve([("X1", True)])
    assert fired is True

def test_resolve_empty():
    assert mig.resolve([("", False)]) == (None, False, "empty")

def test_classify_settable(tmp_path):
    reg = mig.parse_registry(_wb(tmp_path, [("a@polati.ru", "Pass1234", "")]))
    cats, pwmap = mig.classify({"a@polati.ru"}, reg)
    assert cats["settable"] == ["a@polati.ru"] and pwmap["a@polati.ru"] == "Pass1234"

def test_classify_fired_skipped(tmp_path):
    reg = mig.parse_registry(_wb(tmp_path, [("a@polati.ru", "Pass1234", "Уволен")]))
    cats, pwmap = mig.classify({"a@polati.ru"}, reg)
    assert cats["fired"] == ["a@polati.ru"] and "a@polati.ru" not in pwmap

def test_classify_empty_and_norow(tmp_path):
    reg = mig.parse_registry(_wb(tmp_path, [("a@polati.ru", "", "")]))
    cats, _ = mig.classify({"a@polati.ru", "b@polati.ru"}, reg)
    assert cats["empty"] == ["a@polati.ru"] and cats["norow"] == ["b@polati.ru"]

def test_classify_conflict(tmp_path):
    reg = mig.parse_registry(_wb(tmp_path, [("a@polati.ru", "A1", ""), ("a@polati.ru", "B2", "")]))
    cats, _ = mig.classify({"a@polati.ru"}, reg)
    assert cats["conflict"] == ["a@polati.ru"]

def test_classify_toolong_skipped(tmp_path):
    long = "x" * 73
    reg = mig.parse_registry(_wb(tmp_path, [("a@polati.ru", long, "")]))
    cats, pwmap = mig.classify({"a@polati.ru"}, reg)
    assert cats["toolong"] == ["a@polati.ru"] and "a@polati.ru" not in pwmap

def test_make_hash_verifies():
    h = mig.make_hash("Pass1234")
    assert h.startswith("$2b$") and mig.pwd_context.verify("Pass1234", h)
```

### Step 1.2 — Verify RED
```bash
cd "$WT" && /usr/bin/python3 -m pytest scripts/test_migrate_google_to_local.py -v
```
Ожидание: FAIL — `migrate_google_to_local.py` ещё не существует (ModuleNotFound при exec).

### Step 1.3 — GREEN: написать `scripts/migrate_google_to_local.py`

```python
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
            cats["norow"].append(email); continue
        pw, fired, status = resolve(registry[email])
        if fired:
            cats["fired"].append(email); continue
        if status == "empty":
            cats["empty"].append(email); continue
        if status == "conflict":
            cats["conflict"].append(email); continue
        if len(pw.encode("utf-8")) > BCRYPT_MAX_BYTES:
            cats["toolong"].append(email); continue
        cats["settable"].append(email); pwmap[email] = pw
    return cats, pwmap


def _mask(email: str) -> str:
    local = email.split("@")[0]
    return (local[:4] + "…@" + email.split("@", 1)[1]) if "@" in email else email[:4] + "…"


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
            w = csv.writer(logf); w.writerow(["id", "email", "old_provider"])
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
            print("ERROR: --apply requires --dsn", file=sys.stderr); return 2
        n = apply_changes(pwmap, a.dsn, a.log)
        print(f"APPLIED: {n} rows updated; log -> {a.log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

### Step 1.4 — Verify GREEN
```bash
cd "$WT" && /usr/bin/python3 -m pytest scripts/test_migrate_google_to_local.py -v
```
Ожидание: все 14 тестов PASS.

### Step 1.5 — Commit
```bash
cd "$WT"
git add scripts/migrate_google_to_local.py scripts/test_migrate_google_to_local.py
git commit -m "feat(scripts): google->local registry migration parse/resolve/classify (CorpAdmin-AZ-lmu)"
```

---

## Task 2: Dry-run на проде (read-only) → отчёт

- [ ] **2.1** Снимок google-email'ов (read-only) в `$SCRATCH`:
```bash
ssh -p 2201 brolin@wgfi2.p4i.ru "PGPASSWORD='$PGPW' psql -U corpweb -h localhost -d corpweb_db -tAc \"SELECT lower(email) FROM users WHERE auth_provider='google'\"" > "$SCRATCH/google_emails.txt"
wc -l "$SCRATCH/google_emails.txt"   # ожидаем 442
```
- [ ] **2.2** Dry-run отчёт:
```bash
cd "$WT"
/usr/bin/python3 scripts/migrate_google_to_local.py --dry-run \
  --registry "$REG" --emails "$SCRATCH/google_emails.txt"
```
Ожидаем ~ settable 414, fired 18, empty 6, norow 4, conflict ? . **Показать пользователю, approval на apply.**

---

## Task 3: Бэкап прод users (data-only)

- [ ] **3.1**
```bash
ssh -p 2201 brolin@wgfi2.p4i.ru "PGPASSWORD='$PGPW' pg_dump -U corpweb -h localhost -d corpweb_db -t users --data-only -f /tmp/users_backup_lmu.sql && wc -l /tmp/users_backup_lmu.sql"
scp -P 2201 brolin@wgfi2.p4i.ru:/tmp/users_backup_lmu.sql "$SCRATCH/"
ssh -p 2201 brolin@wgfi2.p4i.ru 'rm -f /tmp/users_backup_lmu.sql'
ls -la "$SCRATCH/users_backup_lmu.sql"
```

---

## Task 4: Apply ⚠️ STATE-CHANGING PROD — approval

- [ ] **4.1** Открыть туннель (фоновый), запомнить PID:
```bash
ssh -fN -L 15432:localhost:5432 -p 2201 brolin@wgfi2.p4i.ru
TUNNEL_PID=$(pgrep -f '15432:localhost:5432')
```
- [ ] **4.2** Apply (идемпотентно, одна транзакция, лог для отката):
```bash
cd "$WT"
/usr/bin/python3 scripts/migrate_google_to_local.py --apply \
  --registry "$REG" --emails "$SCRATCH/google_emails.txt" \
  --dsn "postgresql://corpweb:$PGPW@localhost:15432/corpweb_db" \
  --log "$SCRATCH/apply_log.csv"
```
Ожидаем `APPLIED: 414 rows updated`. Лог `id,email,old_provider` в `$SCRATCH/apply_log.csv`.
- [ ] **4.3** Закрыть туннель: `kill "$TUNNEL_PID"`

---

## Task 5: Верификация

- [ ] **5.1** Счётчики (google должно упасть на ~414, local вырасти, total=502):
```bash
ssh -p 2201 brolin@wgfi2.p4i.ru "PGPASSWORD='$PGPW' psql -U corpweb -h localhost -d corpweb_db -c \"SELECT auth_provider, count(*) FROM users GROUP BY 1 ORDER BY 2 DESC\""
```
- [ ] **5.2** Spot-check: 3 случайных мигрированных юзера логинятся реальным паролем из реестра — через прод HTTP:
```bash
# взять 3 email из apply_log.csv, их пароль из dry-run классификации (локально),
# POST /api/v1/auth/login на проде → ожидаем 200 + token
```
(скрипт-помощник `--spotcheck` опционально; либо точечно вручную). Ожидаем 200.
- [ ] **5.3** Выдать пользователю: список 18 уволенных (на блокировку, снимет VPN-peer'ы), 10 manual (саппорт), конфликты (если есть) — из dry-run отчёта.

---

## Task 6: Финал
- [ ] **6.1** Review (`superpowers:requesting-code-review`) + merge `feature/bulk-google-migration → CorpAdmin`, push:
```bash
git push ssh://git@ssh.github.com:443/AlexanderBrolin/CorpAdmin-AZ.git CorpAdmin
```
- [ ] **6.2** `git worktree remove .worktrees/bulk-migration`
- [ ] **6.3** `bd close CorpAdmin-AZ-lmu`. При решении блокировать уволенных — отдельный bug + подтверждённый шаг.

---

## Self-review checklist
- **Concrete:** полный код скрипта и 14 тестов; команды без плейсхолдеров кроме `$PGPW` (секрет — намеренно через env, не в файле).
- **Spec coverage:** parse/resolve/classify → T1; dry-run+approval → T2; backup → T3; idempotent apply → T4; verify+lists → T5.
- **Security:** xlsx gitignored; пароли только локально; на прод плейнтекст не уходит (туннель); скрипт без секретов; `$PGPW` из env.
- **Idempotent:** `WHERE auth_provider='google'`.
- **Rollback:** `users_backup_lmu.sql` (T3) + `apply_log.csv` (T4.2).
- **Prod gate:** T4 — apply только после approved dry-run (T2).
