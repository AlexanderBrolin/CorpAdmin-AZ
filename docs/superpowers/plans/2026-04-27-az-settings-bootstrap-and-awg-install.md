# AZ Settings Bootstrap + AWG Install Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `PATCH /api/v1/antizapret/settings` 500 by seeding default `setup` + empty `config/*.txt` into blob store at backend startup, and provision `amneziawg-{dkms,tools}` automatically when a new node runs the agent install-script.

**Architecture:** Backend lifespan calls a new idempotent `AntizapretService.bootstrap_blob_store()` that writes a default 43-line setup template (loaded from a packaged data file) plus 9 empty editable config files into `wg_file_state` only when each row is missing. The agent install-script gets a new shell block that adds the Amnezia PPA (Ubuntu noble channel, fingerprint `75C9DD72…57290828`) and installs `amneziawg-dkms` + `amneziawg-tools` only when `awg-quick` is not already on the host.

**Tech Stack:** Python 3.13, FastAPI lifespan, SQLAlchemy + WgBlobStore (PostgreSQL via SQLite-shim in tests), pytest, bash (rendered install-script), DKMS, Amnezia PPA.

**Beads epic:** CorpAdmin-AZ-e5r
**Spec:** [docs/superpowers/specs/2026-04-27-az-settings-bootstrap-and-awg-install-design.md](../specs/2026-04-27-az-settings-bootstrap-and-awg-install-design.md)
**Branch:** `feature/e5r-bootstrap-awg-install` (already created, spec already committed)

---

## File Structure

| File | Action | Purpose |
|---|---|---|
| `corpweb/backend/app/services/antizapret_default_setup.txt` | **Create** | Packaged data resource: 43-line default setup with all upstream keys, deployment-specific values blank. |
| `corpweb/backend/app/services/antizapret.py` | **Modify** | Add `_load_default_setup()` loader and `AntizapretService.bootstrap_blob_store()` method. |
| `corpweb/backend/app/main.py` | **Modify** | Inside `lifespan()`, call `AntizapretService(db).bootstrap_blob_store()` right after `vpn_manager.bootstrap(db)`, wrapped in its own try/except (non-fatal). |
| `corpweb/backend/app/api/v1/agent.py` | **Modify** | In `_render_install_script()`, insert amneziawg-install block before the existing `python3 -c "import requests"` line. |
| `corpweb/backend/tests/test_antizapret_bootstrap.py` | **Create** | 5 tests: seed setup, seed config, idempotency × 2, end-to-end PATCH after bootstrap. |
| `corpweb/backend/tests/test_install_script.py` | **Create** | 3 tests: install-script contains amneziawg block / noble repo / `systemctl enable`. |
| `corpweb/backend/tests/test_main_lifespan.py` | **Modify** | Add 1 test: `lifespan()` calls `AntizapretService.bootstrap_blob_store()`. |

---

## Task 1: Create the default-setup packaged data file

**Files:**
- Create: `corpweb/backend/app/services/antizapret_default_setup.txt`

This is the static data resource consumed by `bootstrap_blob_store()`. It mirrors the 43-line file that upstream antizapret install writes to `/root/antizapret/setup`. All deployment-specific values are blank; booleans/numerics carry safe upstream defaults.

- [ ] **Step 1: Write the file**

Create `corpweb/backend/app/services/antizapret_default_setup.txt` with **exactly** this content (no trailing whitespace on lines, single trailing newline):

```
SETUP_DATE=
OPENVPN_PATCH=1
OPENVPN_DCO=y
ANTIZAPRET_WARP=n
VPN_WARP=n
ANTIZAPRET_DNS=1
VPN_DNS=1
BLOCK_ADS=y
ALTERNATIVE_CLIENT_IP=n
ALTERNATIVE_FAKE_IP=n
OPENVPN_BACKUP_TCP=n
OPENVPN_BACKUP_UDP=n
WIREGUARD_BACKUP=y
OPENVPN_DUPLICATE=n
OPENVPN_LOG=n
SSH_PROTECTION=y
ATTACK_PROTECTION=y
TORRENT_GUARD=y
RESTRICT_FORWARD=y
CLIENT_ISOLATION=y
OPENVPN_HOST=
WIREGUARD_HOST=
ROUTE_ALL=n
DISCORD_INCLUDE=y
CLOUDFLARE_INCLUDE=y
TELEGRAM_INCLUDE=y
WHATSAPP_INCLUDE=y
ROBLOX_INCLUDE=y
AMAZON_INCLUDE=
HETZNER_INCLUDE=
DIGITALOCEAN_INCLUDE=
OVH_INCLUDE=
GOOGLE_INCLUDE=
AKAMAI_INCLUDE=
CLEAR_HOSTS=y
DEFAULT_INTERFACE=
DEFAULT_IP=
ANTIZAPRET_OUT_INTERFACE=
ANTIZAPRET_OUT_IP=
VPN_OUT_INTERFACE=
VPN_OUT_IP=
CLIENT_IP=
FAKE_IP=
```

- [ ] **Step 2: Verify line count and that it parses**

Run from `corpweb/backend`:
```bash
wc -l app/services/antizapret_default_setup.txt
```
Expected: `42 app/services/antizapret_default_setup.txt` (42 newlines = 43 lines including the final partial line). If `wc -l` reports something else, your editor stripped or added a newline — fix and re-run.

- [ ] **Step 3: Commit**

```bash
git add corpweb/backend/app/services/antizapret_default_setup.txt
git commit -m "feat(antizapret): add default setup template (CorpAdmin-AZ-e5r)"
```

---

## Task 2: Add `bootstrap_blob_store()` to `AntizapretService` (TDD)

**Files:**
- Test: `corpweb/backend/tests/test_antizapret_bootstrap.py` (create)
- Modify: `corpweb/backend/app/services/antizapret.py`

### Sub-task 2a: RED — write all five failing tests

- [ ] **Step 1: Write the test file**

Create `corpweb/backend/tests/test_antizapret_bootstrap.py` with this exact content:

```python
"""
Tests for AntizapretService.bootstrap_blob_store() — seeds default setup +
empty config files into wg_file_state on a fresh CP and remains idempotent
across restarts.
"""
from app.services.antizapret import (
    AntizapretService,
    EDITABLE_FILES,
    ANTIZAPRET_SETUP_FILE,
    ALL_KNOWN_SETTINGS,
)
from app.services.wg_blob_store import WgBlobStore


def test_bootstrap_seeds_setup_when_blob_empty(db):
    svc = AntizapretService(db)
    svc.bootstrap_blob_store()

    raw = WgBlobStore(db).get(ANTIZAPRET_SETUP_FILE)
    assert raw is not None, "setup blob must be seeded"
    text = raw.decode()
    # All managed keys must be present in the seeded setup
    for key in ALL_KNOWN_SETTINGS:
        assert f"{key}=" in text, f"missing {key} in default setup"


def test_bootstrap_seeds_empty_config_files(db):
    svc = AntizapretService(db)
    svc.bootstrap_blob_store()

    store = WgBlobStore(db)
    for path in EDITABLE_FILES.values():
        assert store.get(path) == b"", f"{path} must be seeded as empty"


def test_bootstrap_idempotent_preserves_existing_setup(db):
    custom = b"WIREGUARD_HOST=admin-set.example.com\nROUTE_ALL=y\n"
    WgBlobStore(db).put(ANTIZAPRET_SETUP_FILE, custom, by="admin")

    AntizapretService(db).bootstrap_blob_store()

    assert WgBlobStore(db).get(ANTIZAPRET_SETUP_FILE) == custom


def test_bootstrap_idempotent_preserves_existing_config(db):
    path = EDITABLE_FILES["include_hosts"]
    custom = b"my-managed-host.example.com\n"
    WgBlobStore(db).put(path, custom, by="admin")

    AntizapretService(db).bootstrap_blob_store()

    assert WgBlobStore(db).get(path) == custom


def test_patch_settings_works_after_bootstrap(db):
    svc = AntizapretService(db)
    svc.bootstrap_blob_store()

    changed = svc.update_settings({"BLOCK_ADS": "n"})
    assert changed == 1
    assert svc.get_settings()["BLOCK_ADS"] == "n"
```

- [ ] **Step 2: Run tests to verify they fail**

Run from `corpweb/backend`:
```bash
pytest tests/test_antizapret_bootstrap.py -v
```
Expected: all 5 tests FAIL with `AttributeError: 'AntizapretService' object has no attribute 'bootstrap_blob_store'` (or `ImportError` if `ANTIZAPRET_SETUP_FILE`/`ALL_KNOWN_SETTINGS` aren't exported — both ARE exported per [services/antizapret.py:16,67](../../../corpweb/backend/app/services/antizapret.py#L16)).

### Sub-task 2b: GREEN — implement the method

- [ ] **Step 3: Add the loader and method**

Open `corpweb/backend/app/services/antizapret.py`. Right above the `class AntizapretServiceError` declaration (currently line 70), insert:

```python
def _load_default_setup() -> bytes:
    """Load the packaged default setup template. Lazy — called from bootstrap."""
    from importlib.resources import files
    return files("app.services").joinpath("antizapret_default_setup.txt").read_bytes()
```

Then inside the `AntizapretService` class, after the `__init__` method (currently line 78), add:

```python
    def bootstrap_blob_store(self) -> None:
        """
        Seed default setup + empty config files into blob store if missing.
        Idempotent — never overwrites an existing blob.
        """
        if self._store.get(ANTIZAPRET_SETUP_FILE) is None:
            self._store.put(ANTIZAPRET_SETUP_FILE, _load_default_setup(), by="bootstrap")
            logger.info("Seeded default %s into blob store", ANTIZAPRET_SETUP_FILE)
        for path in EDITABLE_FILES.values():
            if self._store.get(path) is None:
                self._store.put(path, b"", by="bootstrap")
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
pytest tests/test_antizapret_bootstrap.py -v
```
Expected: all 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add corpweb/backend/app/services/antizapret.py corpweb/backend/tests/test_antizapret_bootstrap.py
git commit -m "feat(antizapret): bootstrap_blob_store seeds default setup + config (CorpAdmin-AZ-e5r)"
```

---

## Task 3: Wire `bootstrap_blob_store()` into FastAPI lifespan (TDD)

**Files:**
- Modify: `corpweb/backend/tests/test_main_lifespan.py`
- Modify: `corpweb/backend/app/main.py`

### Sub-task 3a: RED — add lifespan test

- [ ] **Step 1: Add the failing test**

Open `corpweb/backend/tests/test_main_lifespan.py`. Inside class `TestLifespanBootstrap`, append at the end of the class (after `test_bootstrap_failure_does_not_block_reconcile`):

```python
    def test_antizapret_bootstrap_called_on_startup(self, db):
        """lifespan must call AntizapretService.bootstrap_blob_store after vpn_manager.bootstrap."""
        with patch(
            "app.services.vpn_manager_new.vpn_manager.bootstrap"
        ), patch(
            "app.services.balancer.ensure_ports_reconciled"
        ), patch(
            "app.services.antizapret.AntizapretService.bootstrap_blob_store"
        ) as m_az_boot:
            with TestClient(app):
                pass
            assert m_az_boot.called, (
                "lifespan must call AntizapretService.bootstrap_blob_store"
            )

    def test_antizapret_bootstrap_failure_does_not_block_reconcile(self, db):
        """If antizapret bootstrap raises, ensure_ports_reconciled still runs."""
        called: dict[str, bool] = {"reconcile": False}

        def _reconcile(_db):
            called["reconcile"] = True

        with patch(
            "app.services.vpn_manager_new.vpn_manager.bootstrap"
        ), patch(
            "app.services.balancer.ensure_ports_reconciled",
            side_effect=_reconcile,
        ), patch(
            "app.services.antizapret.AntizapretService.bootstrap_blob_store",
            side_effect=RuntimeError("boom"),
        ):
            with TestClient(app):
                pass

        assert called["reconcile"], (
            "ensure_ports_reconciled must still run when antizapret bootstrap raises"
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run from `corpweb/backend`:
```bash
pytest tests/test_main_lifespan.py -v
```
Expected: 2 new tests FAIL with `AssertionError: lifespan must call AntizapretService.bootstrap_blob_store` (the second test will fail because `RuntimeError("boom")` will propagate up and `ensure_ports_reconciled` won't run yet — there's no try/except around the antizapret call).

### Sub-task 3b: GREEN — wire lifespan

- [ ] **Step 3: Modify lifespan**

Open `corpweb/backend/app/main.py`. Find the existing `vpn_manager.bootstrap(db)` block (around lines 40-45):

```python
        try:
            vpn_manager.bootstrap(db)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "vpn_manager.bootstrap on startup failed (non-fatal): %s", exc
            )
```

**Right after** that block (and before the existing `try: ensure_ports_reconciled(db)` block), insert:

```python
        try:
            from app.services.antizapret import AntizapretService
            AntizapretService(db).bootstrap_blob_store()
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "antizapret bootstrap on startup failed (non-fatal): %s", exc
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
pytest tests/test_main_lifespan.py -v
```
Expected: all tests in this file PASS (existing 3 + new 2 = 5).

- [ ] **Step 5: Commit**

```bash
git add corpweb/backend/app/main.py corpweb/backend/tests/test_main_lifespan.py
git commit -m "feat(main): call antizapret bootstrap in lifespan (CorpAdmin-AZ-e5r)"
```

---

## Task 4: Add amneziawg install block to agent install-script (TDD)

**Files:**
- Test: `corpweb/backend/tests/test_install_script.py` (create)
- Modify: `corpweb/backend/app/api/v1/agent.py`

### Sub-task 4a: RED — write failing tests

- [ ] **Step 1: Write the test file**

Create `corpweb/backend/tests/test_install_script.py` with this exact content:

```python
"""
Tests for the install-script renderer in app.api.v1.agent.

The script is generated per-node by _render_install_script(cp_url, token,
hostname). It must include an amneziawg-tools install block so escape ifaces
work out of the box on freshly enrolled nodes.
"""
from app.api.v1.agent import _render_install_script


def _render() -> str:
    return _render_install_script(
        cp_url="https://example.org",
        token="t-1234567890",
        hostname="node-test01",
    )


def test_install_script_contains_amneziawg_block():
    script = _render()
    assert "command -v awg-quick" in script
    assert "75C9DD72C799870E310542E24166F2C257290828" in script
    assert "amneziawg-dkms" in script
    assert "amneziawg-tools" in script
    assert "signed-by=/usr/share/keyrings/amnezia-ppa.gpg" in script


def test_install_script_uses_noble_repo():
    script = _render()
    assert "ppa.launchpadcontent.net/amnezia/ppa/ubuntu noble main" in script


def test_install_script_enables_awg_quick_units():
    script = _render()
    assert "systemctl enable awg-quick@az_escape awg-quick@vpn_escape" in script
```

- [ ] **Step 2: Run tests to verify they fail**

Run from `corpweb/backend`:
```bash
pytest tests/test_install_script.py -v
```
Expected: all 3 tests FAIL with `AssertionError` on the first missing string check.

### Sub-task 4b: GREEN — extend install-script

- [ ] **Step 3: Edit `_render_install_script`**

Open `corpweb/backend/app/api/v1/agent.py`. Find the line in `_render_install_script` that reads:

```bash
# Install Python requests if missing
python3 -c "import requests" 2>/dev/null || pip3 install requests
```

(currently around line 280). **Insert** the following block **immediately before** that line (still inside the f-string body):

```bash
# Install amneziawg (required for escape ifaces). Idempotent.
if ! command -v awg-quick >/dev/null 2>&1; then
    echo "==> Installing amneziawg from Amnezia PPA"
    apt-get install -y gnupg dirmngr curl
    curl -fsSL "https://keyserver.ubuntu.com/pks/lookup?op=get&search=0x75C9DD72C799870E310542E24166F2C257290828" \\
        | gpg --dearmor > /usr/share/keyrings/amnezia-ppa.gpg
    chmod 644 /usr/share/keyrings/amnezia-ppa.gpg
    echo "deb [signed-by=/usr/share/keyrings/amnezia-ppa.gpg] https://ppa.launchpadcontent.net/amnezia/ppa/ubuntu noble main" \\
        > /etc/apt/sources.list.d/amnezia.list
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y amneziawg-dkms amneziawg-tools
fi

# Ensure escape units start at boot once their .conf files arrive.
systemctl enable awg-quick@az_escape awg-quick@vpn_escape 2>/dev/null || true

```

**Important:** This block lives inside an f-string (the `f'''…'''` body of `_render_install_script`). Backslashes are escaped as `\\` so the rendered shell sees a single `\` (line continuation). No `{...}` placeholders are used in this block, so no f-string-escaping needed for braces.

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
pytest tests/test_install_script.py -v
```
Expected: all 3 PASS.

- [ ] **Step 5: Sanity-render the script and eyeball it**

Run:
```bash
cd corpweb/backend
python3 -c "
from app.api.v1.agent import _render_install_script
print(_render_install_script('https://example.org', 't-XXX', 'node-test'))
" | grep -A 14 "Installing amneziawg"
```
Expected output: the 14-line block with proper line-continuation backslashes (single `\`, not `\\`), no `{...}` artefacts. If you see `\\` in the rendered output — your f-string escaping is wrong, fix it.

- [ ] **Step 6: Commit**

```bash
git add corpweb/backend/app/api/v1/agent.py corpweb/backend/tests/test_install_script.py
git commit -m "feat(agent): install amneziawg from Amnezia PPA on agent setup (CorpAdmin-AZ-e5r)"
```

---

## Task 5: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend test suite**

From `corpweb/backend`:
```bash
pytest -x -q
```
Expected: all tests pass. Pay attention to test count — should be at least `previous_count + 5 + 2 + 3 = previous_count + 10` new tests (5 from `test_antizapret_bootstrap.py`, 2 from `test_main_lifespan.py`, 3 from `test_install_script.py`).

If anything pre-existing breaks: stop, investigate, do NOT commit a "fix" that touches code outside this plan. The plan changes are tightly scoped — a regression elsewhere likely means we accidentally changed something we shouldn't have.

- [ ] **Step 2: Verify the data file is loadable via `importlib.resources`**

```bash
cd corpweb/backend
python3 - <<'PY'
from app.services.antizapret import _load_default_setup
data = _load_default_setup()
print("len=", len(data), "lines=", data.count(b"\n"))
assert b"WIREGUARD_HOST=" in data
assert b"BLOCK_ADS=y" in data
print("OK")
PY
```

Expected: `len=683 lines=42` (or thereabouts — exact byte count depends on if your editor added BOM; line count must be 42), then `OK`.

If `_load_default_setup()` raises `FileNotFoundError`: the data file isn't being found by `importlib.resources`. Most likely cause: missing `__init__.py` in `app/services/` (it should already exist — verify with `ls corpweb/backend/app/services/__init__.py`). If file IS there but loader fails — check that the build/install configuration (`pyproject.toml` or `setup.py`) ships `*.txt` files in `app.services`. The current backend has no setup files — runs from source — so this should "just work".

- [ ] **Step 3: Local smoke test of bootstrap**

```bash
cd corpweb/backend
python3 - <<'PY'
import os
os.environ.setdefault("DATABASE_URL", "sqlite:///./smoke.db")
os.environ.setdefault("SECRET_KEY", "x" * 64)

from app.db.base import Base
from app.db.session import engine, SessionLocal

# Patch PG-only types like conftest does
from sqlalchemy import Text, types
from sqlalchemy.dialects.postgresql import JSONB
class SQLiteJSON(types.TypeDecorator):
    impl = Text
    cache_ok = True
for t in Base.metadata.tables.values():
    for col in t.columns:
        if isinstance(col.type, JSONB):
            col.type = SQLiteJSON()

Base.metadata.create_all(engine)
db = SessionLocal()
try:
    from app.services.antizapret import AntizapretService, ANTIZAPRET_SETUP_FILE
    from app.services.wg_blob_store import WgBlobStore

    svc = AntizapretService(db)
    svc.bootstrap_blob_store()

    raw = WgBlobStore(db).get(ANTIZAPRET_SETUP_FILE)
    print("setup len:", len(raw))
    print("settings sample:", svc.get_settings()["BLOCK_ADS"])

    # Idempotency
    svc.bootstrap_blob_store()
    assert WgBlobStore(db).get(ANTIZAPRET_SETUP_FILE) == raw
    print("OK — bootstrap idempotent")
finally:
    db.close()
    os.remove("smoke.db")
PY
```

Expected:
```
setup len: 683
settings sample: y
OK — bootstrap idempotent
```

- [ ] **Step 4: Sanity-render install-script for visual review**

```bash
cd corpweb/backend
python3 - <<'PY'
from app.api.v1.agent import _render_install_script
print(_render_install_script("https://bb.azfi.ru", "FAKE-TOKEN", "node-bb01"))
PY
```

Expected: a complete bash script. Visually verify:
1. The amneziawg block appears between the header and the python3/curl block.
2. Single backslash line-continuations (`\`), not double (`\\`).
3. No `{cp_url}` / `{token}` / `{hostname}` literal artefacts (those are real f-string substitutions and should resolve to the values you passed).

- [ ] **Step 5: Confirm no remaining work for this plan**

```bash
git log --oneline feature/e5r-bootstrap-awg-install ^CorpAdmin
```

Expected output (5 commits, in this order from oldest to newest reading bottom-up):
1. `docs(spec): AZ settings bootstrap + awg install on agent setup (CorpAdmin-AZ-e5r)` (already exists from brainstorming)
2. `feat(antizapret): add default setup template (CorpAdmin-AZ-e5r)`
3. `feat(antizapret): bootstrap_blob_store seeds default setup + config (CorpAdmin-AZ-e5r)`
4. `feat(main): call antizapret bootstrap in lifespan (CorpAdmin-AZ-e5r)`
5. `feat(agent): install amneziawg from Amnezia PPA on agent setup (CorpAdmin-AZ-e5r)`

If git log differs: you skipped a task or committed under a different message. Do NOT push to remote — go back and reconcile.

- [ ] **Step 6: Hand off to review**

Plan execution complete. Next steps (NOT part of this plan — operator decides):
1. Invoke `superpowers:requesting-code-review` to review the diff against the spec.
2. After review approval: invoke `superpowers:finishing-a-development-branch` to push the branch and open the PR (`feature/e5r-bootstrap-awg-install` → `CorpAdmin`).
3. After PR merge → `systemctl restart corpweb-backend` on `bb.azfi.ru` (per memory `feedback_no_deploy_without_mr.md`: deploy ONLY after merge).
4. Verify on CP that `PATCH /api/v1/antizapret/settings` returns 200 from the admin UI.
5. Close `CorpAdmin-AZ-e5r` with `bd close`.

---

## Plan Self-Review (for the writer, not part of execution)

**Spec coverage:**
- Defect 1 fix → Tasks 1, 2, 3 (default setup file + bootstrap method + lifespan wiring) ✓
- Defect 2 fix → Task 4 (install-script extension) ✓
- Idempotency invariant → tested in Task 2 (3rd and 4th tests) ✓
- Non-fatal lifespan → tested in Task 3 (2nd new test) ✓
- All 8 spec tests (5 + 3) → Tasks 2 and 4 ✓
- 5 acceptance criteria → covered by Task 5 verification + downstream merge/deploy steps ✓

**Type/identifier consistency:**
- `bootstrap_blob_store` — same name in spec, Task 2 implementation, Task 3 patches ✓
- `_load_default_setup` — same name throughout ✓
- `ANTIZAPRET_SETUP_FILE`, `EDITABLE_FILES`, `ALL_KNOWN_SETTINGS` — already exported from `services/antizapret.py`, used unchanged in Task 2 tests ✓
- Fingerprint `75C9DD72C799870E310542E24166F2C257290828` — same in spec, Task 4 install block, Task 4 test ✓

**Placeholder scan:** none. Every step has executable commands or literal code.
