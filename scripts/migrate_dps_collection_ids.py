#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "psycopg[binary]>=3.2,<4",
# ]
# ///
"""Merge legacy DPS tag collections into tag-free collection IDs.

Collections that cannot be merged because of duplicate item IDs retain their
legacy collection ID, but their Items still receive DPS metadata inferred from
that ID. By default (or with ``--dry-run``) this reports the changes. Pass
``--apply`` to make them. The script uses the local compose database by default; use the
Docker-network URL when running from a container:
``postgresql://username:password@database:5432/postgis``.

For deployed RDS, follow the connection guide in
README.md#connect-to-rds-through-an-ssm-tunnel, selecting userSTAC (internal).
Keep the tunnel open and run these commands in the terminal with the PG*
environment variables configured (requires uv)::

    uv run --script scripts/migrate_dps_collection_ids.py --database-url "" --dry-run
    uv run --script scripts/migrate_dps_collection_ids.py --database-url "" --apply
    uv run --script scripts/migrate_dps_collection_ids.py --database-url "" \\
        --apply --batch-size 100

The empty --database-url tells psycopg to use the PG* environment variables.
Omitting it uses DATABASE_URL or the local Compose default instead. Apply uses
100 source collections per transaction by default; this default is not
production-validated. Destination groups are split across batches when needed;
the first chunk creates the destination collection and later chunks append to
it. Completed batches remain committed if a later batch fails; rerun after
fixing the failure to complete the remaining work. Metadata-only updates for
retained collision sources are batched too.
Before applying, review the dry-run plan, confirm a recoverable backup, pause
all writers, and drain in-flight ingestion and existing pgSTAC queued work.
Apply disables queueing only within each batch transaction. The conflict check
does not prevent concurrent writes. Verify collection and item counts and check
pgSTAC queued work before resuming ingestion; the deployed stack enables
use_queue.
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
DEFAULT_BATCH_SIZE = 100


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


def conflicting_source_collections(
    connection: Any, plan: dict[str, list[str]]
) -> list[str]:
    """Return legacy collections containing items that conflict after merging."""
    skipped: set[str] = set()
    with connection.cursor() as cursor:
        for target_id, source_ids in plan.items():
            LOGGER.info("Checking item-ID conflicts for %s", target_id)
            cursor.execute(
                """
                WITH conflicts AS (
                    SELECT id
                    FROM pgstac.items
                    WHERE collection = ANY(%s)
                    GROUP BY id
                    HAVING count(*) > 1
                )
                SELECT DISTINCT items.collection AS source_id
                FROM pgstac.items
                JOIN conflicts USING (id)
                WHERE items.collection = ANY(%s)
                """,
                ([target_id, *source_ids], source_ids),
            )
            skipped.update(row["source_id"] for row in cursor.fetchall())
    return sorted(skipped)


def conflicting_item_ids(connection: Any, plan: dict[str, list[str]]) -> list[str]:
    """Return item-ID conflicts that would be created by the migration."""
    conflicts: list[str] = []
    with connection.cursor() as cursor:
        for target_id, source_ids in sorted(plan.items()):
            cursor.execute(
                """
                SELECT id
                FROM pgstac.items
                WHERE collection = ANY(%s)
                GROUP BY id
                HAVING count(*) > 1
                ORDER BY id
                """,
                ([target_id, *source_ids],),
            )
            conflicts.extend(f"{target_id}/{row['id']}" for row in cursor.fetchall())
    return conflicts


def migration_batches(
    plan: dict[str, list[str]], skipped_sources: list[str], batch_size: int
) -> list[tuple[dict[str, list[str]], list[str]]]:
    """Split migration and metadata work into source-collection-sized batches."""
    groups = [
        ({target_id: source_ids[start : start + batch_size]}, [])
        for target_id, source_ids in sorted(plan.items())
        for start in range(0, len(source_ids), batch_size)
    ]
    groups.extend(({}, [source_id]) for source_id in skipped_sources)

    batches: list[tuple[dict[str, list[str]], list[str]]] = []
    batch_plan: dict[str, list[str]] = {}
    batch_skipped: list[str] = []
    source_count = 0
    for group_plan, group_skipped in groups:
        group_size = sum(map(len, group_plan.values())) + len(group_skipped)
        if source_count and source_count + group_size > batch_size:
            batches.append((batch_plan, batch_skipped))
            batch_plan, batch_skipped, source_count = {}, [], 0
        batch_plan.update(group_plan)
        batch_skipped.extend(group_skipped)
        source_count += group_size
    if batch_plan or batch_skipped:
        batches.append((batch_plan, batch_skipped))
    return batches


def configure_transaction(connection: Any, *, disable_queue: bool) -> None:
    """Apply the migration's per-transaction resource and queue settings."""
    connection.execute("SET LOCAL work_mem = '4MB'")
    connection.execute("SET LOCAL hash_mem_multiplier = 1")
    connection.execute("SET LOCAL max_parallel_workers_per_gather = 0")
    connection.execute("SET LOCAL jit = off")
    if disable_queue:
        connection.execute("SET LOCAL pgstac.use_queue = false")


def positive_batch_size(value: str) -> int:
    """Parse a strictly positive batch size for the command line."""
    try:
        batch_size = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "batch size must be a positive integer"
        ) from error
    if batch_size <= 0:
        raise argparse.ArgumentTypeError("batch size must be a positive integer")
    return batch_size


def apply_item_metadata(connection: Any, source_ids: list[str]) -> None:
    """Add DPS metadata inferred from legacy IDs without moving their items."""
    if not source_ids:
        return

    source_parts = [source_id.split("__") for source_id in source_ids]
    with connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE TEMP TABLE dps_metadata_items (content) ON COMMIT DROP AS
            SELECT jsonb_set(
                jsonb_set(
                    jsonb_set(
                        jsonb_set(
                            pgstac.format_item(items),
                            '{properties,maap-dps:algorithm_name}',
                            to_jsonb(mapping.algorithm_name)
                        ),
                        '{properties,processing:version}',
                        to_jsonb(mapping.algorithm_version)
                    ),
                    '{properties,maap-dps:username}', to_jsonb(mapping.username)
                ),
                '{properties,maap-dps:tag}', to_jsonb(mapping.tag)
            )
            FROM pgstac.items
            JOIN unnest(
                %s::text[], %s::text[], %s::text[], %s::text[], %s::text[]
            ) AS mapping(
                source_id, username, algorithm_name, algorithm_version, tag
            ) ON items.collection = mapping.source_id
            """,
            (
                source_ids,
                [parts[0] for parts in source_parts],
                [parts[1] for parts in source_parts],
                [parts[2] for parts in source_parts],
                [parts[3] for parts in source_parts],
            ),
        )
        # Finish reading items before pgSTAC's triggers alter their partitions.
        cursor.execute(
            "INSERT INTO pgstac.items_staging_upsert (content) "
            "SELECT content FROM pg_temp.dps_metadata_items"
        )
        cursor.execute("DROP TABLE pg_temp.dps_metadata_items")


def apply_migration(connection: Any, plan: dict[str, list[str]]) -> None:
    """Create tag-free collections, move their items, and remove old collections."""
    with connection.cursor() as cursor:
        cursor.execute(
            "CREATE TEMP TABLE dps_migration_items (content jsonb) ON COMMIT DROP"
        )
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
            source_parts = [source_id.split("__") for source_id in source_ids]
            cursor.execute(
                """
                INSERT INTO pg_temp.dps_migration_items (content)
                SELECT jsonb_set(
                    jsonb_set(
                        jsonb_set(
                            jsonb_set(
                                jsonb_set(
                                    pgstac.format_item(items),
                                    '{collection}',
                                    to_jsonb(mapping.target_id)
                                ),
                                '{properties,maap-dps:algorithm_name}',
                                to_jsonb(mapping.algorithm_name)
                            ),
                            '{properties,processing:version}',
                            to_jsonb(mapping.algorithm_version)
                        ),
                        '{properties,maap-dps:username}', to_jsonb(mapping.username)
                    ),
                    '{properties,maap-dps:tag}', to_jsonb(mapping.tag)
                )
                FROM pgstac.items
                JOIN unnest(
                    %s::text[],
                    %s::text[],
                    %s::text[],
                    %s::text[],
                    %s::text[],
                    %s::text[]
                ) AS mapping(
                    source_id,
                    target_id,
                    username,
                    algorithm_name,
                    algorithm_version,
                    tag
                )
                    ON items.collection = mapping.source_id
                """,
                (
                    source_ids,
                    [target_id] * len(source_ids),
                    [parts[0] for parts in source_parts],
                    [parts[1] for parts in source_parts],
                    [parts[2] for parts in source_parts],
                    [parts[3] for parts in source_parts],
                ),
            )
            # A separate statement releases the read's active partition scans.
            cursor.execute(
                "INSERT INTO pgstac.items_staging_upsert (content) "
                "SELECT content FROM pg_temp.dps_migration_items"
            )
            cursor.execute("TRUNCATE pg_temp.dps_migration_items")
            cursor.execute(
                "DELETE FROM pgstac.items WHERE collection = ANY(%s)", (source_ids,)
            )
            cursor.execute(
                "DELETE FROM pgstac.collections WHERE id = ANY(%s)", (source_ids,)
            )
        cursor.execute("DROP TABLE pg_temp.dps_migration_items")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
        help=(
            "PostgreSQL URL; defaults to DATABASE_URL then the local compose database."
        ),
    )
    parser.add_argument("--apply", action="store_true", help="Perform the migration.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Report changes without applying them."
    )
    parser.add_argument(
        "--batch-size",
        type=positive_batch_size,
        default=DEFAULT_BATCH_SIZE,
        help=(
            "Maximum source collections per apply transaction; destination groups "
            "are split across batches (default: 100)."
        ),
    )
    return parser.parse_args()


def main() -> None:
    """Report or apply the DPS collection-ID migration."""
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    # Autocommit keeps planning and each apply batch in explicit, short
    # transactions instead of leaving a planning transaction open.
    with connect(
        args.database_url, row_factory=dict_row, autocommit=True
    ) as connection:
        with connection.transaction():
            configure_transaction(connection, disable_queue=False)
            plan = migration_plan(connection)
            if not plan:
                LOGGER.info("No four-part legacy DPS collection IDs found.")
                return

            skipped_sources = conflicting_source_collections(connection, plan)
            if skipped_sources:
                LOGGER.warning(
                    "Skipping %d legacy collection(s) with duplicate item IDs: %s",
                    len(skipped_sources),
                    ", ".join(skipped_sources),
                )
                skipped = set(skipped_sources)
                plan = {
                    target_id: [
                        source_id
                        for source_id in source_ids
                        if source_id not in skipped
                    ]
                    for target_id, source_ids in plan.items()
                }
                plan = {
                    target_id: source_ids
                    for target_id, source_ids in plan.items()
                    if source_ids
                }

            for source_id in skipped_sources:
                LOGGER.info(
                    "%s -> retain collection and add DPS item metadata", source_id
                )
            for target_id, source_ids in plan.items():
                LOGGER.info("%s -> %s", ", ".join(source_ids), target_id)

            conflicts = conflicting_item_ids(connection, plan)
            if conflicts:
                raise SystemExit(
                    "Refusing to merge duplicate item IDs: " + ", ".join(conflicts)
                )

            batches = migration_batches(plan, skipped_sources, args.batch_size)
            LOGGER.info(
                "Proposed %d batch(es), up to %d source collection(s) each.",
                len(batches),
                args.batch_size,
            )
            for batch_number, (batch_plan, batch_skipped) in enumerate(batches, 1):
                source_count = sum(map(len, batch_plan.values())) + len(batch_skipped)
                LOGGER.info(
                    "Batch %d/%d: %d source collection(s), %d destination group(s)",
                    batch_number,
                    len(batches),
                    source_count,
                    len(batch_plan),
                )

            if args.dry_run or not args.apply:
                LOGGER.info(
                    "Dry run. Re-run with --apply to migrate %d collection(s) and add "
                    "DPS item metadata to %d retained collection(s).",
                    sum(map(len, plan.values())),
                    len(skipped_sources),
                )
                return

        for batch_number, (batch_plan, batch_skipped) in enumerate(batches, 1):
            source_count = sum(map(len, batch_plan.values())) + len(batch_skipped)
            LOGGER.info(
                "Applying batch %d/%d: %d source collection(s), "
                "%d destination group(s)",
                batch_number,
                len(batches),
                source_count,
                len(batch_plan),
            )
            try:
                with connection.transaction():
                    configure_transaction(connection, disable_queue=True)
                    apply_item_metadata(connection, batch_skipped)
                    apply_migration(connection, batch_plan)
            except Exception:
                LOGGER.exception(
                    "Batch %d/%d failed and was rolled back; %d earlier batch(es) "
                    "remain committed. Restore the connection, fix the error, "
                    "and rerun.",
                    batch_number,
                    len(batches),
                    batch_number - 1,
                )
                raise
            LOGGER.info("Committed batch %d/%d.", batch_number, len(batches))

        LOGGER.info(
            "Migrated %d collection(s) and added DPS item metadata to %d retained "
            "collection(s) across %d committed batch(es).",
            sum(map(len, plan.values())),
            len(skipped_sources),
            len(batches),
        )


if __name__ == "__main__":
    main()
