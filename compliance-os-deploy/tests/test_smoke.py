"""End-to-end API smoke test on a throwaway SQLite DB. Run: pytest -q"""
import os
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/test.db"
os.environ.setdefault("SEARCH_PROVIDER", "none")
os.environ.setdefault("LLM_PROVIDER", "none")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def c():
    with TestClient(app) as client:
        yield client


def test_health_and_state(c):
    assert c.get("/health").json()["database"] is True
    state = c.get("/v1/state").json()
    assert len(state["controls"]) == 8 and len(state["breachMatrix"]) == 50


def test_unconfigured_connector_is_explicit(c):
    r = c.post("/v1/connectors/okta/run")
    assert r.status_code == 400 and "not configured" in r.json()["detail"]


def test_evidence_test_gap_loop(c):
    # failing roster -> gap opens; complete roster -> gap resolves
    c.post("/v1/connectors/hr/upload", json={"csv": "email,training_completed\na@x.com,yes\nb@x.com,no"})
    assert c.post("/v1/tests/TEST-006/run").json()["status"] == "fail"
    assert any(g["test_id"] == "TEST-006" and g["status"] == "open" for g in c.get("/v1/objects/gap").json())
    ev = c.post("/v1/connectors/hr/upload", json={"csv": "email,training_completed\na@x.com,yes\nb@x.com,2026-09-01"}).json()
    assert c.get(f"/v1/evidence/{ev['id']}/verify").json()["hash_matches"] is True
    assert c.post("/v1/tests/TEST-006/run").json()["status"] == "pass"
    assert not any(g["test_id"] == "TEST-006" and g["status"] == "open" for g in c.get("/v1/objects/gap").json())


def test_dsar_chain(c):
    d = c.post("/v1/dsar/start", json={"requester_email": "a@b.co"}).json()["dsar"]["id"]
    for _ in range(10):
        c.post(f"/v1/dsar/{d}/advance")
    chain = c.get(f"/v1/dsar/{d}/chain").json()
    assert chain["workflow"]["status"] == "completed"
    assert all(len(s["evidence"]) == 1 for s in chain["steps"])


def test_breach_matrix_change_via_review(c):
    u = c.post("/v1/reg-radar/updates", json={"state_code": "OR", "individual_days": 30}).json()
    c.post(f"/v1/reg-radar/{u['id']}/analyze")
    item = next(r for r in c.get("/v1/objects/review_item").json() if r["payload"].get("update_id") == u["id"])
    c.post(f"/v1/review-items/{item['id']}/approve")
    assert c.get("/v1/objects/breach_matrix/BM-OR").json()["individual_days"] == 30


def test_incident_triage_deadlines(c):
    i = c.post("/v1/incidents", json={"title": "t", "affected_states": ["CO"], "discovered_at": "2026-01-01"}).json()
    ob = c.post(f"/v1/incidents/{i['id']}/triage").json()["obligations"]["CO"]
    assert ob["individual_notice_deadline"] == "2026-01-31"


def test_legal_ai_fallback_and_plans(c):
    r = c.post("/v1/legal/documents", json={"title": "x", "text": "Firms must notify the regulator within 72 hours. Fines apply."}).json()
    assert r["analysis"]["provider"] == "heuristic" and r["analysis"]["obligations"]
    assert len(c.post(f"/v1/implementation/plans/generate/{r['analysis']['id']}").json()) == 3


def test_questionnaire(c):
    q = c.post("/v1/questionnaires", json={"customer": "X", "questions": "Do you enforce multi-factor authentication?"}).json()
    ans = c.post(f"/v1/questionnaires/{q['id']}/process").json()["answers"][0]
    assert ans["status"] == "draft_ready_auto"


def test_report(c):
    body = c.post("/v1/reports/generate").json()["payload"]
    assert 0 <= body["overall_readiness_score"] <= 100


# ---------------------------------------------------------------- connector parsing against mocked APIs
import httpx  # noqa: E402

from app import config, connectors  # noqa: E402


def _mock_client(handler):
    def factory(**kw):
        headers = kw.pop("headers", {})
        return httpx.Client(transport=httpx.MockTransport(handler), headers=headers, **kw)
    return factory


def test_github_collector(monkeypatch):
    monkeypatch.setattr(config, "GITHUB_TOKEN", "t")
    monkeypatch.setattr(config, "GITHUB_ORG", "acme")

    def handler(req):
        p = req.url.path
        if p == "/orgs/acme/repos":
            return httpx.Response(200, json=[{"full_name": "acme/a", "default_branch": "main"},
                                             {"full_name": "acme/b", "default_branch": "main"},
                                             {"full_name": "acme/c", "default_branch": "dev"},
                                             {"full_name": "acme/old", "archived": True}])
        if p == "/repos/acme/a/rules/branches/main":
            return httpx.Response(200, json=[{"type": "pull_request", "parameters": {"required_approving_review_count": 1}}])
        if p.endswith("/rules/branches/main") or p.endswith("/rules/branches/dev"):
            return httpx.Response(200, json=[])
        if p == "/repos/acme/b/branches/main/protection":
            return httpx.Response(404, json={})
        if p == "/repos/acme/c/branches/dev/protection":
            return httpx.Response(200, json={"required_pull_request_reviews": {"required_approving_review_count": 2}})
        return httpx.Response(500)

    monkeypatch.setattr(connectors, "_client", _mock_client(handler))
    payload = connectors.collect_github()[0]["payload"]
    assert payload["repositories_checked"] == 3 and payload["required_reviews_enabled"] == 2
    assert payload["unprotected_repos"] == ["acme/b"]


def test_okta_collector(monkeypatch):
    monkeypatch.setattr(config, "OKTA_ORG_URL", "https://acme.okta.com")
    monkeypatch.setattr(config, "OKTA_API_TOKEN", "t")

    def handler(req):
        p = req.url.path
        if p == "/api/v1/users" and "after" not in str(req.url):
            return httpx.Response(200, json=[{"id": "u1", "profile": {"login": "a@x"}}],
                                  headers={"link": '<https://acme.okta.com/api/v1/users?after=u1>; rel="next"'})
        if p == "/api/v1/users":
            return httpx.Response(200, json=[{"id": "u2", "profile": {"login": "b@x"}}])
        if p == "/api/v1/users/u1/factors":
            return httpx.Response(200, json=[{"status": "ACTIVE"}])
        if p == "/api/v1/users/u2/factors":
            return httpx.Response(200, json=[{"status": "PENDING_ACTIVATION"}])
        return httpx.Response(500)

    monkeypatch.setattr(connectors, "_client", _mock_client(handler))
    payload = connectors.collect_okta()[0]["payload"]
    assert payload["active_users"] == 2 and payload["users_without_mfa"] == 1
    assert payload["users_without_mfa_sample"] == ["b@x"]
