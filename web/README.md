# tracewell — agent insight console

A lightweight single-page dashboard for the Browser Agent Insight Pipeline:
list ingested sessions, read each session's cleaned trajectory, and generate a
guardrailed `Insight` on demand.

Stack: Vite + React + TypeScript, plain CSS (no UI lib). One screen, no router.

## Run

The frontend talks to the FastAPI backend in the repo root. Start both:

```bash
# 1. backend (from repo root) — insight generation calls the Anthropic API
export ANTHROPIC_API_KEY=sk-ant-...
DB_PATH=$(pwd)/data/app.db uvicorn app.main:app --port 8000

# 2. frontend (this dir)
cd web
npm install      # first time only
npm run dev      # http://localhost:5173
```

Vite proxies `/api/*` → `http://localhost:8000` (see `vite.config.ts`), so the
browser makes same-origin calls in dev — no CORS setup needed. The backend also
enables CORS for a hosted/static build. Point a deployed build at a remote API
with `VITE_API_BASE=https://host` at build time.

## What it shows

- **Left rail** — every session (`GET /sessions`) with event count, relative
  time, and flag dots. Filter by id / flagged / injection.
- **Center** — the session's trajectory (`GET /sessions/:id/events`) as a status
  timeline. Friction steps dash the rail; repeated actions get a `loop ×N` badge;
  consecutive `low_progress` steps get a `stall ×N` bracket. An injected
  observation is rendered in a quarantined ```data fence (mirrors `PROMPT_V2`),
  never as a normal line.
- **Right** — the generated insight (`GET /sessions/:id/insight`, on the
  button). Confidence meter with a cap tick + a client-side cap ledger, friction
  points (click a `step N` ref to jump to it), and the recommended follow-up.

> Each "generate insight" click hits `GET /sessions/:id/insight`, which
> regenerates and appends a fresh `insight_runs` row (no caching) — by design.

## Honesty notes (design ↔ data)

The UI only renders fields the API actually returns. The confidence cap ledger
is derived client-side from real fields (`injection_count`, `integrity_flag`);
the reject term (`−0.1·rejects` in `guards.py`) is **not** exposed by the API, so
it's shown as `~ n/a` with a disclaimer — the backend applies the authoritative
cap. The validation seal reads a neutral `generated` because `validation_status`
lives only in the `insight_runs` table, not in the `Insight` response.
