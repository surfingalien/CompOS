# CompOS — Compliance Operating System (deployable)

FastAPI + SQLAlchemy backend with a single-page UI. Every screen reads and writes through the API and a
real database. Evidence comes from live connectors, and regulatory intelligence comes from live public sources.

| Area | What is real |
|---|---|
| **Evidence connectors** | **AWS** (boto3: CloudTrail logging, EBS default encryption, S3 account public-access block, KMS), **Okta** (active users without an active MFA factor), **GitHub** (rulesets / branch protection requiring PR review on default branches), **HR** (CSV roster upload), **manual attestation** for anything else |
| **Evidence vault** | SHA-256 hash over `{control, connector, payload, collected_at}`, verifiable via `/v1/evidence/{id}/verify`; evidence cannot be deleted |
| **Control testing** | Operators `eq, neq, exists, gte, lte, eq_field`. Failing tests open a gap + remediation; passing tests auto-resolve them |
| **Global Radar** | **Federal Register API** (keyless) for SEC, CFTC, OCC, FDIC, Fed, CFPB, FTC, HHS, CISA, NIST final/proposed rules, with effective dates and comment deadlines; other regulators via your search provider |
| **Market Intelligence** | Regulator press/enforcement RSS feeds (SEC, FTC, CFPB, Federal Reserve, CISA, EDPB, FCA; editable per source) + search |
| **Search** | Tavily, Brave, SerpAPI or Perplexity |
| **Legal AI / Policy AI / Questionnaire drafting** | Claude (`LLM_PROVIDER=anthropic`) or OpenAI (`LLM_PROVIDER=openai`). Without a key: rule-based obligation extraction and control-catalog policy templates, clearly labelled `heuristic` / `template` |
| **Privacy, incidents, vendors, trust, review, reporting, MCP, telemetry** | Persistent workflows. DSAR steps each produce hashed evidence. Incident triage computes notice deadlines from the breach matrix. Breach-matrix values change only through approved Expert Review proposals. Reports compute readiness from the latest test runs |

Nothing is faked. An unconfigured integration returns a clear error such as `Okta connector not configured: OKTA_ORG_URL, OKTA_API_TOKEN`. A failing feed is recorded on its source (`last_error`) instead of being replaced with mock data.

## Run with Docker

```bash
cp .env.example .env        # fill in the integrations you want
docker compose up --build
```

- UI: http://localhost:8000
- API docs: http://localhost:8000/docs
- Health: http://localhost:8000/health

Postgres instead of SQLite: set `DATABASE_URL=postgresql+psycopg2://compos:compos@db:5432/compos` in `.env`
and run `docker compose --profile postgres up --build`.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

`.env` is loaded automatically. Real environment variables take precedence.

## Connecting integrations

| Integration | `.env` | Minimum permissions |
|---|---|---|
| AWS | `AWS_REGION` + keys, or an IAM role / mounted `~/.aws` | `sts:GetCallerIdentity`, `ec2:GetEbsEncryptionByDefault`, `s3:GetAccountPublicAccessBlock`, `kms:ListKeys`, `cloudtrail:DescribeTrails`, `cloudtrail:GetTrailStatus` |
| Okta | `OKTA_ORG_URL`, `OKTA_API_TOKEN` | Read-only admin API token |
| GitHub | `GITHUB_TOKEN`, `GITHUB_ORG` | Fine-grained: Administration (read) + Metadata (read). Classic: `repo` |
| HR | — | Upload a CSV with `email,training_completed` columns in **Integrations** |
| Search | `SEARCH_PROVIDER` + matching key | — |
| LLM | `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY` (default model `claude-opus-5`, with server-side refusal fallback), or `openai` + `OPENAI_API_KEY` | — |

Use **Integrations → Test Connection** to check credentials, then **Collect Evidence**, then **Control Tests → Run All**.
Set `RADAR_SYNC_INTERVAL_HOURS=24` to pull Federal Register rules and regulator feeds automatically.

## Security

- Set `AUTH_ENABLED=true` and `API_KEY=...` before exposing the service. Every `/v1` call then requires `X-API-Key`; the UI asks for it once.
- Put TLS in front of the container (reverse proxy / load balancer).
- The breach-notification matrix is a starting point, not legal advice. Validate it with counsel.

## API quick reference

```bash
curl localhost:8000/v1/status                                   # providers + connector status
curl -X POST localhost:8000/v1/objects/tenant                   # create a tenant object
curl -X POST localhost:8000/v1/connectors/github/run            # collect live evidence
curl -X POST localhost:8000/v1/tests/run-all                    # evaluate all control tests
curl -X POST localhost:8000/v1/global-radar/sync                # Federal Register + search
curl -X POST localhost:8000/v1/market/sync                      # regulator RSS feeds
curl -X POST localhost:8000/v1/legal/documents -H 'content-type: application/json' \
  -d '{"source_url":"https://www.federalregister.gov/documents/...","jurisdiction":"US"}'
curl -X POST localhost:8000/v1/mcp/call -H 'content-type: application/json' \
  -d '{"tool":"run_connector","arguments":{"connector":"aws"}}'
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

## Layout

```
app/
  main.py         API routes and workflows
  config.py       environment configuration (+ .env loader)
  db.py           object store, telemetry, hashing helpers
  seed.py         reference data (controls, tests, 50-state matrix, regulator sources) + optional demo data
  connectors.py   AWS / Okta / GitHub / HR collectors
  intel.py        Federal Register, RSS, search providers, LLM (Claude / OpenAI), legal analysis
  static/index.html  single-page UI
tests/test_smoke.py
```
