# Shopping Agent repository guide

## Project map

- `app/api/`: FastAPI lifecycle, task coordination, uploads, files, and WebSocket transport.
- `app/agent/`: main research workflow, LLM boundary, branch dispatch, and prompts.
- `app/tools/`: typed shopping tools and provider adapters.
- `app/memory/`, `app/recall/`: preference storage and optional retrieval backends.
- `frontend/src/`: React workspace, API client, task state machine, and product UI.
- `tests/backend/`: backend unit and API contract tests.
- `docs/API_CONTRACT.md`: frontend/backend contract; keep it aligned with Pydantic and TypeScript types.

## Verification

Run `make verify` before handing off a change. It checks Python lint/format, backend tests,
frontend tests, TypeScript, and the production bundle. For a focused backend loop use
`uv run pytest -q`; for a focused frontend loop use `cd frontend && npm run test`.

Do not claim a UI change is complete from type checks alone. Render it at 1280px, 375px,
and 320px and inspect task-ready, running, mixed-source, empty, and error states.

## Runtime boundaries

- Live mode fails closed when no marketplace gateway is configured.
- Fixture data requires explicit `SANDBOX_MODE=true` or non-production
  `ALLOW_FIXTURE_FALLBACK=true`; production must always fail closed.
- Provider source, status, fallback reason, exchange-rate provenance, and estimation notices are
  part of the product contract. Preserve them through backend and frontend changes.
- Image upload and image analysis are separate capabilities. Do not expose image upload as a
  search input until `/api/readiness` reports `image_analysis=true`.
- Do not add credentials, generated reports, uploads, local settings, or runtime data to Git.
- Treat `output/`, `uploaded/`, and `data/` as runtime-owned directories.

## Change discipline

- Prefer established schemas and adapters over untyped dictionaries or provider-specific branches.
- Update tests and `docs/API_CONTRACT.md` when an endpoint, event, or result field changes.
- Keep live and sandbox behavior independently testable.
- Avoid unrelated refactors in the same change.

## Agent skills

### Issue tracker

Issues and PRDs are tracked in GitHub Issues. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the five canonical Matt Pocock triage labels. See `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repository. See `docs/agents/domain.md`.
