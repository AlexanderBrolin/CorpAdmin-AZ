# AZ Settings Bootstrap + AWG Install on Agent Setup

**Beads epic:** CorpAdmin-AZ-e5r
**Date:** 2026-04-27
**Author:** brolin (with Claude)

## Problem

Two related defects observed on production CP `bb.azfi.ru`:

### Defect 1 — `PATCH /api/v1/antizapret/settings` returns 500

Saving any AntiZapret setting from the admin UI fails with **«Ошибка при сохранении настроек»**. The backend logs:

```
PATCH /api/v1/antizapret/settings  500 Internal Server Error
```

Root cause: [corpweb/backend/app/services/antizapret.py:124-126](../../../corpweb/backend/app/services/antizapret.py#L124-L126) raises `AntizapretServiceError("Setup file not found: /root/antizapret/setup")` when the blob store has no row for that path. On a fresh CP install the blob store contains only the four WireGuard / AmneziaWG `.conf` files (created by `vpn_manager.bootstrap()`); none of the AntiZapret-managed files (`/root/antizapret/setup` and the nine `/root/antizapret/config/*.txt`) ever get seeded. They live on the data-plane node but never travel up to CP, and `python -m app.migrate` only reads them off the local CP filesystem (where they don't exist).

Confirmed via `psql` on `bb.azfi.ru`:

```
                  path                  | length
----------------------------------------+--------
 /etc/amnezia/amneziawg/az_escape.conf  |    217
 /etc/amnezia/amneziawg/vpn_escape.conf |    218
 /etc/wireguard/antizapret.conf         |    112
 /etc/wireguard/vpn.conf                |    112
```

### Defect 2 — Agent fails to bring up escape interfaces

Sync agent on `node-bb01` (78.17.54.19) logs at every startup:

```
ERROR  systemctl start awg-quick@az_escape.service failed (rc=5):
       Failed to start awg-quick@az_escape.service: Unit awg-quick@az_escape.service not found.
```

`amneziawg-tools` is not installed on the node (`which awg awg-quick` is empty, `dpkg -l | grep amnezia` returns nothing). The current agent install-script ([corpweb/backend/app/api/v1/agent.py:264-313](../../../corpweb/backend/app/api/v1/agent.py#L264-L313)) only installs the Python sync agent; nothing on the data-plane provisions amneziawg. Escape interfaces (`az_escape`, `vpn_escape`) therefore can never come up regardless of whether `escape_enabled` is toggled on CP.

## Goals

1. `PATCH /api/v1/antizapret/settings` returns 200 and persists changes on a fresh CP, without manual `psql` intervention.
2. New nodes installed via `curl .../api/v1/agent/install.sh?token=… | bash` end up with `amneziawg-dkms` + `amneziawg-tools` installed, escape `awg-quick@*` units enabled, and ready to bring up escape interfaces as soon as CP delivers the configs.
3. Both fixes are **idempotent**: re-running on already-configured systems must not destroy state.

## Non-goals (explicit scope-out)

- **Backfill of `amneziawg-tools` on already-installed nodes.** Out of this PR — separate issue if needed. Operationally `node-bb01` was provisioned by hand during this debugging session; the install procedure is documented in the user's reference memory.
- **Agent idempotency bug** (`apply_path` does not bring up an iface when the conf is unchanged but the iface is down) — tracked as side-quest **CorpAdmin-AZ-84e**, separate PR.
- **Conditional pull of escape configs by `admin.escape_enabled`.** Today CP unconditionally serves `*_escape.conf` and the agent unconditionally pulls them. Changing that gate is a follow-up; this PR only ensures that when the agent does pull, awg is present.
- **Seeding non-empty defaults into `config/*.txt`** (include-hosts, exclude-hosts, etc.). These are user-data files; they ship empty.

## Design

### Part 1 — CP backend: bootstrap blob store on lifespan

#### New file: `corpweb/backend/app/services/antizapret_default_setup.txt`

The 43-line shell-style key=value file that mirrors what upstream AntiZapret writes to `/root/antizapret/setup` on a fresh node. Built from the file currently present on `node-bb01` (43 lines, captured 2026-04-27), with **all deployment-specific values reset to empty** so the file is generic:

- `SETUP_DATE=` (empty — this is a bootstrap, not a real install)
- `WIREGUARD_HOST=` (empty — admin must set per deployment via UI)
- `OPENVPN_HOST=` (empty)
- `DEFAULT_INTERFACE=`, `DEFAULT_IP=`, `ANTIZAPRET_OUT_INTERFACE=`, `ANTIZAPRET_OUT_IP=`, `VPN_OUT_INTERFACE=`, `VPN_OUT_IP=`, `CLIENT_IP=`, `FAKE_IP=` — all empty (antizapret scripts auto-derive at runtime when blank)
- All boolean / numeric keys carry **upstream antizapret defaults**: `ROUTE_ALL=n`, `BLOCK_ADS=y`, `ANTIZAPRET_DNS=1`, `VPN_DNS=1`, `WIREGUARD_BACKUP=y`, `SSH_PROTECTION=y`, `ATTACK_PROTECTION=y`, `TORRENT_GUARD=y`, `RESTRICT_FORWARD=y`, `CLIENT_ISOLATION=y`, `DISCORD_INCLUDE=y`, `CLOUDFLARE_INCLUDE=y`, `TELEGRAM_INCLUDE=y`, `WHATSAPP_INCLUDE=y`, `ROBLOX_INCLUDE=y`, `CLEAR_HOSTS=y`; the rest empty as they ship from upstream.

The file is shipped as a **package data resource**, loaded via `importlib.resources.files("app.services").joinpath("antizapret_default_setup.txt").read_bytes()` (so it works under both editable install and `pip install`). The exact bytes (including trailing newline) are committed to git.

#### Modified: `corpweb/backend/app/services/antizapret.py`

Add a lazy loader and a public method on `AntizapretService`:

```python
def _load_default_setup() -> bytes:
    from importlib.resources import files
    return files("app.services").joinpath("antizapret_default_setup.txt").read_bytes()


class AntizapretService:
    ...

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

The default-setup file is loaded **lazily inside `bootstrap_blob_store()`** rather than at module import. Reason: if the package data file is missing or unreadable, the failure is contained inside the `try/except` in `lifespan()` (next subsection) rather than crashing the entire backend at import time.

The "if `get(...) is None`" guard is the critical correctness invariant: any subsequent restart of the backend with admin-customised settings already in the blob store must leave them untouched.

#### Modified: `corpweb/backend/app/main.py`

Inside the existing `lifespan()` (around line 38-57), add right after `vpn_manager.bootstrap(db)`:

```python
try:
    from app.services.antizapret import AntizapretService
    AntizapretService(db).bootstrap_blob_store()
except Exception as exc:
    logging.getLogger(__name__).warning(
        "antizapret bootstrap on startup failed (non-fatal): %s", exc
    )
```

Non-fatal: if seeding fails (e.g. broken DB), the backend still comes up — the existing `PATCH` will simply continue to return the same 500 it does today, no regression.

### Part 2 — Agent install script: provision amneziawg

#### Modified: `corpweb/backend/app/api/v1/agent.py`

Inside `_render_install_script(cp_url, token, hostname)`, insert **before** the `python3 -c "import requests"` line:

```bash
# Install amneziawg (required for escape ifaces). Idempotent.
if ! command -v awg-quick >/dev/null 2>&1; then
  echo "==> Installing amneziawg from Amnezia PPA"
  apt-get install -y gnupg dirmngr curl
  curl -fsSL "https://keyserver.ubuntu.com/pks/lookup?op=get&search=0x75C9DD72C799870E310542E24166F2C257290828" \
    | gpg --dearmor > /usr/share/keyrings/amnezia-ppa.gpg
  chmod 644 /usr/share/keyrings/amnezia-ppa.gpg
  echo "deb [signed-by=/usr/share/keyrings/amnezia-ppa.gpg] https://ppa.launchpadcontent.net/amnezia/ppa/ubuntu noble main" \
    > /etc/apt/sources.list.d/amnezia.list
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y amneziawg-dkms amneziawg-tools
fi

# Ensure escape units start at boot once their .conf files arrive.
# 'enable' is safe even before .conf exists; 'start' is the agent's job.
systemctl enable awg-quick@az_escape awg-quick@vpn_escape 2>/dev/null || true
```

**Why Amnezia PPA `noble` channel works on Debian 12/13:** packages have no Ubuntu-specific dependency (`libc6 (>= 2.35)` only) and DKMS recompiles the kernel module against the running kernel. Verified on Debian 13 trixie + kernel 6.19.11 during this debugging session.

**Fingerprint `75C9DD72C799870E310542E24166F2C257290828`** is the canonical Amnezia PPA signing key (queried from the launchpad API: `/api/1.0/~amnezia/+archive/ubuntu/ppa.signing_key_fingerprint`).

### Part 3 — Tests (TDD)

#### New: `corpweb/backend/tests/test_antizapret_bootstrap.py`

Five tests against an in-memory SQLite session (re-uses existing test fixture style from `test_antizapret_blob.py`):

1. **`test_bootstrap_seeds_setup_when_blob_empty`** — empty store → `bootstrap_blob_store()` → `store.get(ANTIZAPRET_SETUP_FILE)` returns the 43-line default; parsed via `service.get_settings()` returns non-None for every key in `ALL_KNOWN_SETTINGS`.
2. **`test_bootstrap_seeds_empty_config_files`** — empty store → bootstrap → for every path in `EDITABLE_FILES.values()`, `store.get(path)` returns `b""`.
3. **`test_bootstrap_idempotent_preserves_existing_setup`** — pre-write `setup` with custom bytes via `store.put(...)` → bootstrap → blob still equals the pre-written bytes (NOT the default).
4. **`test_bootstrap_idempotent_preserves_existing_config`** — pre-write one editable file with non-empty content → bootstrap → unchanged.
5. **`test_patch_settings_works_after_bootstrap`** — bootstrap → call `service.update_settings({"BLOCK_ADS": "n"})` → returns `1` (one changed) → `service.get_settings()["BLOCK_ADS"] == "n"`. This is the regression test that locks in Defect 1's fix.

#### New: `corpweb/backend/tests/test_install_script.py`

Three tests against the pure rendering function `_render_install_script`:

1. **`test_install_script_contains_amneziawg_block`** — output text contains the strings `command -v awg-quick`, the fingerprint `75C9DD72C799870E310542E24166F2C257290828`, `amneziawg-dkms`, `amneziawg-tools`, `signed-by=/usr/share/keyrings/amnezia-ppa.gpg`.
2. **`test_install_script_uses_noble_repo`** — output contains `ppa.launchpadcontent.net/amnezia/ppa/ubuntu noble main`.
3. **`test_install_script_enables_awg_quick_units`** — output contains `systemctl enable awg-quick@az_escape awg-quick@vpn_escape`.

These are pure-text assertions on the rendered script — no shell execution needed. They lock in the install-script's contract; if anyone later changes the PPA URL or fingerprint without updating tests, CI breaks loudly.

## Architecture decisions & alternatives rejected

| Choice | Rejected | Reason |
|---|---|---|
| **B1**: seed only managed keys (24 lines) | Yes | Removes ~19 keys (`OPENVPN_*`, `DEFAULT_INTERFACE`, `CLIENT_IP`, …) that upstream `parse.sh`/`doall.sh` `source` and reference. Risks breaking antizapret on the node. |
| **C2**: lazy seed in `update_settings` only | Yes | `GET /antizapret/settings` would still return all-None until first PATCH; agent's `startup_reconcile` would still 404 for setup until first PATCH. C1 fixes both at once. |
| **C3**: alembic data migration | Yes | Not self-healing — if blob is wiped post-migration, no recovery. C1 self-heals on every restart. |
| **D1 alone** (auto-install awg in agent on demand) | Yes | User feedback: "agent install should provision awg upfront, not lazily." Putting it in `_render_install_script` is the simplest place. |
| Push-from-agent bootstrap (variant A) | Yes | Inverts the established pull architecture. CP is SSOT; nodes consume. Confirmed by reading `corpweb_sync_agent.py:759-778` (`startup_reconcile` only pulls). |

## Deployment plan

1. **Merge to main**, no other action on CP needed beyond `systemctl restart corpweb-backend`. Bootstrap runs in `lifespan()`; the 500 disappears immediately.
2. **For `node-bb01`** — already manually provisioned with `amneziawg-{tools,dkms}` during this debug session (see memory `reference_amneziawg_install_debian.md`). No further action.
3. **For future new nodes** — install-script will install awg automatically as part of `curl .../api/v1/agent/install.sh?token=… | bash`.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| `_DEFAULT_SETUP` loaded at module import time fails (file missing from package) | Catch in `lifespan()`'s try/except → logs warning, backend continues up. Existing tests would have caught it earlier. |
| Amnezia PPA goes down or changes fingerprint | Install script fails noisily on `apt-get update` (unsigned repo); user sees clear error in install output. Fingerprint is asserted by `test_install_script_contains_amneziawg_block` — if upstream rotates, we update both. |
| Bootstrap overwrites admin's customisations after restart | The `if get() is None` guard is the contract; `test_bootstrap_idempotent_preserves_*` locks it in. |
| DKMS module fails to build on a node with no kernel headers | `apt-get install amneziawg-dkms` pulls headers via dependencies; if it still fails, install-script exits non-zero (`set -e` is already at top of the rendered script) — user sees the failure. |

## Acceptance criteria

- [ ] All five tests in `test_antizapret_bootstrap.py` pass.
- [ ] All three tests in `test_install_script.py` pass.
- [ ] After `systemctl restart corpweb-backend` on a CP whose blob store lacks `/root/antizapret/setup`, the row appears in `wg_file_state` with `updated_by='bootstrap'`.
- [ ] `PATCH /api/v1/antizapret/settings` from the admin UI returns 200 and the changed key persists across requests.
- [ ] On a fresh-installed node (using updated install-script), `which awg-quick` returns `/usr/bin/awg-quick` and `systemctl is-enabled awg-quick@az_escape` returns `enabled`.
