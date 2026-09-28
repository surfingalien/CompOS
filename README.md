# CompOS
Compliance OS System

**The working, deployable app is in [`compliance-os-deploy/`](compliance-os-deploy/README.md).** It is a FastAPI backend
with live AWS / Okta / GitHub connectors, Federal Register and regulator-feed intelligence, Claude/OpenAI-powered
Legal AI, and a single-page UI.

```bash
cd compliance-os-deploy
cp .env.example .env
docker compose up --build   # http://localhost:8000
```

The other files at the repository root (`Batch *.txt`, `batch*.txt`, `split.py`, the standalone `*.html` demos)
are earlier design archives and offline sandboxes. They are kept for reference.
