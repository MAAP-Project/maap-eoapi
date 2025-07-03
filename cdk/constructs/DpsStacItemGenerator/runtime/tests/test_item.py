from unittest.mock import MagicMock, patch

import pystac
import pytest
from dps_stac_item_generator.item import get_stac_items
from pystac.errors import STACValidationError
from stac_pydantic.item import Item


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
        item1.validate.return_value = None

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
        item2.validate.return_value = None

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

    def test_get_stac_items_validation_called(self, mock_catalog, mock_job_metadata):
        """Test that validation is called on each item."""
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
            list(get_stac_items(catalog_s3_key))

            for item in mock_catalog.get_all_items.return_value:
                item.validate.assert_called_once()

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

    def test_get_stac_items_validation_failure(self, mock_catalog, mock_job_metadata):
        """Test handling of item validation failure."""
        catalog_s3_key = "s3://test-bucket/2023/01/15/10/30/45/123456/catalog.json"

        items = mock_catalog.get_all_items.return_value
        items[0].validate.side_effect = Exception("Validation failed")

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
            with pytest.raises(Exception, match="Validation failed"):
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

    def test_get_stac_items_stac_validation_error(
        self, mock_catalog, mock_job_metadata
    ):
        """Test handling of STACValidationError during item validation."""
        catalog_s3_key = "s3://test-bucket/2023/01/15/10/30/45/123456/catalog.json"

        items = mock_catalog.get_all_items.return_value
        validation_error_msg = "Item does not conform to STAC specification"
        items[0].validate.side_effect = STACValidationError(validation_error_msg)

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
            with pytest.raises(STACValidationError, match=validation_error_msg):
                list(get_stac_items(catalog_s3_key))

            items[0].validate.assert_called_once()

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
