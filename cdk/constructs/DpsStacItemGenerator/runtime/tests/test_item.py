from unittest.mock import MagicMock, patch

import pystac
import pytest
from dps_stac_item_generator.item import get_stac_items
from stac_pydantic.item import Item


class TestGetStacItems:
    """Test cases for get_stac_items function."""

    @pytest.fixture
    def mock_catalog(self):
        """Create a mock STAC catalog with items."""
        catalog = MagicMock(spec=pystac.Catalog)

        # Create mock items that behave like STAC items
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

    def test_get_stac_items_success(self, mock_catalog):
        """Test successful generation of STAC items from catalog."""
        # Arrange
        catalog_s3_key = "s3://test-bucket/user123/dps_output/job1/catalog.json"

        with patch(
            "dps_stac_item_generator.item.pystac.Catalog.from_file",
            return_value=mock_catalog,
        ):
            # Act
            items = list(get_stac_items(catalog_s3_key))

            # Assert
            assert len(items) == 2

            # Verify each item is properly constructed
            for i, item in enumerate(items):
                assert isinstance(item, Item)
                assert item.collection == "user123"  # Extracted from S3 path
                assert item.id == f"item{i + 1}"

            # Verify catalog operations were called
            mock_catalog.make_all_asset_hrefs_absolute.assert_called_once()
            mock_catalog.get_all_items.assert_called_once()

    def test_get_stac_items_extracts_collection_id_from_path(self, mock_catalog):
        """Test that collection ID is correctly extracted from S3 path."""
        # Arrange
        catalog_s3_key = "s3://test-bucket/my-collection-name/nested/path/catalog.json"

        with patch(
            "dps_stac_item_generator.item.pystac.Catalog.from_file",
            return_value=mock_catalog,
        ):
            # Act
            items = list(get_stac_items(catalog_s3_key))

            # Assert
            for item in items:
                assert item.collection == "my-collection-name"

    def test_get_stac_items_validation_called(self, mock_catalog):
        """Test that validation is called on each item."""
        # Arrange
        catalog_s3_key = "s3://test-bucket/user123/catalog.json"

        with patch(
            "dps_stac_item_generator.item.pystac.Catalog.from_file",
            return_value=mock_catalog,
        ):
            # Act
            list(get_stac_items(catalog_s3_key))

            # Assert
            for item in mock_catalog.get_all_items.return_value:
                item.validate.assert_called_once()

    def test_get_stac_items_empty_catalog(self):
        """Test handling of catalog with no items."""
        # Arrange
        catalog_s3_key = "s3://test-bucket/user123/catalog.json"
        empty_catalog = MagicMock(spec=pystac.Catalog)
        empty_catalog.get_all_items.return_value = []
        empty_catalog.make_all_asset_hrefs_absolute.return_value = None

        with patch(
            "dps_stac_item_generator.item.pystac.Catalog.from_file",
            return_value=empty_catalog,
        ):
            # Act
            items = list(get_stac_items(catalog_s3_key))

            # Assert
            assert len(items) == 0
            empty_catalog.make_all_asset_hrefs_absolute.assert_called_once()
            empty_catalog.get_all_items.assert_called_once()

    def test_get_stac_items_catalog_loading_failure(self):
        """Test handling of catalog loading failure."""
        # Arrange
        catalog_s3_key = "s3://test-bucket/user123/catalog.json"

        with patch(
            "dps_stac_item_generator.item.pystac.Catalog.from_file",
            side_effect=Exception("Failed to load catalog"),
        ):
            # Act & Assert
            with pytest.raises(Exception, match="Failed to load catalog"):
                list(get_stac_items(catalog_s3_key))

    def test_get_stac_items_validation_failure(self, mock_catalog):
        """Test handling of item validation failure."""
        # Arrange
        catalog_s3_key = "s3://test-bucket/user123/catalog.json"

        # Make the first item's validation fail
        items = mock_catalog.get_all_items.return_value
        items[0].validate.side_effect = Exception("Validation failed")

        with patch(
            "dps_stac_item_generator.item.pystac.Catalog.from_file",
            return_value=mock_catalog,
        ):
            # Act & Assert
            with pytest.raises(Exception, match="Validation failed"):
                list(get_stac_items(catalog_s3_key))

    def test_get_stac_items_generator_behavior(self, mock_catalog):
        """Test that get_stac_items returns a generator and yields items lazily."""
        # Arrange
        catalog_s3_key = "s3://test-bucket/user123/catalog.json"

        with patch(
            "dps_stac_item_generator.item.pystac.Catalog.from_file",
            return_value=mock_catalog,
        ):
            # Act
            items_generator = get_stac_items(catalog_s3_key)

            # Assert - verify it's a generator
            assert hasattr(items_generator, "__iter__")
            assert hasattr(items_generator, "__next__")

            # Verify lazy evaluation - catalog operations shouldn't be called yet
            mock_catalog.make_all_asset_hrefs_absolute.assert_not_called()
            mock_catalog.get_all_items.assert_not_called()

            # Now consume the generator
            items = list(items_generator)
            assert len(items) == 2

            # Now catalog operations should have been called
            mock_catalog.make_all_asset_hrefs_absolute.assert_called_once()
            mock_catalog.get_all_items.assert_called_once()

    def test_get_stac_items_path_parsing_edge_cases(self, mock_catalog):
        """Test path parsing with various edge cases."""
        test_cases = [
            ("s3://bucket/simple", "simple"),
            ("s3://bucket/with-dashes", "with-dashes"),
            ("s3://bucket/with_underscores", "with_underscores"),
            ("s3://bucket/123numeric", "123numeric"),
            ("s3://bucket/mixed-123_chars", "mixed-123_chars"),
        ]

        for catalog_key, expected_collection_id in test_cases:
            with patch(
                "dps_stac_item_generator.item.pystac.Catalog.from_file",
                return_value=mock_catalog,
            ):
                # Act
                items = list(get_stac_items(catalog_key))

                # Assert
                for item in items:
                    assert item.collection == expected_collection_id, (
                        f"Failed for path: {catalog_key}"
                    )

    def test_get_stac_items_item_construction_with_all_fields(self):
        """Test that Item objects are constructed with all necessary fields from item data."""
        # Arrange
        catalog_s3_key = "s3://test-bucket/test-collection/catalog.json"

        # Create a more detailed mock item
        mock_item = MagicMock()
        mock_item.to_dict.return_value = {
            "type": "Feature",
            "stac_version": "1.0.0",
            "id": "detailed-item",
            "collection": "original-collection",
            "properties": {
                "datetime": "2023-01-15T12:00:00Z",
                "description": "Detailed test item",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[-10, -10], [10, -10], [10, 10], [-10, 10], [-10, -10]]
                ],
            },
            "bbox": [-10, -10, 10, 10],
            "links": [{"rel": "self", "href": "https://example.com/item"}],
            "assets": {"thumbnail": {"href": "https://example.com/thumb.jpg"}},
            "stac_extensions": ["https://example.com/extension"],
        }
        mock_item.validate.return_value = None

        mock_catalog = MagicMock(spec=pystac.Catalog)
        mock_catalog.get_all_items.return_value = [mock_item]
        mock_catalog.make_all_asset_hrefs_absolute.return_value = None

        with patch(
            "dps_stac_item_generator.item.pystac.Catalog.from_file",
            return_value=mock_catalog,
        ):
            # Act
            items = list(get_stac_items(catalog_s3_key))

            # Assert
            assert len(items) == 1
            item = items[0]

            assert isinstance(item, Item)
            assert item.collection == "test-collection"
            assert item.id == "detailed-item"
            assert item.type == "Feature"
            assert item.stac_version == "1.0.0"
