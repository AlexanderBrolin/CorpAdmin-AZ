# Bulk Google→local migration — Implementation Plan

> REQUIRED: superpowers:test-driven-development (Task 1). Prod write (Task 4) — STOP for explicit approval. Checkbox steps.

**Goal:** Проставить пароли 414 google-юзерам `@polati.ru` из реестра, `auth_provider google→local`, разгрузив саппорт. Безопасно: локальный bcrypt + SSH-туннель, идемпотентно, с бэкапом.

**Architecture:** standalone-скрипт `scripts/migrate_google_to_local.py` (чистые функции parse/resolve/classify + apply через psycopg2-туннель) + тесты `scripts/test_migrate_google_to_local.py`. Запуск — `/usr/bin/python3` (есть openpyxl/passlib/psycopg2 --user). Не трогает backend-venv.

**Spec:** `docs/superpowers/specs/2026-06-29-bulk-google-to-local-migration-design.md`
**Epic:** CorpAdmin-AZ-lmu
**Working tree:** worktree `feature/bulk-google-migration` в `.worktrees/bulk-migration`.

---

## File Structure

| File | Action |
|---|---|
| `scripts/migrate_google_to_local.py` | New — parse/resolve/classify + apply/dry-run CLI |
| `scripts/test_migrate_google_to_local.py` | New — unit-тесты чистых функций |

Реестр и снимок email'ов — вне git (scratchpad). Скрипт секретов не содержит.

---

## Task 0: Worktree
- [ ] `git worktree add -b feature/bulk-google-migration .worktrees/bulk-migration CorpAdmin`
- [ ] `bd update CorpAdmin-AZ-lmu --claim`

## Task 1: TDD чистых функций

**Контракты:**
```python
def parse_registry(path, sheet_name) -> dict[str, list[tuple[str, bool]]]:
    """email(lower) -> [(password, fired_bool), ...] из колонок 2/3/5."""
def resolve(entries) -> tuple[str | None, bool]:
    """Дедуп: вернуть (password|None, fired). Предпочесть не-fired с непустым pw.
       Конфликт (≥2 разных непустых pw среди не-fired) -> (None, fired) + пометка."""
CATEGORIES = settable | fired | empty | norow | conflict
def classify(db_emails: set[str], registry: dict) -> dict[str, list[str]]:
    """Раскидать db_emails по категориям. settable = матч, не уволен, pw непустой, не конфликт."""
```
Колонка «Уволен» -> fired=True, если значение в {уволен,true,да,1} (lower).

- [ ] **1.1 RED** — `scripts/test_migrate_google_to_local.py`. Тесты строят in-memory workbook через `openpyxl.Workbook()` и пишут во временный файл (`tmp_path`):
  - `test_parse_maps_columns_and_lowercases_email`
  - `test_parse_skips_rows_without_email`
  - `test_resolve_single_password`
  - `test_resolve_duplicate_same_password_ok`
  - `test_resolve_conflict_returns_none` (2 разных непустых pw, оба не-fired)
  - `test_resolve_prefers_active_over_fired`
  - `test_classify_settable_active`
  - `test_classify_fired_skipped`
  - `test_classify_empty_password_skipped`
  - `test_classify_norow_skipped`
  - `test_classify_long_password_over_72_bytes_skipped`
- [ ] **1.2 Verify RED:** `/usr/bin/python3 -m pytest scripts/test_migrate_google_to_local.py -v` → fail (ImportError/нет функций).
- [ ] **1.3 GREEN** — реализовать `parse_registry`, `resolve`, `classify`, `make_hash` (passlib bcrypt), `apply` (psycopg2, idempotent SQL) в `scripts/migrate_google_to_local.py`. CLI: `--dry-run` (только классификация+отчёт) / `--apply` (запись), `--registry PATH`, `--emails PATH`, `--dsn`.
- [ ] **1.4 Verify GREEN:** все юнит-тесты pass.
- [ ] **1.5 Commit:** `feat(scripts): google->local registry migration (parse/resolve/classify) (CorpAdmin-AZ-lmu)`

## Task 2: Dry-run на проде (read-only) → отчёт

- [ ] **2.1** Снимок google email'ов (read-only SELECT) в scratchpad:
```bash
ssh -p 2201 brolin@wgfi2.p4i.ru 'export PGPASSWORD=…; psql -U corpweb -h localhost -d corpweb_db -tAc "SELECT lower(email) FROM users WHERE auth_provider='\''google'\''"' > $SCRATCH/google_emails.txt
```
- [ ] **2.2** Dry-run:
```bash
/usr/bin/python3 scripts/migrate_google_to_local.py --dry-run \
  --registry "docs/Реестр аккаунтов.xlsx" --emails $SCRATCH/google_emails.txt
```
Отчёт: счётчики по категориям + списки (fired 18 / conflict / empty / norow) — emails маскированы. **Показать пользователю, получить approval на apply.**

## Task 3: Бэкап прод users

- [ ] **3.1**
```bash
ssh -p 2201 brolin@wgfi2.p4i.ru 'export PGPASSWORD=…; pg_dump -U corpweb -h localhost -d corpweb_db -t users --data-only > /tmp/users_backup_406fz.sql && wc -l /tmp/users_backup_406fz.sql'
scp -P 2201 brolin@wgfi2.p4i.ru:/tmp/users_backup_406fz.sql $SCRATCH/ && ssh -p 2201 brolin@wgfi2.p4i.ru 'rm -f /tmp/users_backup_406fz.sql'
```
Бэкап лежит локально в scratchpad (вне git).

## Task 4: Apply ⚠️ STATE-CHANGING PROD — approval

- [ ] **4.1** Открыть туннель: `ssh -fN -L 15432:localhost:5432 -p 2201 brolin@wgfi2.p4i.ru`
- [ ] **4.2** Apply (идемпотентно, одна транзакция):
```bash
/usr/bin/python3 scripts/migrate_google_to_local.py --apply \
  --registry "docs/Реестр аккаунтов.xlsx" --emails $SCRATCH/google_emails.txt \
  --dsn "postgresql://corpweb:PWD@localhost:15432/corpweb_db"
```
Скрипт пишет лог `id,old_provider` в `$SCRATCH/apply_log.csv` (для отката), печатает кол-во обновлённых строк (ожидаем ~414).
- [ ] **4.3** Закрыть туннель.

## Task 5: Верификация

- [ ] **5.1** Счётчики на проде:
```bash
# google должно упасть на ~414; local вырасти; total=502
psql … -c "SELECT auth_provider, count(*) FROM users GROUP BY 1;"
```
- [ ] **5.2** Spot-check: для 3-5 случайных мигрированных — `verify_password(plaintext_из_реестра, password_hash)` через прод-venv (или локально по выгруженному хэшу). Ожидаем True.
- [ ] **5.3** Выдать пользователю списки: 18 уволенных (на блокировку), 10 manual (саппорт), конфликты (если есть).

## Task 6: Финал
- [ ] **6.1** Review + merge `feature/bulk-google-migration → CorpAdmin`, push (через ssh.github.com:443).
- [ ] **6.2** `git worktree remove`.
- [ ] **6.3** `bd close CorpAdmin-AZ-lmu`. Отдельный bug на блокировку 18 уволенных (если решим делать).

---

## Self-review checklist
- **Spec coverage:** parse/resolve/classify → Task 1; dry-run+approval → Task 2; backup → Task 3; idempotent apply → Task 4; verify+lists → Task 5.
- **Security:** xlsx gitignored; пароли только локально; на прод плейнтекст не уходит (туннель); скрипт без секретов.
- **Idempotent:** `WHERE auth_provider='google'` — повторный прогон безопасен.
- **Rollback:** pg_dump users (Task 3) + apply_log.csv (Task 4.2).
- **Prod gate:** Task 4 помечен, apply только после approved dry-run.
