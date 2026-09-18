import importlib.util
import pathlib

spec = importlib.util.spec_from_file_location(
    "merge", pathlib.Path(__file__).parent / "merge_revived_cp.py")
merge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(merge)


DUMP = (
    "SET statement_timeout = 0;\n"
    "COPY public.users (id, email, username, password_hash, role, is_active) FROM stdin;\n"
    "u1\tann@x.ru\tann\thash1\tuser\tt\n"
    "u2\tbob@x.ru\tbob\t\\N\tuser\tt\n"
    "\\.\n"
    "\n"
    "COPY public.vpn_configs (id, user_id, client_name, config_metadata, is_active, created_at) FROM stdin;\n"
    "c1\tu1\tann-1\t{\"private_key\": \"AAA\"}\tt\t2026-09-10 10:00:00\n"
    "c2\tu2\tbob-1\t{\"private_key\": \"BBB\"}\tt\t2026-09-11 10:00:00\n"
    "\\.\n"
)


def test_parse_copy_block_reads_rows_and_nulls():
    rows = merge.parse_copy_block(DUMP, "users")
    assert [r["username"] for r in rows] == ["ann", "bob"]
    assert rows[0]["password_hash"] == "hash1"
    assert rows[1]["password_hash"] is None, "\\N должен становиться None"


def test_parse_copy_block_does_not_leak_into_next_table():
    rows = merge.parse_copy_block(DUMP, "users")
    assert len(rows) == 2, "блок обязан заканчиваться на \\."


def test_select_to_restore_skips_existing_and_skiplist():
    rows = merge.parse_copy_block(DUMP, "vpn_configs")
    got = merge.select_to_restore(rows, existing_names={"ann-1"}, skip_names={"bob-1"})
    assert got == [], "ann-1 уже есть, bob-1 в skip — восстанавливать нечего"

    got = merge.select_to_restore(rows, existing_names=set(), skip_names={"bob-1"})
    assert [r["client_name"] for r in got] == ["ann-1"]


def test_select_ghosts_spares_configs_created_after_cutoff():
    """Конфиги, созданные на новом CP уже после восстановления, — не призраки."""
    current = [
        {"client_name": "old-1", "created_at": "2026-08-30 12:00:00"},
        {"client_name": "fresh-1", "created_at": "2026-09-18 09:35:25.684505"},
    ]
    ghosts = merge.select_ghosts(current, revived_names=set(), cutoff="2026-09-18 08:00:00")
    assert ghosts == ["old-1"], "fresh-1 создан после cutoff и удалению не подлежит"


def test_select_ghosts_spares_names_present_in_revived():
    current = [{"client_name": "kept-1", "created_at": "2026-08-01 00:00:00"}]
    assert merge.select_ghosts(current, revived_names={"kept-1"}, cutoff="2026-09-18 08:00:00") == []


def test_render_insert_quotes_and_handles_null():
    rows = [{"id": "c1", "client_name": "o'brien-1", "config_metadata": '{"k": "v"}',
             "is_active": "t", "created_at": "2026-09-10 10:00:00", "user_id": None}]
    sql = merge.render_insert("vpn_configs", rows)
    assert "INSERT INTO public.vpn_configs" in sql
    assert "'o''brien-1'" in sql, "одинарная кавычка должна удваиваться"
    assert "NULL" in sql
    assert sql.rstrip().endswith(";")


def test_render_delete_lists_names():
    sql = merge.render_delete(["a-1", "b-2"])
    assert "DELETE FROM public.vpn_configs" in sql
    assert "'a-1'" in sql and "'b-2'" in sql
