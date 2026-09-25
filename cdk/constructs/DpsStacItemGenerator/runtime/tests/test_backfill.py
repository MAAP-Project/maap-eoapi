import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parents[5] / "scripts" / "backfill_dps_user_catalogs.py"
MODULE_SPEC = importlib.util.spec_from_file_location(
    "backfill_dps_user_catalogs", SCRIPT
)
assert MODULE_SPEC and MODULE_SPEC.loader
backfill = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(backfill)


def row(collection_id: str, **metadata: str) -> dict:
    """Build one hydrated item row for the backfill planner."""
    return {
        "collection_id": collection_id,
        "collection_content": {"type": "Collection", "id": collection_id},
        **metadata,
    }


def test_backfill_plan_is_dry_run_safe_and_idempotent():
    """Planning does not mutate records and a linked collection is skipped."""
    metadata = {
        "username": "user/name",
        "algorithm_name": "algo",
        "algorithm_version": "1.0",
        "tag": "nightly",
    }
    collection_id = backfill.generated_collection_id(metadata)
    records = {
        collection_id: {"type": "Collection", "id": collection_id, "parent_ids": []}
    }
    before = {key: value.copy() for key, value in records.items()}

    plan, skipped = backfill.build_plan([row(collection_id, **metadata)], records, {})

    assert len(plan) == 1
    assert skipped == []
    assert records == before
    assert plan[0]["catalog_id"] == backfill.user_catalog_id("user/name")
    assert plan[0]["catalog"]["stac_version"] == "1.1.0"

    records[collection_id]["parent_ids"] = [plan[0]["catalog_id"]]
    plan, skipped = backfill.build_plan([row(collection_id, **metadata)], records, {})
    assert plan == []
    assert skipped == [(collection_id, "already linked")]


def test_backfill_plans_legacy_tag_specific_collection():
    """The planner links legacy IDs without assessing item tags."""
    metadata = {
        "username": "alice",
        "algorithm_name": "algo",
        "algorithm_version": "1.0",
        "tag": "nightly",
    }
    collection_id = backfill.generated_collection_id(
        metadata, backfill.LEGACY_COLLECTION_ID_FORMAT
    )
    records = {
        collection_id: {"type": "Collection", "id": collection_id, "parent_ids": []}
    }

    plan, skipped = backfill.build_plan([row(collection_id, **metadata)], records, {})

    assert skipped == []
    assert plan[0]["catalog_id"] == backfill.user_catalog_id("alice")


def test_backfill_allows_unslugified_collection_ids():
    """Ownership metadata can match a raw, mixed-case generated ID."""
    metadata = {
        "username": "Alice",
        "algorithm_name": "My Algorithm",
        "algorithm_version": "1.0",
    }
    collection_id = backfill.COLLECTION_ID_FORMAT.format(**metadata)
    records = {
        collection_id: {"type": "Collection", "id": collection_id, "parent_ids": []}
    }

    plan, skipped = backfill.build_plan([row(collection_id, **metadata)], records, {})

    assert skipped == []
    assert plan[0]["catalog_id"] == backfill.user_catalog_id("Alice")


def test_backfill_ignores_mixed_and_missing_tags():
    """Tags do not affect ownership of a current generated collection."""
    metadata = {
        "username": "alice",
        "algorithm_name": "algo",
        "algorithm_version": "1.0",
    }
    collection_id = backfill.generated_collection_id(metadata)
    records = {
        collection_id: {"type": "Collection", "id": collection_id, "parent_ids": []}
    }

    plan, skipped = backfill.build_plan(
        [
            row(collection_id, **metadata, tag="nightly"),
            row(collection_id, **metadata, tag="release"),
            row(collection_id, **metadata),
        ],
        records,
        {},
    )

    assert skipped == []
    assert plan[0]["catalog_id"] == backfill.user_catalog_id("alice")


def test_backfill_skips_ambiguous_and_authorized_collections():
    """The planner reports all conservative exclusions instead of guessing."""
    generated_metadata = {
        "username": "alice",
        "algorithm_name": "algo",
        "algorithm_version": "1.0",
        "tag": "nightly",
    }
    generated_id = backfill.generated_collection_id(generated_metadata)
    mixed_metadata = {**generated_metadata, "username": "bob"}
    authorized_metadata = {**generated_metadata, "username": "carol"}
    authorized_id = backfill.generated_collection_id(authorized_metadata)
    named_id = "alice-shared"
    rows = [
        row(generated_id, **generated_metadata),
        row(generated_id, **mixed_metadata),
        row(authorized_id, **authorized_metadata),
        row(named_id, **generated_metadata),
        row("missing", username="alice", algorithm_name="algo"),
    ]
    records = {
        generated_id: {"type": "Collection", "id": generated_id, "parent_ids": []},
        authorized_id: {"type": "Collection", "id": authorized_id, "parent_ids": []},
        named_id: {"type": "Collection", "id": named_id, "parent_ids": []},
        "missing": {"type": "Collection", "id": "missing", "parent_ids": []},
    }

    plan, skipped = backfill.build_plan(rows, records, {authorized_id: ["carol"]})

    assert plan == []
    assert (generated_id, "mixed DPS metadata") in skipped
    assert (authorized_id, "authorized collection override") in skipped
    assert (named_id, "named or non-generated collection ID") in skipped
    assert ("missing", "missing DPS metadata") in skipped


def test_user_catalog_id_uses_collection_slugification_rules():
    """Backfilled catalog IDs match the generator's readable format."""
    assert backfill.user_catalog_id("User Name/One") == "user-user-name-one"
