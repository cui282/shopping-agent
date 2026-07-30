# Contributing

## Setup

Install backend and frontend development dependencies:

```bash
make install
cp examples/sandbox.env.example .env
```

Run FastAPI and Vite in separate terminals with `make dev-backend` and `make dev-frontend`.

## Before opening a pull request

Run the repository quality gate:

```bash
make verify
```

Keep changes focused. API and event changes must update the Pydantic schema, frontend TypeScript types, tests, and `docs/API_CONTRACT.md` together. UI changes must be rendered at 1280px, 375px, and 320px before handoff.

Use GitHub Issues for defects and planned work. The repository labels and agent workflow are documented under `docs/agents/`.

## Runtime truthfulness

Never turn missing live data into an undisclosed fixture result. Sandbox data requires `SANDBOX_MODE=true`, and every provider transition must preserve source, status, and fallback metadata. Do not expose image-based search until readiness reports `image_analysis=true`.
