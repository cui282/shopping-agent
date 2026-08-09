# Security policy

## Reporting a vulnerability

Do not open a public issue for credentials, authentication bypasses, cross-user data access, path traversal, or other sensitive findings. Contact the repository owner privately through GitHub and include the affected revision, impact, and a minimal reproduction. Do not include real platform keys or customer data.

## Deployment boundary

The application supports a trusted identity-gateway boundary through `AUTH_ENABLED=true`, `X-Auth-User`, and `X-Auth-Tenant` (all names are configurable). When enabled, task snapshots, WebSockets, preferences, uploads, reports, and file downloads are tenant-scoped; an optional HMAC signature authenticates the injected headers. The gateway must still validate the end-user token and enforce network reachability, because the app intentionally does not parse JWTs.

Enable `RATE_LIMIT_ENABLED` in production. The built-in limiter is process-local protection; a multi-worker deployment must also enforce a shared gateway/Redis limiter. Provider retry, per-provider concurrency isolation, and circuit breaking are local safeguards rather than a replacement for an upstream egress policy.

Keep provider credentials server-side, use explicit CORS origins, enable TLS and authentication for Redis and OpenSearch, and configure retention for generated files and user data.

The sandbox profile is for local testing only and is rejected when `APP_ENV=production`.
