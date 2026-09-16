import json
import os

import boto3
import pystac
import requests
from pystac import STACValidationError


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
        except STACValidationError as e:
            raise STACValidationError("Validation failed for the collection") from e

    def validate_item(self, item):
        try:
            pystac.validation.validate_dict(item)
        except STACValidationError as e:
            raise STACValidationError("Validation failed for the item") from e

    def get_authentication_token(self):

        # session = boto3.session.Session()
        client = boto3.client("secretsmanager", region_name="us-west-2")
        secret_id = os.getenv("SECRET_ID")

        try:
            res_secret = client.get_secret_value(SecretId=secret_id)
        except client.exceptions.ResourceNotFoundException as e:
            raise Exception(
                f"Unable to find a secret for '{secret_id}'. "
                "\n\nHint: Check your stage and service id. Also, verify that "
                "the correct AWS_PROFILE is set on your environment."
            ) from e

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
                # A space-separated list of scopes to request
                # for the generated access token.
                "scope": scope,
            },
        )

        return res_token.json()["access_token"]

    def insert_collection(self, token, collection):
        headers = {"Authorization": f"bearer {token}"}
        return requests.post(
            self.ingestor_url + self.collections_endpoint,
            json=collection,
            headers=headers,
        )

    def insert_item(self, token, item):
        headers = {"Authorization": f"bearer {token}"}
        return requests.post(
            self.ingestor_url + self.items_endpoint, json=item, headers=headers
        )

    def query_collection(self, collection_id):
        return requests.get(
            self.stac_url + self.collections_endpoint + f"/{collection_id}"
        )

    def query_items(self, collection_id):
        return requests.get(
            self.stac_url + self.collections_endpoint + f"/{collection_id}/items"
        )

    def register_mosaic(self, search_request):
        return requests.post(
            f"{self.titiler_pgstac_url}/mosaic/register", json=search_request
        )

    def list_mosaic_assets(self, search_id):
        """list the assets of the first tile"""
        return requests.get(
            f"{self.titiler_pgstac_url}/mosaic/{search_id}/tiles/0/0/0/assets"
        )

    def get_test_collection(self):
        with open(
            os.path.join(self.current_file_path, "fixtures", "test_collection.json"),
        ) as f:
            return json.load(f)

    def get_test_item(self):
        with open(
            os.path.join(self.current_file_path, "fixtures", "test_item.json")
        ) as f:
            return json.load(f)

    def get_test_titiler_search_request(self):
        with open(
            os.path.join(
                self.current_file_path, "fixtures", "test_titiler_search_request.json"
            ),
        ) as f:
            return json.load(f)

    def delete_collection(self, token, collection_id):
        headers = {"Authorization": f"bearer {token}"}
        return requests.delete(
            self.ingestor_url + self.collections_endpoint + f"/{collection_id}",
            headers=headers,
        )
