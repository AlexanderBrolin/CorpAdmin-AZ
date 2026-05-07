# Install-native CP fresh-install gaps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fresh CP installation via `corpweb/install-native.sh` succeeds on a clean Debian 12/13 VM without manual post-install steps (iptables/iptables-persistent installed, ip_forward enabled, alembic errors visible).

**Architecture:** Single PR `feature/6jl-install-native-fixes → CorpAdmin`, three logical groups in `corpweb/install-native.sh`: (1) iptables deps after Certbot block, (2) sysctl drop-in for ip_forward, (3) un-suppress alembic stderr. Plus shell-grep regression tests in new `tests/install/test_install_native.py`.

**Tech Stack:** bash (install-native.sh), pytest (regression tests).

**Beads:** epic CorpAdmin-AZ-6jl; sub-tasks 1oz (iptables), lpa (ip_forward), 9nq (alembic).

**Spec:** [docs/superpowers/specs/2026-05-07-install-native-fresh-cp-gaps.md](../specs/2026-05-07-install-native-fresh-cp-gaps.md)

---

## File Structure

- **Modify:** `corpweb/install-native.sh` (add iptables block, add sysctl block, remove `2>/dev/null` from alembic line)
- **Create:** `tests/install/test_install_native.py` (3 grep-based regression tests)
- **Create:** `tests/install/__init__.py` (empty, makes `tests/install` a package — even though pytest auto-discovers, the `__init__.py` keeps imports stable)

No backend code changes. No frontend changes. `init_db.py:24` (`Base.metadata.create_all`) is **out of scope** (see Spec / Non-goals).

---

## Task 1: Bootstrap test infrastructure (RED for 1oz)

**Files:**
- Create: `tests/install/__init__.py`
- Create: `tests/install/test_install_native.py`

- [ ] **Step 1: Create empty `tests/install/__init__.py`**

```bash
mkdir -p tests/install && touch tests/install/__init__.py
```

- [ ] **Step 2: Write the failing test for iptables install**

Create `tests/install/test_install_native.py`:

```python
"""Grep-based regression tests for corpweb/install-native.sh.

These do NOT execute the script; they only assert that required commands
are present in the script text. Manual verification on a clean Debian 13
VM is the real acceptance gate (see spec Acceptance #4).
"""
from __future__ import annotations

import pathlib

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "corpweb" / "install-native.sh"
TEXT = SCRIPT.read_text()


def test_script_exists_and_is_bash():
    assert SCRIPT.exists(), f"missing {SCRIPT}"
    assert TEXT.startswith("#!/"), "install-native.sh must start with shebang"


def test_installs_iptables_and_persistent():
    # CorpAdmin-AZ-1oz: balancer.py needs iptables binary; iptables-persistent
    # keeps DNAT rules across reboot; netfilter-persistent provides the save/restore.
    assert (
        "apt-get install -y -qq iptables iptables-persistent netfilter-persistent"
        in TEXT
    ), "iptables/iptables-persistent/netfilter-persistent install line missing"
```

- [ ] **Step 3: Run tests, verify RED**

Run: `pytest tests/install/test_install_native.py -v`
Expected: `test_script_exists_and_is_bash` PASS, `test_installs_iptables_and_persistent` FAIL with "iptables… install line missing".

- [ ] **Step 4: Commit RED**

```bash
git add tests/install/__init__.py tests/install/test_install_native.py
git commit -m "test(install): RED for iptables/iptables-persistent install (CorpAdmin-AZ-1oz)"
```

---

## Task 2: GREEN for 1oz — install iptables in install-native.sh

**Files:**
- Modify: `corpweb/install-native.sh` (insert block after Certbot install, around line 162)

- [ ] **Step 1: Locate Certbot block and add iptables block immediately after**

Current state ([corpweb/install-native.sh:154-162](../../../corpweb/install-native.sh#L154-L162)):

```bash
# Certbot — автоматический SSL от Let's Encrypt
if ! command -v certbot &> /dev/null; then
    print_info "Установка Certbot..."
    apt-get install -y -qq certbot python3-certbot-nginx > /dev/null
    print_success "Certbot установлен"
else
    print_success "Certbot уже установлен"
fi

# ── Шаг 2: Ввод параметров ───
```

Insert after the `fi` of the Certbot block, before the `# ── Шаг 2:` separator:

```bash
# iptables + iptables-persistent — для DNAT-балансировщика (52443→51443 и т.д.).
# Backend services/balancer.py:ensure_ports_reconciled пишет правила через `iptables`;
# netfilter-persistent сохраняет их при reboot. Без этих пакетов балансировщик молча
# no-op'ит (FileNotFoundError) → клиенты не подключаются.
if ! command -v iptables-save &> /dev/null; then
    print_info "Установка iptables и iptables-persistent..."
    apt-get install -y -qq iptables iptables-persistent netfilter-persistent > /dev/null
    print_success "iptables установлен"
else
    print_success "iptables уже установлен"
fi
```

Use the Edit tool to add this block. The marker `# ── Шаг 2: Ввод параметров ─` follows immediately after the new block.

- [ ] **Step 2: Verify the test now passes**

Run: `pytest tests/install/test_install_native.py::test_installs_iptables_and_persistent -v`
Expected: PASS.

- [ ] **Step 3: Bash syntax check**

Run: `bash -n corpweb/install-native.sh; echo "rc=$?"`
Expected: `rc=0`.

- [ ] **Step 4: Commit GREEN**

```bash
git add corpweb/install-native.sh
git commit -m "fix(install): install iptables + iptables-persistent (CorpAdmin-AZ-1oz)"
```

---

## Task 3: RED for lpa — ip_forward sysctl drop-in

**Files:**
- Modify: `tests/install/test_install_native.py` (add test)

- [ ] **Step 1: Append test for ip_forward sysctl**

Append to `tests/install/test_install_native.py`:

```python
def test_writes_ip_forward_sysctl_drop_in():
    # CorpAdmin-AZ-lpa: without net.ipv4.ip_forward=1 the kernel drops packets
    # on the FORWARD chain after DNAT, so the balancer rules look fine
    # (counters tick) but no traffic reaches the WG ifaces.
    assert "/etc/sysctl.d/99-corpweb-forwarding.conf" in TEXT, \
        "expected drop-in file path /etc/sysctl.d/99-corpweb-forwarding.conf"
    assert "net.ipv4.ip_forward=1" in TEXT, \
        "expected net.ipv4.ip_forward=1 directive"
    assert "sysctl --system" in TEXT, \
        "expected `sysctl --system` to apply the drop-in immediately"
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/install/test_install_native.py::test_writes_ip_forward_sysctl_drop_in -v`
Expected: FAIL with "expected drop-in file path …".

- [ ] **Step 3: Commit RED**

```bash
git add tests/install/test_install_native.py
git commit -m "test(install): RED for ip_forward sysctl drop-in (CorpAdmin-AZ-lpa)"
```

---

## Task 4: GREEN for lpa — write sysctl drop-in in install-native.sh

**Files:**
- Modify: `corpweb/install-native.sh` (extend the iptables block area with sysctl drop-in)

- [ ] **Step 1: Add sysctl block immediately after the iptables block (from Task 2)**

After the iptables block's `fi`, before `# ── Шаг 2: Ввод параметров ─`, append:

```bash
# net.ipv4.ip_forward — без него ядро дропает пакеты в FORWARD после DNAT.
# Drop-in в /etc/sysctl.d переживает upgrade /etc/sysctl.conf;
# `sysctl --system` применяет его в текущей сессии.
print_info "Включение net.ipv4.ip_forward..."
cat > /etc/sysctl.d/99-corpweb-forwarding.conf <<'EOF'
# Managed by corpweb/install-native.sh — CorpAdmin-AZ-lpa
net.ipv4.ip_forward=1
EOF
sysctl --system > /dev/null
print_success "net.ipv4.ip_forward=1 (persistent)"
```

Use the Edit tool. The single-quoted heredoc terminator `'EOF'` prevents bash from interpolating `$` in the file content.

- [ ] **Step 2: Verify test passes**

Run: `pytest tests/install/test_install_native.py::test_writes_ip_forward_sysctl_drop_in -v`
Expected: PASS.

- [ ] **Step 3: Bash syntax check**

Run: `bash -n corpweb/install-native.sh; echo "rc=$?"`
Expected: `rc=0`.

- [ ] **Step 4: Commit GREEN**

```bash
git add corpweb/install-native.sh
git commit -m "fix(install): enable net.ipv4.ip_forward via sysctl drop-in (CorpAdmin-AZ-lpa)"
```

---

## Task 5: RED for 9nq — alembic must not suppress stderr

**Files:**
- Modify: `tests/install/test_install_native.py` (add test)

- [ ] **Step 1: Append test**

Append to `tests/install/test_install_native.py`:

```python
def test_alembic_does_not_swallow_stderr():
    # CorpAdmin-AZ-9nq partial: hiding alembic stderr behind 2>/dev/null
    # masks real migration failures; install reports success while DB schema
    # is partially broken (missing pg_notify triggers → SSE/sync silently dies).
    lines = [l for l in TEXT.splitlines() if "alembic" in l and "upgrade head" in l]
    assert lines, "alembic upgrade head invocation missing from install-native.sh"
    for line in lines:
        assert "2>/dev/null" not in line, \
            f"alembic stderr is being swallowed: {line.strip()!r}"
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/install/test_install_native.py::test_alembic_does_not_swallow_stderr -v`
Expected: FAIL with "alembic stderr is being swallowed: '… 2>/dev/null …'".

- [ ] **Step 3: Commit RED**

```bash
git add tests/install/test_install_native.py
git commit -m "test(install): RED for alembic stderr suppression (CorpAdmin-AZ-9nq)"
```

---

## Task 6: GREEN for 9nq — un-suppress alembic stderr

**Files:**
- Modify: `corpweb/install-native.sh:363`

- [ ] **Step 1: Remove `2>/dev/null` from the alembic line**

Current ([install-native.sh:362-365](../../../corpweb/install-native.sh#L362-L365)):

```bash
print_info "Применение миграций БД..."
cd "$INSTALL_DIR/backend"
"$INSTALL_DIR/backend/venv/bin/alembic" upgrade head 2>/dev/null || \
    print_warning "Alembic: часть миграций уже применена"
print_success "Миграции применены"
```

Change line 363 to remove `2>/dev/null`:

```bash
"$INSTALL_DIR/backend/venv/bin/alembic" upgrade head || \
    print_warning "Alembic: часть миграций уже применена (или произошла ошибка — см. вывод выше)"
```

The trailing `|| print_warning …` keeps install going on non-zero exit (idempotency on already-migrated DB), but stderr now flows to the user. Updating the warning message clarifies that it's not unconditionally benign.

Use the Edit tool with the full line as `old_string` to keep it unique.

- [ ] **Step 2: Verify test passes**

Run: `pytest tests/install/test_install_native.py::test_alembic_does_not_swallow_stderr -v`
Expected: PASS.

- [ ] **Step 3: Bash syntax check**

Run: `bash -n corpweb/install-native.sh; echo "rc=$?"`
Expected: `rc=0`.

- [ ] **Step 4: Commit GREEN**

```bash
git add corpweb/install-native.sh
git commit -m "fix(install): stop swallowing alembic stderr (CorpAdmin-AZ-9nq)"
```

---

## Task 7: Full regression sweep + final verification

**Files:** none modified.

- [ ] **Step 1: Run install tests**

Run: `pytest tests/install/ -v`
Expected: 4 tests pass (1 sanity + 3 regression).

- [ ] **Step 2: Run backend regression**

Run: `cd corpweb/backend && python3 -m pytest -q && cd ../..`
Expected: 350 passed.

- [ ] **Step 3: Run agent regression**

Run: `cd agent && python3 -m pytest -q && cd ..`
Expected: 112 passed.

- [ ] **Step 4: Final shellcheck-style verification**

Run: `bash -n corpweb/install-native.sh && shellcheck corpweb/install-native.sh 2>&1 | head -30 || true; echo "syntax ok"`

Expected: `syntax ok` (shellcheck may or may not be installed; absence is OK, only `bash -n` is mandatory).

- [ ] **Step 5: Diff summary**

Run: `git diff --stat origin/CorpAdmin..HEAD`

Expected:
- `corpweb/install-native.sh` ~+15 lines
- `tests/install/__init__.py` new
- `tests/install/test_install_native.py` new

- [ ] **Step 6: Push branch**

```bash
git push -u origin worktree-feature+6jl-install-native-fixes:feature/6jl-install-native-fixes
```

- [ ] **Step 7: Open PR**

```bash
gh pr create --base CorpAdmin --head feature/6jl-install-native-fixes \
  --title "fix(install): iptables + ip_forward + un-suppress alembic stderr (CorpAdmin-AZ-6jl)" \
  --body "$(cat <<'EOF'
## Summary
- 1oz: install iptables + iptables-persistent + netfilter-persistent (balancer DNAT, persists across reboot).
- lpa: write /etc/sysctl.d/99-corpweb-forwarding.conf with net.ipv4.ip_forward=1, apply via sysctl --system.
- 9nq partial: stop swallowing alembic stderr behind 2>/dev/null. Real migration errors now visible to operator.

Out of scope: removing Base.metadata.create_all() from init_db.py (separate issue, requires alembic-coverage validation).

## Test plan
- [x] tests/install/test_install_native.py: 4 passed (1 sanity, 3 regression)
- [x] Backend regression: 350 passed
- [x] Agent regression: 112 passed
- [x] bash -n install-native.sh: rc=0
- [ ] Manual verification on a clean Debian 13 VM (acceptance #4 — done at the next CP install).

Closes CorpAdmin-AZ-1oz, CorpAdmin-AZ-lpa, CorpAdmin-AZ-9nq.
Closes CorpAdmin-AZ-6jl.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review

**Spec coverage:**
- Defect 1 (1oz iptables) — Tasks 1, 2.
- Defect 2 (lpa ip_forward) — Tasks 3, 4.
- Defect 3 (9nq alembic noisy errors) — Tasks 5, 6.
- Defect 3b (`Base.metadata.create_all` removal) — explicitly out-of-scope per spec, no task.
- Goal 1 (fresh install works without manual steps) — Tasks 2, 4 directly; manual verification per acceptance #4.
- Goal 2 (DNAT survives reboot) — Task 2 (`iptables-persistent`).
- Goal 3 (alembic errors visible) — Task 6.
- Goal 4 (idempotency) — preserved by `if ! command -v …` guard pattern (Task 2) and `cat > …` (sysctl drop-in is overwritten safely on re-run; Task 4); covered by acceptance #5 in spec, manually verifiable.
- Acceptance #1 (3 RED tests before fix) — Tasks 1, 3, 5 are RED; 2, 4, 6 are GREEN.
- Acceptance #2 (backend regression) — Task 7 step 2.
- Acceptance #3 (`bash -n` clean) — Tasks 2, 4, 6 each have a syntax check.
- Acceptance #4 (manual VM verification) — explicitly noted as out-of-CI; PR description flags as remaining.
- Acceptance #5 (idempotency on re-run) — same as Goal 4 above.

**Placeholder scan:** no TBD/TODO/"add validation"; every step has concrete code or command. Test bodies are written out, not "similar to above".

**Type/name consistency:** test names match across tasks; the `99-corpweb-forwarding.conf` filename is stable; all bash variable refs (`$INSTALL_DIR`) match what the script already uses.
