from datetime import datetime as DateTime
from datetime import timezone
from unittest.mock import MagicMock, patch

import pystac
import pytest
from dps_stac_item_generator.stac import (
    get_stac_documents,
    get_stac_items,
    is_authorized,
    load_met_json,
    user_catalog_id,
)
from stac_pydantic.item import Item


class TestIsAuthorized:
    """Test cases for is_authorized helper."""

    def test_exact_match_authorized(self):
        registry = {"my-collection": ["user1", "user2"]}
        assert is_authorized("user1", "my-collection", registry) is True

    def test_exact_match_wrong_user(self):
        registry = {"my-collection": ["user1"]}
        assert is_authorized("user2", "my-collection", registry) is False

    def test_exact_match_wrong_collection(self):
        registry = {"my-collection": ["user1"]}
        assert is_authorized("user1", "other-collection", registry) is False

    def test_wildcard_match_authorized(self):
        registry = {"maap-*": ["user3"]}
        assert is_authorized("user3", "maap-sentinel-2", registry) is True

    def test_wildcard_match_wrong_user(self):
        registry = {"maap-*": ["user3"]}
        assert is_authorized("user1", "maap-sentinel-2", registry) is False

    def test_wildcard_no_match(self):
        registry = {"maap-*": ["user1"]}
        assert is_authorized("user1", "other-prefix-data", registry) is False

    def test_empty_registry(self):
        assert is_authorized("user1", "any-collection", {}) is False

    def test_multiple_patterns_first_match_wins(self):
        registry = {"exact-collection": ["user1"], "exact-*": ["user2"]}
        assert is_authorized("user1", "exact-collection", registry) is True
        assert is_authorized("user2", "exact-collection", registry) is True


class TestGetStacItems:
    """Test cases for get_stac_items function."""

    def test_load_met_json_returns_discovered_key(self):
        store = MagicMock()
        met_json_object = MagicMock()
        met_json_object.bytes.return_value.to_bytes.return_value.decode.return_value = (
            '{"algorithm_name": "awesome-algo"}'
        )
        with (
            patch("dps_stac_item_generator.stac.from_url", return_value=store),
            patch(
                "dps_stac_item_generator.stac.obstore.list",
                return_value=[
                    [
                        {"path": "2023/01/15/10/30/45/123456/catalog.json"},
                        {"path": "2023/01/15/10/30/45/123456/job.met.json"},
                    ]
                ],
            ) as mock_list,
            patch(
                "dps_stac_item_generator.stac.obstore.get",
                return_value=met_json_object,
            ) as mock_get,
        ):
            result = load_met_json("test-bucket", "2023/01/15/10/30/45/123456/")

        assert result == (
            {"algorithm_name": "awesome-algo"},
            "2023/01/15/10/30/45/123456/job.met.json",
        )
        mock_list.assert_called_once_with(store, chunk_size=10)
        mock_get.assert_called_once_with(
            store, "2023/01/15/10/30/45/123456/job.met.json"
        )

    @pytest.fixture
    def mock_catalog(self):
        """Create a mock STAC catalog with items."""
        catalog = MagicMock(spec=pystac.Catalog)

        geometry = {
            "type": "Polygon",
            "coordinates": [[[-180, -90], [180, -90], [180, 90], [-180, -90]]],
        }
        item1 = pystac.Item(
            id="item1",
            geometry=geometry,
            bbox=[-180, -90, 180, 90],
            datetime=DateTime(2023, 1, 1, tzinfo=timezone.utc),
            properties={"created": "2000-01-01T00:00:00Z"},
            collection="test-collection",
            stac_extensions=[
                "https://example.com/existing-extension.json",
                "https://maap-project.github.io/maap-dps-stac-extension/v0.1.0/schema.json",
                "https://maap-project.github.io/maap-dps-stac-extension/v0.1.0/schema.json",
            ],
        )
        item1.add_link(
            pystac.Link(
                "via",
                "s3://test-bucket/2023/01/15/10/30/45/123456/.met.json",
                media_type="application/json",
            )
        )
        item2 = pystac.Item(
            id="item2",
            geometry=geometry,
            bbox=[-180, -90, 180, 90],
            datetime=DateTime(2023, 1, 2, tzinfo=timezone.utc),
            properties={},
            collection="test-collection",
            stac_extensions=["https://example.com/existing-extension.json"],
        )
        item2.add_asset(
            "existing-data",
            pystac.Asset(
                "s3://test-bucket/2023/01/15/10/30/45/123456/data.tif",
                media_type="image/tiff",
                roles=["data"],
            ),
        )
        catalog.get_all_items.return_value = [item1, item2]
        catalog.get_all_collections.return_value = []
        catalog.make_all_asset_hrefs_absolute.return_value = None

        return catalog

    @pytest.fixture
    def mock_job_metadata(self):
        """Create mock job metadata that would be returned by load_met_json."""
        return {
            "algorithm_name": "awesome-algo",
            "algorithm_version": "0.1",
            "username": "superman",
            "tag": "test",
        }

    def test_get_stac_items_success(self, mock_catalog, mock_job_metadata):
        """Test successful generation of STAC items from catalog."""
        catalog_s3_key = "s3://test-bucket/2023/01/15/10/30/45/123456/catalog.json"
        expected_collection_id = "superman__awesome-algo__0.1"
        expected_met_json_href = "s3://test-bucket/2023/01/15/10/30/45/123456/.met.json"
        processing_time = DateTime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        expected_created = "2024-01-02T03:04:05Z"

        with (
            patch(
                "dps_stac_item_generator.stac.pystac.Catalog.from_file",
                return_value=mock_catalog,
            ),
            patch(
                "dps_stac_item_generator.stac.load_met_json",
                return_value=(
                    mock_job_metadata,
                    "2023/01/15/10/30/45/123456/.met.json",
                ),
            ),
            patch("dps_stac_item_generator.stac.datetime") as mock_datetime,
        ):
            mock_datetime.now.return_value = processing_time
            items = list(get_stac_items(catalog_s3_key))

            assert len(items) == 2

            for item in items:
                assert isinstance(item, Item)
                assert item.collection == expected_collection_id
                assert item.properties.model_dump() == {
                    "datetime": item.properties.datetime,
                    "maap-dps:algorithm_name": "awesome-algo",
                    "processing:version": "0.1",
                    "maap-dps:username": "superman",
                    "maap-dps:tag": "test",
                    "created": item.properties.created,
                }
                assert item.properties.created == processing_time
                assert [str(extension) for extension in item.stac_extensions] == [
                    "https://example.com/existing-extension.json",
                    "https://maap-project.github.io/maap-dps-stac-extension/v0.1.0/schema.json",
                    "https://stac-extensions.github.io/processing/v1.2.0/schema.json",
                ]
                assert item.model_dump()["assets"]["dps-metadata"] == {
                    "href": expected_met_json_href,
                    "type": "application/json",
                    "roles": ["metadata"],
                    "title": "DPS job metadata",
                }
                if item.id == "item2":
                    assert "existing-data" in item.model_dump()["assets"]
                assert not any(
                    link.get("rel") == "via"
                    and link.get("href") == expected_met_json_href
                    for link in item.model_dump()["links"]
                )

            mock_datetime.now.assert_called_once_with(timezone.utc)
            assert all(
                item.properties["created"] == expected_created
                for item in mock_catalog.get_all_items.return_value
            )
            mock_catalog.make_all_asset_hrefs_absolute.assert_called_once()
            mock_catalog.get_all_items.assert_called_once()

    def test_get_stac_items_invalid_s3_key_format(
        self, mock_catalog, mock_job_metadata
    ):
        """Test handling of S3 key that doesn't match DPS output pattern."""
        catalog_s3_key = "s3://test-bucket/invalid/path/catalog.json"

        with (
            patch(
                "dps_stac_item_generator.stac.pystac.Catalog.from_file",
                return_value=mock_catalog,
            ),
            patch(
                "dps_stac_item_generator.stac.load_met_json",
                return_value=(
                    mock_job_metadata,
                    "2023/01/15/10/30/45/123456/.met.json",
                ),
            ),
            pytest.raises(ValueError, match="could not identify the DPS output prefix"),
        ):
            list(get_stac_items(catalog_s3_key))

    def test_get_stac_items_missing_met_json(self, mock_catalog):
        """Test handling when met.json file is not found."""
        catalog_s3_key = "s3://test-bucket/2023/01/15/10/30/45/123456/catalog.json"

        with (
            patch(
                "dps_stac_item_generator.stac.pystac.Catalog.from_file",
                return_value=mock_catalog,
            ),
            patch("dps_stac_item_generator.stac.load_met_json", return_value=None),
            pytest.raises(ValueError, match="could not locate the .met.json file"),
        ):
            list(get_stac_items(catalog_s3_key))

    def test_get_stac_items_load_met_json_called_correctly(
        self, mock_catalog, mock_job_metadata
    ):
        """Test that load_met_json is called with correct parameters."""
        catalog_s3_key = "s3://test-bucket/2023/01/15/10/30/45/123456/catalog.json"

        with (
            patch(
                "dps_stac_item_generator.stac.pystac.Catalog.from_file",
                return_value=mock_catalog,
            ),
            patch(
                "dps_stac_item_generator.stac.load_met_json",
                return_value=(
                    mock_job_metadata,
                    "2023/01/15/10/30/45/123456/.met.json",
                ),
            ) as mock_load_met,
        ):
            list(get_stac_items(catalog_s3_key))

            mock_load_met.assert_called_once_with(
                "test-bucket", "2023/01/15/10/30/45/123456/"
            )

    def test_get_stac_items_empty_catalog(self, mock_job_metadata):
        """Test handling of catalog with no items."""
        catalog_s3_key = "s3://test-bucket/2023/01/15/10/30/45/123456/catalog.json"
        empty_catalog = MagicMock(spec=pystac.Catalog)
        empty_catalog.get_all_items.return_value = []
        empty_catalog.make_all_asset_hrefs_absolute.return_value = None

        with (
            patch(
                "dps_stac_item_generator.stac.pystac.Catalog.from_file",
                return_value=empty_catalog,
            ),
            patch(
                "dps_stac_item_generator.stac.load_met_json",
                return_value=(
                    mock_job_metadata,
                    "2023/01/15/10/30/45/123456/.met.json",
                ),
            ),
        ):
            items = list(get_stac_items(catalog_s3_key))

            assert len(items) == 0
            empty_catalog.make_all_asset_hrefs_absolute.assert_called_once()
            empty_catalog.get_all_items.assert_called_once()

    def test_get_stac_items_catalog_loading_failure(self, mock_job_metadata):
        """Test handling of catalog loading failure."""
        catalog_s3_key = "s3://test-bucket/2023/01/15/10/30/45/123456/catalog.json"

        with (
            patch(
                "dps_stac_item_generator.stac.pystac.Catalog.from_file",
                side_effect=Exception("Failed to load catalog"),
            ),
            patch(
                "dps_stac_item_generator.stac.load_met_json",
                return_value=(
                    mock_job_metadata,
                    "2023/01/15/10/30/45/123456/.met.json",
                ),
            ),
            pytest.raises(Exception, match="Failed to load catalog"),
        ):
            list(get_stac_items(catalog_s3_key))

    def test_get_stac_items_generator_behavior(self, mock_catalog, mock_job_metadata):
        """Test that get_stac_items returns a generator and yields items lazily."""
        catalog_s3_key = "s3://test-bucket/2023/01/15/10/30/45/123456/catalog.json"

        with (
            patch(
                "dps_stac_item_generator.stac.pystac.Catalog.from_file",
                return_value=mock_catalog,
            ),
            patch(
                "dps_stac_item_generator.stac.load_met_json",
                return_value=(
                    mock_job_metadata,
                    "2023/01/15/10/30/45/123456/.met.json",
                ),
            ),
        ):
            items_generator = get_stac_items(catalog_s3_key)

            assert hasattr(items_generator, "__iter__")
            assert hasattr(items_generator, "__next__")

            items = list(items_generator)
            assert len(items) == 2

            mock_catalog.make_all_asset_hrefs_absolute.assert_called_once()
            mock_catalog.get_all_items.assert_called_once()

    def test_get_stac_items_invalid_catalog_json(self, mock_job_metadata):
        """Test handling of invalid catalog.json file."""
        catalog_s3_key = "s3://test-bucket/2023/01/15/10/30/45/123456/catalog.json"

        with (
            patch(
                "dps_stac_item_generator.stac.load_met_json",
                return_value=(
                    mock_job_metadata,
                    "2023/01/15/10/30/45/123456/.met.json",
                ),
            ),
            patch(
                "dps_stac_item_generator.stac.pystac.Catalog.from_file",
                side_effect=Exception("Failed to parse catalog.json: invalid format"),
            ),
            pytest.raises(
                Exception, match="Failed to parse catalog.json: invalid format"
            ),
        ):
            list(get_stac_items(catalog_s3_key))

    def test_santitize_collection_id(self, mock_catalog, mock_job_metadata):
        """Test that collection ID is sanitized correctly."""
        catalog_s3_key = "s3://test-bucket/2023/01/15/10/30/45/123456/catalog.json"
        mock_job_metadata["username"] = "user/name"
        mock_job_metadata["algorithm_name"] = "algo?name"
        expected_collection_id = "user-name__algo-name__0.1"

        with (
            patch(
                "dps_stac_item_generator.stac.pystac.Catalog.from_file",
                return_value=mock_catalog,
            ),
            patch(
                "dps_stac_item_generator.stac.load_met_json",
                return_value=(
                    mock_job_metadata,
                    "2023/01/15/10/30/45/123456/.met.json",
                ),
            ),
        ):
            items = list(get_stac_items(catalog_s3_key))

            for item in items:
                assert item.collection == expected_collection_id

    def test_authorized_collection_id_preserved(self, mock_catalog, mock_job_metadata):
        """Items keep their existing collection ID when the user is authorized."""
        catalog_s3_key = "s3://test-bucket/2023/01/15/10/30/45/123456/catalog.json"
        registry = {"test-collection": ["superman"]}

        with (
            patch(
                "dps_stac_item_generator.stac.pystac.Catalog.from_file",
                return_value=mock_catalog,
            ),
            patch(
                "dps_stac_item_generator.stac.load_met_json",
                return_value=(
                    mock_job_metadata,
                    "2023/01/15/10/30/45/123456/.met.json",
                ),
            ),
        ):
            items = list(
                get_stac_items(catalog_s3_key, collection_id_registry=registry)
            )

        for item in items:
            assert item.collection == "test-collection"

    def test_unauthorized_collection_id_replaced(self, mock_catalog, mock_job_metadata):
        """Items get the deterministic ID when the user is not authorized."""
        catalog_s3_key = "s3://test-bucket/2023/01/15/10/30/45/123456/catalog.json"
        registry = {"test-collection": ["other-user"]}
        expected_collection_id = "superman__awesome-algo__0.1"

        with (
            patch(
                "dps_stac_item_generator.stac.pystac.Catalog.from_file",
                return_value=mock_catalog,
            ),
            patch(
                "dps_stac_item_generator.stac.load_met_json",
                return_value=(
                    mock_job_metadata,
                    "2023/01/15/10/30/45/123456/.met.json",
                ),
            ),
        ):
            items = list(
                get_stac_items(catalog_s3_key, collection_id_registry=registry)
            )

        for item in items:
            assert item.collection == expected_collection_id

    def test_wildcard_registry_pattern(self, mock_catalog, mock_job_metadata):
        """Items keep their collection ID when matched by a wildcard pattern."""
        catalog_s3_key = "s3://test-bucket/2023/01/15/10/30/45/123456/catalog.json"
        registry = {"test-*": ["superman"]}

        with (
            patch(
                "dps_stac_item_generator.stac.pystac.Catalog.from_file",
                return_value=mock_catalog,
            ),
            patch(
                "dps_stac_item_generator.stac.load_met_json",
                return_value=(
                    mock_job_metadata,
                    "2023/01/15/10/30/45/123456/.met.json",
                ),
            ),
        ):
            items = list(
                get_stac_items(catalog_s3_key, collection_id_registry=registry)
            )

        for item in items:
            assert item.collection == "test-collection"

    def test_generated_documents_include_hierarchy_once(
        self, mock_catalog, mock_job_metadata
    ):
        """Generated items publish one 1.1 hierarchy with conservative extents."""
        catalog_s3_key = "s3://test-bucket/2023/01/15/10/30/45/123456/catalog.json"

        with (
            patch(
                "dps_stac_item_generator.stac.pystac.Catalog.from_file",
                return_value=mock_catalog,
            ),
            patch(
                "dps_stac_item_generator.stac.load_met_json",
                return_value=(
                    mock_job_metadata,
                    "2023/01/15/10/30/45/123456/.met.json",
                ),
            ),
        ):
            documents = list(get_stac_documents(catalog_s3_key))

        assert [
            document["type"] if isinstance(document, dict) else "Feature"
            for document in documents
        ] == ["Catalog", "Collection", "Feature", "Feature"]
        catalog, collection = documents[:2]
        assert catalog["stac_version"] == "1.1.0"
        assert collection["stac_version"] == "1.1.0"
        assert collection["parent_ids"] == [user_catalog_id("superman")]
        assert collection["extent"] == {
            "spatial": {"bbox": [[-180.0, -90.0, 180.0, 90.0]]},
            "temporal": {"interval": [[None, None]]},
        }

    def test_generated_collection_reuses_source_metadata(
        self, mock_catalog, mock_job_metadata
    ):
        """One source Collection supplies metadata while identity is regenerated."""
        source = pystac.Collection(
            id="test-collection",
            description="Curated source description",
            title="Source title",
            license="CC-BY-4.0",
            keywords=["source-keyword"],
            extent=pystac.Extent(
                pystac.SpatialExtent([[-10, -5, 10, 5]]),
                pystac.TemporalExtent(
                    [[DateTime(2020, 1, 1, tzinfo=timezone.utc), None]]
                ),
            ),
        )
        source.set_self_href("s3://test-bucket/2023/01/15/10/30/45/123456/source.json")
        source.add_asset("preview", pystac.Asset("preview.png"))
        source.add_link(pystac.Link("documentation", "https://example.test/docs"))
        source.add_link(pystac.Link("parent", "https://example.test/old-parent"))
        mock_catalog.get_all_collections.return_value = [source]
        catalog_s3_key = "s3://test-bucket/2023/01/15/10/30/45/123456/catalog.json"

        with (
            patch(
                "dps_stac_item_generator.stac.pystac.Catalog.from_file",
                return_value=mock_catalog,
            ),
            patch(
                "dps_stac_item_generator.stac.load_met_json",
                return_value=(
                    mock_job_metadata,
                    "2023/01/15/10/30/45/123456/.met.json",
                ),
            ),
        ):
            documents = list(get_stac_documents(catalog_s3_key))

        collection = documents[1]
        assert collection["id"] == "superman__awesome-algo__0.1"
        assert collection["parent_ids"] == [user_catalog_id("superman")]
        assert collection["description"] == "Curated source description"
        assert collection["license"] == "CC-BY-4.0"
        assert collection["keywords"] == ["source-keyword"]
        assert collection["assets"]["preview"]["href"] == (
            "s3://test-bucket/2023/01/15/10/30/45/123456/preview.png"
        )
        assert [link["rel"] for link in collection["links"]] == ["documentation"]

    def test_generated_collection_rejects_ambiguous_sources(
        self, mock_catalog, mock_job_metadata
    ):
        """Generated items from multiple source Collections fail explicitly."""
        first = pystac.Collection(
            id="first",
            description="first",
            extent=pystac.Extent(
                pystac.SpatialExtent([[-180, -90, 180, 90]]),
                pystac.TemporalExtent([[None, None]]),
            ),
        )
        second = first.clone()
        second.id = "second"
        mock_catalog.get_all_items.return_value[0].collection_id = "first"
        mock_catalog.get_all_items.return_value[1].collection_id = "second"
        mock_catalog.get_all_collections.return_value = [first, second]
        catalog_s3_key = "s3://test-bucket/2023/01/15/10/30/45/123456/catalog.json"

        with (
            patch(
                "dps_stac_item_generator.stac.pystac.Catalog.from_file",
                return_value=mock_catalog,
            ),
            patch(
                "dps_stac_item_generator.stac.load_met_json",
                return_value=(
                    mock_job_metadata,
                    "2023/01/15/10/30/45/123456/.met.json",
                ),
            ),
            pytest.raises(ValueError, match="multiple source Collections"),
        ):
            list(get_stac_documents(catalog_s3_key))

    def test_authorized_generated_looking_collection_is_item_only(
        self, mock_catalog, mock_job_metadata
    ):
        """An authorized override never receives generated hierarchy documents."""
        generated_id = "superman__awesome-algo__0.1"
        for item in mock_catalog.get_all_items.return_value:
            item.collection_id = generated_id
        catalog_s3_key = "s3://test-bucket/2023/01/15/10/30/45/123456/catalog.json"

        with (
            patch(
                "dps_stac_item_generator.stac.pystac.Catalog.from_file",
                return_value=mock_catalog,
            ),
            patch(
                "dps_stac_item_generator.stac.load_met_json",
                return_value=(
                    mock_job_metadata,
                    "2023/01/15/10/30/45/123456/.met.json",
                ),
            ),
        ):
            documents = list(
                get_stac_documents(
                    catalog_s3_key,
                    collection_id_registry={generated_id: ["superman"]},
                )
            )

        assert all(not isinstance(document, dict) for document in documents)
        assert all(document.collection == generated_id for document in documents)

    def test_user_catalog_id_is_collision_safe_and_url_safe(self):
        """Distinct usernames produce distinct URL-safe IDs."""
        first = user_catalog_id("user/name")
        second = user_catalog_id("user_name")
        assert first != second
        assert first == "user-dXNlci9uYW1l"
        assert all(character.isalnum() or character in "-_" for character in first)

    def test_empty_registry_uses_deterministic_id(
        self, mock_catalog, mock_job_metadata
    ):
        """Empty registry results in the deterministic collection ID for all items."""
        catalog_s3_key = "s3://test-bucket/2023/01/15/10/30/45/123456/catalog.json"
        expected_collection_id = "superman__awesome-algo__0.1"

        with (
            patch(
                "dps_stac_item_generator.stac.pystac.Catalog.from_file",
                return_value=mock_catalog,
            ),
            patch(
                "dps_stac_item_generator.stac.load_met_json",
                return_value=(
                    mock_job_metadata,
                    "2023/01/15/10/30/45/123456/.met.json",
                ),
            ),
        ):
            items = list(get_stac_items(catalog_s3_key, collection_id_registry={}))

        for item in items:
            assert item.collection == expected_collection_id
