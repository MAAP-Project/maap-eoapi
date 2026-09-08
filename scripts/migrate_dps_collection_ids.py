#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "psycopg[binary]>=3.2,<4",
# ]
# ///
"""Merge legacy DPS tag collections into tag-free collection IDs.

By default (or with ``--dry-run``) this reports the changes. Pass ``--apply`` to make them. The script
uses the local compose database by default; use the Docker-network URL when
running from a container:
``postgresql://username:password@database:5432/postgis``.
"""

from __future__ import annotations

import argparse
import logging
import os
from collections import defaultdict
from typing import Any

from psycopg import connect
from psycopg.rows import dict_row

LOGGER = logging.getLogger(__name__)
DEFAULT_DATABASE_URL = "postgresql://username:password@127.0.0.1:5439/postgis"


def target_collection_id(collection_id: str) -> str | None:
    """Return the tag-free ID for a four-part legacy DPS collection ID."""
    parts = collection_id.split("__")
    if len(parts) != 4 or not all(parts):
        return None
    return "__".join(parts[:-1])


def migration_plan(connection: Any) -> dict[str, list[str]]:
    """Return legacy collection IDs grouped by their replacement ID."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT id FROM pgstac.collections ORDER BY id")
        collection_ids = [row["id"] for row in cursor.fetchall()]

    plan: dict[str, list[str]] = defaultdict(list)
    for collection_id in collection_ids:
        if target_id := target_collection_id(collection_id):
            plan[target_id].append(collection_id)
    return dict(plan)


def conflicting_item_ids(connection: Any, plan: dict[str, list[str]]) -> list[str]:
    """Return item-ID conflicts that would be created by the migration."""
    sources = [source for source_ids in plan.values() for source in source_ids]
    if not sources:
        return []

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT target_id, id
            FROM (
                SELECT COALESCE(mapping.target_id, items.collection) AS target_id, items.id
                FROM pgstac.items
                LEFT JOIN unnest(%s::text[], %s::text[]) AS mapping(source_id, target_id)
                    ON items.collection = mapping.source_id
                WHERE items.collection = ANY(%s)
                   OR items.collection = ANY(%s)
            ) AS migrated_items
            GROUP BY target_id, id
            HAVING count(*) > 1
            ORDER BY target_id, id
            """,
            (
                sources,
                [target for target, source_ids in plan.items() for _ in source_ids],
                sources,
                list(plan),
            ),
        )
        return [f"{row['target_id']}/{row['id']}" for row in cursor.fetchall()]


def apply_migration(connection: Any, plan: dict[str, list[str]]) -> None:
    """Create tag-free collections, move their items, and remove old collections."""
    with connection.cursor() as cursor:
        for target_id, source_ids in plan.items():
            source_id = source_ids[0]
            cursor.execute(
                """
                INSERT INTO pgstac.collections (content)
                SELECT jsonb_set(content, '{id}', to_jsonb(%s::text))
                FROM pgstac.collections
                WHERE id = %s
                ON CONFLICT (id) DO NOTHING
                """,
                (target_id, source_id),
            )
            cursor.execute(
                """
                INSERT INTO pgstac.items_staging_upsert (content)
                SELECT jsonb_set(pgstac.format_item(items), '{collection}', to_jsonb(%s::text))
                FROM pgstac.items
                WHERE collection = ANY(%s)
                """,
                (target_id, source_ids),
            )
            cursor.execute(
                "DELETE FROM pgstac.items WHERE collection = ANY(%s)", (source_ids,)
            )
            cursor.execute(
                "DELETE FROM pgstac.collections WHERE id = ANY(%s)", (source_ids,)
            )


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
        help="PostgreSQL URL; defaults to DATABASE_URL then the local compose database.",
    )
    parser.add_argument("--apply", action="store_true", help="Perform the migration.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Report changes without applying them."
    )
    return parser.parse_args()


def main() -> None:
    """Report or apply the DPS collection-ID migration."""
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    with connect(args.database_url, row_factory=dict_row) as connection:
        plan = migration_plan(connection)
        if not plan:
            LOGGER.info("No four-part legacy DPS collection IDs found.")
            return

        for target_id, source_ids in plan.items():
            LOGGER.info("%s -> %s", ", ".join(source_ids), target_id)

        conflicts = conflicting_item_ids(connection, plan)
        if conflicts:
            raise SystemExit(
                "Refusing to merge duplicate item IDs: " + ", ".join(conflicts)
            )
        if args.dry_run or not args.apply:
            LOGGER.info(
                "Dry run. Re-run with --apply to migrate %d collection(s).",
                sum(map(len, plan.values())),
            )
            return

        apply_migration(connection, plan)
        LOGGER.info("Migrated %d collection(s).", sum(map(len, plan.values())))


if __name__ == "__main__":
    main()
