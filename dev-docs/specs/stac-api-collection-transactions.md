# Spec: STAC API collection transactions runtime

## Context
MAAP eoAPI currently deploys the stock `eoapi-cdk` pgSTAC STAC API Lambda through `PgStacApiLambda` in `cdk/PgStacInfra.ts`. That runtime exposes the standard read-only STAC API and can enable the upstream STAC transaction extension.

The problem is that upstream `stac-fastapi` transaction support is all-or-nothing:
- enabling `TransactionExtension` registers both collection and item write routes
- it advertises both collection and item transaction conformance classes
- it exposes those routes in OpenAPI docs

For MAAP, we only want collection-level transactions for now. We do not want item-management transaction routes exposed, documented, or accidentally usable. We also need an auth layer on the collection write routes, with HTTP Basic acceptable now and JWT expected later. Finally, this must be switchable per deployment so it can be enabled for `userInfrastructure` while other deployments stay read-only on the same runtime.

We also want to preserve a clean path to using `developmentseed/stac-auth-proxy` inside the runtime as in-process middleware rather than only as a separate reverse proxy. That project already provides OIDC/JWT enforcement, OpenAPI security augmentation, STAC Authentication Extension responses, and policy-driven filtering. Its docs explicitly support applying the middleware stack to an existing FastAPI app via `configure_app(...)`, which makes it relevant to the long-term auth design here.

## Goals
- Add a custom STAC API runtime under `cdk/runtimes/eoapi/stac/`.
- Support collection transaction routes only:
  - `POST /collections`
  - `PUT /collections/{collection_id}`
  - `PATCH /collections/{collection_id}`
  - `DELETE /collections/{collection_id}`
- Do not expose item transaction routes:
  - no route registration
  - no OpenAPI entries
  - no item transaction conformance class
- Add an auth layer for collection transaction routes.
- Preserve a clean path to optional in-process `stac-auth-proxy` middleware for future OIDC/JWT auth.
- Make transaction support opt-in per `PgStacInfra` deployment.
- Keep the existing read-only STAC API behavior unchanged when the feature is disabled.
- Add a local `docker-compose.yml` for running the MAAP custom STAC and raster runtimes together during development.
- Leave a clean path to JWT-based auth later.

## Non-goals
- Implement JWT auth now.
- Deploy `stac-auth-proxy` as a separate standalone reverse proxy in front of the Lambda as part of this first change.
- Add item-level transaction support.
- Build tenant- or collection-specific authorization rules.
- Change ingestion flows outside the STAC API Lambda.
- Upstream a general-purpose fix to `stac-fastapi` as part of this change.

## Constraints and Assumptions
- Current STAC API deployment uses `eoapi-cdk` `PgStacApiLambda`.
- `eoapi-cdk` already supports overriding Lambda code via `lambdaFunctionOptions.code` and `handler`.
- Upstream `stac-fastapi-pgstac` v6.2 runtime imports `app` from `stac_fastapi.pgstac.app`, not `stac_fastapi.pgstac.main`.
- Upstream transaction wiring currently couples collection and item transaction routes in one extension.
- `stac-auth-proxy` is primarily packaged as a reverse proxy, but its middleware stack can also be applied directly to an existing FastAPI app via `configure_app(...)`.
- Current `stac-auth-proxy` auth enforcement is OIDC/JWT-oriented. It does not replace the need for a simple first-pass Basic auth path.
- Running the full proxy app in front of the Lambda would add an extra hop and duplicate some request/response shaping that we already control in the runtime.
- Secrets should not be stored as plaintext CDK config values when avoidable.
- We should minimize blast radius for the public STAC deployment.

## Architecture Overview
The change has two layers.

1. Runtime layer
   - Add a custom Python runtime package at `cdk/runtimes/eoapi/stac/`.
   - Rebuild the STAC API app locally instead of relying on the upstream all-in-one transaction extension.
   - Register normal read-only STAC behavior exactly as today.
   - Conditionally register a MAAP-specific collection-transactions extension.
     - this will just require a subclass of `stac_fastapi.extensions.transaction.TransactionExtension` with a `register()` method that omits the item routes
   - Apply auth only to the collection write routes.
     - for initial Basic auth, use the upstream `TransactionExtension(..., route_dependencies=...)` support added in `stac-fastapi` PR #885 once that release is available
   - Keep an auth-provider seam so future OIDC/JWT mode can install `stac-auth-proxy` middleware in-process on the same FastAPI app instead of introducing a separate proxy tier.

2. Infrastructure layer
   - Add a `transactions` config block under `stacApiConfig` in `PgStacInfra` props.
   - All deployments will use the same custom runtime.
   - The collection-transactions extension will only be activated (via Lambda env var) for instances where the `transactions` config is enabled.
   - Enable this only for `userInfrastructure` initially.

This keeps route behavior read-only unless transactions are explicitly enabled, while standardizing the runtime shape across deployments.

## Runtime Design

### File layout
Proposed layout:

```text
cdk/
  dockerfiles/
    Dockerfile.stac
  runtimes/
    eoapi/
      stac/
        README.md
        pyproject.toml
        uv.lock
        .python-version
        eoapi/
          stac/
            __init__.py
            main.py
            auth.py
            transactions.py
            handler.py
docker-compose.yml
```

### App construction
`eoapi/stac/main.py` will build the FastAPI app.

Implementation approach:
- make `eoapi/stac/main.py` a near 1:1 copy of `/home/henry/workspace/stac-utils/stac-fastapi-pgstac/stac_fastapi/pgstac/app.py`
- keep upstream app construction, middleware, lifespan wiring, request-model setup, and extension composition aligned as closely as possible
- continue to use upstream pgSTAC clients for core read behavior
- continue to use upstream request/response models where possible
- do not enable the default upstream `TransactionExtension`
- instead, register a local `CollectionTransactionExtension` in the same spot, with the rest of the app structure staying unchanged unless MAAP has a specific reason to diverge

Why copy the app closely instead of mutating the upstream app after import?
- removing routes after registration is brittle
- conformance classes and docs become easy to miss
- auth attachment is cleaner when routes are created locally
- it avoids depending on upstream internal route order or router structure
- keeping the file near-identical to upstream makes future drift easier to review and reduce

### CollectionTransactionExtension
Add a local extension in `eoapi/stac/transactions.py`.

It should:
- subclass `TransactionExtension`
- set `conformance_classes = [TransactionConformanceClasses.COLLECTIONS]`
- implement the same collection transaction route contracts as upstream
- reuse upstream `TransactionsClient` for collection CRUD methods
- use the upstream `route_dependencies` constructor support from `stac-fastapi` PR #885 so auth can be attached at extension construction time rather than by hand on each route
- override `register(app: FastAPI)` to set `self.router.prefix = app.state.router_prefix`, call the four collection registration helpers, and then `include_router(...)`
- register only these routes:

```text
POST   /collections
PUT    /collections/{collection_id}
PATCH  /collections/{collection_id}
DELETE /collections/{collection_id}
```

It should not register any `/collections/{collection_id}/items...` write routes.

It should advertise only this conformance class:

```text
https://api.stacspec.org/v1.0.0/collections/extensions/transaction
```

It must not advertise:

```text
https://api.stacspec.org/v1.0.0/ogcapi-features/extensions/transaction
```

This should stay intentionally small: reuse upstream route helper methods such as `register_create_collection()` and only narrow the registered surface, rather than forking transaction route implementations.

### Auth model
Add a small auth abstraction in `eoapi/stac/auth.py`.

Initial and planned modes:
- `basic` for the first implementation
- `oidc` as the future mode backed by `stac-auth-proxy` middleware

The route-level dependency contract for the initial Basic path is:

```python
async def require_transaction_auth(request: Request) -> None:
    ...
```

Behavior in `basic` mode:
- passed through `TransactionExtension(..., route_dependencies=[...])` when collection transactions are enabled
- applied only to collection transaction routes
- no effect on read-only routes
- returns `401` with `WWW-Authenticate: Basic` when credentials are missing or invalid
- should not be wired per-route manually unless the upstream release plan changes

Basic auth credential source:
- Lambda env contains `MAAP_TRANSACTION_AUTH_MODE=basic`
- Lambda env contains `MAAP_TRANSACTION_AUTH_SECRET_ARN=<secret arn>`
- referenced secret payload format:

```json
{
  "username": "...",
  "password": "..."
}
```

Planned `oidc` mode using `stac-auth-proxy` middleware:
- do not run the full reverse proxy app in front of the Lambda
- instead, apply the middleware stack to the in-process FastAPI app, using `stac_auth_proxy.configure_app(app, settings=...)` or selected middleware classes directly if we need tighter control
- configure path/method protection so only collection transaction routes are private:

```json
{
  "^/collections$": ["POST"],
  "^/collections/([^/]+)$": ["PUT", "PATCH", "DELETE"]
}
```

- do not mark item transaction routes as private because they should not exist in this runtime variant
- optionally use its OpenAPI and Authentication Extension middleware so docs and STAC responses advertise OIDC requirements consistently
- leave filtering middleware disabled unless and until we explicitly adopt record-level authorization

Why this is a future path rather than the first implementation:
- `stac-auth-proxy` currently assumes OIDC/JWT, not Basic auth
- our immediate need is a minimal collection-write guard
- the in-process middleware route is still valuable because it gives us a ready-made JWT/OIDC layer later without adding a separate network hop

Future JWT compatibility:
- auth dispatch should be mode-based, not hardcoded inside route handlers
- adding `oidc` later should require selecting a different auth provider, not rewriting collection transaction routes

### Runtime environment variables
The custom runtime should support these env vars in addition to existing STAC API env vars:

```text
ENABLED_EXTENSIONS=collection_transaction,collection_search,...
MAAP_TRANSACTION_AUTH_MODE=basic|oidc
MAAP_TRANSACTION_AUTH_SECRET_ARN=arn:aws:secretsmanager:...
MAAP_OIDC_DISCOVERY_URL=https://issuer/.well-known/openid-configuration
MAAP_ALLOWED_JWT_AUDIENCES=aud1,aud2
```

Rules:
- if `collection_transaction` is not in the `ENABLED_EXTENSIONS` env var, do not register the transaction endpoints
- if enabled and auth mode is `basic`, secret ARN is required
- if enabled and auth mode is `oidc`, OIDC discovery URL is required
- item transaction routes remain absent in all modes for this runtime version
- the runtime may internally map MAAP env vars into `stac-auth-proxy` settings rather than exposing the proxy's full env surface directly

### DB connection behavior
The runtime must create a write pool only when collection transactions are enabled.

Equivalent intent to upstream:
- read-only deployments use read pool only
- transaction-enabled deployments initialize write pool too

`handler.py` should keep the same Lambda/Mangum and SnapStart lifecycle pattern already used by the upstream `eoapi-cdk` runtime so connection handling stays consistent.

## API or Interface Design

### TypeScript props
Add a new optional transactions block under `stacApiConfig`.

```ts
stacApiConfig: {
  customDomainName?: string;
  integrationApiArn?: string;
  transactions?: {
    enabled: boolean;
    authMode: "basic" | "oidc";
    authSecretArn?: string;
    oidcDiscoveryUrl?: string;
    allowedJwtAudiences?: string[];
  };
};
```

Validation rules:
- `transactions` omitted => current behavior
- `transactions.enabled === false` => current behavior
- `transactions.enabled === true` and `authMode === "basic"` => `authSecretArn` required
- `transactions.enabled === true` and `authMode === "oidc"` => `oidcDiscoveryUrl` required
- `transactions.enabled === true` and `authMode === "oidc"` with audience enforcement => `allowedJwtAudiences` optional but recommended

### CDK usage
Initial intended usage in `cdk/app.ts`:

- `coreInfrastructure`: no transactions block
- `userInfrastructure`: transactions enabled

Example:

```ts
stacApiConfig: {
  customDomainName: userStacStacApiCustomDomainName,
  transactions: {
    enabled: true,
    authMode: "basic",
    authSecretArn: userStacCollectionTransactionsAuthSecretArn,
  },
}
```

### Runtime override in PgStacInfra
For all deployments:
- keep using `new PgStacApiLambda(...)`
- pass `lambdaFunctionOptions.code = lambda.Code.fromDockerBuild(...)`
- pass `lambdaFunctionOptions.handler = "handler.handler"`
- pass extension-selection env vars through `apiEnv`
- do not rely on upstream `enabledExtensions` transaction flag

When transactions are enabled, also pass auth env vars and, for `basic` mode, secret access.

This preserves existing API Gateway, custom domain, VPC, DB, and SnapStart behavior managed by `eoapi-cdk` while standardizing on one MAAP-owned runtime.

## Data Model
No database schema change is required.

New configuration data introduced:

### CDK deployment config
```ts
interface StacTransactionsConfig {
  enabled: boolean;
  authMode: "basic" | "oidc";
  authSecretArn?: string;
  oidcDiscoveryUrl?: string;
  allowedJwtAudiences?: string[];
}
```

### Secrets Manager payload for basic auth
```json
{
  "username": "stac-writer",
  "password": "<strong-random-secret>"
}
```

## Integration Points

### `cdk/PgStacInfra.ts`
Changes:
- extend `Props.stacApiConfig`
- always use the custom STAC runtime override for the STAC API Lambda
- grant transaction auth secret read access to the STAC API Lambda when needed
- pass auth and transactions env vars into the Lambda
- keep disabled deployments on the read-only path inside the shared custom runtime

### `cdk/app.ts`
Changes:
- wire transactions config only for `userInfrastructure`
- leave `coreInfrastructure` unchanged

### `cdk/config.ts`
Add optional config values for the user stack, for example:

```text
USER_STAC_COLLECTION_TRANSACTIONS_ENABLED
USER_STAC_COLLECTION_TRANSACTIONS_AUTH_MODE
USER_STAC_COLLECTION_TRANSACTIONS_AUTH_SECRET_ARN
USER_STAC_COLLECTION_TRANSACTIONS_OIDC_DISCOVERY_URL
USER_STAC_COLLECTION_TRANSACTIONS_ALLOWED_JWT_AUDIENCES
```

These should default to disabled when unset.

### Tests
Likely touch points:
- `test/config.test.ts` for config parsing
- new unit or synth-level tests for `PgStacInfra` transaction config behavior
- runtime tests for auth and route exposure
- if we add `oidc` mode later, runtime tests that `stac-auth-proxy` middleware protects only collection transaction routes and leaves read routes untouched

### Docs
Update at least:
- repo `README.md` if deployment configuration is user-facing
- runtime `README.md` under `cdk/runtimes/eoapi/stac/`
- `docker-compose.yml` usage notes for local runtime development

## Migration Path
1. Add custom runtime package and Dockerfile.
2. Add a local `docker-compose.yml` for the custom STAC and raster runtimes, following the shape of `/home/henry/workspace/devseed/eoapi-devseed/docker-compose.yml` where it still fits this repo.
3. Copy `stac_fastapi.pgstac.app` into the MAAP runtime as a near 1:1 local app, swapping in `CollectionTransactionExtension` for the default transaction extension.
4. Add transactions config to `PgStacInfra` and config loading in `cdk/config.ts`.
5. Wire `userInfrastructure` to use the new config.
6. When the `stac-fastapi` release that includes PR #885 is available, update any MAAP dependency pins needed to consume it and finish the extension-level auth attachment through `route_dependencies`.
7. Keep the runtime auth abstraction narrow so a later `oidc` mode can install `stac-auth-proxy` middleware without changing the collection transaction extension.
8. Deploy to a non-prod internal environment.
9. Verify:
   - collection transaction routes work with auth
   - item transaction routes return `404`
   - OpenAPI docs show only collection transaction routes
   - conformance output includes only the collection transaction class
10. Promote to other user STAC deployments as needed.

No backfill or data migration is required.

## Testing Strategy

### Runtime tests
Add Python tests around the custom app builder:
- transactions disabled => no write routes present
- transactions enabled => collection write routes present
- item write routes absent
- OpenAPI schema excludes item transaction routes
- conformance classes exclude item transaction URI
- basic auth rejects unauthenticated requests with `401`
- basic auth accepts valid credentials
- future `oidc` mode can be enabled without reintroducing item transaction docs or routes
- future `oidc` mode protects only the collection transaction endpoints when configured with collection-only private endpoint regexes

### Infrastructure tests
Add TypeScript tests for:
- config parsing defaults to disabled
- enabling basic auth without secret ARN throws
- all deployments use the custom Lambda handler/code override
- enabling transactions adds expected Lambda env vars
- disabled mode omits transaction auth env vars and keeps read-only behavior

### Local development checks
Add local verification for `docker-compose.yml`:
- custom STAC runtime starts against local pgSTAC
- custom raster runtime starts alongside it
- disabled-mode STAC startup works before the auth-hook release lands
- compose configuration remains usable for the final auth-enabled verification pass once PR #885 is available

### Smoke tests
Post-deploy manual/API checks:
- `GET /` and `GET /conformance`
- `POST /collections` with and without auth
- `PUT/PATCH/DELETE /collections/{collection_id}` with auth
- `POST /collections/{collection_id}/items` should be `404`
- Swagger/OpenAPI should not document item transaction routes

## Decision Log
| Decision | Options Considered | Rationale |
|----------|--------------------|-----------|
| Use one custom runtime for all STAC deployments | Only use the custom runtime when transactions are enabled; patch routes in place; use stock runtime unchanged | Keeps route behavior switchable per deployment while avoiding two runtime code paths for the same API surface |
| Keep a near 1:1 local copy of `stac_fastapi.pgstac.app` | Mutate the upstream app after import; reassemble a more custom MAAP app from smaller pieces | A near-identical copy keeps behavior aligned with upstream while making the transaction-extension substitution explicit and reviewable |
| Implement a collection-only extension | Monkeypatch the upstream router | A local extension is explicit, testable, and resilient to upstream internal changes |
| Use `TransactionExtension.route_dependencies` for initial Basic auth attachment | API Gateway auth only; middleware on all routes; manually attaching dependencies per route | We only need protection on collection write routes right now, and the upstream `route_dependencies` hook added in PR #885 gives us a clean extension-level attachment point for the minimal Basic-auth first step |
| Preserve `stac-auth-proxy` as an in-process middleware option for future OIDC/JWT | Run a standalone reverse proxy in front of the Lambda; build our own JWT middleware from scratch; ignore the project for now | `stac-auth-proxy` already solves OIDC enforcement, OpenAPI security augmentation, and STAC Authentication Extension responses. Using it in-process keeps that path open without committing this first iteration to a separate proxy hop or to OIDC immediately |
| Store basic auth credentials in Secrets Manager | Plain env vars; SSM parameters | Secrets Manager is the least bad option for credentials and matches existing Lambda secret-read patterns |
| Omit item transaction routes entirely | Expose them but block with auth/authorization | Not registering them is safer and keeps docs/conformance honest |

## Open Questions
- Should failed item transaction requests return `404` or an explicit `403` from a defensive blocker route? The cleaner default is `404` by not registering them.
- Do we want to add explicit public-stack transaction config now for symmetry, or keep the config surface user-stack-only until there is a real second use case?
- Should basic auth credentials be a single shared writer credential, or do we expect multiple clients soon enough to justify a richer secret format?
- Do we want CloudWatch metrics or structured logs specifically for collection transaction attempts and auth failures?
- Which released `stac-fastapi` version first includes PR #885, and do any downstream MAAP dependencies need version bumps before we can rely on it for the final auth attachment?
- If we adopt `stac-auth-proxy` in-process later, should we call `configure_app(...)` wholesale or add only `EnforceAuthMiddleware`, `OpenApiMiddleware`, and `AuthenticationExtensionMiddleware` directly for tighter control?
- Do we want the future `oidc` mode to expose the STAC Authentication Extension immediately, or should that remain separately configurable?
- How do we want to keep the local `app.py` copy aligned with future upstream `stac-fastapi-pgstac` changes after this fork lands?
- Is there any existing upstream work toward collection-only transaction registration that we may want to track before maintaining this long term?

## References
- `cdk/PgStacInfra.ts`
- `cdk/app.ts`
- `cdk/config.ts`
- `cdk/runtimes/eoapi/raster/`
- `node_modules/eoapi-cdk/lib/stac-api/index.d.ts`
- `node_modules/eoapi-cdk/lib/stac-api/runtime/src/stac_api/handler.py`
- `/home/henry/workspace/stac-utils/stac-fastapi-pgstac/stac_fastapi/pgstac/app.py`
- `https://github.com/stac-utils/stac-fastapi-pgstac/blob/main/stac_fastapi/pgstac/app.py`
- `https://github.com/stac-utils/stac-fastapi-pgstac/blob/main/stac_fastapi/pgstac/transactions.py`
- `https://github.com/stac-utils/stac-fastapi/blob/main/stac_fastapi/extensions/stac_fastapi/extensions/transaction/transaction.py`
- `https://github.com/stac-utils/stac-fastapi/issues/884`
- `https://github.com/stac-utils/stac-fastapi/pull/885`
- `https://github.com/developmentseed/stac-auth-proxy`
- `https://developmentseed.org/stac-auth-proxy/user-guide/getting-started/`
- `https://developmentseed.org/stac-auth-proxy/user-guide/configuration/`
- `https://developmentseed.org/stac-auth-proxy/user-guide/route-level-auth/`
- `https://developmentseed.org/stac-auth-proxy/architecture/middleware-stack/`
- `/home/henry/workspace/devseed/eoapi-devseed/docker-compose.yml`
