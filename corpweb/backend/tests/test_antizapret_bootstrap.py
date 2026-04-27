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
