"""End-to-end migration checks in disposable databases cloned from pgSTAC."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from psycopg import connect
from psycopg.conninfo import make_conninfo
from psycopg.sql import SQL, Identifier
from psycopg.types.json import Jsonb

SCRIPT = Path(__file__).parents[5] / "scripts" / "migrate_dps_collection_ids.py"


@pytest.fixture
def database_url():
    """Clone the test database and drop the clone even when assertions fail."""
    template_url = os.environ.get("PGSTAC_TEST_DATABASE_URL")
    if not template_url:
        pytest.skip(
            "Set PGSTAC_TEST_DATABASE_URL to an empty disposable pgSTAC database"
        )
    with connect(template_url) as connection:
        template = connection.info.dbname
        assert (
            connection.execute("SELECT count(*) FROM pgstac.collections").fetchone()[0]
            == 0
        )
    database = f"migration_cli_{uuid4().hex}"
    with connect(template_url, dbname="template1", autocommit=True) as admin:
        admin.execute(
            SQL("CREATE DATABASE {} TEMPLATE {}").format(
                Identifier(database), Identifier(template)
            )
        )
        try:
            yield make_conninfo(template_url, dbname=database)
        finally:
            admin.execute(
                SQL("DROP DATABASE {} WITH (FORCE)").format(Identifier(database))
            )


def snapshot(database_url):
    """Read committed catalog contents and pending work from a new connection."""
    with connect(database_url) as connection:
        return (
            connection.execute(
                "SELECT content FROM pgstac.collections ORDER BY id"
            ).fetchall(),
            connection.execute(
                "SELECT pgstac.format_item(items) FROM pgstac.items "
                "ORDER BY collection, id"
            ).fetchall(),
            connection.execute(
                "SELECT query FROM pgstac.query_queue ORDER BY query"
            ).fetchall(),
        )


@pytest.mark.parametrize("use_queue", [True, False])
def test_cli_batch_commit_rollback_and_maintenance(database_url, use_queue):
    """Commit earlier batches, roll back a failed batch, then recover on rerun."""
    database_url = make_conninfo(
        database_url,
        options="-c search_path=pgstac,public "
        f"-c pgstac.use_queue={str(use_queue).lower()} "
        "-c enable_partition_pruning=off -c plan_cache_mode=force_generic_plan",
    )
    existing = "aa__algo__1"
    moving = existing + "__day"
    retained = existing + "__night"
    retained_again = existing + "__dawn"
    new_target = "zz__algo__1"
    new_sources = [new_target + "__day", new_target + "__night"]
    collections = [
        existing,
        moving,
        retained,
        retained_again,
        *new_sources,
        "unrelated",
    ]
    with connect(database_url) as connection:
        for collection_id in collections:
            connection.execute(
                "SELECT pgstac.create_collection(%s)",
                (
                    Jsonb(
                        {
                            "type": "Collection",
                            "stac_version": "1.0.0",
                            "id": collection_id,
                            "description": collection_id,
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
        for collection_id, item_id, year in (
            (existing, "duplicate", 2020),
            (moving, "moving", 2021),
            (retained, "duplicate", 2022),
            (retained_again, "duplicate", 2022),
            (new_sources[0], "new-day", 2023),
            (new_sources[1], "new-night", 2024),
            ("unrelated", "untouched", 2025),
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
                            "properties": {
                                "datetime": f"{year}-01-01T00:00:00Z",
                                "custom": 42,
                            },
                            "links": [],
                            "assets": {"data": {"href": "s3://test/data.tif"}},
                        }
                    ),
                ),
            )
        connection.execute(
            "SELECT pgstac.update_partition_stats(partition) "
            "FROM pgstac.partition_sys_meta WHERE collection = %s",
            (existing,),
        )
        # Record transaction-local settings on every committed source deletion.
        connection.execute("""
            CREATE TABLE public.migration_settings_log (
                work_mem text,
                hash_mem_multiplier text,
                max_parallel_workers_per_gather text,
                jit text,
                use_queue text
            );
            CREATE FUNCTION public.record_migration_settings() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                INSERT INTO public.migration_settings_log
                VALUES (
                    current_setting('work_mem'),
                    current_setting('hash_mem_multiplier'),
                    current_setting('max_parallel_workers_per_gather'),
                    current_setting('jit'),
                    current_setting('pgstac.use_queue')
                );
                RETURN OLD;
            END $$;
            CREATE TRIGGER record_migration_settings
            AFTER DELETE ON pgstac.collections
            FOR EACH ROW EXECUTE FUNCTION public.record_migration_settings();
        """)
        # Fail in the second source-chunk batch, after the first commits.
        connection.execute("""
            CREATE FUNCTION public.reject_late_delete() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'deliberate late migration failure';
            END $$;
            CREATE TRIGGER reject_late_delete BEFORE DELETE ON pgstac.collections
            FOR EACH ROW WHEN (OLD.id = 'zz__algo__1__day')
            EXECUTE FUNCTION public.reject_late_delete();
        """)
    # Start with no pending ingestion work, as required for production apply.
    with connect(database_url, autocommit=True) as connection:
        connection.execute("CALL pgstac.run_queued_queries()")
        assert (
            connection.execute("SELECT count(*) FROM pgstac.query_queue").fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM pgstac.query_queue_history "
                "WHERE error IS NOT NULL"
            ).fetchone()[0]
            == 0
        )
    before = snapshot(database_url)
    command = [sys.executable, str(SCRIPT), "--database-url", database_url]
    dry_run = subprocess.run(
        [*command, "--dry-run", "--batch-size", "1"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert dry_run.returncode == 0, dry_run.stderr
    assert snapshot(database_url) == before

    failed = subprocess.run(
        [*command, "--apply", "--batch-size", "1"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert failed.returncode != 0
    assert "deliberate late migration failure" in failed.stderr
    assert "Batch 2/5 failed and was rolled back" in failed.stderr
    assert "Committed batch 1/5." in failed.stderr
    assert "Committed batch 2/5." not in failed.stderr
    partial = snapshot(database_url)
    assert {row[0]["id"] for row in partial[0]} == {
        existing,
        retained,
        retained_again,
        new_target + "__day",
        new_target + "__night",
        "unrelated",
    }
    assert any(
        item["collection"] == existing and item["id"] == "moving"
        for (item,) in partial[1]
    )
    assert all(
        "maap-dps:tag" not in item["properties"]
        for (item,) in partial[1]
        if item["collection"] in (retained, retained_again)
    )
    assert partial[2] == []
    with connect(database_url) as connection:
        connection.execute("DROP TRIGGER reject_late_delete ON pgstac.collections")
        connection.execute("DROP FUNCTION public.reject_late_delete()")

    applied = subprocess.run(
        [*command, "--apply", "--batch-size", "1"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert applied.returncode == 0, applied.stderr
    after = snapshot(database_url)
    assert after[2] == [], "Migration must not leave deferred partition maintenance"
    with connect(database_url) as connection:
        assert (
            connection.execute(
                "SELECT pgstac.get_setting_bool('use_queue')"
            ).fetchone()[0]
            is use_queue
        )
        settings = connection.execute(
            "SELECT work_mem, hash_mem_multiplier, "
            "max_parallel_workers_per_gather, jit, use_queue "
            "FROM public.migration_settings_log"
        ).fetchall()
        assert settings
        assert set(settings) == {("4MB", "1", "0", "off", "false")}
    assert {row[0]["id"] for row in after[0]} == {
        existing,
        retained,
        retained_again,
        new_target,
        "unrelated",
    }
    expected_items = []
    for (item,) in before[1]:
        source = item["collection"]
        if source in (moving, retained, retained_again, *new_sources):
            username, algorithm, version, tag = source.split("__")
            item["properties"].update(
                {
                    "maap-dps:username": username,
                    "maap-dps:algorithm_name": algorithm,
                    "processing:version": version,
                    "maap-dps:tag": tag,
                }
            )
            if source not in (retained, retained_again):
                item["collection"] = "__".join(source.split("__")[:-1])
        expected_items.append(item)
    assert sorted(
        (row[0] for row in after[1]), key=lambda item: (item["collection"], item["id"])
    ) == sorted(expected_items, key=lambda item: (item["collection"], item["id"]))

    # The procedure commits internally, so it must run outside a transaction.
    with connect(database_url, autocommit=True) as connection:
        connection.execute("CALL pgstac.run_queued_queries()")
        assert (
            connection.execute("SELECT count(*) FROM pgstac.query_queue").fetchone()[0]
            == 0
        )
        errors = connection.execute(
            "SELECT query, error FROM pgstac.query_queue_history "
            "WHERE error IS NOT NULL"
        ).fetchall()
    maintained = snapshot(database_url)
    assert maintained[1] == after[1]
    rerun = subprocess.run(
        [*command, "--apply", "--batch-size", "1"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert rerun.returncode == 0, rerun.stderr
    assert snapshot(database_url)[:2] == maintained[:2]
    assert not errors, errors
