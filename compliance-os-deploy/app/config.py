"""Runtime configuration, read once from the environment (.env via docker compose)."""
import os


def _load_dotenv(path: str = ".env"):
    """Minimal .env loader for local `uvicorn` runs (docker compose injects env itself).
    Real environment variables always win."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip().strip('"').strip("'")
            if value:
                os.environ.setdefault(key.strip(), value)


_load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/compliance_os.db")
SEED_DEMO_DATA = _bool("SEED_DEMO_DATA", True)

# Auth: when enabled every /v1 call needs header X-API-Key: <API_KEY>
AUTH_ENABLED = _bool("AUTH_ENABLED", False)
API_KEY = os.getenv("API_KEY", "")

# Internet search: none | mock | tavily | brave | serpapi | perplexity
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "none").lower()
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "")
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY", "")
PERPLEXITY_MODEL = os.getenv("PERPLEXITY_MODEL", "sonar")

# LLM for Legal AI / Policy AI / Questionnaire drafting: none | mock | anthropic | openai
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "none").lower()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

# Connectors
AWS_REGION = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", ""))
OKTA_ORG_URL = os.getenv("OKTA_ORG_URL", "").rstrip("/")
OKTA_API_TOKEN = os.getenv("OKTA_API_TOKEN", "")
OKTA_MAX_USERS = int(os.getenv("OKTA_MAX_USERS", "500"))
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_ORG = os.getenv("GITHUB_ORG", "")
GITHUB_API_URL = os.getenv("GITHUB_API_URL", "https://api.github.com").rstrip("/")
GITHUB_MAX_REPOS = int(os.getenv("GITHUB_MAX_REPOS", "200"))

# Outbound HTTP identity. SEC.gov rejects requests without a contact User-Agent.
HTTP_USER_AGENT = os.getenv("HTTP_USER_AGENT", "CompOS/3.1 (compliance-radar; contact: admin@example.com)")
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "30"))

# Background radar/market sync. 0 disables the scheduler.
RADAR_SYNC_INTERVAL_HOURS = float(os.getenv("RADAR_SYNC_INTERVAL_HOURS", "0"))
