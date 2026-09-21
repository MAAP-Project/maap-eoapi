from __future__ import annotations

import importlib.util
from pathlib import Path

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

    def execute(self, statement: str, parameters: tuple[list[str], ...]) -> None:
        """Record an executed statement and its parameters."""
        self.calls.append((statement, parameters))


class RecordingConnection:
    """Provide the cursor boundary needed by the migration helper."""

    def __init__(self) -> None:
        self.recording_cursor = RecordingCursor()

    def cursor(self) -> RecordingCursor:
        """Return the recording cursor."""
        return self.recording_cursor


def test_apply_item_metadata_keeps_legacy_collection_id():
    """Conflicting legacy collections receive metadata without being moved."""
    connection = RecordingConnection()
    source_id = "alice__algorithm__1.0__nightly"

    migration.apply_item_metadata(connection, [source_id])

    statement, parameters = connection.recording_cursor.calls[0]
    assert "INSERT INTO pgstac.items_staging_upsert" in statement
    assert "'{collection}'" not in statement
    assert parameters == ([source_id], ["alice"], ["algorithm"], ["1.0"], ["nightly"])
