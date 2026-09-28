import asyncio
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Optional

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from . import config, connectors, intel
from .db import (Base, Event, Obj, SessionLocal, add_obj, engine, ensure_sqlite_directory, get_db,
                 get_default_tenant, get_obj, iso, list_objs, new_id, parse_json, patch_payload, payload_of,
                 query_objs, record_event, serialize, serialize_event, sha256_json, update_obj, utcnow)
from .seed import GDPR_STEPS, seed_if_empty

log = logging.getLogger("compos")
STATES = {"AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming"}


# ====================================================================== lifecycle
async def _scheduler():
    interval = config.RADAR_SYNC_INTERVAL_HOURS * 3600
    while True:
        await asyncio.sleep(interval)
        db = SessionLocal()
        try:
            await global_radar_sync_core(db, 3)
            await market_sync_core(db, 5)
        except Exception:
            log.exception("scheduled intelligence sync failed")
        finally:
            db.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if config.AUTH_ENABLED and not config.API_KEY:
        raise RuntimeError("AUTH_ENABLED=true requires API_KEY to be set")
    ensure_sqlite_directory()
    Base.metadata.create_all(bind=engine)
    seed_if_empty()
    task = asyncio.create_task(_scheduler()) if config.RADAR_SYNC_INTERVAL_HOURS > 0 else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="CompOS", version="3.1.0", lifespan=lifespan,
              description="Integrated compliance platform: controls, live evidence connectors, testing, privacy, "
                          "regulatory radar (Federal Register + feeds + search), Legal AI and market intelligence.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def neuralink_middleware(request: Request, call_next):
    path = request.url.path
    if config.AUTH_ENABLED and path.startswith("/v1") and request.method != "OPTIONS":
        if request.headers.get("x-api-key") != config.API_KEY:
            return JSONResponse({"detail": "Missing or invalid X-API-Key"}, status_code=401)
    correlation_id = request.headers.get("x-correlation-id") or new_id("CORR")
    request.state.correlation_id = correlation_id
    start = time.time()
    response = await call_next(request)
    # Telemetry for state-changing calls and failures only, so UI polling does not flood the event stream.
    if path.startswith("/v1") and (request.method != "GET" or response.status_code >= 400):
        record_event("api", "request.completed", "success" if response.status_code < 400 else "error", "api.request",
                     correlation_id=correlation_id, metadata={"method": request.method, "path": path,
                                                              "status_code": response.status_code,
                                                              "duration_ms": round((time.time() - start) * 1000, 1)})
    response.headers["X-Correlation-ID"] = correlation_id
    return response


def corr(request: Optional[Request]) -> Optional[str]:
    return getattr(getattr(request, "state", None), "correlation_id", None)


def emit(request, _flow, _step, _status="success", tenant_id=None, **metadata):
    record_event(_flow, _step, _status, f"{_flow}.{_step}", tenant_id=tenant_id, correlation_id=corr(request), metadata=metadata)


# ====================================================================== core / UI
@app.get("/", response_class=HTMLResponse)
def index():
    path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>CompOS API</h1><p><a href='/docs'>Open API docs</a></p>"


@app.get("/health")
def health():
    try:
        db = SessionLocal()
        db.query(Obj.id).limit(1).all()
        db.close()
        db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "database": db_ok, "service": "compliance-os", "timestamp": iso(utcnow())}


@app.get("/v1/status")
def platform_status():
    return {
        "version": app.version, "auth_enabled": config.AUTH_ENABLED,
        "database": config.DATABASE_URL.split("://")[0],
        "search_provider": config.SEARCH_PROVIDER, "search_live": intel.search_live(),
        "llm_provider": intel.llm_name(), "llm_live": intel.llm_live(),
        "federal_register": "enabled (keyless)", "scheduler_hours": config.RADAR_SYNC_INTERVAL_HOURS,
        "connectors": connectors.connector_status(),
    }


STATE_COLLECTIONS = {
    "frameworks": ("framework", None), "controls": ("control", None), "mappings": ("control_mapping", None),
    "evidence": ("evidence", 300), "tests": ("test", None), "testRuns": ("test_run", 200), "gaps": ("gap", 300),
    "remediation": ("remediation_task", 300), "policies": ("policy", None), "answerLibrary": ("answer_library", None),
    "questionnaires": ("questionnaire", None), "answers": ("questionnaire_answer", 500), "dsars": ("dsar", 200),
    "auditEngagements": ("audit_engagement", None), "trustDocs": ("trust_document", None), "ndas": ("nda_request", None),
    "vendors": ("vendor", None), "vendorAssessments": ("vendor_assessment", 200), "incidents": ("incident", 200),
    "breachMatrix": ("breach_matrix", None), "regulatorySources": ("global_rule_source", None),
    "regulatoryUpdates": ("regulatory_update", 200), "matrixProposals": ("matrix_change_proposal", 200),
    "reviewItems": ("review_item", 300), "reports": ("report", 50), "entitlements": ("entitlement", None),
    "globalRuleDocs": ("global_rule_document", 300), "ruleChanges": ("rule_change", 300), "legalDocs": ("legal_document", 100),
    "legalAnalyses": ("legal_analysis", 100), "marketSignals": ("market_signal", 300), "implPlans": ("implementation_plan", 200),
}


@app.get("/v1/state")
def full_state(db: Session = Depends(get_db)):
    """Everything the single-page UI renders, in one round trip."""
    out = {key: list_objs(db, t, limit, exclude=("text",)) for key, (t, limit) in STATE_COLLECTIONS.items()}
    out["breachMatrix"].sort(key=lambda r: r.get("state_code") or "")
    out["regulatorySources"].sort(key=lambda r: r.get("id"))
    out["controls"].sort(key=lambda r: r.get("id"))
    out["tests"].sort(key=lambda r: r.get("id"))
    out["events"] = [serialize_event(e) for e in db.query(Event).order_by(Event.created_at.desc()).limit(100).all()]
    out["metrics"] = brain_metrics(db)
    out["anomalies"] = brain_anomalies(db)
    out["status"] = platform_status()
    return out


# ---------------------------------------------------------------------- generic objects
@app.get("/v1/objects/{obj_type}")
def api_list_objects(obj_type: str, limit: int = Query(default=500, le=5000), db: Session = Depends(get_db)):
    return list_objs(db, obj_type, limit)


@app.post("/v1/objects/{obj_type}")
def api_create_object(obj_type: str, request: Request, payload: dict = Body(default={}), db: Session = Depends(get_db)):
    if payload.get("id") and db.get(Obj, payload["id"]):
        raise HTTPException(409, f"{payload['id']} already exists")
    tenant_id = get_default_tenant(db)
    data = {k: v for k, v in payload.items() if k not in ("id", "title", "status", "type", "tenant_id", "created_at", "updated_at")}
    item = add_obj(db, obj_type, payload.get("title") or payload.get("name") or obj_type, payload.get("status", "active"),
                   data, tenant_id, payload.get("id"))
    db.commit()
    db.refresh(item)
    emit(request, obj_type, f"{obj_type}.created", tenant_id=tenant_id, id=item.id)
    return serialize(item)


@app.get("/v1/objects/{obj_type}/{obj_id}")
def api_get_object(obj_type: str, obj_id: str, db: Session = Depends(get_db)):
    return serialize(get_obj(db, obj_type, obj_id))


@app.patch("/v1/objects/{obj_type}/{obj_id}")
def api_patch_object(obj_type: str, obj_id: str, payload: dict = Body(...), db: Session = Depends(get_db)):
    item = get_obj(db, obj_type, obj_id)
    data = payload_of(item)
    data.update({k: v for k, v in payload.items() if k not in ("id", "title", "status", "type", "tenant_id", "created_at", "updated_at")})
    return update_obj(db, item, payload=data, status=payload.get("status"), title=payload.get("title"))


@app.delete("/v1/objects/{obj_type}/{obj_id}")
def api_delete_object(obj_type: str, obj_id: str, request: Request, db: Session = Depends(get_db)):
    item = get_obj(db, obj_type, obj_id)
    if obj_type == "evidence":
        raise HTTPException(400, "Evidence is immutable; reject it instead of deleting")
    db.delete(item)
    db.commit()
    emit(request, obj_type, f"{obj_type}.deleted", "warning", id=obj_id)
    return {"deleted": obj_id}


# ====================================================================== controls, evidence, connectors
@app.post("/v1/controls")
def control_create(request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    code = (payload.get("code") or "").strip().upper()
    title = (payload.get("title") or "").strip()
    if not code or not title:
        raise HTTPException(400, "code and title are required")
    if db.get(Obj, code):
        raise HTTPException(409, f"{code} already exists")
    tenant_id = get_default_tenant(db)
    item = add_obj(db, "control", title, "active", {"code": code, "category": payload.get("category") or "General",
                                                     "risk_level": payload.get("risk_level", "medium")}, tenant_id, code)
    for m in payload.get("mappings", []):
        add_obj(db, "control_mapping", f"{code}->{m.get('framework_id')}:{m.get('requirement')}", "active",
                {"control_id": code, "framework_id": m.get("framework_id"), "requirement": m.get("requirement")}, tenant_id)
    db.commit()
    emit(request, "controls", "control.created", tenant_id=tenant_id, code=code)
    return serialize(item)


def store_evidence(db: Session, tenant_id: str, control_id: Optional[str], connector: str, payload: dict,
                   quality: int, status: str = "collected", extra: Optional[dict] = None) -> Obj:
    collected_at = iso(utcnow())
    record = {"control_id": control_id, "connector": connector, "payload": payload, "collected_at": collected_at}
    return add_obj(db, "evidence", f"{connector} evidence", status,
                   {**record, "hash": sha256_json(record), "quality_score": quality, **(extra or {})}, tenant_id, new_id("EV"))


@app.get("/v1/connectors")
def connectors_list():
    return connectors.connector_status()


@app.post("/v1/connectors/{name}/test")
async def connector_test(name: str):
    try:
        return await run_in_threadpool(connectors.test_connection, name)
    except connectors.ConnectorError as e:
        raise HTTPException(e.status_code, str(e))
    except Exception as e:
        raise HTTPException(502, f"{name} connection failed: {e}")


async def connector_run_core(db: Session, name: str, request: Optional[Request] = None) -> dict:
    if name == "hr":
        raise HTTPException(400, "HR evidence is uploaded: POST /v1/connectors/hr/upload with {\"csv\": \"...\"}")
    collector = connectors.COLLECTORS.get(name)
    if not collector:
        raise HTTPException(404, f"Unknown connector {name}")
    tenant_id = get_default_tenant(db)
    try:
        records = await run_in_threadpool(collector)
    except connectors.ConnectorError as e:
        emit(request, "connectors", "connector.failed", "error", tenant_id, connector=name, error=str(e))
        raise HTTPException(e.status_code, str(e))
    except Exception as e:
        emit(request, "connectors", "connector.failed", "error", tenant_id, connector=name, error=str(e))
        raise HTTPException(502, f"{name} collection failed: {e}")
    created = []
    for rec in records:
        ev = store_evidence(db, tenant_id, rec["control_id"], name, rec["payload"],
                            connectors.quality_score(rec["payload"], rec["errors"]),
                            extra={"source": "live", "collection_errors": rec["errors"]})
        created.append(ev)
    db.commit()
    emit(request, "connectors", "connector.run", tenant_id=tenant_id, connector=name, evidence=len(created))
    return {"connector": name, "evidence": [serialize(x) for x in created]}


@app.post("/v1/connectors/{name}/run")
async def connector_run(name: str, request: Request, db: Session = Depends(get_db)):
    return await connector_run_core(db, name, request)


@app.post("/v1/connectors/hr/upload")
def connector_hr_upload(request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    try:
        records = connectors.parse_hr_csv(payload.get("csv", ""))
    except connectors.ConnectorError as e:
        raise HTTPException(400, str(e))
    tenant_id = get_default_tenant(db)
    rec = records[0]
    ev = store_evidence(db, tenant_id, rec["control_id"], "hr", rec["payload"],
                        connectors.quality_score(rec["payload"], {}, live=False),
                        extra={"source": "upload", "filename": payload.get("filename")})
    db.commit()
    emit(request, "connectors", "hr.uploaded", tenant_id=tenant_id, employees=rec["payload"]["employees"])
    return serialize(ev)


@app.post("/v1/evidence")
def evidence_manual(request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    """Attest evidence that no connector can collect (e.g. backup restore test results)."""
    control_id = payload.get("control_id")
    get_obj(db, "control", control_id)
    data = payload.get("payload")
    if not isinstance(data, dict) or not data:
        raise HTTPException(400, "payload must be a non-empty JSON object, e.g. {\"backup_restore_test\": \"completed\"}")
    tenant_id = get_default_tenant(db)
    ev = store_evidence(db, tenant_id, control_id, "manual", data, connectors.quality_score(data, {}, live=False),
                        extra={"source": "manual", "attested_by": payload.get("attested_by"), "note": payload.get("note")})
    db.commit()
    emit(request, "evidence", "evidence.attested", tenant_id=tenant_id, control_id=control_id)
    return serialize(ev)


@app.post("/v1/evidence/{evidence_id}/approve")
def evidence_approve(evidence_id: str, request: Request, db: Session = Depends(get_db)):
    item = get_obj(db, "evidence", evidence_id)
    result = patch_payload(db, item, {"reviewed_at": iso(utcnow())}, status="approved")
    emit(request, "evidence", "evidence.approved", tenant_id=item.tenant_id, id=evidence_id)
    return result


@app.post("/v1/evidence/{evidence_id}/reject")
def evidence_reject(evidence_id: str, request: Request, db: Session = Depends(get_db)):
    item = get_obj(db, "evidence", evidence_id)
    result = patch_payload(db, item, {"reviewed_at": iso(utcnow())}, status="rejected")
    emit(request, "evidence", "evidence.rejected", "warning", item.tenant_id, id=evidence_id)
    return result


@app.get("/v1/evidence/{evidence_id}/verify")
def evidence_verify(evidence_id: str, db: Session = Depends(get_db)):
    ev = serialize(get_obj(db, "evidence", evidence_id))
    if not ev.get("collected_at"):
        return {"id": evidence_id, "verifiable": False, "reason": "legacy record without collected_at"}
    record = {k: ev.get(k) for k in ("control_id", "connector", "payload", "collected_at")}
    return {"id": evidence_id, "verifiable": True, "hash_matches": sha256_json(record) == ev.get("hash")}


# ====================================================================== testing, gaps
def _evaluate(op: str, actual, expected, payload: dict) -> bool:
    if op == "eq":
        return actual == expected
    if op == "neq":
        return actual != expected
    if op == "exists":
        return actual is not None
    if op == "eq_field":
        return actual is not None and actual == payload.get(expected)
    try:
        if op == "gte":
            return float(actual) >= float(expected)
        if op == "lte":
            return float(actual) <= float(expected)
    except (TypeError, ValueError):
        return False
    raise HTTPException(400, f"Unsupported operator {op}")


def run_test_core(db: Session, test_id: str, request: Optional[Request] = None) -> dict:
    test = get_obj(db, "test", test_id)
    t = payload_of(test)
    field, op = t.get("expected_field"), t.get("operator", "eq")
    candidates = [e for e in query_objs(db, "evidence") if e.status != "rejected"]
    evidence = next((e for e in candidates if payload_of(e).get("control_id") == t.get("control_id")
                     and field in (payload_of(e).get("payload") or {})), None)
    if not evidence:
        status, result = "missing_evidence", {"reason": f"No non-rejected evidence for {t.get('control_id')} containing '{field}'"}
    else:
        ev_payload = payload_of(evidence).get("payload") or {}
        actual = ev_payload.get(field)
        expected = ev_payload.get(t.get("expected_value")) if op == "eq_field" else t.get("expected_value")
        status = "pass" if _evaluate(op, actual, t.get("expected_value"), ev_payload) else "fail"
        result = {"actual": actual, "expected": expected, "operator": op, "evidence_id": evidence.id}
    run = add_obj(db, "test_run", f"Run {test_id}", status, {"test_id": test_id, "control_id": t.get("control_id"), "result": result},
                  test.tenant_id, new_id("RUN"))
    open_gaps = [g for g in query_objs(db, "gap") if g.status == "open" and payload_of(g).get("test_id") == test_id]
    if status == "pass":
        for g in open_gaps:  # closed loop: a passing test resolves its gap and remediation
            patch_payload(db, g, {"resolved_by_run": run.id, "resolved_at": iso(utcnow())}, status="resolved", commit=False)
            for r in query_objs(db, "remediation_task"):
                if payload_of(r).get("gap_id") == g.id and r.status != "done":
                    update_obj(db, r, status="done", commit=False)
    elif not open_gaps:
        gap = add_obj(db, "gap", f"Test {status}: {t.get('name')}", "open",
                      {"control_id": t.get("control_id"), "test_id": test_id, "severity": "high" if status == "fail" else "medium",
                       "first_run": run.id}, test.tenant_id, new_id("GAP"))
        add_obj(db, "remediation_task", f"Remediate: {t.get('name')}", "open", {"gap_id": gap.id, "owner": "unassigned"},
                test.tenant_id, new_id("REM"))
    db.commit()
    emit(request, "testing", "test.run", "success" if status == "pass" else "warning", test.tenant_id, test_id=test_id, result=status)
    return serialize(run)


@app.post("/v1/tests/{test_id}/run")
def test_run(test_id: str, request: Request, db: Session = Depends(get_db)):
    return run_test_core(db, test_id, request)


@app.post("/v1/tests/run-all")
def test_run_all(request: Request, db: Session = Depends(get_db)):
    return [run_test_core(db, t.id, request) for t in query_objs(db, "test")]


@app.post("/v1/tests")
def test_create(request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    get_obj(db, "control", payload.get("control_id"))
    if not payload.get("name") or not payload.get("expected_field"):
        raise HTTPException(400, "name and expected_field are required")
    item = add_obj(db, "test", payload["name"], "active", {k: payload.get(k) for k in ("control_id", "name", "expected_field", "operator", "expected_value")},
                   get_default_tenant(db), new_id("TEST"))
    db.commit()
    emit(request, "testing", "test.created", id=item.id)
    return serialize(item)


@app.post("/v1/remediation/{task_id}")
def remediation_update(task_id: str, payload: dict = Body(...), db: Session = Depends(get_db)):
    item = get_obj(db, "remediation_task", task_id)
    changes = {k: payload[k] for k in ("owner", "due_date", "note") if k in payload}
    return patch_payload(db, item, changes, status=payload.get("status"))


# ====================================================================== policy AI
@app.post("/v1/policies/generate")
async def policy_generate(request: Request, payload: dict = Body(default={}), db: Session = Depends(get_db)):
    tenant_id = get_default_tenant(db)
    policy_type = payload.get("policy_type") or "information security"
    company = payload.get("company") or "The Company"
    frameworks = payload.get("frameworks") or [f.id for f in query_objs(db, "framework")]
    controls = sorted(list_objs(db, "control"), key=lambda c: c["id"])
    try:
        draft = await run_in_threadpool(intel.draft_policy, policy_type, company, frameworks, controls)
    except intel.IntelError as e:
        raise HTTPException(502, str(e))
    policy = add_obj(db, "policy", f"{company} {policy_type.title()} Policy", "draft",
                     {"policy_type": policy_type, "version": "1.0", "frameworks": frameworks, "provider": draft["provider"]}, tenant_id, new_id("POL"))
    add_obj(db, "policy_version", "v1.0", "draft", {"policy_id": policy.id, "version": "1.0", "content": draft["content"],
                                                     "provider": draft["provider"]}, tenant_id, new_id("PV"))
    db.commit()
    emit(request, "policy_ai", "policy.generated", tenant_id=tenant_id, policy_id=policy.id, provider=draft["provider"])
    return serialize(policy)


@app.get("/v1/policies/{policy_id}/versions")
def policy_versions(policy_id: str, db: Session = Depends(get_db)):
    get_obj(db, "policy", policy_id)
    return [v for v in list_objs(db, "policy_version") if v.get("policy_id") == policy_id]


@app.post("/v1/policies/{policy_id}/versions")
def policy_new_version(policy_id: str, request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    policy = get_obj(db, "policy", policy_id)
    p = payload_of(policy)
    major, _, minor = str(p.get("version", "1.0")).partition(".")
    version = f"{major}.{int(minor or 0) + 1}"
    add_obj(db, "policy_version", f"v{version}", "draft", {"policy_id": policy_id, "version": version,
                                                           "content": payload.get("content", ""), "provider": "manual"}, policy.tenant_id, new_id("PV"))
    result = patch_payload(db, policy, {"version": version}, status="draft")
    emit(request, "policy_ai", "policy.versioned", policy_id=policy_id, version=version)
    return result


@app.post("/v1/policies/{policy_id}/approve")
def policy_approve(policy_id: str, request: Request, db: Session = Depends(get_db)):
    policy = get_obj(db, "policy", policy_id)
    version = payload_of(policy).get("version")
    for v in query_objs(db, "policy_version"):
        vp = payload_of(v)
        if vp.get("policy_id") == policy_id and vp.get("version") == version:
            update_obj(db, v, status="approved", commit=False)
    result = patch_payload(db, policy, {"approved_at": iso(utcnow())}, status="approved")
    emit(request, "policy_ai", "policy.approved", policy_id=policy_id)
    return result


# ====================================================================== questionnaire AI
def _tokens(text: str) -> set:
    return set(re.findall(r"[a-z]{4,}", (text or "").lower())) - {"your", "have", "does", "with", "that", "this", "from", "what"}


def retrieve_answer(question: str, library: list):
    q = _tokens(question)
    best, best_score = None, 0.0
    for item in library:
        a = _tokens(item.get("question", ""))
        overlap = len(q & a) / max(1, len(q | a))
        score = min(1.0, overlap * float(item.get("weight", 1.0) or 1.0))
        if score > best_score:
            best, best_score = item, score
    return best, round(best_score, 3)


@app.post("/v1/questionnaires")
def questionnaire_create(request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    questions = payload.get("questions")
    if isinstance(questions, str):
        questions = [q.strip() for q in questions.splitlines() if q.strip()]
    if not questions:
        raise HTTPException(400, "questions required (list or newline-separated text)")
    item = add_obj(db, "questionnaire", payload.get("customer", "Customer"), "imported",
                   {"customer": payload.get("customer", "Customer"), "questions": questions, "total_questions": len(questions),
                    "auto_answered": 0, "needs_review": 0}, get_default_tenant(db), new_id("QST"))
    db.commit()
    emit(request, "questionnaire_ai", "questionnaire.imported", id=item.id, questions=len(questions))
    return serialize(item)


@app.post("/v1/questionnaires/{questionnaire_id}/process")
async def questionnaire_process(questionnaire_id: str, request: Request, db: Session = Depends(get_db)):
    q = get_obj(db, "questionnaire", questionnaire_id)
    qp = payload_of(q)
    questions = qp.get("questions") or []
    if not questions:
        raise HTTPException(400, "Questionnaire has no questions")
    for old in query_objs(db, "questionnaire_answer"):
        if payload_of(old).get("questionnaire_id") == questionnaire_id:
            db.delete(old)
    library = [a for a in list_objs(db, "answer_library") if a.get("status") == "approved"]
    auto = review = 0
    created = []
    for question in questions:
        best, confidence = retrieve_answer(question, library)
        answer, sources, provider = (best or {}).get("answer"), ([{"answer_id": best["id"], "source_type": best.get("source_type")}] if best else []), "retrieval"
        if confidence < 0.45:
            try:
                drafted = await run_in_threadpool(intel.draft_answer, question, library)
            except intel.IntelError as e:
                drafted = None
                emit(request, "questionnaire_ai", "llm.failed", "error", error=str(e))
            if drafted and drafted != "INSUFFICIENT_SOURCE":
                answer, provider, confidence = drafted, intel.llm_name(), max(confidence, 0.45)
        status = "draft_ready_auto" if confidence >= 0.75 else "draft_ready_review" if confidence >= 0.45 else "needs_source"
        auto += status == "draft_ready_auto"
        review += status != "draft_ready_auto"
        created.append(add_obj(db, "questionnaire_answer", question, status, {
            "questionnaire_id": questionnaire_id, "question": question, "answer": answer or "Requires human review — no approved source.",
            "confidence": confidence, "sources": sources, "provider": provider}, q.tenant_id, new_id("ANS")))
    qp.update({"auto_answered": auto, "needs_review": review, "total_questions": len(questions)})
    update_obj(db, q, payload=qp, status="in_review", commit=False)
    db.commit()
    emit(request, "questionnaire_ai", "questionnaire.processed", tenant_id=q.tenant_id, questionnaire_id=questionnaire_id, auto=auto, review=review)
    return {"questionnaire": serialize(q), "answers": [serialize(x) for x in created]}


@app.post("/v1/questionnaires/feedback")
def questionnaire_feedback(request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    answer = get_obj(db, "questionnaire_answer", payload.get("answer_id"))
    feedback_type = payload.get("feedback_type", "rejected")
    approved = feedback_type == "approved"
    ap = payload_of(answer)
    if payload.get("edited_answer"):
        ap["answer"] = payload["edited_answer"]
    update_obj(db, answer, payload=ap, status="approved" if approved else "rejected", commit=False)
    fb = add_obj(db, "questionnaire_feedback", ap.get("question", ""), feedback_type, {**payload, "question": ap.get("question")}, answer.tenant_id)
    sources = ap.get("sources") or []
    if sources and sources[0].get("answer_id"):
        lib = db.get(Obj, sources[0]["answer_id"])
        if lib:
            lp = payload_of(lib)
            lp["weight"] = round(max(0.1, min(2.0, float(lp.get("weight", 1.0)) + (0.02 if approved else -0.05))), 3)
            update_obj(db, lib, payload=lp, commit=False)
    elif approved and payload.get("add_to_library", True):
        # Approved LLM/edited answers grow the library.
        add_obj(db, "answer_library", ap.get("question"), "approved", {"question": ap.get("question"), "answer": ap.get("answer"),
                                                                       "source_type": "approved_answer", "weight": 1.0}, answer.tenant_id, new_id("AL"))
    db.commit()
    emit(request, "questionnaire_ai", f"feedback.{feedback_type}", "success" if approved else "warning", answer.tenant_id, answer_id=answer.id)
    return serialize(fb)


@app.post("/v1/answer-library")
def answer_library_add(request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    if not payload.get("question") or not payload.get("answer"):
        raise HTTPException(400, "question and answer required")
    item = add_obj(db, "answer_library", payload["question"], "approved", {"question": payload["question"], "answer": payload["answer"],
                   "source_type": payload.get("source_type", "manual"), "weight": 1.0}, get_default_tenant(db), new_id("AL"))
    db.commit()
    emit(request, "questionnaire_ai", "library.added", id=item.id)
    return serialize(item)


# ====================================================================== privacy / DSAR
@app.post("/v1/dsar/start")
def dsar_start(request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    email = (payload.get("requester_email") or "").strip()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise HTTPException(400, "valid requester_email required")
    tenant_id = get_default_tenant(db)
    rtype = payload.get("request_type", "access")
    dsar = add_obj(db, "dsar", f"{rtype.title()} request", "received", {"request_type": rtype, "requester_email": email,
                   "due_date": (utcnow() + timedelta(days=30)).date().isoformat()}, tenant_id, new_id("DSAR"))
    wf = add_obj(db, "workflow_satisfaction", "gdpr_dsar_v1", "in_progress", {"dsar_id": dsar.id, "workflow": "gdpr_dsar_v1",
                 "required_steps": GDPR_STEPS, "completed_steps": [], "sla_met": True}, tenant_id, new_id("WS"))
    for idx, step in enumerate(GDPR_STEPS):
        add_obj(db, "workflow_step", step, "pending", {"satisfaction_id": wf.id, "name": step, "order": idx, "output": {}}, tenant_id, new_id("STEP"))
    db.commit()
    emit(request, "privacy", "dsar.started", tenant_id=tenant_id, dsar_id=dsar.id)
    return {"dsar": serialize(dsar), "workflow": serialize(wf)}


def _workflow_for(db: Session, dsar_id: str) -> Obj:
    wf = next((w for w in query_objs(db, "workflow_satisfaction") if payload_of(w).get("dsar_id") == dsar_id), None)
    if not wf:
        raise HTTPException(404, "Workflow not found")
    return wf


def _steps_for(db: Session, wf_id: str):
    steps = [s for s in query_objs(db, "workflow_step") if payload_of(s).get("satisfaction_id") == wf_id]
    return sorted(steps, key=lambda s: GDPR_STEPS.index(s.title) if s.title in GDPR_STEPS else 99)


@app.post("/v1/dsar/{dsar_id}/advance")
def dsar_advance(dsar_id: str, request: Request, payload: dict = Body(default={}), db: Session = Depends(get_db)):
    dsar = get_obj(db, "dsar", dsar_id)
    wf = _workflow_for(db, dsar_id)
    steps = _steps_for(db, wf.id)
    step = next((s for s in steps if s.status == "pending"), None)
    if not step:
        return serialize(wf)
    output = {"completed_at": iso(utcnow()), "note": payload.get("note"), "actor": payload.get("actor")}
    patch_payload(db, step, {"output": output}, status="completed", commit=False)
    ev_payload = {"dsar_id": dsar_id, "workflow_step": step.title, **{k: v for k, v in output.items() if v}}
    ev = store_evidence(db, dsar.tenant_id, None, "workflow", ev_payload, 88, "approved", {"source": "workflow"})
    add_obj(db, "workflow_step_evidence", f"{step.title} evidence", "linked", {"step_id": step.id, "evidence_id": ev.id}, dsar.tenant_id)
    completed = [s.title for s in steps if s.status == "completed"]
    due = payload_of(dsar).get("due_date")
    wp = payload_of(wf)
    wp.update({"completed_steps": completed, "sla_met": not due or date.today().isoformat() <= due})
    done = len(completed) == len(GDPR_STEPS)
    update_obj(db, wf, payload=wp, status="completed" if done else "in_progress", commit=False)
    update_obj(db, dsar, status="completed" if done else "in_progress", commit=False)
    db.commit()
    emit(request, "privacy", "dsar.step_completed", tenant_id=dsar.tenant_id, dsar_id=dsar_id, step=step.title)
    return serialize(wf)


@app.get("/v1/dsar/{dsar_id}/chain")
def dsar_chain(dsar_id: str, db: Session = Depends(get_db)):
    wf = _workflow_for(db, dsar_id)
    links = [payload_of(l) for l in query_objs(db, "workflow_step_evidence")]
    out = []
    for s in _steps_for(db, wf.id):
        ev_ids = [l["evidence_id"] for l in links if l.get("step_id") == s.id]
        out.append({"step": serialize(s), "evidence": [serialize(e) for e in (db.get(Obj, i) for i in ev_ids) if e]})
    return {"workflow": serialize(wf), "steps": out}


@app.get("/v1/auditor/dsar/{dsar_id}/evidence-chain")
def auditor_dsar_chain(dsar_id: str, db: Session = Depends(get_db)):
    return dsar_chain(dsar_id, db)


# ====================================================================== incidents, breach matrix, state radar
@app.post("/v1/incidents")
def incident_create(request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    states = [s.strip().upper() for s in (payload.get("affected_states") or []) if s.strip()]
    bad = [s for s in states if s not in STATES]
    if bad:
        raise HTTPException(400, f"Unknown state codes: {bad}")
    item = add_obj(db, "incident", payload.get("title") or "Untitled incident", "open", {
        "severity": payload.get("severity", "medium"), "affected_states": states, "records_affected": payload.get("records_affected"),
        "discovered_at": payload.get("discovered_at") or iso(utcnow()), "obligations": {}}, get_default_tenant(db), new_id("INC"))
    db.commit()
    emit(request, "incident_response", "incident.created", id=item.id)
    return serialize(item)


@app.post("/v1/incidents/{incident_id}/triage")
def incident_triage(incident_id: str, request: Request, db: Session = Depends(get_db)):
    incident = get_obj(db, "incident", incident_id)
    p = payload_of(incident)
    matrix = {r.get("state_code"): r for r in list_objs(db, "breach_matrix")}
    try:
        discovered = date.fromisoformat((p.get("discovered_at") or iso(utcnow()))[:10])
    except ValueError:
        raise HTTPException(400, "discovered_at must be an ISO date (YYYY-MM-DD)")
    obligations = {}
    for sc in p.get("affected_states", []):
        row = matrix.get(sc)
        if not row:
            continue
        days = row.get("individual_days")
        obligations[sc] = {**{k: row.get(k) for k in ("state_name", "individual_days", "regulator_notice", "regulator_threshold", "legal_review_required")},
                           "individual_notice_deadline": (discovered + timedelta(days=days)).isoformat() if days else "without unreasonable delay"}
    p["obligations"] = obligations
    result = update_obj(db, incident, payload=p, status="triaged")
    emit(request, "incident_response", "incident.triaged", tenant_id=incident.tenant_id, incident_id=incident_id, states=list(obligations))
    return result


def _detect_state(text: str) -> Optional[str]:
    # Longest names first so "West Virginia" is not detected as "Virginia".
    for code, name in sorted(STATES.items(), key=lambda kv: -len(kv[1])):
        if re.search(rf"\b{name}\b", text or ""):
            return code
    return None


@app.post("/v1/reg-radar/crawl")
async def reg_radar_crawl(request: Request, payload: dict = Body(default={}), db: Session = Depends(get_db)):
    if not intel.search_live() and config.SEARCH_PROVIDER != "mock":
        raise HTTPException(400, "State radar needs an internet search provider (SEARCH_PROVIDER + API key). "
                                 "You can also add updates manually via POST /v1/reg-radar/updates.")
    tenant_id = get_default_tenant(db)
    year = utcnow().year
    states = [s.upper() for s in payload.get("states", [])] or [None]
    known = {u.get("url") for u in list_objs(db, "regulatory_update")}
    created, errors = [], []
    for sc in states:
        query = f"{STATES.get(sc, '')} data breach notification law amendment {year}".strip() if sc else \
            f"state data breach notification law amended signed {year}"
        try:
            results = await intel.internet_search(query, int(payload.get("max_results", 8)))
        except intel.IntelError as e:
            errors.append(str(e))
            continue
        for r in results:
            if not r.get("url") or r["url"] in known:
                continue
            known.add(r["url"])
            code = sc or _detect_state(f"{r.get('title')} {r.get('snippet')}") or "US"
            created.append(add_obj(db, "regulatory_update", r.get("title") or r["url"], "detected", {
                "state_code": code, "url": r["url"], "snippet": r.get("snippet"), "provider": r.get("source"),
                "severity": "high" if code != "US" else "medium", "proposed_change": {}}, tenant_id, new_id("RU")))
    db.commit()
    emit(request, "regulatory_radar", "radar.crawl", "warning" if errors else "success", tenant_id, created=len(created), errors=errors)
    return {"created": [serialize(x) for x in created], "errors": errors}


@app.post("/v1/reg-radar/updates")
def reg_radar_manual(request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    code = (payload.get("state_code") or "").upper()
    if code not in STATES:
        raise HTTPException(400, "state_code must be a US state code")
    change = {k: payload[k] for k in ("individual_days", "regulator_threshold", "regulator_notice") if payload.get(k) not in (None, "")}
    if "individual_days" in change:
        change["individual_days"] = int(change["individual_days"])
    item = add_obj(db, "regulatory_update", payload.get("title") or f"{code} breach law change", "detected", {
        "state_code": code, "url": payload.get("url"), "snippet": payload.get("note"), "provider": "manual",
        "severity": payload.get("severity", "high"), "proposed_change": change}, get_default_tenant(db), new_id("RU"))
    db.commit()
    emit(request, "regulatory_radar", "update.manual", id=item.id)
    return serialize(item)


@app.post("/v1/reg-radar/{update_id}/analyze")
async def reg_radar_analyze(update_id: str, request: Request, db: Session = Depends(get_db)):
    update = get_obj(db, "regulatory_update", update_id)
    p = payload_of(update)
    state_code = p.get("state_code")
    change, summary, note = dict(p.get("proposed_change") or {}), None, None
    if not change and intel.llm_live():
        text = p.get("snippet") or ""
        if p.get("url"):
            try:
                text = await intel.fetch_page_text(p["url"])
            except intel.IntelError as e:
                note = str(e)
        try:
            extracted = await run_in_threadpool(intel.extract_breach_change, text, state_code)
            if extracted:
                change, summary = extracted["proposed_change"], extracted["summary"]
        except intel.IntelError as e:
            note = str(e)
    current = next((r for r in list_objs(db, "breach_matrix") if r.get("state_code") == state_code), None)
    proposal = None
    if current and change:
        proposal = add_obj(db, "matrix_change_proposal", f"{state_code} proposal", "pending_legal_review", {
            "update_id": update_id, "state_code": state_code,
            "current_values": {k: current.get(k) for k in change}, "proposed_values": change}, update.tenant_id, new_id("MCP"))
    add_obj(db, "review_item", f"Review breach matrix change for {state_code}" if proposal else f"Analyst input needed: {update.title}",
            "pending_triage", {"queue_type": "matrix_proposal" if proposal else "regulatory_change",
                               "payload": {"proposal_id": proposal.id if proposal else None, "update_id": update_id, "state_code": state_code,
                                           "proposed_change": change, "summary": summary, "url": p.get("url"), "note": note}},
            update.tenant_id, new_id("REV"))
    patch_payload(db, update, {"analysis_summary": summary, "analysis_note": note}, status="impact_analyzed", commit=False)
    db.commit()
    emit(request, "regulatory_radar", "update.analyzed", tenant_id=update.tenant_id, update_id=update_id, proposal=bool(proposal))
    return serialize(proposal) if proposal else {"status": "needs_analyst_input", "note": note or "No structured change could be extracted"}


# ====================================================================== expert review
@app.post("/v1/review-items/{item_id}/approve")
def review_approve(item_id: str, request: Request, db: Session = Depends(get_db)):
    item = get_obj(db, "review_item", item_id)
    p = payload_of(item)
    inner = p.get("payload") or {}
    if p.get("queue_type") == "matrix_proposal" and inner.get("proposal_id"):
        proposal = db.get(Obj, inner["proposal_id"])
        if proposal:
            update_obj(db, proposal, status="approved", commit=False)
            row = db.get(Obj, f"BM-{inner.get('state_code')}")
            if row:
                history = payload_of(row).get("history", [])
                history.append({"changed_at": iso(utcnow()), "proposal_id": proposal.id, "before": payload_of(proposal).get("current_values")})
                patch_payload(db, row, {**(payload_of(proposal).get("proposed_values") or {}), "history": history}, commit=False)
    if p.get("queue_type") == "rule_change" and inner.get("rule_change_id"):
        rc = db.get(Obj, inner["rule_change_id"])
        if rc:
            update_obj(db, rc, status="accepted", commit=False)
    result = update_obj(db, item, status="approved")
    emit(request, "expert_review", "review.approved", tenant_id=item.tenant_id, item_id=item_id)
    return result


@app.post("/v1/review-items/{item_id}/reject")
def review_reject(item_id: str, request: Request, db: Session = Depends(get_db)):
    item = get_obj(db, "review_item", item_id)
    inner = payload_of(item).get("payload") or {}
    for key in ("proposal_id", "rule_change_id"):
        target = db.get(Obj, inner.get(key)) if inner.get(key) else None
        if target:
            update_obj(db, target, status="rejected", commit=False)
    result = update_obj(db, item, status="rejected")
    emit(request, "expert_review", "review.rejected", "warning", item.tenant_id, item_id=item_id)
    return result


# ====================================================================== vendors, trust center, reports
@app.post("/v1/vendors")
def vendor_create(request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    if not payload.get("name"):
        raise HTTPException(400, "name required")
    item = add_obj(db, "vendor", payload["name"], "new", {k: payload.get(k) for k in ("category", "risk_tier", "data_access", "has_soc2", "website")},
                   get_default_tenant(db), new_id("VEN"))
    db.commit()
    emit(request, "vendor_risk", "vendor.created", id=item.id)
    return serialize(item)


@app.post("/v1/vendors/{vendor_id}/assess")
def vendor_assess(vendor_id: str, request: Request, payload: dict = Body(default={}), db: Session = Depends(get_db)):
    vendor = get_obj(db, "vendor", vendor_id)
    v = {**payload_of(vendor), **payload}
    factors = {"tier": {"high": 50, "medium": 30, "low": 10}.get(v.get("risk_tier"), 30),
               "data_access": {"customer_pii": 25, "phi": 30, "payment": 30, "contact_data": 10}.get(v.get("data_access"), 5),
               "no_soc2": 0 if v.get("has_soc2") else 15, "open_findings": 5 * int(v.get("open_findings") or 0)}
    score = min(100, sum(factors.values()))
    assessment = add_obj(db, "vendor_assessment", "Risk assessment", "completed", {
        "vendor_id": vendor_id, "assessment_type": payload.get("assessment_type", "annual_review"), "risk_score": score,
        "factors": factors, "next_review": (utcnow() + timedelta(days=365 if score < 50 else 180)).date().isoformat()},
        vendor.tenant_id, new_id("VA"))
    patch_payload(db, vendor, {k: payload[k] for k in payload if k in ("risk_tier", "data_access", "has_soc2", "open_findings")},
                  status="assessed", commit=False)
    db.commit()
    emit(request, "vendor_risk", "vendor.assessed", tenant_id=vendor.tenant_id, vendor_id=vendor_id, score=score)
    return serialize(assessment)


@app.post("/v1/trust-documents")
def trust_document_create(request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    if not payload.get("title"):
        raise HTTPException(400, "title required")
    item = add_obj(db, "trust_document", payload["title"], "active", {"access_level": payload.get("access_level", "public"),
                   "url": payload.get("url")}, get_default_tenant(db), new_id("TD"))
    db.commit()
    emit(request, "trust_center", "trust_document.created", id=item.id)
    return serialize(item)


@app.post("/v1/nda-requests")
def nda_request_create(request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    item = add_obj(db, "nda_request", payload.get("company") or "Unknown", "pending", {"company": payload.get("company"),
                   "requester_email": payload.get("requester_email")}, get_default_tenant(db), new_id("NDA"))
    db.commit()
    emit(request, "trust_center", "nda.requested", id=item.id)
    return serialize(item)


@app.post("/v1/nda-requests/{nda_id}/sign")
def nda_sign(nda_id: str, request: Request, db: Session = Depends(get_db)):
    item = get_obj(db, "nda_request", nda_id)
    result = patch_payload(db, item, {"signed_at": iso(utcnow())}, status="signed")
    emit(request, "trust_center", "nda.signed", tenant_id=item.tenant_id, id=nda_id)
    return result


def _latest_runs(db: Session) -> dict:
    latest = {}
    for r in query_objs(db, "test_run"):  # newest first
        tid = payload_of(r).get("test_id")
        latest.setdefault(tid, r)
    return latest


@app.post("/v1/reports/generate")
def report_generate(request: Request, db: Session = Depends(get_db)):
    tenant_id = get_default_tenant(db)
    tests = query_objs(db, "test")
    latest = _latest_runs(db)
    passing = sum(1 for t in tests if latest.get(t.id) is not None and latest[t.id].status == "pass")
    open_gaps = [g for g in query_objs(db, "gap") if g.status == "open"]
    evidence = [payload_of(e).get("quality_score", 0) for e in query_objs(db, "evidence", 300)]
    readiness = round(100 * passing / max(1, len(tests)))
    body = {"overall_readiness_score": readiness, "tests_passing": passing, "tests_total": len(tests),
            "tests_never_run": [t.id for t in tests if t.id not in latest],
            "average_evidence_quality": round(sum(evidence) / max(1, len(evidence))),
            "open_gaps": len(open_gaps), "top_risks": [g.title for g in open_gaps[:5]],
            "pending_reviews": sum(1 for r in query_objs(db, "review_item") if r.status == "pending_triage"),
            "open_incidents": sum(1 for i in query_objs(db, "incident") if i.status != "closed"),
            "new_rule_changes_30d": sum(1 for c in query_objs(db, "rule_change") if c.created_at >= utcnow() - timedelta(days=30))}
    report = add_obj(db, "report", "Executive summary", "generated", {"report_type": "executive_summary", "period": date.today().isoformat(),
                     "payload": body}, tenant_id, new_id("REP"))
    db.commit()
    emit(request, "reporting", "report.generated", tenant_id=tenant_id, id=report.id, readiness=readiness)
    return serialize(report)


# ====================================================================== global radar, legal AI, market intel
async def global_radar_sync_core(db: Session, max_per_source: int = 3, request: Optional[Request] = None) -> dict:
    tenant_id = get_default_tenant(db)
    known = {d.get("url") for d in list_objs(db, "global_rule_document")}
    docs = changes = 0
    errors, skipped = {}, []
    year = utcnow().year
    for source_obj in query_objs(db, "global_rule_source"):
        s = payload_of(source_obj)
        if not s.get("active", True):
            continue
        try:
            if s.get("federal_register_agency"):
                results = [{"title": d.get("title"), "url": d.get("html_url"), "snippet": d.get("abstract") or d.get("action"),
                            "published_date": d.get("publication_date"), "source": "federal_register", "fr_type": d.get("type"),
                            "effective_on": d.get("effective_on"), "comments_close_on": d.get("comments_close_on"),
                            "document_number": d.get("document_number")}
                           for d in await intel.federal_register_documents(s["federal_register_agency"], max_per_source)]
            elif intel.search_live() or config.SEARCH_PROVIDER == "mock":
                site = s.get("website", "").split("/")[0]
                results = await intel.internet_search(f"site:{site} new rule OR regulation OR guidance OR consultation {year}", max_per_source)
            else:
                skipped.append(s.get("body_name"))
                continue
        except intel.IntelError as e:
            errors[s.get("body_name")] = str(e)
            patch_payload(db, source_obj, {"last_error": str(e), "last_synced_at": iso(utcnow())}, commit=False)
            continue
        patch_payload(db, source_obj, {"last_error": None, "last_synced_at": iso(utcnow())}, commit=False)
        for r in results:
            if not r.get("url") or r["url"] in known:
                continue
            known.add(r["url"])
            doc = add_obj(db, "global_rule_document", r.get("title") or r["url"], "ingested", {
                "url": r["url"], "source_body": s.get("body_name"), "jurisdiction": s.get("jurisdiction"), "category": s.get("category"),
                "snippet": r.get("snippet"), "provider": r.get("source"), "published_at": r.get("published_date"),
                "document_number": r.get("document_number")}, tenant_id, new_id("GRD"))
            is_final = r.get("fr_type") == "Rule"
            add_obj(db, "rule_change", r.get("title") or r["url"], "detected", {
                "document_id": doc.id, "url": r["url"], "source_body": s.get("body_name"),
                "change_type": {"Rule": "final_rule", "Proposed Rule": "proposed_rule"}.get(r.get("fr_type"), "publication"),
                "summary": r.get("snippet") or "", "severity": "high" if is_final or s.get("category") in ("financial", "privacy") else "medium",
                "effective_date": r.get("effective_on"), "deadline": r.get("comments_close_on"), "published_at": r.get("published_date"),
                "jurisdictions": [s.get("jurisdiction")], "provider": r.get("source"),
                "recommended_actions": ["Assess applicability", "Run Legal AI on the source document", "Map obligations to controls"]},
                tenant_id, new_id("RC"))
            docs += 1
            changes += 1
    db.commit()
    summary = {"status": "sync_completed", "documents_created": docs, "changes_created": changes, "errors": errors,
               "skipped_no_provider": skipped, "search_provider": config.SEARCH_PROVIDER}
    emit(request, "global_radar", "radar.sync", "warning" if errors else "success", tenant_id,
         documents=docs, errors=len(errors), skipped=len(skipped))
    return summary


@app.post("/v1/global-radar/sync")
async def global_radar_sync(request: Request, payload: dict = Body(default={}), db: Session = Depends(get_db)):
    return await global_radar_sync_core(db, int(payload.get("max_per_source", 3)), request)


@app.post("/v1/global-radar/search")
async def global_radar_search(payload: dict = Body(...)):
    query = payload.get("query")
    if not query:
        raise HTTPException(400, "query required")
    if not intel.search_live() and config.SEARCH_PROVIDER != "mock":
        raise HTTPException(400, "No internet search provider configured (set SEARCH_PROVIDER and its API key)")
    try:
        results = await intel.internet_search(query, int(payload.get("max_results", 5)))
    except intel.IntelError as e:
        raise HTTPException(502, str(e))
    return {"provider": config.SEARCH_PROVIDER, "query": query, "results": results}


@app.post("/v1/rule-changes/{change_id}/review")
def rule_change_review(change_id: str, request: Request, db: Session = Depends(get_db)):
    rc = get_obj(db, "rule_change", change_id)
    p = payload_of(rc)
    item = add_obj(db, "review_item", f"Assess rule change: {rc.title[:120]}", "pending_triage", {"queue_type": "rule_change",
                   "payload": {"rule_change_id": change_id, "url": p.get("url"), "source_body": p.get("source_body"),
                               "effective_date": p.get("effective_date")}}, rc.tenant_id, new_id("REV"))
    update_obj(db, rc, status="in_review", commit=False)
    db.commit()
    emit(request, "global_radar", "rule_change.queued", change_id=change_id)
    return serialize(item)


async def _analyze_document(db: Session, doc: Obj) -> Obj:
    p = payload_of(doc)
    controls = [f"{c.id} {c.title}" for c in query_objs(db, "control")]
    try:
        data = await run_in_threadpool(intel.legal_analysis, p.get("text", ""), doc.title, p.get("jurisdiction", "Global"), controls)
    except intel.IntelError as e:
        raise HTTPException(502, f"Legal AI failed: {e}")
    analysis = add_obj(db, "legal_analysis", f"Analysis for {doc.title}", "draft", {"document_id": doc.id, **data}, doc.tenant_id, new_id("LA"))
    update_obj(db, doc, status="analyzed", commit=False)
    db.commit()
    return analysis


@app.post("/v1/legal/documents")
async def legal_document_create(request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    text = (payload.get("text") or "").strip()
    source_url = payload.get("source_url")
    if not text and source_url:
        try:
            text = await intel.fetch_page_text(source_url)
        except intel.IntelError as e:
            raise HTTPException(502, str(e))
    if not text:
        raise HTTPException(400, "Provide text or a fetchable source_url")
    tenant_id = get_default_tenant(db)
    doc = add_obj(db, "legal_document", payload.get("title") or source_url or "Untitled legal document", "uploaded", {
        "doc_type": payload.get("doc_type", "regulation"), "jurisdiction": payload.get("jurisdiction") or "Global",
        "source_url": source_url, "text": text, "characters": len(text), "hash": sha256_json(text)}, tenant_id, new_id("LD"))
    db.commit()
    emit(request, "legal_ai", "document.uploaded", tenant_id=tenant_id, id=doc.id, characters=len(text))
    if payload.get("analyze", True):
        analysis = await _analyze_document(db, doc)
        emit(request, "legal_ai", "document.analyzed", tenant_id=tenant_id, document_id=doc.id, analysis_id=analysis.id)
        return {"document": serialize(doc, ("text",)), "analysis": serialize(analysis)}
    return {"document": serialize(doc, ("text",))}


@app.post("/v1/legal/analyze/{document_id}")
async def legal_analyze(document_id: str, request: Request, db: Session = Depends(get_db)):
    doc = get_obj(db, "legal_document", document_id)
    analysis = await _analyze_document(db, doc)
    emit(request, "legal_ai", "document.analyzed", tenant_id=doc.tenant_id, document_id=doc.id, analysis_id=analysis.id)
    return serialize(analysis)


@app.get("/v1/legal/obligations")
def legal_obligations(db: Session = Depends(get_db)):
    return [{"analysis_id": a["id"], "document_id": a.get("document_id"), "obligation": o, "created_at": a.get("created_at")}
            for a in list_objs(db, "legal_analysis") for o in a.get("obligations", [])]


@app.post("/v1/implementation/plans/generate/{analysis_id}")
def implementation_generate(analysis_id: str, request: Request, db: Session = Depends(get_db)):
    analysis = get_obj(db, "legal_analysis", analysis_id)
    p = payload_of(analysis)
    start = utcnow().date()
    plans = []
    for phase in (p.get("plan") or {}).get("phases", []):
        end_days = int((re.findall(r"\d+", phase.get("horizon", "")) or [30])[-1])
        plans.append(add_obj(db, "implementation_plan", phase.get("objective") or phase.get("horizon"), "draft", {
            "analysis_id": analysis_id, "horizon": phase.get("horizon"), "target_date": (start + timedelta(days=end_days)).isoformat(),
            "tasks": phase.get("tasks", []), "owner_roles": sorted({t.get("owner_role") for t in phase.get("tasks", []) if t.get("owner_role")}),
            "deadlines": p.get("deadlines", [])}, analysis.tenant_id, new_id("IP")))
    update_obj(db, analysis, status="planned", commit=False)
    db.commit()
    emit(request, "implementation_planning", "plan.generated", tenant_id=analysis.tenant_id, analysis_id=analysis_id, plans=len(plans))
    return [serialize(x) for x in plans]


NEGATIVE = re.compile(r"\b(fine[sd]?|penalt\w*|enforcement|charge[sd]|settle\w*|sanction\w*|cease|violat\w*|fraud|breach)\b", re.I)


async def market_sync_core(db: Session, per_source: int = 5, request: Optional[Request] = None) -> dict:
    tenant_id = get_default_tenant(db)
    known = {m.get("url") for m in list_objs(db, "market_signal")}
    created, errors, skipped = 0, {}, []
    year = utcnow().year
    for source_obj in query_objs(db, "global_rule_source"):
        s = payload_of(source_obj)
        if not s.get("active", True):
            continue
        try:
            if s.get("feed_url"):
                results = [{**r, "source": "rss"} for r in await intel.fetch_feed(s["feed_url"], per_source)]
            elif intel.search_live() or config.SEARCH_PROVIDER == "mock":
                site = s.get("website", "").split("/")[0]
                results = await intel.internet_search(f"site:{site} enforcement action OR penalty OR fine {year}", min(per_source, 3))
            else:
                skipped.append(s.get("body_name"))
                continue
        except intel.IntelError as e:
            errors[s.get("body_name")] = str(e)
            continue
        for r in results:
            if not r.get("url") or r["url"] in known:
                continue
            known.add(r["url"])
            text = f"{r.get('title')} {r.get('snippet')}"
            negative = bool(NEGATIVE.search(text))
            add_obj(db, "market_signal", r.get("title") or r["url"], "detected", {
                "url": r["url"], "snippet": r.get("snippet"), "source_body": s.get("body_name"), "jurisdiction": s.get("jurisdiction"),
                "category": s.get("category"), "published_at": r.get("published_date"),
                "sentiment": "negative" if negative else "neutral",
                "impact": "high" if negative and s.get("category") in ("financial", "privacy", "privacy_security", "consumer_finance") else "medium" if negative else "low",
                "provider": r.get("source")}, tenant_id, new_id("MS"))
            created += 1
    db.commit()
    emit(request, "market_intelligence", "market.sync", "warning" if errors else "success", tenant_id, signals=created, errors=len(errors))
    return {"status": "market_sync_completed", "signals_created": created, "errors": errors, "skipped_no_provider": skipped}


@app.post("/v1/market/sync")
async def market_sync(request: Request, payload: dict = Body(default={}), db: Session = Depends(get_db)):
    return await market_sync_core(db, int(payload.get("max_per_source", 5)), request)


@app.get("/v1/intel/summary")
def intel_summary(db: Session = Depends(get_db)):
    counts = brain_metrics(db)
    return {k: counts.get(t, 0) for k, t in (("rule_documents", "global_rule_document"), ("rule_changes", "rule_change"),
                                               ("legal_documents", "legal_document"), ("legal_analyses", "legal_analysis"),
                                               ("market_signals", "market_signal"), ("implementation_plans", "implementation_plan"))} | {
        "search_provider": config.SEARCH_PROVIDER, "llm_provider": intel.llm_name()}


# ====================================================================== MCP, brain, pricing
MCP_TOOLS = [
    {"name": "get_metrics", "description": "Get compliance metrics", "scope": "compliance.read"},
    {"name": "list_controls", "description": "List controls", "scope": "controls.read"},
    {"name": "run_connector", "description": "Run a live connector: {\"connector\": \"aws|okta|github\"}", "scope": "evidence.write"},
    {"name": "run_all_tests", "description": "Run every control test", "scope": "testing.write"},
    {"name": "generate_policy", "description": "Generate a policy: {\"policy_type\", \"company\"}", "scope": "policies.write"},
    {"name": "process_questionnaire", "description": "Process a questionnaire: {\"questionnaire_id\"}", "scope": "questionnaires.write"},
    {"name": "advance_dsar", "description": "Advance a DSAR: {\"dsar_id\"}", "scope": "privacy.write"},
    {"name": "triage_incident", "description": "Triage an incident: {\"incident_id\"}", "scope": "incidents.write"},
    {"name": "sync_global_radar", "description": "Sync Federal Register / search sources", "scope": "regulatory.write"},
    {"name": "sync_market", "description": "Sync regulator news feeds", "scope": "regulatory.write"},
]


@app.get("/v1/mcp/tools")
def mcp_tools():
    return MCP_TOOLS


def _first(db: Session, obj_type: str, preferred: Optional[str]) -> str:
    if preferred:
        return preferred
    items = query_objs(db, obj_type, 1)
    if not items:
        raise HTTPException(404, f"No {obj_type} found")
    return items[0].id


@app.post("/v1/mcp/call")
async def mcp_call(request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    tool, args = payload.get("tool"), payload.get("arguments") or {}
    emit(request, "mcp", "tool.call", tool=tool)
    if tool == "get_metrics":
        return brain_metrics(db)
    if tool == "list_controls":
        return list_objs(db, "control")
    if tool == "run_connector":
        return await connector_run_core(db, args.get("connector", "aws"), request)
    if tool == "run_all_tests":
        return [run_test_core(db, t.id, request) for t in query_objs(db, "test")]
    if tool == "generate_policy":
        return await policy_generate(request, args, db)
    if tool == "process_questionnaire":
        return await questionnaire_process(_first(db, "questionnaire", args.get("questionnaire_id")), request, db)
    if tool == "advance_dsar":
        return dsar_advance(_first(db, "dsar", args.get("dsar_id")), request, {}, db)
    if tool == "triage_incident":
        return incident_triage(_first(db, "incident", args.get("incident_id")), request, db)
    if tool == "sync_global_radar":
        return await global_radar_sync_core(db, int(args.get("max_per_source", 3)), request)
    if tool == "sync_market":
        return await market_sync_core(db, int(args.get("max_per_source", 5)), request)
    raise HTTPException(400, f"Unsupported tool {tool}")


METRIC_TYPES = ["control", "evidence", "test", "test_run", "gap", "remediation_task", "policy", "questionnaire", "questionnaire_answer",
                "dsar", "vendor", "incident", "regulatory_update", "review_item", "report", "global_rule_document", "rule_change",
                "legal_document", "legal_analysis", "implementation_plan", "market_signal"]


@app.get("/v1/brain/metrics")
def brain_metrics(db: Session = Depends(get_db)):
    counts = {t: db.query(Obj).filter(Obj.type == t).count() for t in METRIC_TYPES}
    counts["open_gaps"] = db.query(Obj).filter(Obj.type == "gap", Obj.status == "open").count()
    counts["telemetry_events"] = db.query(Event).count()
    return counts


@app.get("/v1/brain/events")
def brain_events(limit: int = Query(default=50, le=1000), db: Session = Depends(get_db)):
    return [serialize_event(e) for e in db.query(Event).order_by(Event.created_at.desc()).limit(limit).all()]


@app.get("/v1/brain/flows/{correlation_id}")
def brain_flow(correlation_id: str, db: Session = Depends(get_db)):
    events = db.query(Event).filter(Event.correlation_id == correlation_id).order_by(Event.created_at.asc()).all()
    return {"correlation_id": correlation_id, "events": [serialize_event(e) for e in events]}


@app.get("/v1/brain/anomalies")
def brain_anomalies(db: Session = Depends(get_db)):
    now = utcnow()
    recent = db.query(Event).filter(Event.created_at >= now - timedelta(minutes=5)).count()
    previous = db.query(Event).filter(Event.created_at >= now - timedelta(minutes=10), Event.created_at < now - timedelta(minutes=5)).count()
    errors = db.query(Event).filter(Event.created_at >= now - timedelta(minutes=5), Event.status == "error").count()
    return {"recent_events_5m": recent, "previous_events_5m": previous, "errors_5m": errors,
            "anomaly_detected": recent > max(25, previous * 3) or errors >= 5}


@app.get("/v1/pricing/entitlements")
def pricing_entitlements(db: Session = Depends(get_db)):
    return list_objs(db, "entitlement")


@app.post("/v1/pricing/entitlements/{ent_id}")
def pricing_entitlement_update(ent_id: str, payload: dict = Body(...), db: Session = Depends(get_db)):
    item = get_obj(db, "entitlement", ent_id)
    changes = {k: payload[k] for k in ("enabled", "limit_value") if k in payload}
    return patch_payload(db, item, changes, status="enabled" if changes.get("enabled", payload_of(item).get("enabled")) else "disabled")
