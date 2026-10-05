"""
store/deletion_journal.py - what has been deleted for good, so backups forget it too

A deleted note is entirely gone (docs/plans/notes-hard-delete-plan.md), and that
includes the nightly database dumps. Deleting the live rows can't reach those,
so every hard delete also writes the ids it removed here, and
deploy/scrub-backups.sh deletes the same rows from every dump in data/backups,
then empties the journal.

    record(user_id, table, ids)                    -> rows deleted for good
    record(user_id, "persona", ids, "overwrite")   -> rows that survive changed;
                                                      the dumps get the live copy
    record(user_id, "account", [user_id])          -> a deleted account: every
                                                      row of it, in every table,
                                                      and its auth.users entry

Ids only, never content: the journal is itself in the dumps, and must not
become a list of what was deleted. Table names are the database's, checked
against what the scrub knows how to delete (db/schema.sql section 12).
"""
from __future__ import annotations

from typing import Iterable, Literal, Union
from uuid import UUID

from app.core.db import get_client

_TABLE = "deletion_journal"

Table = Literal["notes", "episodic_memory", "persona", "account"]
Kind = Literal["delete", "overwrite"]
TABLES: frozenset[str] = frozenset({"notes", "episodic_memory", "persona", "account"})

UserId = Union[UUID, str]


def record(user_id: UserId, table: Table, ids: Iterable[str], kind: Kind = "delete") -> None:
    """Journal these rows of `table` as deleted (or, with kind "overwrite",
    changed) for good. Raises on an unknown table or kind; does nothing for no
    ids."""
    if table not in TABLES:
        raise ValueError(f"the backup scrub doesn't know table {table!r}")
    if kind not in ("delete", "overwrite"):
        raise ValueError(f"unknown kind {kind!r}")
    rows = [
        {"user_id": str(user_id), "table_name": table, "row_id": str(i), "kind": kind}
        for i in dict.fromkeys(str(i) for i in ids if i)
    ]
    if rows:
        get_client().table(_TABLE).insert(rows).execute()
