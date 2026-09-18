#!/usr/bin/env python3
"""Merge the revived control plane's dump into the control plane that is live now.

Context (2026-09-18): the prod CP died, a replacement was built from the 31.08 dump,
and the dead server later came back with 18 days of newer rows — including the
``private_key`` of every config issued in between. Both panels were briefly edited at
the same time, so neither database is a superset of the other:

* the revived dump holds configs whose peers are already live on the nodes but whose
  private keys the current DB lacks — those are restored here;
* the current DB holds configs created in the new panel today — those must survive;
* the current DB also holds rows that came from the 31.08 dump and were deleted on the
  old CP in the meantime — those are the ghosts this script removes.

The script only prints SQL. Nothing is executed against a database from here: the
generated file is reviewed, then applied with psql on the CP.
"""
import argparse
import sys

TABLE_PREFIX = "COPY public."
NULL_TOKEN = "\\N"


def parse_copy_block(dump_text: str, table: str) -> list[dict]:
    """Return the rows of one ``COPY public.<table> (...) FROM stdin;`` block.

    Postgres writes ``\\N`` for NULL; it becomes None so the renderer can emit NULL.
    """
    rows: list[dict] = []
    cols: list[str] | None = None
    header = f"{TABLE_PREFIX}{table} ("

    for line in dump_text.splitlines():
        if cols is None:
            if line.startswith(header):
                cols = [c.strip() for c in line.split("(", 1)[1].split(")", 1)[0].split(",")]
            continue
        if line.startswith("\\."):
            break
        values = [None if v == NULL_TOKEN else v for v in line.split("\t")]
        rows.append(dict(zip(cols, values)))

    return rows


def select_to_restore(revived_rows: list[dict], existing_names: set[str],
                      skip_names: set[str]) -> list[dict]:
    """Rows to insert: present in the revived dump, absent here, not deliberately skipped."""
    return [r for r in revived_rows
            if r["client_name"] not in existing_names
            and r["client_name"] not in skip_names]


def select_ghosts(current_rows: list[dict], revived_names: set[str], cutoff: str) -> list[str]:
    """Names to delete: absent from the revived dump AND older than *cutoff*.

    The cutoff spares everything created in the new panel after the rebuild — those rows
    cannot exist in the revived dump and must not be mistaken for deletions.
    """
    return [r["client_name"] for r in current_rows
            if r["client_name"] not in revived_names
            and (r.get("created_at") or "") < cutoff]


def _lit(value) -> str:
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def render_insert(table: str, rows: list[dict]) -> str:
    """Render INSERTs that skip rows already present (idempotent re-runs)."""
    if not rows:
        return f"-- nothing to insert into {table}\n"
    cols = list(rows[0].keys())
    out = [f"-- {len(rows)} row(s) into public.{table}"]
    for r in rows:
        values = ", ".join(_lit(r[c]) for c in cols)
        out.append(
            f"INSERT INTO public.{table} ({', '.join(cols)}) VALUES ({values}) "
            f"ON CONFLICT DO NOTHING;"
        )
    return "\n".join(out) + "\n"


def render_delete(names: list[str]) -> str:
    if not names:
        return "-- no ghosts to delete\n"
    listed = ", ".join(_lit(n) for n in names)
    return (f"-- {len(names)} ghost config(s)\n"
            f"DELETE FROM public.vpn_configs WHERE client_name IN ({listed});\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--revived-dump", required=True, help="SQL dump taken from the revived CP")
    ap.add_argument("--current-dump", required=True, help="SQL dump of the CP that is live now")
    ap.add_argument("--skip", default="", help="comma-separated client_names not to restore")
    ap.add_argument("--cutoff", required=True,
                    help="rows created at/after this timestamp are never treated as ghosts")
    ap.add_argument("--out", required=True, help="where to write the SQL")
    args = ap.parse_args(argv)

    revived = open(args.revived_dump, encoding="utf-8").read()
    current = open(args.current_dump, encoding="utf-8").read()

    revived_cfg = parse_copy_block(revived, "vpn_configs")
    revived_usr = parse_copy_block(revived, "users")
    current_cfg = parse_copy_block(current, "vpn_configs")
    current_usr = parse_copy_block(current, "users")

    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    existing_cfg = {r["client_name"] for r in current_cfg}
    existing_usr_ids = {r["id"] for r in current_usr}

    cfg_to_add = select_to_restore(revived_cfg, existing_cfg, skip)
    needed_user_ids = {r["user_id"] for r in cfg_to_add} - existing_usr_ids
    usr_to_add = [r for r in revived_usr if r["id"] in needed_user_ids]
    ghosts = select_ghosts(current_cfg, {r["client_name"] for r in revived_cfg}, args.cutoff)

    sql = ["BEGIN;", render_insert("users", usr_to_add),
           render_insert("vpn_configs", cfg_to_add), render_delete(ghosts), "COMMIT;"]
    open(args.out, "w", encoding="utf-8").write("\n".join(sql))

    print(f"users to insert:   {len(usr_to_add)}")
    print(f"configs to restore:{len(cfg_to_add)}")
    print(f"ghosts to delete:  {len(ghosts)}")
    print(f"SQL written to:    {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
