#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "psycopg[binary]>=3.2,<4",
#   "python-slugify==8.0.4",
# ]
# ///
"""Safely backfill generated DPS collections into per-user STAC catalogs.

The default is a dry run. Review the report, then rerun with ``--apply``. The
backfill uses hydrated item metadata and the actual collection ID; it does not
parse collection IDs to infer ownership. It only handles collections whose
items agree on one complete DPS metadata tuple and whose ID matches either the
current generator default or its legacy tag-specific format. Collections
authorized by the supplied registry, named collections, mixed collections, and
incomplete or ambiguous metadata are reported and skipped.

Historical authorization is not present in every item record. A collection
that happens to have a generated-looking ID can therefore be indistinguishable
from an old authorized override when the registry is incomplete. This script
stays conservative by requiring an exact metadata match and skips any ID
currently authorized by the registry; review the remaining report before apply.
Existing catalog and collection metadata is preserved. Applying this script
only creates a missing user Catalog and adds its parent ID to the existing
Collection. It does not rewrite or rename Items.

Examples::

    uv run --script scripts/backfill_dps_user_catalogs.py --dry-run
    uv run --script scripts/backfill_dps_user_catalogs.py --apply
    uv run --script scripts/backfill_dps_user_catalogs.py --registry registry.json
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import logging
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

from slugify import slugify

LOGGER = logging.getLogger(__name__)
DEFAULT_DATABASE_URL = "postgresql://username:password@127.0.0.1:5439/postgis"
COLLECTION_ID_FORMAT = "{username}__{algorithm_name}__{algorithm_version}"
LEGACY_COLLECTION_ID_FORMAT = "{username}__{algorithm_name}__{algorithm_version}__{tag}"
METADATA_FIELDS = (
    "username",
    "algorithm_name",
    "algorithm_version",
    "tag",
)


def user_catalog_id(username: str) -> str:
    """Return the generator's stable, readable catalog ID for a username."""
    return f"user-{slugify(username, regex_pattern=r'[/\?#%& ]+')}"


def generated_collection_id(
    metadata: dict[str, str], collection_id_format: str = COLLECTION_ID_FORMAT
) -> str:
    """Return a slugified generated collection ID for the supplied format."""
    return slugify(collection_id_format.format(**metadata), regex_pattern=r"[/\?#%& ]+")


def catalog_document(username: str) -> dict[str, Any]:
    """Build a fresh generated user Catalog document."""
    return {
        "type": "Catalog",
        "stac_version": "1.1.0",
        "id": user_catalog_id(username),
        "title": f"{username} DPS Outputs",
        "description": f"DPS output collections generated for {username}.",
        "parent_ids": [],
        "links": [],
    }


def load_registry(path: str | None) -> dict[str, list[str]]:
    """Load an authorization registry from JSON or the matching environment."""
    raw = (
        Path(path).read_text(encoding="utf-8")
        if path
        else os.environ.get("USER_STAC_COLLECTION_ID_REGISTRY", "{}")
    )
    registry = json.loads(raw)
    if not isinstance(registry, dict):
        raise ValueError("registry must be a JSON object")
    return registry


def is_authorized(
    username: str, collection_id: str, registry: dict[str, list[str]]
) -> bool:
    """Return whether the registry authorizes this user for this collection."""
    return any(
        fnmatch.fnmatch(collection_id, pattern) and username in users
        for pattern, users in registry.items()
    )


def collection_rows(connection: Any) -> list[dict[str, Any]]:
    """Read collections with hydrated item-level DPS metadata."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                collections.id AS collection_id,
                collections.content AS collection_content,
                pgstac.format_item(items)->'properties'->>'maap-dps:username'
                    AS username,
                pgstac.format_item(items)->'properties'->>'maap-dps:algorithm_name'
                    AS algorithm_name,
                pgstac.format_item(items)->'properties'->>'processing:version'
                    AS algorithm_version,
                pgstac.format_item(items)->'properties'->>'maap-dps:tag' AS tag
            FROM pgstac.collections AS collections
            JOIN pgstac.items AS items
              ON items.collection = collections.id
            WHERE collections.content->>'type' = 'Collection'
            ORDER BY collections.id
            """
        )
        return list(cursor.fetchall())


def existing_records(connection: Any) -> dict[str, dict[str, Any]]:
    """Return existing pgSTAC Catalog and Collection documents by ID."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT id, content FROM pgstac.collections")
        return {row["id"]: row["content"] for row in cursor.fetchall()}


def build_plan(
    rows: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
    registry: dict[str, list[str]],
) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    """Build candidate backfills and explicit skip reasons without writing."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["collection_id"]].append(row)

    plan: list[dict[str, Any]] = []
    skipped: list[tuple[str, str]] = []
    for collection_id, item_rows in grouped.items():
        values = {
            field: {row.get(field) for row in item_rows if row.get(field)}
            for field in METADATA_FIELDS
        }
        if any(not values[field] for field in METADATA_FIELDS):
            skipped.append((collection_id, "missing DPS metadata"))
            continue
        if any(len(values[field]) != 1 for field in METADATA_FIELDS):
            skipped.append((collection_id, "mixed DPS metadata"))
            continue

        metadata = {field: values[field].pop() for field in METADATA_FIELDS}
        username = metadata["username"]
        generated_ids = {
            generated_collection_id(metadata),
            generated_collection_id(metadata, LEGACY_COLLECTION_ID_FORMAT),
        }
        if collection_id not in generated_ids:
            skipped.append((collection_id, "named or non-generated collection ID"))
            continue
        if is_authorized(username, collection_id, registry):
            skipped.append((collection_id, "authorized collection override"))
            continue

        catalog_id = user_catalog_id(username)
        catalog = records.get(catalog_id)
        if catalog is not None and catalog.get("type") != "Catalog":
            skipped.append((collection_id, f"ambiguous catalog ID {catalog_id}"))
            continue
        collection = records.get(collection_id, {})
        parent_ids = collection.get("parent_ids", [])
        if catalog_id in parent_ids:
            skipped.append((collection_id, "already linked"))
            continue
        plan.append(
            {
                "collection_id": collection_id,
                "catalog_id": catalog_id,
                "username": username,
                "catalog": catalog_document(username),
            }
        )

    return plan, skipped


def apply_plan(connection: Any, plan: list[dict[str, Any]]) -> None:
    """Create missing Catalogs and add parent links without replacing metadata."""
    with connection.cursor() as cursor:
        for candidate in plan:
            cursor.execute(
                """
                INSERT INTO pgstac.collections (content)
                VALUES (%s::jsonb)
                ON CONFLICT (id) DO NOTHING
                """,
                (json.dumps(candidate["catalog"]),),
            )
            cursor.execute(
                """
                UPDATE pgstac.collections
                SET content = jsonb_set(
                    content,
                    '{parent_ids}',
                    CASE
                        WHEN COALESCE(content->'parent_ids', '[]'::jsonb)
                             ? %s
                        THEN COALESCE(content->'parent_ids', '[]'::jsonb)
                        ELSE COALESCE(content->'parent_ids', '[]'::jsonb)
                             || jsonb_build_array(%s::text)
                    END,
                    true
                )
                WHERE id = %s
                """,
                (
                    candidate["catalog_id"],
                    candidate["catalog_id"],
                    candidate["collection_id"],
                ),
            )


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
        help=(
            "PostgreSQL URL; defaults to DATABASE_URL then the local Compose database."
        ),
    )
    parser.add_argument("--apply", action="store_true", help="Perform the backfill.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Report changes without applying them."
    )
    parser.add_argument(
        "--registry",
        help="JSON authorization registry file; defaults to the environment variable.",
    )
    return parser.parse_args()


def main() -> None:
    """Report or apply the conservative DPS catalog backfill."""
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    registry = load_registry(args.registry)

    from psycopg import connect
    from psycopg.errors import UndefinedTable
    from psycopg.rows import dict_row

    try:
        with connect(args.database_url, row_factory=dict_row) as connection:
            rows = collection_rows(connection)
            records = existing_records(connection)
            plan, skipped = build_plan(rows, records, registry)
            for candidate in plan:
                LOGGER.info(
                    "%s -> %s (catalog %s)",
                    candidate["collection_id"],
                    "add parent relationship",
                    candidate["catalog_id"],
                )
            for collection_id, reason in skipped:
                LOGGER.info("Skipping %s: %s", collection_id, reason)

            if args.dry_run or not args.apply:
                LOGGER.info(
                    "Dry run. Re-run with --apply to backfill %d collection(s).",
                    len(plan),
                )
                return
            apply_plan(connection, plan)
            LOGGER.info("Backfilled %d collection(s).", len(plan))
    except UndefinedTable as exc:
        raise SystemExit("The target database does not look like pgSTAC.") from exc


if __name__ == "__main__":
    main()
