# Security policy

## Reporting a vulnerability

Do not open a public issue for credentials, authentication bypasses, cross-user data access, path traversal, or other sensitive findings. Contact the repository owner privately through GitHub and include the affected revision, impact, and a minimal reproduction. Do not include real platform keys or customer data.

## Deployment boundary

Version `0.1.x` does not provide login or tenant authorization. Deploy it only behind a trusted identity gateway that enforces ownership of task, WebSocket, preference, upload, and report resources. Keep provider credentials server-side, use explicit CORS origins, enable TLS and authentication for Redis and OpenSearch, and configure retention for generated files and user data.

The sandbox profile is for local evaluation only and is rejected when `APP_ENV=production`.
