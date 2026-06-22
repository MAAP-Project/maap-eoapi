import requests
import json
import pystac
from pystac import STACValidationError
import os
import boto3


class StacIngestion:
    """Class representing various test operations"""

    def __init__(self):
        self.ingestor_url, self.stac_url, self.titiler_pgstac_url = tuple(
            [
                f"https://{os.getenv(v)}"
                for v in [
                    "INGESTOR_DOMAIN_NAME",
                    "STAC_API_CUSTOM_DOMAIN_NAME",
                    "TITILER_PGSTAC_API_CUSTOM_DOMAIN_NAME",
                ]
            ]
        )
        self.collections_endpoint = "/collections"
        self.items_endpoint = "/ingestions"
        self.current_file_path = os.path.dirname(os.path.abspath(__file__))

    def validate_collection(self, collection):
        try:
            pystac.validation.validate_dict(collection)
        except STACValidationError:
            raise STACValidationError("Validation failed for the collection")

    def validate_item(self, item):
        try:
            pystac.validation.validate_dict(item)
        except STACValidationError:
            raise STACValidationError("Validation failed for the item")

    def get_authentication_token(self):

        # session = boto3.session.Session()
        client = boto3.client("secretsmanager", region_name="us-west-2")
        secret_id = os.getenv("SECRET_ID")

        try:
            res_secret = client.get_secret_value(SecretId=secret_id)
        except client.exceptions.ResourceNotFoundException:
            raise Exception(
                f"Unable to find a secret for '{secret_id}'. "
                "\n\nHint: Check your stage and service id. Also, verify that the correct "
                "AWS_PROFILE is set on your environment."
            )

        # Authentication - Get TOKEN
        secret = json.loads(res_secret["SecretString"])
        client_secret = secret["client_secret"]
        client_id = secret["client_id"]
        cognito_domain = secret["cognito_domain"]
        scope = secret["scope"]

        res_token = requests.post(
            f"{cognito_domain}/oauth2/token",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
            },
            auth=(client_id, client_secret),
            data={
                "grant_type": "client_credentials",
                # A space-separated list of scopes to request for the generated access token.
                "scope": scope,
            },
        )

        token = res_token.json()["access_token"]
        return token

    def insert_collection(self, token, collection):
        headers = {"Authorization": f"bearer {token}"}
        response = requests.post(
            self.ingestor_url + self.collections_endpoint,
            json=collection,
            headers=headers,
        )
        return response

    def insert_item(self, token, item):
        headers = {"Authorization": f"bearer {token}"}
        response = requests.post(
            self.ingestor_url + self.items_endpoint, json=item, headers=headers
        )
        return response

    def query_collection(self, collection_id):
        response = requests.get(
            self.stac_url + self.collections_endpoint + f"/{collection_id}"
        )
        return response

    def query_items(self, collection_id):
        response = requests.get(
            self.stac_url + self.collections_endpoint + f"/{collection_id}/items"
        )
        return response

    def register_mosaic(self, search_request):
        response = requests.post(
            self.titiler_pgstac_url + "/mosaic/register", json=search_request
        )
        return response

    def list_mosaic_assets(self, search_id):
        """list the assets of the first tile"""
        response = requests.get(
            self.titiler_pgstac_url + f"/mosaic/{search_id}/tiles/0/0/0/assets"
        )
        return response

    def get_test_collection(self):
        with open(
            os.path.join(self.current_file_path, "fixtures", "test_collection.json"),
            "r",
        ) as f:
            test_collection = json.load(f)
        return test_collection

    def get_test_item(self):
        with open(
            os.path.join(self.current_file_path, "fixtures", "test_item.json"), "r"
        ) as f:
            test_item = json.load(f)
        return test_item

    def get_test_titiler_search_request(self):
        with open(
            os.path.join(
                self.current_file_path, "fixtures", "test_titiler_search_request.json"
            ),
            "r",
        ) as f:
            test_titiler_search_request = json.load(f)
        return test_titiler_search_request

    def delete_collection(self, token, collection_id):
        headers = {"Authorization": f"bearer {token}"}
        response = requests.delete(
            self.ingestor_url + self.collections_endpoint + f"/{collection_id}",
            headers=headers,
        )
        return response
