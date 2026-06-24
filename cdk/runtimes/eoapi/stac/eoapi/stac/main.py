"""MAAP-owned STAC API application assembly."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, cast
from urllib.parse import urljoin

import attr
from brotli_asgi import BrotliMiddleware
from eoapi.stac.auth import build_transaction_route_dependencies
from eoapi.stac.transactions import CollectionTransactionExtension
from fastapi import APIRouter, FastAPI
from fastapi.params import Depends
from stac_fastapi.api.app import StacApi
from stac_fastapi.api.middleware import ProxyHeaderMiddleware
from stac_fastapi.api.models import (
    EmptyRequest,
    ItemCollectionUri,
    JSONResponse,
    create_get_request_model,
    create_post_request_model,
    create_request_model,
)
from stac_fastapi.extensions import (
    CollectionSearchExtension,
    CollectionSearchFilterExtension,
    FieldsExtension,
    ItemCollectionFilterExtension,
    OffsetPaginationExtension,
    SearchFilterExtension,
    SortExtension,
    TokenPaginationExtension,
)
from stac_fastapi.extensions.fields import FieldsConformanceClasses
from stac_fastapi.extensions.free_text import FreeTextConformanceClasses
from stac_fastapi.extensions.query import QueryConformanceClasses
from stac_fastapi.extensions.sort import SortConformanceClasses
from stac_fastapi.pgstac.config import Settings
from stac_fastapi.pgstac.core import CoreCrudClient, health_check
from stac_fastapi.pgstac.db import close_db_connection, connect_to_db
from stac_fastapi.pgstac.extensions import (
    CatalogsDatabaseLogic,
    FreeTextExtension,
    QueryExtension,
)
from stac_fastapi.pgstac.extensions.catalogs.catalogs_client import CatalogsClient
from stac_fastapi.pgstac.extensions.filter import FiltersClient
from stac_fastapi.pgstac.transactions import TransactionsClient
from stac_fastapi.pgstac.types.search import PgstacSearch
from stac_fastapi.types.extension import ApiExtension
from stac_fastapi.types.requests import get_base_url
from stac_fastapi.types.search import APIRequest
from stac_fastapi_catalogs_extension import (
    CatalogsExtension,
    CatalogsTransactionExtension,
)
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

settings = Settings()

COLLECTION_TRANSACTION_EXTENSION = "collection_transaction"
CATALOGS_EXTENSION = "catalogs"
CATALOG_TRANSACTION_EXTENSION = "catalog_transaction"

SEARCH_EXTENSIONS_MAP: dict[str, ApiExtension] = {
    "query": QueryExtension(),
    "sort": SortExtension(),
    "fields": FieldsExtension(),
    "filter": SearchFilterExtension(client=FiltersClient()),
    "pagination": TokenPaginationExtension(),
}

COLLECTION_SEARCH_EXTENSIONS_MAP: dict[str, ApiExtension] = {
    "query": QueryExtension(conformance_classes=[QueryConformanceClasses.COLLECTIONS]),
    "sort": SortExtension(conformance_classes=[SortConformanceClasses.COLLECTIONS]),
    "fields": FieldsExtension(
        conformance_classes=[FieldsConformanceClasses.COLLECTIONS]
    ),
    "filter": CollectionSearchFilterExtension(client=FiltersClient()),
    "free_text": FreeTextExtension(
        conformance_classes=[FreeTextConformanceClasses.COLLECTIONS]
    ),
    "pagination": OffsetPaginationExtension(),
}

ITEM_COLLECTION_EXTENSIONS_MAP: dict[str, ApiExtension] = {
    "query": QueryExtension(conformance_classes=[QueryConformanceClasses.ITEMS]),
    "sort": SortExtension(conformance_classes=[SortConformanceClasses.ITEMS]),
    "fields": FieldsExtension(conformance_classes=[FieldsConformanceClasses.ITEMS]),
    "filter": ItemCollectionFilterExtension(client=FiltersClient()),
    "pagination": TokenPaginationExtension(),
}

DEFAULT_ENABLED_EXTENSIONS: set[str] = {
    *SEARCH_EXTENSIONS_MAP.keys(),
    *COLLECTION_SEARCH_EXTENSIONS_MAP.keys(),
    *ITEM_COLLECTION_EXTENSIONS_MAP.keys(),
    "collection_search",
    CATALOGS_EXTENSION,
}

KNOWN_EXTENSIONS: set[str] = {
    *DEFAULT_ENABLED_EXTENSIONS,
    COLLECTION_TRANSACTION_EXTENSION,
    CATALOG_TRANSACTION_EXTENSION,
}


@attr.s
class MaapCoreCrudClient(CoreCrudClient):
    """MAAP core client with catalog-aware landing page links."""

    catalogs_client: CatalogsClient | None = attr.ib(default=None, kw_only=True)

    async def landing_page(self, **kwargs: Any) -> Any:
        """Return the STAC landing page with catalogs exposed as children."""
        landing_page = await super().landing_page(**kwargs)

        if not self.extension_is_enabled("CatalogsExtension"):
            return landing_page

        if self.catalogs_client is None:
            return landing_page

        request = kwargs["request"]
        base_url = get_base_url(request)
        for link in landing_page["links"]:
            if link.get("rel") == "catalogs":
                link["href"] = urljoin(base_url, "catalogs")

        catalogs, _, _ = await self.catalogs_client.database.get_all_catalogs(
            token=None,
            limit=1000,
            request=request,
        )

        for catalog in catalogs:
            catalog_id = catalog.get("id")
            if not catalog_id:
                continue
            if catalog.get("parent_ids"):
                continue

            landing_page["links"].append(
                {
                    "rel": "child",
                    "type": "application/json",
                    "title": catalog.get("title", catalog_id),
                    "href": urljoin(base_url, f"catalogs/{catalog_id}"),
                }
            )

        return landing_page


@attr.s
class AuthenticatedCatalogsTransactionExtension(CatalogsTransactionExtension):
    """Catalog transaction extension adapter with route-level dependencies."""

    route_dependencies: list[Depends] = attr.ib(factory=list, kw_only=True)

    def register(self, app: FastAPI) -> None:
        """Register catalog write routes with auth dependencies on each route."""
        self.router.dependencies = list(self.route_dependencies)
        super().register(app)


def parse_enabled_extensions(raw_value: str | None) -> set[str]:
    """Parse and validate the ENABLED_EXTENSIONS environment value."""
    if raw_value is None:
        return set(DEFAULT_ENABLED_EXTENSIONS)

    enabled_extensions = {part.strip() for part in raw_value.split(",")}
    if "" in enabled_extensions:
        raise ValueError("Invalid ENABLED_EXTENSIONS: empty extension name")

    unknown_extensions = enabled_extensions - KNOWN_EXTENSIONS
    if unknown_extensions:
        joined_unknown_extensions = ", ".join(sorted(unknown_extensions))
        raise ValueError(
            f"Invalid ENABLED_EXTENSIONS: unsupported extensions: {joined_unknown_extensions}"
        )

    return enabled_extensions


def _build_middlewares() -> list[Middleware]:
    """Build the middleware stack used by the upstream pgSTAC app."""
    return [
        Middleware(BrotliMiddleware),
        Middleware(ProxyHeaderMiddleware),
        Middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_origin_regex=settings.cors_origin_regex,
            allow_methods=settings.cors_methods,
            allow_credentials=settings.cors_credentials,
            allow_headers=settings.cors_headers,
            max_age=600,
        ),
    ]


def _build_lifespan(with_write_transactions: bool):
    """Build the FastAPI lifespan for local app execution."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await connect_to_db(
            app,
            add_write_connection_pool=with_write_transactions,
        )
        yield
        await close_db_connection(app)

    return lifespan


def create_app(
    *,
    enabled_extensions: set[str] | None = None,
    connect_to_database: bool = True,
) -> FastAPI:
    """Create the MAAP STAC app with optional catalog and collection transactions."""
    resolved_extensions = (
        enabled_extensions
        if enabled_extensions is not None
        else parse_enabled_extensions(os.environ.get("ENABLED_EXTENSIONS"))
    )
    application_extensions: list[ApiExtension] = []
    with_collection_transactions = (
        COLLECTION_TRANSACTION_EXTENSION in resolved_extensions
    )
    with_catalogs = (
        CATALOGS_EXTENSION in resolved_extensions or settings.enable_catalogs_extension
    )
    with_catalog_transactions = CATALOG_TRANSACTION_EXTENSION in resolved_extensions
    with_write_transactions = with_collection_transactions or with_catalog_transactions
    transaction_route_dependencies: list[Depends] = []
    catalogs_client: CatalogsClient | None = None

    if with_catalog_transactions and not with_catalogs:
        raise ValueError("catalog_transaction requires catalogs in ENABLED_EXTENSIONS")

    if with_write_transactions:
        transaction_route_dependencies = build_transaction_route_dependencies()

    if with_collection_transactions:
        application_extensions.append(
            CollectionTransactionExtension(
                client=TransactionsClient(),
                settings=settings,
                response_class=JSONResponse,
                route_dependencies=transaction_route_dependencies,
            )
        )

    search_extensions = [
        extension
        for key, extension in SEARCH_EXTENSIONS_MAP.items()
        if key in resolved_extensions
    ]
    post_request_model = create_post_request_model(
        search_extensions,
        base_model=PgstacSearch,
    )
    get_request_model = create_get_request_model(search_extensions)
    application_extensions.extend(search_extensions)

    items_get_request_model: type[APIRequest] = ItemCollectionUri
    item_collection_extensions = [
        extension
        for key, extension in ITEM_COLLECTION_EXTENSIONS_MAP.items()
        if key in resolved_extensions
    ]
    if item_collection_extensions:
        items_get_request_model = cast(
            type[APIRequest],
            create_request_model(
                model_name="ItemCollectionUri",
                base_model=ItemCollectionUri,
                extensions=item_collection_extensions,
                request_type="GET",
            ),
        )
        application_extensions.extend(item_collection_extensions)

    collections_get_request_model: type[APIRequest] = EmptyRequest
    if "collection_search" in resolved_extensions:
        collection_search_extensions = [
            extension
            for key, extension in COLLECTION_SEARCH_EXTENSIONS_MAP.items()
            if key in resolved_extensions
        ]
        collection_search_extension = CollectionSearchExtension.from_extensions(
            collection_search_extensions
        )
        collections_get_request_model = collection_search_extension.GET
        application_extensions.append(collection_search_extension)

    if with_catalogs:
        catalogs_client = CatalogsClient(database=CatalogsDatabaseLogic())
        application_extensions.append(
            CatalogsExtension(
                client=catalogs_client,
                settings={"enable_response_models": settings.enable_response_models},
                hide_alternate_parents=settings.hide_alternate_parents,
            )
        )
        if with_catalog_transactions:
            application_extensions.append(
                AuthenticatedCatalogsTransactionExtension(
                    client=catalogs_client,
                    settings={
                        "enable_response_models": settings.enable_response_models
                    },
                    route_dependencies=transaction_route_dependencies,
                )
            )

    api = StacApi(
        app=FastAPI(
            openapi_url=settings.openapi_url,
            docs_url=settings.docs_url,
            redoc_url=None,
            root_path=settings.root_path,
            title=settings.stac_fastapi_title,
            version=settings.stac_fastapi_version,
            description=settings.stac_fastapi_description,
            lifespan=(
                _build_lifespan(with_write_transactions)
                if connect_to_database
                else None
            ),
        ),
        router=APIRouter(prefix=settings.prefix_path),
        settings=settings,
        extensions=application_extensions,
        client=MaapCoreCrudClient(
            pgstac_search_model=post_request_model,
            catalogs_client=catalogs_client,
        ),  # type: ignore[arg-type]
        response_class=JSONResponse,
        items_get_request_model=items_get_request_model,
        search_get_request_model=get_request_model,
        search_post_request_model=post_request_model,
        collections_get_request_model=collections_get_request_model,
        middlewares=_build_middlewares(),
        health_check=health_check,  # type: ignore[arg-type]
    )
    return api.app


def run() -> None:
    """Run the app locally with uvicorn if it is installed."""
    try:
        import uvicorn
    except ImportError as error:
        raise RuntimeError(
            "Uvicorn must be installed in order to use command"
        ) from error

    uvicorn.run(
        "eoapi.stac.main:app",
        host=settings.app_host,
        port=settings.app_port,
        log_level="info",
        reload=settings.reload,
        root_path=os.getenv("UVICORN_ROOT_PATH", ""),
    )


app = create_app()


if __name__ == "__main__":
    run()
