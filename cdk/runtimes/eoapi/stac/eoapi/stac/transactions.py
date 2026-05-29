"""Collection-only transaction extension for the MAAP STAC runtime."""

from typing import Any

import attr
from fastapi import APIRouter, FastAPI
from starlette.responses import Response

from stac_fastapi.api.models import JSONResponse
from stac_fastapi.extensions.core.transaction import (
    AsyncBaseTransactionsClient,
    TransactionConformanceClasses,
    TransactionExtension,
)
from stac_fastapi.types.config import ApiSettings


@attr.s
class CollectionTransactionExtension(TransactionExtension):
    """Register only collection transaction routes and conformance classes."""

    client: AsyncBaseTransactionsClient = attr.ib()
    settings: ApiSettings = attr.ib()
    conformance_classes: list[str] = attr.ib(
        factory=lambda: [TransactionConformanceClasses.COLLECTIONS]
    )
    schema_href: str | None = attr.ib(default=None)
    router: APIRouter = attr.ib(factory=APIRouter)
    response_class: type[Response] = attr.ib(default=JSONResponse)
    route_dependencies: list[Any] = attr.ib(factory=list)

    def register(self, app: FastAPI) -> None:
        """Register collection transaction routes with the target app."""
        self.router.prefix = app.state.router_prefix
        self.router.dependencies = list(self.route_dependencies)
        self.register_create_collection()
        self.register_update_collection()
        self.register_patch_collection()
        self.register_delete_collection()
        app.include_router(self.router, tags=["Collection Transaction Extension"])
