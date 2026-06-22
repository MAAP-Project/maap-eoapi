import pytest
import pystac
import time


# Test validating the collection
def test_validate_collection(test_collection):
    pystac.validation.validate_dict(test_collection)


# Test validating the item
def test_validate_item(test_item):
    pystac.validation.validate_dict(test_item)


# Test inserting collection
def test_insert_collection(
    stac_ingestion_instance, authentication_token, test_collection
):
    response = stac_ingestion_instance.insert_collection(
        authentication_token, test_collection
    )
    assert response.status_code in [200, 201], (
        f"Failed to insert the test_collection :\n{response.text}"
    )
    # Wait for the collection to be inserted
    time.sleep(60)


# Test inserting item
def test_insert_item(stac_ingestion_instance, authentication_token, test_item):
    response = stac_ingestion_instance.insert_item(authentication_token, test_item)
    assert response.status_code in [200, 201], (
        f"Failed to insert the test_item :\n{response.text}"
    )
    # Wait for the item to be inserted
    time.sleep(60)


# Test querying collection and verifying inserted collection
def test_query_collection(stac_ingestion_instance, test_collection):
    response = stac_ingestion_instance.query_collection(test_collection["id"])
    assert response.status_code in [200, 201], (
        f"Failed to query the test_collection :\n{response.text}"
    )


# Test registering a mosaic and querying its assets
def test_titiler_pgstac(
    stac_ingestion_instance, test_titiler_search_request, test_item
):
    register_response = stac_ingestion_instance.register_mosaic(
        test_titiler_search_request
    )
    assert register_response.status_code in [200, 201], (
        f"Failed to register the mosaic :\n{register_response.text}"
    )
    search_id = register_response.json()["id"]
    # allow for some time for the mosaic to be inserted
    time.sleep(10)
    asset_query_response = stac_ingestion_instance.list_mosaic_assets(search_id)
    assert asset_query_response.status_code in [200, 201], (
        f"Failed to query the mosaic's assets for mosaic {search_id} :\n{asset_query_response.text}"
    )
    assets_json = asset_query_response.json()
    # expects a single item in the collection
    assert len(assets_json) == 1
    assert all([k in assets_json[0]["assets"] for k in test_item["assets"].keys()])


# Test querying items and verifying inserted items
def test_query_items(stac_ingestion_instance, test_collection, test_item):
    response = stac_ingestion_instance.query_items(test_collection["id"])
    assert response.status_code in [200, 201], (
        f"Failed to query the items :\n{response.text}"
    )
    item = response.json()["features"][0]
    assert item["id"] == test_item["id"], (
        f"Inserted item - {test_item} \n not found in the queried items {item}"
    )


# Test querying collection and verifying inserted collection
def test_delete_collection(
    stac_ingestion_instance, authentication_token, test_collection
):
    response = stac_ingestion_instance.delete_collection(
        authentication_token, test_collection["id"]
    )
    assert response.status_code in [200, 201], (
        f"Failed to delete the test_collection :\n{response.text}"
    )


# Run the tests
if __name__ == "__main__":
    pytest.main()
