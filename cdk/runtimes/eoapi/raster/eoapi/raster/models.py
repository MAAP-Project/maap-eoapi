"""Models to support mosaicjson endpoints"""

import re

from pydantic import BaseModel, field_validator
from stac_pydantic.api import Search


def to_camel(snake_str: str) -> str:
    """
    Converts snake_case_string to camelCaseString
    """
    first, *others = snake_str.split("_")
    return "".join([first.lower(), *map(str.title, others)])


# Link and Links derived from models in https://github.com/stac-utils/stac-pydantic
class Link(BaseModel):
    """Link Relation"""

    href: str
    rel: str
    type: str | None
    title: str | None


class MosaicEntity(BaseModel):
    """Mosaic Model."""

    id: str
    links: list[Link]


rfc3339_regex_str = (
    r"^(\d\d\d\d)\-(\d\d)\-(\d\d)(T|t)"
    r"(\d\d):(\d\d):(\d\d)(\.\d+)?(Z|([-+])(\d\d):(\d\d))$"
)
rfc3339_regex = re.compile(rfc3339_regex_str)


class StacApiQueryRequestBody(Search):
    """Common request params for MosaicJSON CRUD operations"""

    stac_api_root: str
    asset_name: str | None = None
    name: str | None = None
    description: str | None = None
    attribution: str | None = None
    version: str | None = None

    # override default Search field for collections, which is List[str]
    collections: list[str] | None = None
    # overriding limit so we can tell if it's defined or not
    limit: int | None = None

    @field_validator("datetime")
    def validate_datetime(cls, v):
        """
        datetime validation
        overrides default validation due to issue https://github.com/stac-utils/stac-pydantic/issues/78
        """
        if "/" in v:
            values = v.split("/")
        else:
            # Single date is interpreted as end date
            values = ["..", v]

        dates = []
        for value in values:
            if value == "..":
                dates.append(value)
                continue
            if not rfc3339_regex.match(value):
                raise ValueError(
                    f"Invalid datetime, must match format ({rfc3339_regex_str})."
                )
            dates.append(value)

        return v


class UrisRequestBody(BaseModel):
    """model for a source body to create a mosaicjson"""

    # option 2 - a list of files and min/max zoom
    urls: list[str]
    minzoom: int | None = None
    maxzoom: int | None = None
    name: str | None = None
    description: str | None = None
    attribution: str | None = None
    version: str | None = None


class TooManyResultsException(Exception):
    """exception when there are too many STAC API results to generate a mosaicjson"""

    def __init__(self, message):
        """init"""
        self.message = message


class StoreException(Exception):
    """exception when there is a problem storing the mosaicjson in the datastore"""

    def __init__(self, message):
        """init"""
        self.message = message


class UnsupportedOperationException(Exception):
    """exception for unsupported operation"""

    def __init__(self, message):
        """init"""
        self.message = message
