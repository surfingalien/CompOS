# CompOS

Compliance OS — an AI-first compliance platform, built around **the
Brain**: an event-driven nervous system that every module wires into for
observability and safe automated response.

## What's built

| Status | Meaning |
|---|---|
| ✅ | Real, buildable code in this repo |
| 🔶 | Interface defined, backing implementation pending a module that doesn't exist yet |
| 📋 | Design only (`docs/architecture.md`) |

- ✅ **`packages/brain`** — event bus (Redis Streams) → append-only event
  lake (Postgres) → flow correlation → health scoring → anomaly detection
  → guarded reflexes → RAG diagnostician agent → `/v1/brain/*` API.
- ✅ **`apps/api`** — Fastify app mounting the Brain's REST surface.
- ✅ **`packages/ethics-crypto`** — crypto-shred date computation for the
  (future) isolated ethics hotline, with tests.
- 🔶 **`packages/db`**, **`packages/core`**, **`packages/skills`** —
  minimal seed packages (Prisma client + RLS helper, a KMS credential
  interface, a skill registry) that the modules above depend on but that
  need real backing implementations (a full compliance data model, an
  actual KMS integration, registered connector skills) before they do
  anything beyond compile.
- 📋 Everything else in `docs/architecture.md`'s target picture —
  `apps/web`, `apps/workers` (Temporal), the isolated `apps/ethics`
  stack, the control-framework/evidence/policy/questionnaire data model,
  connectors, the MCP tool catalog — is design, not code, in this repo.

See `docs/merge-ledger.md` for the specific bugs found and fixed while
turning the original design draft into this code, and what was
deliberately left as an honest stub instead.

## Quickstart (dev)

```bash
docker compose -f infra/docker-compose.yml up -d   # postgres(pgvector) + redis
cp .env.example .env                                # fill in ANTHROPIC_API_KEY at least
pnpm install
pnpm --filter @compos/db --filter @compos/brain run db:generate
pnpm --filter @compos/db exec prisma migrate dev --schema prisma/schema.prisma
pnpm --filter @compos/brain exec prisma migrate dev --schema prisma/schema.prisma
pnpm --filter @compos/brain dev     # the Brain worker :3010
pnpm --filter @compos/api dev       # the API :3000
curl localhost:3010/status.json
curl localhost:3000/healthz
```

## Production deploy

```bash
cd infra
cp .env.example ../.env      # fill in DATABASE_URL, REDIS_URL, ANTHROPIC_API_KEY, PG_USER/PG_PASSWORD
./deploy.sh                  # build → migrate → up → smoke test
```

Single node: `infra/docker-compose.prod.yml` + Caddy (automatic TLS,
`infra/Caddyfile`). Scale-out: `infra/k8s/` (managed Postgres/Redis
recommended at that tier — see `infra/k8s/README.md`).

## Environment

| Var | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | ✅ | Postgres (pgvector) |
| `REDIS_URL` | ✅ | Brain event bus |
| `ANTHROPIC_API_KEY` | for `/v1/brain/ask` | Diagnostician agent |
| `PAGERDUTY_WEBHOOK` | optional | Brain CRITICAL-anomaly paging |
| `API_HTTP_PORT` / `BRAIN_HTTP_PORT` | optional | default 3000 / 3010 |

`.env.example` also lists vars for the not-yet-built ethics stack and
connectors — harmless to leave blank until those modules exist.

## The Brain (ops)

Any module calls `emit()` from `@compos/brain` with a typed event
(`packages/brain/src/catalog.ts`) plus `traceId`/`flowId`. The Brain
(a plain Node process, deliberately outside any workflow engine — see
`docs/architecture.md`) consumes these via Redis Streams into an
append-only event lake, then:

- **Correlates** events into flow timelines and flags flows stuck past
  their expected duration.
- **Scores health** per subsystem (api, connectors, check_runner, llm,
  temporal, sla_timers, review_queue, ethics_service) every ~60s, plus a
  weakest-leg composite.
- **Detects anomalies** — silence, error-rate (z-score), and stuck-flow
  detectors are implemented; cost and evidence-freshness detectors are
  stubbed pending their source data.
- **Reflexes** — safe automated response restricted to operational state
  (pause a dead connector, page on-call, open an ops incident). It never
  touches compliance data — nothing in `reflexes.ts` imports a
  compliance-data model.
- **Diagnoses** — `POST /v1/brain/ask` runs a retrieval-augmented root
  cause analysis over the event lake via Claude, citing event IDs.

Endpoints: `GET /v1/brain/health`, `/health/history`, `/anomalies`,
`/incidents`, `/flows/:flowId`, `POST /ask`; the Brain's own process also
serves `GET /healthz` and `GET /status.json` for an external uptime
monitor to watch (its own deadman switch).

## Security model (read before customer use)

1. **RLS is the intended multi-tenant boundary** — `packages/db`'s
   `withOrgContext()` sets `app.current_org` per transaction; the actual
   `FORCE ROW LEVEL SECURITY` policies (`rls.sql`) don't exist yet
   because the org-scoped data model they'd protect hasn't been built.
   Write both together, not one after the other.
2. **Ethics isolation is structural, not a promise** — `packages/brain`'s
   event catalog only accepts `ethics.heartbeat`/`ethics.sla.fired` from
   an ethics source, and nothing in the Brain's schema has a field for
   case content. Keep it that way when the ethics service is built.
3. **Reflexes are operational-only by construction** — see
   `packages/brain/src/reflexes.ts`; don't add an import there that
   touches compliance data.
4. **No AI-generated legal/compliance content ships without human
   review** — this applies the moment the control-framework/policy
   modules are built; there's no such content in this repo yet to review.

## Testing

```bash
pnpm --filter @compos/ethics-crypto test   # retention/crypto-shred date logic
pnpm typecheck                             # across all packages
```

There is no RLS audit or load-test suite yet — both depend on the
org-scoped data model this repo doesn't have.

## License

See `LICENSE`.
