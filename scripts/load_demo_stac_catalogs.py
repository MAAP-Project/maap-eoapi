#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "pypgstac[psycopg]>=0.9,<0.10",
# ]
# ///
"""Load demo STAC catalogs and collections into the local pgSTAC database.

The script is intentionally standalone. Run it from the repository root after
starting the local database with `docker compose up database`, or point it at a
pgSTAC database with `--database-url`.
"""

from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from psycopg.errors import UndefinedFunction, UndefinedTable
from pypgstac.db import PgstacDB
from pypgstac.load import Loader, Methods

LOGGER = logging.getLogger(__name__)

DEFAULT_USERS = ("hrodmn", "jjfrench")
DEFAULT_DATABASE_URL = "postgresql://username:password@127.0.0.1:5439/postgis"
DEMO_GROUP_ID = "maap-demo-team"
DEMO_TEAM_CATALOGS_ID = "maap-demo-dps-team-catalogs"
DEMO_USER_CATALOGS_ID = "maap-demo-dps-user-catalogs"


@dataclass(frozen=True)
class DemoCollection:
    """Configuration for a demo collection."""

    id: str
    title: str
    description: str
    owner: str
    parent_ids: tuple[str, ...]
    keywords: tuple[str, ...]


def utc_now() -> str:
    """Return the current UTC time formatted for STAC metadata."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def catalog(
    catalog_id: str,
    title: str,
    description: str,
    parent_ids: tuple[str, ...],
) -> dict[str, Any]:
    """Build a minimal STAC Catalog record compatible with the Catalogs Extension."""
    return {
        "type": "Catalog",
        "stac_version": "1.0.0",
        "id": catalog_id,
        "title": title,
        "description": description,
        "parent_ids": list(parent_ids),
        "links": [],
    }


def collection(config: DemoCollection) -> dict[str, Any]:
    """Build a minimal STAC Collection record linked to one or more catalogs."""
    now = utc_now()
    return {
        "type": "Collection",
        "stac_version": "1.0.0",
        "id": config.id,
        "title": config.title,
        "description": config.description,
        "license": "proprietary",
        "keywords": list(config.keywords),
        "providers": [
            {
                "name": "MAAP Demo DPS",
                "roles": ["producer", "processor"],
                "url": "https://maap-project.org/",
            }
        ],
        "extent": {
            "spatial": {"bbox": [[-180.0, -90.0, 180.0, 90.0]]},
            "temporal": {"interval": [["2020-01-01T00:00:00Z", None]]},
        },
        "summaries": {
            "platform": ["MAAP DPS"],
            "instruments": ["demo"],
            "maap:owner": [config.owner],
        },
        "created": now,
        "updated": now,
        "parent_ids": list(config.parent_ids),
        "links": [],
    }


def build_demo_records(users: tuple[str, ...]) -> list[dict[str, Any]]:
    """Build the catalog and collection records for the sample MAAP users."""
    records: list[dict[str, Any]] = [
        catalog(
            DEMO_USER_CATALOGS_ID,
            "DPS User Catalogs",
            "Container for demo per-user DPS output catalogs.",
            (),
        ),
        catalog(
            DEMO_TEAM_CATALOGS_ID,
            "DPS Team Catalogs",
            "Container for demo shared team DPS output catalogs.",
            (),
        ),
        catalog(
            DEMO_GROUP_ID,
            "MAAP Demo Team",
            "Shared catalog showing how user collections can also appear in a group view.",
            (DEMO_TEAM_CATALOGS_ID,),
        ),
    ]

    for username in users:
        user_catalog_id = f"user-{username}"
        records.append(
            catalog(
                user_catalog_id,
                f"{username} DPS Outputs",
                f"Demo per-user catalog for DPS outputs owned by {username}.",
                (DEMO_USER_CATALOGS_ID,),
            )
        )
        records.extend(
            [
                collection(
                    DemoCollection(
                        id=f"{username}-canopy-height-demo",
                        title=f"{username} Canopy Height Demo",
                        description=(
                            "Synthetic DPS output collection for exploring STAC collection "
                            "management, per-user catalogs, and scoped catalog browsing."
                        ),
                        owner=username,
                        parent_ids=(user_catalog_id, DEMO_GROUP_ID),
                        keywords=("maap", "dps", "canopy-height", username),
                    )
                ),
                collection(
                    DemoCollection(
                        id=f"{username}-biomass-demo",
                        title=f"{username} Biomass Demo",
                        description=(
                            "Synthetic biomass DPS output collection used as local demo data "
                            "for transaction-backed collection and catalog workflows."
                        ),
                        owner=username,
                        parent_ids=(user_catalog_id,),
                        keywords=("maap", "dps", "biomass", username),
                    )
                ),
            ]
        )

    return records


def load_records(db: PgstacDB, records: list[dict[str, Any]]) -> None:
    """Upsert catalog and collection records with pypgstac."""
    loader = Loader(db)
    loader.load_collections(iter(records), insert_mode=Methods.upsert)


def collection_parent_ids(db: PgstacDB) -> dict[str, tuple[str, ...]]:
    """Return all catalog and collection ids keyed to their catalog parents."""
    rows = db.query(
        """
        SELECT id, COALESCE(content->'parent_ids', '[]'::jsonb)
        FROM collections
        """,
    )
    return {record_id: tuple(parent_ids or []) for record_id, parent_ids in rows}


def deletion_order(parent_ids_by_record: dict[str, tuple[str, ...]]) -> list[str]:
    """Return collection ids ordered so children are deleted before parents."""

    def depth(record_id: str, visited: frozenset[str] = frozenset()) -> int:
        if record_id in visited:
            return 0
        parent_ids = parent_ids_by_record.get(record_id, ())
        parent_depths = [
            depth(parent_id, visited | {record_id})
            for parent_id in parent_ids
            if parent_id in parent_ids_by_record
        ]
        return 1 + max(parent_depths, default=0)

    return sorted(parent_ids_by_record, key=depth, reverse=True)


def delete_all_records(db: PgstacDB) -> None:
    """Delete all catalog and collection records in child-first order."""
    for record_id in deletion_order(collection_parent_ids(db)):
        LOGGER.info("Deleting %s", record_id)
        list(db.func("delete_collection", record_id))


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
        help=(
            "PostgreSQL connection URL. Defaults to DATABASE_URL, then the local "
            "docker-compose database on 127.0.0.1:5439. Use "
            "postgresql://username:password@database:5432/postgis from inside the "
            "maap-eoapi Docker network."
        ),
    )
    parser.add_argument(
        "--user",
        dest="users",
        action="append",
        help="Sample username to include. Can be passed multiple times.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete all existing catalog and collection records before loading demo records.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log the records that would be loaded without touching the database.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser.parse_args()


def main() -> None:
    """Load demo STAC catalog records into pgSTAC."""
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    users = tuple(args.users) if args.users else DEFAULT_USERS
    records = build_demo_records(users)

    if args.dry_run:
        for record in records:
            LOGGER.info(
                "Would load %s %s with parents %s",
                record["type"],
                record["id"],
                record.get("parent_ids", []),
            )
        return

    db = PgstacDB(dsn=args.database_url)
    try:
        if args.reset:
            delete_all_records(db)

        load_records(db, records)
        for record in records:
            LOGGER.info("upsert %s %s", record["type"], record["id"])
    except (UndefinedFunction, UndefinedTable) as exc:
        raise SystemExit(
            "The target database does not look like a pgSTAC database. "
            "Start the local stack with `docker compose up database` and retry."
        ) from exc
    finally:
        db.close()

    LOGGER.info(
        "Loaded %d demo STAC catalog records for users: %s",
        len(records),
        ", ".join(users),
    )


if __name__ == "__main__":
    main()
