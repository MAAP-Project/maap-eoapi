from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest
from psycopg import connect
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

SCRIPT = Path(__file__).parents[5] / "scripts" / "migrate_dps_collection_ids.py"
MODULE_SPEC = importlib.util.spec_from_file_location(
    "migrate_dps_collection_ids", SCRIPT
)
assert MODULE_SPEC and MODULE_SPEC.loader
migration = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(migration)


class RecordingCursor:
    """Record the SQL issued by a migration helper."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[list[str], ...]]] = []

    def __enter__(self) -> RecordingCursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: str, parameters: tuple[list[str], ...] = ()) -> None:
        """Record an executed statement and its parameters."""
        self.calls.append((statement, parameters))


class RecordingConnection:
    """Provide the cursor boundary needed by the migration helper."""

    def __init__(self) -> None:
        self.recording_cursor = RecordingCursor()

    def cursor(self) -> RecordingCursor:
        """Return the recording cursor."""
        return self.recording_cursor


def test_migration_batches_share_destination_groups():
    """Destination groups share a batch up to the source cap."""
    plan = {
        "x": ["x__one", "x__two"],
        "y": ["y__one", "y__two"],
    }

    batches = migration.migration_batches(plan, [], batch_size=4)

    assert batches == [(plan, [])]


def test_migration_batches_split_destination_groups():
    """A destination group larger than the cap is migrated in source chunks."""
    plan = {"x": ["x__one", "x__two"], "y": ["y__one", "y__two", "y__three"]}

    batches = migration.migration_batches(
        plan, ["retained__algo__1__tag"], batch_size=2
    )

    assert batches == [
        ({"x": ["x__one", "x__two"]}, []),
        ({"y": ["y__one", "y__two"]}, []),
        ({"y": ["y__three"]}, ["retained__algo__1__tag"]),
    ]


def test_migration_batches_bound_metadata_only_sources():
    """Retained collision sources count toward the batch cap."""
    plan = {"x": ["x__one"]}

    batches = migration.migration_batches(
        plan, ["retained__algo__1__a", "retained__algo__1__b"], batch_size=2
    )

    assert batches == [
        ({"x": ["x__one"]}, ["retained__algo__1__a"]),
        ({}, ["retained__algo__1__b"]),
    ]


@pytest.mark.parametrize("value", ["0", "-1", "one"])
def test_positive_batch_size_rejects_invalid_values(value):
    """Batch size validation fails before a database connection is needed."""
    with pytest.raises(migration.argparse.ArgumentTypeError):
        migration.positive_batch_size(value)


def test_apply_item_metadata_keeps_legacy_collection_id():
    """Conflicting legacy collections receive metadata without being moved."""
    connection = RecordingConnection()
    source_id = "alice__algorithm__1.0__nightly"

    migration.apply_item_metadata(connection, [source_id])

    statement, parameters = connection.recording_cursor.calls[0]
    assert "CREATE TEMP TABLE dps_metadata_items" in statement
    assert "'{collection}'" not in statement
    assert parameters == ([source_id], ["alice"], ["algorithm"], ["1.0"], ["nightly"])


@pytest.mark.parametrize("use_queue", [True, False])
def test_apply_with_pgstac_partition_triggers(use_queue):
    """Preserve items while pgSTAC expands an existing destination partition."""
    database_url = os.environ.get("PGSTAC_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set PGSTAC_TEST_DATABASE_URL to a disposable pgSTAC database")

    target = "migration_test__algo__1"
    source = f"{target}__day"
    retained = f"{target}__night"
    with (
        connect(database_url, row_factory=dict_row) as connection,
        connection.transaction(force_rollback=True),
    ):
        connection.execute("SET LOCAL search_path = pgstac, public")
        connection.execute(
            "SELECT set_config('pgstac.use_queue', %s, true)",
            (str(use_queue).lower(),),
        )
        # Exercise the generic plan that can scan destination partitions too.
        connection.execute("SET LOCAL plan_cache_mode = force_generic_plan")
        connection.execute("SET LOCAL enable_partition_pruning = off")
        connection.prepare_threshold = 0
        for collection_id in (target, source, retained):
            connection.execute(
                "SELECT pgstac.create_collection(%s)",
                (
                    Jsonb(
                        {
                            "type": "Collection",
                            "stac_version": "1.0.0",
                            "id": collection_id,
                            "description": "Migration regression test",
                            "license": "proprietary",
                            "links": [],
                            "extent": {
                                "spatial": {"bbox": [[-180, -90, 180, 90]]},
                                "temporal": {"interval": [[None, None]]},
                            },
                        }
                    ),
                ),
            )
        for collection_id, item_id, date in (
            (target, "existing", "2020-01-01T00:00:00Z"),
            (source, "moving", "2021-01-01T00:00:00Z"),
            (retained, "existing", "2022-01-01T00:00:00Z"),
        ):
            connection.execute(
                "INSERT INTO pgstac.items_staging_upsert (content) VALUES (%s)",
                (
                    Jsonb(
                        {
                            "type": "Feature",
                            "stac_version": "1.0.0",
                            "id": item_id,
                            "collection": collection_id,
                            "geometry": {"type": "Point", "coordinates": [0, 0]},
                            "bbox": [0, 0, 0, 0],
                            "properties": {"datetime": date},
                            "links": [],
                            "assets": {},
                        }
                    ),
                ),
            )

        connection.execute(
            "SELECT pgstac.update_partition_stats(partition) "
            "FROM pgstac.partition_sys_meta WHERE collection = %s",
            (target,),
        )
        migration.apply_item_metadata(connection, [retained])
        migration.apply_migration(connection, {target: [source]})

        rows = connection.execute(
            "SELECT collection, id, pgstac.format_item(items) AS content "
            "FROM pgstac.items WHERE collection = ANY(%s) ORDER BY collection, id",
            ([target, source, retained],),
        ).fetchall()
        assert [(row["collection"], row["id"]) for row in rows] == [
            (target, "existing"),
            (target, "moving"),
            (retained, "existing"),
        ]
        assert rows[1]["content"]["properties"]["maap-dps:tag"] == "day"
        assert rows[2]["content"]["properties"]["maap-dps:tag"] == "night"
        assert rows[1]["content"]["properties"]["datetime"] == "2021-01-01T00:00:00Z"
        assert (
            connection.execute(
                "SELECT id FROM pgstac.collections WHERE id = %s", (source,)
            ).fetchone()
            is None
        )


def test_conflicts_across_collection_groups():
    """Execute conflict checks against an empty disposable PostgreSQL database."""
    database_url = os.environ.get("MIGRATION_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set MIGRATION_TEST_DATABASE_URL to an empty test database")

    with (
        connect(database_url, row_factory=dict_row) as connection,
        connection.transaction(force_rollback=True),
    ):
        # Deliberately fail if pgstac exists; never replace real catalog tables.
        connection.execute("CREATE SCHEMA pgstac")
        connection.execute("CREATE TABLE pgstac.collections (id text PRIMARY KEY)")
        connection.execute(
            "CREATE TABLE pgstac.items (collection text, id text) "
            "PARTITION BY LIST (collection)"
        )
        connection.execute(
            "CREATE TABLE pgstac.items_default PARTITION OF pgstac.items DEFAULT"
        )
        connection.execute(
            """
                INSERT INTO pgstac.collections VALUES
                    ('a__algo__1'), ('a__algo__1__day'), ('a__algo__1__night'),
                    ('a__algo__1__safe'), ('b__algo__1__day'), ('b__algo__1__night'),
                    ('c__algo__1__empty'), ('unrelated');
                INSERT INTO pgstac.items VALUES
                    ('a__algo__1__day', 'sibling-collision'),
                    ('a__algo__1__night', 'sibling-collision'),
                    ('a__algo__1', 'target-collision'),
                    ('a__algo__1__day', 'target-collision'),
                    ('a__algo__1__safe', 'safe'),
                    ('b__algo__1__day', 'sibling-collision'),
                    ('b__algo__1__night', 'safe'),
                    ('unrelated', 'safe');
                """
        )
        plan = migration.migration_plan(connection)
        assert migration.conflicting_source_collections(connection, {}) == []
        assert migration.conflicting_item_ids(connection, {}) == []
        skipped = migration.conflicting_source_collections(connection, plan)
        assert skipped == ["a__algo__1__day", "a__algo__1__night"]
        assert migration.conflicting_item_ids(connection, plan) == [
            "a__algo__1/sibling-collision",
            "a__algo__1/target-collision",
        ]
        remaining = {
            target: [source for source in sources if source not in skipped]
            for target, sources in plan.items()
        }
        assert migration.conflicting_source_collections(connection, remaining) == []
        assert migration.conflicting_item_ids(connection, remaining) == []
