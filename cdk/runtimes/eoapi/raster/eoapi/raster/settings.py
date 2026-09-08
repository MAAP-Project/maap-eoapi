"""settings for titiler-pgstac runtime"""

from pydantic_settings import BaseSettings


class MosaicSettings(BaseSettings):
    """Application settings"""

    backend: str | None
    host: str | None
    format: str = ".json.gz"  # format will be ignored for dynamodb backend

    class Config:
        """model config"""

        env_prefix = "MOSAIC_"
        env_file = ".env"
