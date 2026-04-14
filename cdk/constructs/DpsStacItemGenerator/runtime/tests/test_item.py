from unittest.mock import MagicMock, patch

import pystac
import pytest
from dps_stac_item_generator.item import get_stac_items, is_authorized
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

    @pytest.fixture
    def mock_catalog(self):
        """Create a mock STAC catalog with items."""
        catalog = MagicMock(spec=pystac.Catalog)

        item1 = MagicMock()
        item1.to_dict.return_value = {
            "type": "Feature",
            "stac_version": "1.0.0",
            "id": "item1",
            "collection": "test-collection",
            "properties": {"datetime": "2023-01-01T00:00:00Z"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[-180, -90], [180, -90], [180, 90], [-180, 90], [-180, -90]]
                ],
            },
            "bbox": [-180, -90, 180, 90],
            "links": [],
            "assets": {},
            "stac_extensions": [],
        }
        item2 = MagicMock()
        item2.to_dict.return_value = {
            "type": "Feature",
            "stac_version": "1.0.0",
            "id": "item2",
            "collection": "test-collection",
            "properties": {"datetime": "2023-01-02T00:00:00Z"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[-180, -90], [180, -90], [180, 90], [-180, 90], [-180, -90]]
                ],
            },
            "bbox": [-180, -90, 180, 90],
            "links": [],
            "assets": {},
            "stac_extensions": [],
        }
        catalog.get_all_items.return_value = [item1, item2]
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
        expected_collection_id = "superman__awesome-algo__0.1__test"

        with (
            patch(
                "dps_stac_item_generator.item.pystac.Catalog.from_file",
                return_value=mock_catalog,
            ),
            patch(
                "dps_stac_item_generator.item.load_met_json",
                return_value=mock_job_metadata,
            ),
        ):
            items = list(get_stac_items(catalog_s3_key))

            assert len(items) == 2

            for item in items:
                assert isinstance(item, Item)
                assert item.collection == expected_collection_id

            mock_catalog.make_all_asset_hrefs_absolute.assert_called_once()
            mock_catalog.get_all_items.assert_called_once()

    def test_get_stac_items_invalid_s3_key_format(
        self, mock_catalog, mock_job_metadata
    ):
        """Test handling of S3 key that doesn't match DPS output pattern."""
        catalog_s3_key = "s3://test-bucket/invalid/path/catalog.json"

        with (
            patch(
                "dps_stac_item_generator.item.pystac.Catalog.from_file",
                return_value=mock_catalog,
            ),
            patch(
                "dps_stac_item_generator.item.load_met_json",
                return_value=mock_job_metadata,
            ),
        ):
            with pytest.raises(
                ValueError, match="could not identify the DPS output prefix"
            ):
                list(get_stac_items(catalog_s3_key))

    def test_get_stac_items_missing_met_json(self, mock_catalog):
        """Test handling when met.json file is not found."""
        catalog_s3_key = "s3://test-bucket/2023/01/15/10/30/45/123456/catalog.json"

        with (
            patch(
                "dps_stac_item_generator.item.pystac.Catalog.from_file",
                return_value=mock_catalog,
            ),
            patch("dps_stac_item_generator.item.load_met_json", return_value=None),
        ):
            with pytest.raises(ValueError, match="could not locate the .met.json file"):
                list(get_stac_items(catalog_s3_key))

    def test_get_stac_items_load_met_json_called_correctly(
        self, mock_catalog, mock_job_metadata
    ):
        """Test that load_met_json is called with correct parameters."""
        catalog_s3_key = "s3://test-bucket/2023/01/15/10/30/45/123456/catalog.json"

        with (
            patch(
                "dps_stac_item_generator.item.pystac.Catalog.from_file",
                return_value=mock_catalog,
            ),
            patch(
                "dps_stac_item_generator.item.load_met_json",
                return_value=mock_job_metadata,
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
                "dps_stac_item_generator.item.pystac.Catalog.from_file",
                return_value=empty_catalog,
            ),
            patch(
                "dps_stac_item_generator.item.load_met_json",
                return_value=mock_job_metadata,
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
                "dps_stac_item_generator.item.pystac.Catalog.from_file",
                side_effect=Exception("Failed to load catalog"),
            ),
            patch(
                "dps_stac_item_generator.item.load_met_json",
                return_value=mock_job_metadata,
            ),
        ):
            with pytest.raises(Exception, match="Failed to load catalog"):
                list(get_stac_items(catalog_s3_key))

    def test_get_stac_items_generator_behavior(self, mock_catalog, mock_job_metadata):
        """Test that get_stac_items returns a generator and yields items lazily."""
        catalog_s3_key = "s3://test-bucket/2023/01/15/10/30/45/123456/catalog.json"

        with (
            patch(
                "dps_stac_item_generator.item.pystac.Catalog.from_file",
                return_value=mock_catalog,
            ),
            patch(
                "dps_stac_item_generator.item.load_met_json",
                return_value=mock_job_metadata,
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
                "dps_stac_item_generator.item.load_met_json",
                return_value=mock_job_metadata,
            ),
            patch(
                "dps_stac_item_generator.item.pystac.Catalog.from_file",
                side_effect=Exception("Failed to parse catalog.json: invalid format"),
            ),
        ):
            with pytest.raises(
                Exception, match="Failed to parse catalog.json: invalid format"
            ):
                list(get_stac_items(catalog_s3_key))

    def test_santitize_collection_id(self, mock_catalog, mock_job_metadata):
        """Test that collection ID is sanitized correctly."""
        catalog_s3_key = "s3://test-bucket/2023/01/15/10/30/45/123456/catalog.json"
        mock_job_metadata["username"] = "user/name"
        mock_job_metadata["algorithm_name"] = "algo?name"
        expected_collection_id = "user-name__algo-name__0.1__test"

        with (
            patch(
                "dps_stac_item_generator.item.pystac.Catalog.from_file",
                return_value=mock_catalog,
            ),
            patch(
                "dps_stac_item_generator.item.load_met_json",
                return_value=mock_job_metadata,
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
                "dps_stac_item_generator.item.pystac.Catalog.from_file",
                return_value=mock_catalog,
            ),
            patch(
                "dps_stac_item_generator.item.load_met_json",
                return_value=mock_job_metadata,
            ),
        ):
            items = list(get_stac_items(catalog_s3_key, collection_id_registry=registry))

        for item in items:
            assert item.collection == "test-collection"

    def test_unauthorized_collection_id_replaced(self, mock_catalog, mock_job_metadata):
        """Items get the deterministic ID when the user is not authorized."""
        catalog_s3_key = "s3://test-bucket/2023/01/15/10/30/45/123456/catalog.json"
        registry = {"test-collection": ["other-user"]}
        expected_collection_id = "superman__awesome-algo__0.1__test"

        with (
            patch(
                "dps_stac_item_generator.item.pystac.Catalog.from_file",
                return_value=mock_catalog,
            ),
            patch(
                "dps_stac_item_generator.item.load_met_json",
                return_value=mock_job_metadata,
            ),
        ):
            items = list(get_stac_items(catalog_s3_key, collection_id_registry=registry))

        for item in items:
            assert item.collection == expected_collection_id

    def test_wildcard_registry_pattern(self, mock_catalog, mock_job_metadata):
        """Items keep their collection ID when matched by a wildcard pattern."""
        catalog_s3_key = "s3://test-bucket/2023/01/15/10/30/45/123456/catalog.json"
        registry = {"test-*": ["superman"]}

        with (
            patch(
                "dps_stac_item_generator.item.pystac.Catalog.from_file",
                return_value=mock_catalog,
            ),
            patch(
                "dps_stac_item_generator.item.load_met_json",
                return_value=mock_job_metadata,
            ),
        ):
            items = list(get_stac_items(catalog_s3_key, collection_id_registry=registry))

        for item in items:
            assert item.collection == "test-collection"

    def test_empty_registry_uses_deterministic_id(self, mock_catalog, mock_job_metadata):
        """An empty registry results in the deterministic collection ID for all items."""
        catalog_s3_key = "s3://test-bucket/2023/01/15/10/30/45/123456/catalog.json"
        expected_collection_id = "superman__awesome-algo__0.1__test"

        with (
            patch(
                "dps_stac_item_generator.item.pystac.Catalog.from_file",
                return_value=mock_catalog,
            ),
            patch(
                "dps_stac_item_generator.item.load_met_json",
                return_value=mock_job_metadata,
            ),
        ):
            items = list(get_stac_items(catalog_s3_key, collection_id_registry={}))

        for item in items:
            assert item.collection == expected_collection_id
