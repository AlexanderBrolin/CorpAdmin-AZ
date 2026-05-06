# Multi-node Blob Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate two related multi-node bugs (`CorpAdmin-AZ-9d7` P0, `CorpAdmin-AZ-2xm` P1) by adding a single agent→CP push path for node-side ground-truth blobs (`antizapret:allowed_ips`, `/root/antizapret/setup`) and deferring the bootstrap setup-default when an active node already exists.

**Architecture:** Single new endpoint `POST /api/v1/agent/seed-blob` (whitelist-protected, Bearer-token auth), two module-level parsers in the agent (template-conf → AllowedIPs; setup file → bytes), push helper invoked in `startup_reconcile` and after successful `_run_doall`. CP-side bootstrap skips the setup default when `Node.health in ('ok', 'degraded')` exists.

**Tech Stack:** FastAPI / Pydantic v2, SQLAlchemy, pytest (backend); Python `requests`, `unittest.mock`, pytest (agent).

**Spec:** [docs/superpowers/specs/2026-05-06-multi-node-blob-sync-design.md](../specs/2026-05-06-multi-node-blob-sync-design.md)

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `corpweb/backend/app/services/antizapret.py` | Modify | Add `_has_registered_active_node()` helper (module-level), gate setup-default in `bootstrap_blob_store` |
| `corpweb/backend/tests/test_antizapret_bootstrap.py` | Modify | 3 new tests covering defer-when-active-node + regression |
| `corpweb/backend/app/api/v1/agent.py` | Modify | Add `_SEED_BLOB_WHITELIST`, `SeedBlobRequest` Pydantic model, `POST /seed-blob` handler |
| `corpweb/backend/tests/test_agent_api.py` | Modify | 5 new tests for `/seed-blob` endpoint |
| `agent/corpweb_sync_agent.py` | Modify | Module-level constants/parsers/`_push_seed_blob` helper; integrate in `startup_reconcile` and `_run_doall` |
| `agent/tests/test_seed_blob.py` | Create | 8 tests (4 parsers + 2 startup-reconcile + 2 doall) |

---

## Task 1: Backend — defer bootstrap setup-default when an active node is registered

**Files:**
- Modify: `corpweb/backend/app/services/antizapret.py:80-95`
- Test: `corpweb/backend/tests/test_antizapret_bootstrap.py` (extend existing file)

**Beads:** `CorpAdmin-AZ-2xm` (and rolls up to `CorpAdmin-AZ-byc` epic)

- [ ] **Step 1: Read existing test file to discover db/Node fixtures**

```bash
cat corpweb/backend/tests/test_antizapret_bootstrap.py
```

Look for: `db` fixture pattern, how `Node` rows are created in tests, any helper for `WgBlobStore` inspection. Reuse them — do not introduce new fixtures.

- [ ] **Step 2: Append three failing tests to `test_antizapret_bootstrap.py`**

```python
# at top of file if Node not already imported
from app.db.models import Node
from app.services.antizapret import AntizapretService, ANTIZAPRET_SETUP_FILE
from app.services.wg_blob_store import WgBlobStore


def test_bootstrap_skips_setup_when_active_node_registered(db):
    db.add(Node(hostname="node-a", enroll_token="t1", health="ok"))
    db.commit()

    AntizapretService(db).bootstrap_blob_store()

    store = WgBlobStore(db)
    assert store.get(ANTIZAPRET_SETUP_FILE) is None
    # config/*.txt should still be seeded (empty) — they are admin-managed
    assert store.get("/root/antizapret/config/include-hosts.txt") == b""


def test_bootstrap_skips_setup_when_degraded_node_registered(db):
    db.add(Node(hostname="node-b", enroll_token="t2", health="degraded"))
    db.commit()

    AntizapretService(db).bootstrap_blob_store()

    assert WgBlobStore(db).get(ANTIZAPRET_SETUP_FILE) is None


def test_bootstrap_writes_setup_when_only_unknown_node(db):
    db.add(Node(hostname="node-c", enroll_token="t3", health="unknown"))
    db.commit()

    AntizapretService(db).bootstrap_blob_store()

    seeded = WgBlobStore(db).get(ANTIZAPRET_SETUP_FILE)
    assert seeded is not None
    assert b"WIREGUARD_HOST=" in seeded  # default template marker
```

If `Node`'s `health` field is an Enum or has different valid string values, adjust to whatever `app/db/models.py` defines. The intent is `ok`/`degraded` = active, anything else = inactive.

- [ ] **Step 3: Run tests, expect failures**

```bash
cd corpweb/backend && pytest tests/test_antizapret_bootstrap.py -v -k "skips_setup or unknown_node"
```

Expected: 3 FAIL — first two fail because setup is currently always written; third passes (it should already pass, since current bootstrap writes setup unconditionally — verify this).

- [ ] **Step 4: Implement `_has_registered_active_node` and gate the setup write**

Modify `corpweb/backend/app/services/antizapret.py`. Add at top (after existing imports):

```python
from app.db.models import Node
```

Add helper module-level function (above `class AntizapretService`):

```python
def _has_registered_active_node(db: Session) -> bool:
    """True if any Node row has health 'ok' or 'degraded'."""
    return db.query(Node).filter(Node.health.in_(("ok", "degraded"))).first() is not None
```

Replace `bootstrap_blob_store` body (currently lines 85-95) with:

```python
    def bootstrap_blob_store(self) -> None:
        """
        Seed default setup + empty config files into blob store if missing.
        Idempotent — never overwrites an existing blob.

        Setup default is deferred when an active node is already registered:
        the agent will push the real setup at startup_reconcile, avoiding
        an SSE-broadcast that would overwrite the node's working setup.
        """
        if self._store.get(ANTIZAPRET_SETUP_FILE) is None:
            if _has_registered_active_node(self._db):
                logger.info(
                    "bootstrap_blob_store: skipping setup default — "
                    "active node will push it via /seed-blob"
                )
            else:
                self._store.put(ANTIZAPRET_SETUP_FILE, _load_default_setup(), by="bootstrap")
                logger.info("Seeded default %s into blob store", ANTIZAPRET_SETUP_FILE)
        for path in EDITABLE_FILES.values():
            if self._store.get(path) is None:
                self._store.put(path, b"", by="bootstrap")
```

`AntizapretService` currently stores only `self._store`. Add `self._db = db` in `__init__` (around line 82):

```python
    def __init__(self, db: Session):
        self._db = db
        self._store = WgBlobStore(db)
```

- [ ] **Step 5: Run tests, expect pass**

```bash
cd corpweb/backend && pytest tests/test_antizapret_bootstrap.py -v
```

Expected: ALL pass (3 new + the existing tests that already covered the no-nodes case).

- [ ] **Step 6: Run full backend suite for regression**

```bash
cd corpweb/backend && pytest -q
```

Expected: 342+ passed (current count was 342; we added 3 → expect 345). No new failures.

- [ ] **Step 7: Commit**

```bash
git add corpweb/backend/app/services/antizapret.py corpweb/backend/tests/test_antizapret_bootstrap.py
git commit -m "feat(antizapret): defer bootstrap setup-default when active node registered (CorpAdmin-AZ-2xm)"
```

---

## Task 2: Backend — `POST /api/v1/agent/seed-blob` skeleton + first test (allowed_ips path)

**Files:**
- Modify: `corpweb/backend/app/api/v1/agent.py` (append new endpoint near `heartbeat`/`applied`)
- Test: `corpweb/backend/tests/test_agent_api.py` (extend)

**Beads:** `CorpAdmin-AZ-9d7` (rolls up to `CorpAdmin-AZ-byc`)

- [ ] **Step 1: Read existing test file for client/auth fixtures**

```bash
cat corpweb/backend/tests/test_agent_api.py | head -80
```

Identify how a test sends an authenticated request (header `Authorization: Bearer <enroll_token>`, fixture name for `client`, fixture name for a registered `Node`). Reuse them.

- [ ] **Step 2: Append failing test to `test_agent_api.py`**

```python
import base64
from app.services.wg_blob_store import WgBlobStore


def test_seed_blob_writes_allowed_ips(client, db, registered_node):
    payload = {
        "path": "antizapret:allowed_ips",
        "content": base64.b64encode(b"10.29.8.0/24, 1.2.3.0/24").decode(),
    }
    resp = client.post(
        "/api/v1/agent/seed-blob",
        json=payload,
        headers={"Authorization": f"Bearer {registered_node.enroll_token}"},
    )
    assert resp.status_code == 204

    db.expire_all()
    blob = WgBlobStore(db).get("antizapret:allowed_ips")
    assert blob == b"10.29.8.0/24, 1.2.3.0/24"
```

If a `registered_node` fixture does not exist, look for the equivalent in existing tests (e.g. `node`, `agent_node`) and use that. Match the project's existing fixture naming.

- [ ] **Step 3: Run test, expect failure**

```bash
cd corpweb/backend && pytest tests/test_agent_api.py::test_seed_blob_writes_allowed_ips -v
```

Expected: FAIL — 404 Not Found (endpoint does not exist).

- [ ] **Step 4: Implement endpoint in `agent.py`**

Add at top (after existing imports — `base64` is already imported at line 6):

```python
# Whitelist of blob paths agents are allowed to seed via /seed-blob.
# Each entry is a path that lives at one well-defined column in WgBlobStore;
# expand only when there is a concrete node-side source of truth that CP
# cannot derive on its own.
_SEED_BLOB_WHITELIST: frozenset[str] = frozenset({
    "antizapret:allowed_ips",
})


class SeedBlobRequest(BaseModel):
    path: str
    content: str  # base64-encoded bytes
```

Append the endpoint after `heartbeat` / `applied` (find the right insertion point — around line 180, after the `drain` endpoint):

```python
@router.post("/seed-blob", status_code=204)
def seed_blob(
    req: SeedBlobRequest,
    db: Session = Depends(get_db),
    node: Node = Depends(_require_node),
) -> None:
    if req.path not in _SEED_BLOB_WHITELIST:
        raise HTTPException(status_code=400, detail=f"path {req.path!r} not allowed")
    try:
        raw = base64.b64decode(req.content, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="content is not valid base64")
    WgBlobStore(db).put(req.path, raw, by="agent-sync")
    db.commit()
    logger.info(
        "agent-sync: node=%s path=%s bytes=%d", node.id, req.path, len(raw),
    )
```

- [ ] **Step 5: Run test, expect pass**

```bash
cd corpweb/backend && pytest tests/test_agent_api.py::test_seed_blob_writes_allowed_ips -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add corpweb/backend/app/api/v1/agent.py corpweb/backend/tests/test_agent_api.py
git commit -m "feat(agent-api): add POST /seed-blob endpoint for allowed_ips (CorpAdmin-AZ-9d7)"
```

---

## Task 3: Backend — extend whitelist to `/root/antizapret/setup` + reject unknown paths

**Files:**
- Modify: `corpweb/backend/app/api/v1/agent.py` (whitelist set)
- Test: `corpweb/backend/tests/test_agent_api.py` (extend)

- [ ] **Step 1: Append two failing tests**

```python
def test_seed_blob_writes_setup(client, db, registered_node):
    raw = b"WIREGUARD_HOST=bb.azfi.ru\nROUTE_ALL=n\n"
    resp = client.post(
        "/api/v1/agent/seed-blob",
        json={"path": "/root/antizapret/setup", "content": base64.b64encode(raw).decode()},
        headers={"Authorization": f"Bearer {registered_node.enroll_token}"},
    )
    assert resp.status_code == 204

    db.expire_all()
    assert WgBlobStore(db).get("/root/antizapret/setup") == raw


def test_seed_blob_rejects_unknown_path(client, db, registered_node):
    resp = client.post(
        "/api/v1/agent/seed-blob",
        json={"path": "/etc/passwd", "content": base64.b64encode(b"x").decode()},
        headers={"Authorization": f"Bearer {registered_node.enroll_token}"},
    )
    assert resp.status_code == 400
    assert WgBlobStore(db).get("/etc/passwd") is None
```

- [ ] **Step 2: Run tests, expect 1 failure**

```bash
cd corpweb/backend && pytest tests/test_agent_api.py -v -k "seed_blob_writes_setup or seed_blob_rejects_unknown_path"
```

Expected: `test_seed_blob_writes_setup` FAILs (400, since `/root/antizapret/setup` is not in whitelist yet); `test_seed_blob_rejects_unknown_path` PASSes (already correct behaviour).

- [ ] **Step 3: Add `/root/antizapret/setup` to whitelist**

In `corpweb/backend/app/api/v1/agent.py`, change the `_SEED_BLOB_WHITELIST` definition to:

```python
_SEED_BLOB_WHITELIST: frozenset[str] = frozenset({
    "antizapret:allowed_ips",
    "/root/antizapret/setup",
})
```

- [ ] **Step 4: Run tests, expect both pass**

```bash
cd corpweb/backend && pytest tests/test_agent_api.py -v -k "seed_blob"
```

Expected: 3 PASS (the original `test_seed_blob_writes_allowed_ips` plus the two new ones).

- [ ] **Step 5: Commit**

```bash
git add corpweb/backend/app/api/v1/agent.py corpweb/backend/tests/test_agent_api.py
git commit -m "feat(agent-api): whitelist /root/antizapret/setup for /seed-blob (CorpAdmin-AZ-2xm)"
```

---

## Task 4: Backend — auth + overwrite-semantics tests

**Files:**
- Test: `corpweb/backend/tests/test_agent_api.py` (extend; no production code changes expected)

- [ ] **Step 1: Append two tests**

```python
def test_seed_blob_rejects_request_without_auth(client, db):
    resp = client.post(
        "/api/v1/agent/seed-blob",
        json={"path": "antizapret:allowed_ips", "content": base64.b64encode(b"x").decode()},
    )
    assert resp.status_code == 401


def test_seed_blob_overwrites_existing_blob(client, db, registered_node):
    # Pre-populate with admin-attributed value
    WgBlobStore(db).put("antizapret:allowed_ips", b"old, admin, value", by="admin")
    db.commit()

    resp = client.post(
        "/api/v1/agent/seed-blob",
        json={"path": "antizapret:allowed_ips", "content": base64.b64encode(b"new value").decode()},
        headers={"Authorization": f"Bearer {registered_node.enroll_token}"},
    )
    assert resp.status_code == 204

    db.expire_all()
    # Agent push wins unconditionally (design: node = source of truth)
    assert WgBlobStore(db).get("antizapret:allowed_ips") == b"new value"
```

- [ ] **Step 2: Run tests, expect both pass with no code changes**

```bash
cd corpweb/backend && pytest tests/test_agent_api.py -v -k "without_auth or overwrites_existing"
```

Expected: 2 PASS — `_require_node` already handles missing-auth → 401, and `WgBlobStore.put` is unconditional, so both behaviours are covered by Task 2's implementation.

If either fails, fix the implementation in `agent.py` (do not weaken the test).

- [ ] **Step 3: Run full agent-api suite + full backend suite**

```bash
cd corpweb/backend && pytest tests/test_agent_api.py -v && pytest -q
```

Expected: ALL pass. Total backend test count = 342 (baseline) + 3 (Task 1) + 5 (Tasks 2–4) = 350.

- [ ] **Step 4: Commit**

```bash
git add corpweb/backend/tests/test_agent_api.py
git commit -m "test(agent-api): cover /seed-blob auth + overwrite semantics"
```

---

## Task 5: Agent — module-level parsers `_parse_allowed_ips_from_template` + `_read_setup`

**Files:**
- Modify: `agent/corpweb_sync_agent.py` (add constants + parsers near top of helpers section)
- Create: `agent/tests/test_seed_blob.py`

**Beads:** `CorpAdmin-AZ-9d7`, `CorpAdmin-AZ-2xm` (both, single agent change covers both)

- [ ] **Step 1: Read agent test file for fixture pattern**

```bash
cat agent/tests/test_sync_agent.py | head -60
```

Note: import style, how the agent module is loaded under test (likely `import corpweb_sync_agent as agent`), how `tmp_path` is used.

- [ ] **Step 2: Create `agent/tests/test_seed_blob.py` with four failing parser tests**

```python
"""Tests for seed-blob parsers and push integration."""
import base64
import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest

# Match the import style used in agent/tests/test_sync_agent.py.
# If that file uses a different import path, mirror it here.
sys.path.insert(0, "agent")  # adjust if test_sync_agent.py uses something else
import corpweb_sync_agent as agent  # noqa: E402


# ---------------------------------------------------------------------------
# Parser: _parse_allowed_ips_from_template
# ---------------------------------------------------------------------------

CONF_FIXTURE = """\
[Interface]
PrivateKey = abc=
Address = 10.29.8.6/32
DNS = 10.29.8.1

[Peer]
PublicKey = xyz=
Endpoint = bb.azfi.ru:52443
AllowedIPs = 10.29.8.0/24, 1.2.3.0/24, 4.5.6.0/24
PersistentKeepalive = 15
"""


def test_parse_allowed_ips_returns_value(tmp_path, monkeypatch):
    target = tmp_path / "client" / "amneziawg" / "antizapret"
    target.mkdir(parents=True)
    (target / "antizapret-foo-am.conf").write_text(CONF_FIXTURE)

    monkeypatch.setattr(
        agent, "_TEMPLATE_CONF_GLOB",
        str(target / "antizapret-*-am.conf"),
    )
    assert agent._parse_allowed_ips_from_template() == \
        b"10.29.8.0/24, 1.2.3.0/24, 4.5.6.0/24"


def test_parse_allowed_ips_returns_none_when_no_match(tmp_path, monkeypatch):
    monkeypatch.setattr(
        agent, "_TEMPLATE_CONF_GLOB",
        str(tmp_path / "no-such-pattern-*.conf"),
    )
    assert agent._parse_allowed_ips_from_template() is None


def test_parse_allowed_ips_picks_lexicographically_first(tmp_path, monkeypatch):
    target = tmp_path
    (target / "antizapret-bbb-am.conf").write_text(
        CONF_FIXTURE.replace("AllowedIPs = 10.29.8.0/24, 1.2.3.0/24, 4.5.6.0/24",
                              "AllowedIPs = 9.9.9.0/24")
    )
    (target / "antizapret-aaa-am.conf").write_text(CONF_FIXTURE)
    monkeypatch.setattr(
        agent, "_TEMPLATE_CONF_GLOB",
        str(target / "antizapret-*-am.conf"),
    )
    # aaa sorts first → returns CONF_FIXTURE's AllowedIPs
    assert agent._parse_allowed_ips_from_template() == \
        b"10.29.8.0/24, 1.2.3.0/24, 4.5.6.0/24"


# ---------------------------------------------------------------------------
# Parser: _read_setup
# ---------------------------------------------------------------------------

def test_read_setup_returns_bytes(tmp_path, monkeypatch):
    setup = tmp_path / "setup"
    setup.write_bytes(b"WIREGUARD_HOST=foo\n")
    monkeypatch.setattr(agent, "_SETUP_PATH", str(setup))
    assert agent._read_setup() == b"WIREGUARD_HOST=foo\n"


def test_read_setup_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "_SETUP_PATH", str(tmp_path / "nope"))
    assert agent._read_setup() is None
```

- [ ] **Step 3: Run tests, expect all to fail**

```bash
cd agent && pytest tests/test_seed_blob.py -v
```

Expected: 5 FAIL with `AttributeError: module 'corpweb_sync_agent' has no attribute '_parse_allowed_ips_from_template'` (and similar for `_read_setup`, `_TEMPLATE_CONF_GLOB`, `_SETUP_PATH`).

- [ ] **Step 4: Add constants and parsers to `corpweb_sync_agent.py`**

Find a logical insertion point — near the top of the helpers section (after the `requests` imports, before `_run_doall`, around line 350). Required imports may not yet be present at the top of the file:

```python
import glob
import pathlib
import re
```

(Skip whichever are already imported.)

Add:

```python
# ---------------------------------------------------------------------------
# Seed-blob parsers (node-side ground truth pushed back to CP)
# ---------------------------------------------------------------------------

_TEMPLATE_CONF_GLOB = "/root/antizapret/client/amneziawg/antizapret/antizapret-*-am.conf"
_SETUP_PATH = "/root/antizapret/setup"
_ALLOWED_IPS_RE = re.compile(r"^\s*AllowedIPs\s*=\s*(.+?)\s*$", re.MULTILINE)


def _parse_allowed_ips_from_template() -> bytes | None:
    """
    Return AllowedIPs from the lexicographically first template-conf,
    or None if no match.

    Why first-by-sort: all client confs on one node share the same AllowedIPs
    (the [Peer] AllowedIPs is defined per-iface, not per-client). Choosing
    sorted()[0] makes the selection deterministic and testable.
    """
    matches = sorted(glob.glob(_TEMPLATE_CONF_GLOB))
    if not matches:
        return None
    text = pathlib.Path(matches[0]).read_text()
    m = _ALLOWED_IPS_RE.search(text)
    return m.group(1).encode() if m else None


def _read_setup() -> bytes | None:
    """Return /root/antizapret/setup bytes, or None if file does not exist."""
    p = pathlib.Path(_SETUP_PATH)
    if not p.exists():
        return None
    return p.read_bytes()
```

- [ ] **Step 5: Run tests, expect all to pass**

```bash
cd agent && pytest tests/test_seed_blob.py -v
```

Expected: 5 PASS.

- [ ] **Step 6: Commit**

```bash
git add agent/corpweb_sync_agent.py agent/tests/test_seed_blob.py
git commit -m "feat(agent): seed-blob parsers for AllowedIPs + setup (CorpAdmin-AZ-byc)"
```

---

## Task 6: Agent — `_push_seed_blob` helper + integration in `startup_reconcile`

**Files:**
- Modify: `agent/corpweb_sync_agent.py` (add helper + extend `startup_reconcile`)
- Test: `agent/tests/test_seed_blob.py` (extend)

- [ ] **Step 1: Append two failing integration tests**

```python
# ---------------------------------------------------------------------------
# Push integration: startup_reconcile
# ---------------------------------------------------------------------------

def test_startup_reconcile_pushes_both_blobs(tmp_path, monkeypatch):
    # Set up template-conf and setup so parsers return real values
    target = tmp_path / "client" / "amneziawg" / "antizapret"
    target.mkdir(parents=True)
    (target / "antizapret-foo-am.conf").write_text(CONF_FIXTURE)
    setup = tmp_path / "setup"
    setup.write_bytes(b"WIREGUARD_HOST=foo\n")

    monkeypatch.setattr(agent, "_TEMPLATE_CONF_GLOB", str(target / "antizapret-*-am.conf"))
    monkeypatch.setattr(agent, "_SETUP_PATH", str(setup))
    # Stub out the existing managed-files fetch so startup_reconcile only
    # exercises the new seed-blob block (and does not hit the network).
    monkeypatch.setattr(agent, "MANAGED_FILES", [])

    with patch.object(agent, "api_post") as mock_post:
        agent.startup_reconcile()

    paths_pushed = sorted(call.args[0] for call in mock_post.call_args_list)
    assert paths_pushed == ["/api/v1/agent/seed-blob", "/api/v1/agent/seed-blob"]

    # Decode the two payloads and assert per-path content
    decoded = {}
    for call in mock_post.call_args_list:
        body = call.args[1]
        decoded[body["path"]] = base64.b64decode(body["content"])
    assert decoded["antizapret:allowed_ips"] == \
        b"10.29.8.0/24, 1.2.3.0/24, 4.5.6.0/24"
    assert decoded["/root/antizapret/setup"] == b"WIREGUARD_HOST=foo\n"


def test_startup_reconcile_skips_push_when_parsers_return_none(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "_TEMPLATE_CONF_GLOB", str(tmp_path / "no-match-*.conf"))
    monkeypatch.setattr(agent, "_SETUP_PATH", str(tmp_path / "no-such-setup"))
    monkeypatch.setattr(agent, "MANAGED_FILES", [])

    with patch.object(agent, "api_post") as mock_post:
        agent.startup_reconcile()

    assert mock_post.call_args_list == []
```

- [ ] **Step 2: Run tests, expect failure**

```bash
cd agent && pytest tests/test_seed_blob.py -v -k "startup_reconcile"
```

Expected: 2 FAIL — `api_post` is never called (no push code yet).

- [ ] **Step 3: Implement `_push_seed_blob` and extend `startup_reconcile`**

Add `import base64` at top if not already present.

Add helper (placed right after `_read_setup`):

```python
def _push_seed_blob(path: str, content: bytes) -> None:
    """POST {path, base64(content)} to /api/v1/agent/seed-blob.

    Errors are logged and swallowed — the next reconcile/doall cycle retries.
    """
    try:
        api_post(
            "/api/v1/agent/seed-blob",
            {"path": path, "content": base64.b64encode(content).decode()},
        )
        log.info("seed-blob pushed: path=%s bytes=%d", path, len(content))
    except (requests.HTTPError, requests.ConnectionError, requests.Timeout):
        log.exception("seed-blob push failed: path=%s", path)
```

Extend `startup_reconcile()` (lines 759-778). Add at the end, before `log.info("Startup reconcile done")`:

```python
    # Push node-side ground truth back to CP (CorpAdmin-AZ-byc)
    for blob_path, parser in (
        ("antizapret:allowed_ips", _parse_allowed_ips_from_template),
        (_SETUP_PATH, _read_setup),
    ):
        content = parser()
        if content is not None:
            _push_seed_blob(blob_path, content)
```

- [ ] **Step 4: Run tests, expect pass**

```bash
cd agent && pytest tests/test_seed_blob.py -v
```

Expected: 7 PASS (5 parser tests from Task 5 + 2 new).

- [ ] **Step 5: Run full agent suite for regression**

```bash
cd agent && pytest -q
```

Expected: existing tests still pass. If `test_sync_agent.py` exercises `startup_reconcile()`, that test may now hit `api_post` — verify it mocks the call or skip via `MANAGED_FILES = []` shim.

- [ ] **Step 6: Commit**

```bash
git add agent/corpweb_sync_agent.py agent/tests/test_seed_blob.py
git commit -m "feat(agent): push seed-blob in startup_reconcile (CorpAdmin-AZ-byc)"
```

---

## Task 7: Agent — push `antizapret:allowed_ips` after successful `_run_doall`

**Files:**
- Modify: `agent/corpweb_sync_agent.py:364-376` (refactor `_run_doall` + add post-success push)
- Test: `agent/tests/test_seed_blob.py` (extend)

- [ ] **Step 1: Append two failing tests**

```python
# ---------------------------------------------------------------------------
# Push integration: _run_doall
# ---------------------------------------------------------------------------

def test_run_doall_success_pushes_allowed_ips(tmp_path, monkeypatch):
    target = tmp_path / "client" / "amneziawg" / "antizapret"
    target.mkdir(parents=True)
    (target / "antizapret-foo-am.conf").write_text(CONF_FIXTURE)
    monkeypatch.setattr(agent, "_TEMPLATE_CONF_GLOB", str(target / "antizapret-*-am.conf"))

    fake_run = MagicMock(return_value=MagicMock(returncode=0, stderr=""))
    with patch.object(agent.subprocess, "run", fake_run), \
         patch.object(agent, "api_post") as mock_post:
        agent._run_doall()

    assert mock_post.call_count == 1
    body = mock_post.call_args.args[1]
    assert body["path"] == "antizapret:allowed_ips"
    assert base64.b64decode(body["content"]) == \
        b"10.29.8.0/24, 1.2.3.0/24, 4.5.6.0/24"


def test_run_doall_failure_does_not_push(tmp_path, monkeypatch):
    import subprocess as sp
    target = tmp_path / "client" / "amneziawg" / "antizapret"
    target.mkdir(parents=True)
    (target / "antizapret-foo-am.conf").write_text(CONF_FIXTURE)
    monkeypatch.setattr(agent, "_TEMPLATE_CONF_GLOB", str(target / "antizapret-*-am.conf"))

    def boom(*args, **kwargs):
        raise sp.CalledProcessError(returncode=1, cmd=args[0], stderr="oops")

    with patch.object(agent.subprocess, "run", side_effect=boom), \
         patch.object(agent, "api_post") as mock_post:
        agent._run_doall()

    assert mock_post.call_args_list == []
```

- [ ] **Step 2: Run tests, expect failure**

```bash
cd agent && pytest tests/test_seed_blob.py -v -k "run_doall"
```

Expected: 2 FAIL — current `_run_doall` does not push anything.

- [ ] **Step 3: Refactor `_run_doall` to push after success**

Replace `_run_doall` (currently lines 364-376) with:

```python
def _run_doall() -> None:
    log.info("Running /root/antizapret/doall.sh")
    try:
        subprocess.run(
            ["/root/antizapret/doall.sh"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        log.error("doall.sh failed (rc=%d): %s", exc.returncode, exc.stderr.strip())
        return
    except FileNotFoundError:
        log.error("/root/antizapret/doall.sh not found")
        return

    # doall succeeded — template-conf may have changed, push fresh blob
    content = _parse_allowed_ips_from_template()
    if content is not None:
        _push_seed_blob("antizapret:allowed_ips", content)
```

- [ ] **Step 4: Run tests, expect all pass**

```bash
cd agent && pytest tests/test_seed_blob.py -v
```

Expected: 9 PASS (5 parser + 2 startup_reconcile + 2 _run_doall).

- [ ] **Step 5: Run full agent suite**

```bash
cd agent && pytest -q
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add agent/corpweb_sync_agent.py agent/tests/test_seed_blob.py
git commit -m "feat(agent): push allowed_ips after successful doall.sh (CorpAdmin-AZ-9d7)"
```

---

## Task 8: Final verification — full suites + spec acceptance criteria walkthrough

**Files:** none (verification only).

- [ ] **Step 1: Run full backend + agent suites**

```bash
cd corpweb/backend && pytest -q
cd ../../agent && pytest -q
```

Expected: ALL green. Backend ≥ 350 tests, agent ≥ existing-baseline + 9.

- [ ] **Step 2: Read spec acceptance criteria and confirm coverage**

Open `docs/superpowers/specs/2026-05-06-multi-node-blob-sync-design.md`, find the `## Acceptance criteria` section, walk through each of the 4 criteria and map to test or runtime evidence:

| Criterion | Where covered |
|---|---|
| 1. Auto-fill blob on agent restart, correct AllowedIPs in download_config | Tasks 5+6 (parser + push), exercised in production after deploy — note in PR description |
| 2. Admin-edit `*_INCLUDE` → blob updated within 30 sec | Task 7 — `_run_doall` push closes the loop after debounced doall |
| 3. Fresh CP with one registered node — bootstrap does not broadcast empty setup | Task 1 — `test_bootstrap_skips_setup_when_active_node_registered` |
| 4. `pytest corpweb/backend/tests/ agent/tests/` all green | Task 8 step 1 |

If any criterion has no corresponding evidence, add a task and rerun TDD.

- [ ] **Step 3: Update epic with verification status**

```bash
bd update CorpAdmin-AZ-byc --notes="All 9 tests green ($(date -u +%Y-%m-%dT%H:%MZ)). Ready for code review."
```

- [ ] **Step 4: Push branch + open PR**

This step uses `superpowers:finishing-a-development-branch` — invoke that skill rather than doing it ad-hoc.
