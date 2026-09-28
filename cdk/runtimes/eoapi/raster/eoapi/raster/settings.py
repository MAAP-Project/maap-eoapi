"""settings for titiler-pgstac runtime"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class MosaicSettings(BaseSettings):
    """Application settings"""

    backend: str | None
    host: str | None
    format: str = ".json.gz"  # format will be ignored for dynamodb backend

    model_config = SettingsConfigDict(env_prefix="MOSAIC_", env_file=".env")
