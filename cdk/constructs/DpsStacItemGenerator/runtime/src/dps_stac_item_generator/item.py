import logging
from typing import Any, Generator, Union
from urllib.parse import urlparse

import boto3
import pystac
from pystac import Link
from pystac.stac_io import DefaultStacIO, StacIO
from stac_pydantic.item import Item

logger = logging.getLogger()
logger.setLevel(logging.INFO)


class CustomStacIO(DefaultStacIO):
    def __init__(self):
        self.s3 = boto3.resource("s3")
        super().__init__()

    def read_text(self, source: Union[str, Link], *args: Any, **kwargs: Any) -> str:
        parsed = urlparse(str(source))
        if parsed.scheme == "s3":
            bucket = parsed.netloc
            key = parsed.path[1:]

            obj = self.s3.Object(bucket, key)
            return obj.get()["Body"].read().decode("utf-8")
        else:
            return super().read_text(source, *args, **kwargs)

    def write_text(
        self, dest: Union[str, Link], txt: str, *args: Any, **kwargs: Any
    ) -> None:
        parsed = urlparse(str(dest))
        if parsed.scheme == "s3":
            bucket = parsed.netloc
            key = parsed.path[1:]
            self.s3.Object(bucket, key).put(Body=txt, ContentEncoding="utf-8")
        else:
            super().write_text(dest, txt, *args, **kwargs)


StacIO.set_default(CustomStacIO)


def get_stac_items(catalog_json_key: str) -> Generator[Item, Any, Any]:
    """
    Yield STAC items out of a catalog.json
    """
    parsed = urlparse(catalog_json_key)

    # get username out of s3 key
    collection_id = parsed.path.split("/")[1]

    catalog = pystac.Catalog.from_file(catalog_json_key)
    catalog.make_all_asset_hrefs_absolute()

    for item in catalog.get_all_items():
        item.validate()

        # Convert item to dict and override collection ID
        item_dict = item.to_dict()
        item_dict["collection"] = collection_id
        
        yield Item(**item_dict)
