"""Live evidence connectors. Each collector talks to the real system and returns
evidence records; nothing is fabricated. Missing configuration raises ConnectorError."""
import csv
import io
import re
from typing import Callable, Dict, List

import httpx

from . import config


class ConnectorError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _missing(*names: str) -> List[str]:
    return [n for n in names if not getattr(config, n)]


def _aws_configured() -> List[str]:
    # boto3 resolves credentials from env, ~/.aws, instance/task roles, SSO... so only
    # verify that boto3 is installed and a region is known.
    try:
        import boto3  # noqa: F401
    except ImportError:
        return ["boto3 (pip install boto3)"]
    return [] if config.AWS_REGION else ["AWS_REGION"]


CONNECTORS = {
    "aws": {"name": "AWS", "description": "CloudTrail logging, EBS default encryption, S3 account public-access block, KMS keys",
            "controls": ["ENC-001", "LOG-001"], "env": ["AWS_REGION", "AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (or an IAM role / AWS_PROFILE)"],
            "missing": _aws_configured},
    "okta": {"name": "Okta", "description": "Active users and enrolled MFA factors",
             "controls": ["AC-001"], "env": ["OKTA_ORG_URL", "OKTA_API_TOKEN"],
             "missing": lambda: _missing("OKTA_ORG_URL", "OKTA_API_TOKEN")},
    "github": {"name": "GitHub", "description": "Branch protection / rulesets requiring pull-request review on default branches",
               "controls": ["SDLC-001"], "env": ["GITHUB_TOKEN", "GITHUB_ORG (optional; defaults to the token owner's repos)"],
               "missing": lambda: _missing("GITHUB_TOKEN")},
    "hr": {"name": "HR training roster", "description": "Upload a CSV export from your HRIS/LMS (columns: email, training_completed)",
           "controls": ["HR-001"], "env": [], "missing": lambda: [], "upload_only": True},
}


def connector_status() -> List[dict]:
    out = []
    for key, c in CONNECTORS.items():
        missing = c["missing"]()
        out.append({"id": key, "name": c["name"], "description": c["description"], "controls": c["controls"],
                    "env": c["env"], "configured": not missing, "missing": missing,
                    "upload_only": c.get("upload_only", False)})
    return out


def _client(**kw) -> httpx.Client:
    return httpx.Client(timeout=config.HTTP_TIMEOUT, follow_redirects=True,
                        headers={"User-Agent": config.HTTP_USER_AGENT, **kw.pop("headers", {})}, **kw)


def _next_link(resp: httpx.Response):
    nxt = resp.links.get("next")
    return nxt.get("url") if nxt else None


# ---------------------------------------------------------------- AWS
def collect_aws() -> List[dict]:
    missing = _aws_configured()
    if missing:
        raise ConnectorError("AWS connector not configured: " + ", ".join(missing))
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

    try:
        session = boto3.session.Session(region_name=config.AWS_REGION)
        account_id = session.client("sts").get_caller_identity()["Account"]
    except NoCredentialsError:
        raise ConnectorError("AWS credentials not found (set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY or attach an IAM role)")
    except (BotoCoreError, ClientError) as e:
        raise ConnectorError(f"AWS authentication failed: {e}", 502)

    errors = {}

    def attempt(label: str, fn: Callable, default=None):
        try:
            return fn()
        except (BotoCoreError, ClientError) as e:
            errors[label] = str(e)
            return default

    ebs = attempt("ec2:GetEbsEncryptionByDefault",
                  lambda: session.client("ec2").get_ebs_encryption_by_default()["EbsEncryptionByDefault"])

    def s3_block():
        try:
            cfg = session.client("s3control").get_public_access_block(AccountId=account_id)["PublicAccessBlockConfiguration"]
            return all(cfg.get(k) for k in ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets"))
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "NoSuchPublicAccessBlockConfiguration":
                return False
            raise

    s3 = attempt("s3control:GetPublicAccessBlock", s3_block)
    kms_keys = attempt("kms:ListKeys", lambda: len(session.client("kms").list_keys(Limit=100).get("Keys", [])))

    def trails():
        ct = session.client("cloudtrail")
        items = ct.describe_trails(includeShadowTrails=True).get("trailList", [])
        logging, multi = [], False
        for t in items:
            status = ct.get_trail_status(Name=t["TrailARN"])
            if status.get("IsLogging"):
                logging.append(t["Name"])
                multi = multi or bool(t.get("IsMultiRegionTrail"))
        return {"trails": len(items), "logging_trails": logging, "multi_region": multi}

    ct = attempt("cloudtrail:DescribeTrails", trails, {})

    base = {"account_id": account_id, "region": config.AWS_REGION}
    enc_payload = {**base, "ebs_encryption_enabled": ebs, "s3_public_access_blocked": s3, "kms_keys": kms_keys}
    log_payload = {**base, "cloudtrail_enabled": bool(ct.get("logging_trails")) if ct else None,
                   "trails": ct.get("trails"), "logging_trails": ct.get("logging_trails"), "multi_region": ct.get("multi_region")}
    enc_errors = {k: v for k, v in errors.items() if not k.startswith("cloudtrail")}
    log_errors = {k: v for k, v in errors.items() if k.startswith("cloudtrail")}
    return [
        {"control_id": "ENC-001", "payload": enc_payload, "errors": enc_errors},
        {"control_id": "LOG-001", "payload": log_payload, "errors": log_errors},
    ]


# ---------------------------------------------------------------- Okta
def collect_okta() -> List[dict]:
    missing = _missing("OKTA_ORG_URL", "OKTA_API_TOKEN")
    if missing:
        raise ConnectorError("Okta connector not configured: " + ", ".join(missing))
    headers = {"Authorization": f"SSWS {config.OKTA_API_TOKEN}", "Accept": "application/json"}
    users, errors = [], {}
    with _client(headers=headers) as c:
        url = f"{config.OKTA_ORG_URL}/api/v1/users"
        params = {"filter": 'status eq "ACTIVE"', "limit": 200}
        while url and len(users) < config.OKTA_MAX_USERS:
            r = c.get(url, params=params)
            if r.status_code in (401, 403):
                raise ConnectorError(f"Okta rejected the API token ({r.status_code})", 502)
            r.raise_for_status()
            users.extend(r.json())
            url, params = _next_link(r), None
        users = users[: config.OKTA_MAX_USERS]
        without_mfa = []
        for u in users:
            r = c.get(f"{config.OKTA_ORG_URL}/api/v1/users/{u['id']}/factors")
            if r.status_code == 429:
                errors["rate_limited"] = "Okta rate limit hit; result is partial"
                break
            if r.status_code >= 400:
                errors[u["id"]] = f"factors lookup failed: {r.status_code}"
                continue
            if not any(f.get("status") == "ACTIVE" for f in r.json()):
                without_mfa.append((u.get("profile") or {}).get("login") or u["id"])
    payload = {"org_url": config.OKTA_ORG_URL, "active_users": len(users), "total_users": len(users),
               "users_without_mfa": len(without_mfa), "users_without_mfa_sample": without_mfa[:25],
               "truncated_at": config.OKTA_MAX_USERS if len(users) >= config.OKTA_MAX_USERS else None}
    return [{"control_id": "AC-001", "payload": payload, "errors": errors}]


# ---------------------------------------------------------------- GitHub
def _repo_requires_review(c: httpx.Client, full_name: str, branch: str):
    """True/False when determinable, None when the API won't say (e.g. plan limits)."""
    api = config.GITHUB_API_URL
    # Repository rulesets (current mechanism)
    r = c.get(f"{api}/repos/{full_name}/rules/branches/{branch}")
    if r.status_code == 200:
        for rule in r.json():
            if rule.get("type") == "pull_request" and (rule.get("parameters") or {}).get("required_approving_review_count", 0) >= 1:
                return True
    # Classic branch protection
    r = c.get(f"{api}/repos/{full_name}/branches/{branch}/protection")
    if r.status_code == 200:
        reviews = r.json().get("required_pull_request_reviews") or {}
        return bool(reviews) and reviews.get("required_approving_review_count", 1) >= 1
    if r.status_code == 404:
        return False
    return None


def collect_github() -> List[dict]:
    if not config.GITHUB_TOKEN:
        raise ConnectorError("GitHub connector not configured: GITHUB_TOKEN")
    headers = {"Authorization": f"Bearer {config.GITHUB_TOKEN}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    api = config.GITHUB_API_URL
    repos, errors = [], {}
    with _client(headers=headers) as c:
        url = f"{api}/orgs/{config.GITHUB_ORG}/repos" if config.GITHUB_ORG else f"{api}/user/repos"
        params = {"per_page": 100, "type": "all"} if config.GITHUB_ORG else {"per_page": 100, "affiliation": "owner"}
        while url and len(repos) < config.GITHUB_MAX_REPOS:
            r = c.get(url, params=params)
            if r.status_code in (401, 403):
                raise ConnectorError(f"GitHub rejected the token ({r.status_code}): {r.text[:200]}", 502)
            if r.status_code == 404:
                raise ConnectorError(f"GitHub org '{config.GITHUB_ORG}' not found or not visible to this token", 502)
            r.raise_for_status()
            repos.extend(x for x in r.json() if not x.get("archived"))
            url, params = _next_link(r), None
        repos = repos[: config.GITHUB_MAX_REPOS]
        protected, unprotected, unknown = [], [], []
        for repo in repos:
            try:
                result = _repo_requires_review(c, repo["full_name"], repo.get("default_branch") or "main")
            except httpx.HTTPError as e:
                errors[repo["full_name"]] = str(e)
                result = None
            (protected if result else unprotected if result is False else unknown).append(repo["full_name"])
    payload = {"owner": config.GITHUB_ORG or "token-owner", "repositories_checked": len(protected) + len(unprotected),
               "required_reviews_enabled": len(protected), "unprotected_repos": unprotected[:50],
               "undeterminable_repos": unknown[:50]}
    return [{"control_id": "SDLC-001", "payload": payload, "errors": errors}]


# ---------------------------------------------------------------- HR (CSV upload)
_TRUE = re.compile(r"^(1|y|yes|true|complete|completed|done|passed|\d{4}-\d{2}-\d{2}.*)$", re.I)


def parse_hr_csv(text: str) -> List[dict]:
    rows = list(csv.DictReader(io.StringIO(text.strip())))
    if not rows:
        raise ConnectorError("CSV is empty")
    cols = {k.lower().strip(): k for k in rows[0].keys() if k}
    email_col = next((cols[k] for k in ("email", "work_email", "employee", "employee_email", "name") if k in cols), None)
    done_col = next((cols[k] for k in ("training_completed", "completed", "security_training", "status", "completion_date") if k in cols), None)
    if not email_col or not done_col:
        raise ConnectorError(f"CSV needs an employee column (email) and a completion column (training_completed); got {list(rows[0].keys())}")
    incomplete = [r[email_col] for r in rows if not _TRUE.match((r.get(done_col) or "").strip())]
    payload = {"employees": len(rows), "security_training_completed": len(rows) - len(incomplete),
               "incomplete_sample": incomplete[:25], "columns": [email_col, done_col]}
    return [{"control_id": "HR-001", "payload": payload, "errors": {}}]


COLLECTORS: Dict[str, Callable[[], List[dict]]] = {"aws": collect_aws, "okta": collect_okta, "github": collect_github}


def test_connection(name: str) -> dict:
    """Cheap credential check without collecting evidence."""
    if name == "aws":
        if _aws_configured():
            raise ConnectorError("AWS not configured: " + ", ".join(_aws_configured()))
        import boto3
        ident = boto3.session.Session(region_name=config.AWS_REGION).client("sts").get_caller_identity()
        return {"ok": True, "account": ident["Account"], "arn": ident["Arn"]}
    if name == "okta":
        if _missing("OKTA_ORG_URL", "OKTA_API_TOKEN"):
            raise ConnectorError("Okta not configured")
        with _client(headers={"Authorization": f"SSWS {config.OKTA_API_TOKEN}", "Accept": "application/json"}) as c:
            r = c.get(f"{config.OKTA_ORG_URL}/api/v1/users", params={"limit": 1})
            return {"ok": r.status_code == 200, "status_code": r.status_code}
    if name == "github":
        if not config.GITHUB_TOKEN:
            raise ConnectorError("GitHub not configured")
        with _client(headers={"Authorization": f"Bearer {config.GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}) as c:
            r = c.get(f"{config.GITHUB_API_URL}/user")
            return {"ok": r.status_code == 200, "status_code": r.status_code,
                    "login": r.json().get("login") if r.status_code == 200 else None}
    if name == "hr":
        return {"ok": True, "note": "HR evidence is uploaded as CSV"}
    raise ConnectorError(f"Unknown connector {name}", 404)


def quality_score(payload: dict, errors: dict, live: bool = True) -> int:
    """Completeness (non-null fields), minus penalties for collection errors."""
    fields = [v for k, v in payload.items() if not k.endswith("_sample")]
    completeness = sum(v is not None for v in fields) / max(1, len(fields))
    score = (95 if live else 75) * completeness - 10 * min(3, len(errors))
    return max(0, min(100, round(score)))
