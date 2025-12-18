import json
import logging
import re
from typing import Any, Dict, Generator, Optional, Union
from urllib.parse import urlparse

import obstore
import pystac
from obstore.store import from_url
from pystac import Link
from pystac.stac_io import DefaultStacIO, StacIO
from stac_pydantic.item import Item
from slugify import slugify

logger = logging.getLogger()
logger.setLevel(logging.INFO)

COLLECTION_ID_FORMAT = "{username}__{algorithm_name}__{algorithm_version}__{tag}"


class ObstoreStacIO(DefaultStacIO):
    def read_text(self, source: Union[str, Link], *args: Any, **kwargs: Any) -> str:
        parsed = urlparse(str(source))
        key = parsed.path[1:]
        store = from_url(f"{parsed.scheme}://{parsed.netloc}")

        obj = obstore.get(store, key)
        return obj.bytes().to_bytes().decode("utf-8")

    def write_text(
        self, dest: Union[str, Link], txt: str, *args: Any, **kwargs: Any
    ) -> None:
        parsed = urlparse(str(dest))
        key = parsed.path[1:]
        store = from_url(f"{parsed.scheme}://{parsed.netloc}")
        obstore.put(store, key, bytes(txt, "utf-8"))


StacIO.set_default(ObstoreStacIO)


def get_dps_output_prefix(s3_key) -> Optional[str]:
    """
    Find the S3 key prefix for the outputs associated with a DPS job

    Args:
        s3_key (str): Full S3 key

    Returns:
        str: Path prefix including timestamp, or None if not found
    """
    parsed = urlparse(s3_key)
    path = parsed.path.lstrip("/")

    timestamp_pattern = r"(\d{4}/\d{2}/\d{2}/\d{2}/\d{2}/\d{2}/\d+)"
    match = re.search(timestamp_pattern, path)

    if match:
        end_pos = match.end()
        return path[:end_pos] + "/"

    return None


def load_met_json(bucket: str, job_output_prefix: str) -> Optional[Dict[str, str]]:
    """Load the .met.json file that gets uploaded with DPS job outputs"""
    store = from_url(f"s3://{bucket}/{job_output_prefix}")
    stream = obstore.list(store, chunk_size=10)
    for list_result in stream:
        for result in list_result:
            if result["path"].endswith("met.json"):
                return json.loads(
                    obstore.get(store, result["path"])
                    .bytes()
                    .to_bytes()
                    .decode("utf-8")
                )


def get_stac_items(catalog_json_key: str) -> Generator[Item, Any, Any]:
    """
    Yield STAC items out of a catalog.json
    """
    job_output_prefix = get_dps_output_prefix(catalog_json_key)
    if not job_output_prefix:
        raise ValueError(
            f"could not identify the DPS output prefix from {catalog_json_key}"
        )

    s3_key_parsed = urlparse(catalog_json_key)

    job_metadata = load_met_json(s3_key_parsed.netloc, job_output_prefix)
    if not job_metadata:
        raise ValueError(
            f"could not locate the .met.json file with the DPS job outputs in {job_output_prefix}"
        )

    collection_id = slugify(COLLECTION_ID_FORMAT.format(**job_metadata), regex_pattern=r'[/\?#%& ]+')

    catalog = pystac.Catalog.from_file(catalog_json_key)
    catalog.make_all_asset_hrefs_absolute()

    for item in catalog.get_all_items():
        item.validate()

        item_dict = item.to_dict()
        item_dict["collection"] = collection_id

        yield Item(**item_dict)
