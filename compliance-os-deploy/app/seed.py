"""Reference data (always seeded) and optional demo records (SEED_DEMO_DATA=true)."""
from datetime import timedelta

from .config import SEED_DEMO_DATA
from .db import Obj, SessionLocal, add_obj, get_default_tenant, sha256_json, utcnow

GDPR_STEPS = [
    "identity_verification", "classification", "data_discovery", "legal_hold", "collection_tasks",
    "redaction_review", "dpo_review", "response_generation", "delivery", "audit_sealing",
]

BREACH_SEED = [
    ("AL","Alabama",45,"conditional","validate"),("AK","Alaska",None,"conditional","validate"),("AZ","Arizona",None,"conditional","validate"),("AR","Arkansas",None,"conditional","validate"),("CA","California",None,"required_if_threshold","500_residents_sample_to_ag"),("CO","Colorado",30,"required_if_threshold","500_residents"),("CT","Connecticut",90,"conditional","validate"),("DE","Delaware",60,"conditional","validate"),("FL","Florida",30,"required_if_threshold","500_residents"),("GA","Georgia",None,"conditional","validate"),("HI","Hawaii",None,"conditional","validate"),("ID","Idaho",None,"conditional","validate"),("IL","Illinois",None,"required_if_threshold","500_residents"),("IN","Indiana",None,"conditional","validate"),("IA","Iowa",None,"conditional","validate"),("KS","Kansas",None,"conditional","validate"),("KY","Kentucky",None,"conditional","validate"),("LA","Louisiana",None,"conditional","validate"),("ME","Maine",None,"conditional","validate"),("MD","Maryland",None,"conditional","validate"),("MA","Massachusetts",None,"conditional","validate"),("MI","Michigan",None,"conditional","validate"),("MN","Minnesota",None,"conditional","validate"),("MS","Mississippi",None,"conditional","validate"),("MO","Missouri",None,"conditional","validate"),("MT","Montana",None,"conditional","validate"),("NE","Nebraska",None,"conditional","validate"),("NV","Nevada",None,"conditional","validate"),("NH","New Hampshire",None,"conditional","validate"),("NJ","New Jersey",None,"conditional","validate"),("NM","New Mexico",45,"conditional","validate"),("NY","New York",None,"required_if_threshold","500_residents_state_agencies"),("NC","North Carolina",30,"conditional","validate"),("ND","North Dakota",None,"conditional","validate"),("OH","Ohio",None,"conditional","validate"),("OK","Oklahoma",45,"conditional","validate"),("OR","Oregon",None,"required_if_threshold","250_residents"),("PA","Pennsylvania",None,"conditional","validate"),("RI","Rhode Island",45,"conditional","validate"),("SC","South Carolina",45,"conditional","validate"),("SD","South Dakota",60,"conditional","validate"),("TN","Tennessee",60,"conditional","validate"),("TX","Texas",60,"required_if_threshold","250_residents"),("UT","Utah",None,"conditional","validate"),("VT","Vermont",None,"conditional","validate"),("VA","Virginia",None,"conditional","validate"),("WA","Washington",30,"required_if_threshold","500_residents"),("WV","West Virginia",None,"conditional","validate"),("WI","Wisconsin",45,"conditional","validate"),("WY","Wyoming",None,"conditional","validate")
]

# (body, jurisdiction, category, website, federal_register_agency_slug, news_feed_url)
# federal_register slugs feed the Global Radar (free, keyless API).
# feed URLs feed Market Intelligence. Both are editable via PATCH /v1/objects/global_rule_source/{id}.
GLOBAL_SOURCE_SEED = [
    ("U.S. SEC","US","financial","sec.gov","securities-and-exchange-commission","https://www.sec.gov/news/pressreleases.rss"),
    ("FINRA","US","financial","finra.org",None,None),
    ("CFTC","US","financial","cftc.gov","commodity-futures-trading-commission",None),
    ("OCC","US","banking","occ.gov","comptroller-of-the-currency",None),
    ("FDIC","US","banking","fdic.gov","federal-deposit-insurance-corporation",None),
    ("Federal Reserve","US","banking","federalreserve.gov","federal-reserve-system","https://www.federalreserve.gov/feeds/press_all.xml"),
    ("CFPB","US","consumer_finance","consumerfinance.gov","consumer-financial-protection-bureau","https://www.consumerfinance.gov/about-us/newsroom/feed/"),
    ("FTC","US","privacy_security","ftc.gov","federal-trade-commission","https://www.ftc.gov/feeds/press-release.xml"),
    ("HHS OCR","US","health_privacy","hhs.gov","health-and-human-services-department",None),
    ("CISA","US","cybersecurity","cisa.gov","cybersecurity-and-infrastructure-security-agency","https://www.cisa.gov/cybersecurity-advisories/all.xml"),
    ("NIST","US","standards","nist.gov","national-institute-of-standards-and-technology",None),
    ("European Commission","EU","regulation","commission.europa.eu",None,None),
    ("EDPB","EU","privacy","edpb.europa.eu",None,"https://www.edpb.europa.eu/feed/news_en"),
    ("ENISA","EU","cybersecurity","enisa.europa.eu",None,None),
    ("ESMA","EU","securities","esma.europa.eu",None,None),
    ("EBA","EU","banking","eba.europa.eu",None,None),
    ("UK FCA","UK","financial","fca.org.uk",None,"https://www.fca.org.uk/news/rss.xml"),
    ("UK ICO","UK","privacy","ico.org.uk",None,None),
    ("Singapore MAS","SG","financial","mas.gov.sg",None,None),
    ("Singapore PDPC","SG","privacy","pdpc.gov.sg",None,None),
    ("Hong Kong HKMA","HK","banking","hkma.gov.hk",None,None),
    ("Japan FSA","JP","financial","fsa.go.jp",None,None),
    ("Australia ASIC","AU","financial","asic.gov.au",None,None),
    ("Australia OAIC","AU","privacy","oaic.gov.au",None,None),
    ("India RBI","IN","banking","rbi.org.in",None,None),
    ("India SEBI","IN","securities","sebi.gov.in",None,None),
    ("Brazil ANPD","BR","privacy","gov.br/anpd",None,None),
    ("Switzerland FINMA","CH","financial","finma.ch",None,None),
    ("PCI SSC","Global","payment_security","pcisecuritystandards.org",None,None),
    ("ISO","Global","standards","iso.org",None,None),
]

MODULES = [
    "controls", "evidence", "testing", "policy_ai", "questionnaire_ai", "auditor_portal", "trust_center",
    "privacy_dsar", "vendor_risk", "regulatory_radar", "incident_response", "executive_reporting", "mcp_api",
    "pricing_entitlements", "global_radar", "legal_ai", "market_intelligence", "implementation_planning",
]

CONTROLS = [
    ("AC-001", "Multi-Factor Authentication for Production Access", "Access Control", "high"),
    ("ENC-001", "Encryption at Rest for Production Data", "Encryption", "critical"),
    ("LOG-001", "Audit Logging Enabled", "Logging", "high"),
    ("SDLC-001", "Peer Review Required for Code Changes", "Secure Development", "medium"),
    ("HR-001", "Security Training Completed", "HR Security", "medium"),
    ("VRM-001", "Vendor Risk Management", "Vendor Management", "medium"),
    ("IR-001", "Incident Response Plan", "Incident Response", "high"),
    ("SAM-001", "Backup Restore Test", "Availability", "high"),
]

MAPPINGS = [
    ("AC-001", "SOC2", "CC6.1"), ("AC-001", "ISO27001", "A.9.4.2"), ("AC-001", "HIPAA", "164.312(d)"), ("AC-001", "GDPR", "Article 32"),
    ("ENC-001", "SOC2", "CC6.1"), ("ENC-001", "ISO27001", "A.10.1"), ("LOG-001", "SOC2", "CC7.2"), ("SDLC-001", "SOC2", "CC8.1"),
    ("HR-001", "SOC2", "CC1.4"), ("VRM-001", "GDPR", "Article 28"), ("IR-001", "GDPR", "Article 33"), ("SAM-001", "SOC2", "A1.2"),
]

# operators: eq | neq | exists | gte | lte | eq_field (compare against another field of the same evidence)
TESTS = [
    ("TEST-001", "AC-001", "All active users have MFA", "users_without_mfa", "eq", 0),
    ("TEST-002", "ENC-001", "EBS encryption by default enabled", "ebs_encryption_enabled", "eq", True),
    ("TEST-003", "LOG-001", "CloudTrail logging enabled", "cloudtrail_enabled", "eq", True),
    ("TEST-004", "SAM-001", "Backup restore completed", "backup_restore_test", "eq", "completed"),
    ("TEST-005", "SDLC-001", "Every repository requires PR review", "required_reviews_enabled", "eq_field", "repositories_checked"),
    ("TEST-006", "HR-001", "All employees completed security training", "security_training_completed", "eq_field", "employees"),
    ("TEST-007", "ENC-001", "S3 account-level public access blocked", "s3_public_access_blocked", "eq", True),
]

ANSWER_LIBRARY = [
    ("AL-001", "Do you encrypt customer data at rest?", "Yes. Customer data is encrypted at rest using AES-256.", "policy", 1.0),
    ("AL-002", "Do you enforce multi-factor authentication?", "Yes. MFA is enforced for all production and administrative access.", "policy", 1.0),
    ("AL-003", "Do you perform penetration testing?", "Yes. Third-party penetration testing is performed annually.", "trust_center", 0.9),
    ("AL-004", "Do you test backup restoration?", "Yes. Backup restoration is tested quarterly.", "policy", 1.0),
]


def _seed_reference(db, tenant_id):
    for module in MODULES:
        add_obj(db, "entitlement", module, "enabled", {"module": module, "enabled": True, "limit_value": None}, tenant_id)
    for code, name in [("SOC2", "SOC 2"), ("ISO27001", "ISO 27001"), ("HIPAA", "HIPAA"), ("GDPR", "GDPR")]:
        add_obj(db, "framework", name, "active", {"code": code, "name": name}, tenant_id, code)
    for code, title, category, risk in CONTROLS:
        add_obj(db, "control", title, "active", {"code": code, "category": category, "risk_level": risk}, tenant_id, code)
    for i, (control_id, framework_id, requirement) in enumerate(MAPPINGS):
        add_obj(db, "control_mapping", f"{control_id}->{framework_id}:{requirement}", "active",
                {"control_id": control_id, "framework_id": framework_id, "requirement": requirement}, tenant_id, f"MAP-{i+1:03d}")
    for tid, cid, name, field, op, expected in TESTS:
        add_obj(db, "test", name, "active", {"control_id": cid, "name": name, "expected_field": field,
                                             "operator": op, "expected_value": expected}, tenant_id, tid)
    for code, name, days, notice, threshold in BREACH_SEED:
        add_obj(db, "breach_matrix", f"{code} breach notification", "active", {
            "state_code": code, "state_name": name, "individual_days": days, "regulator_notice": notice,
            "regulator_threshold": threshold, "legal_review_required": True}, tenant_id, f"BM-{code}")
    for i, (body, juris, cat, site, fr_slug, feed) in enumerate(GLOBAL_SOURCE_SEED):
        add_obj(db, "global_rule_source", body, "active", {
            "body_name": body, "jurisdiction": juris, "category": cat, "website": site, "active": True,
            "federal_register_agency": fr_slug, "feed_url": feed}, tenant_id, f"GS-{i+1:03d}")
    for aid, q, a, source, weight in ANSWER_LIBRARY:
        add_obj(db, "answer_library", q, "approved", {"question": q, "answer": a, "source_type": source, "weight": weight}, tenant_id, aid)


def _seed_demo(db, tenant_id):
    """Sample records so every screen has something to show. Everything is labelled 'demo'."""
    demo = {"demo": True}
    evidence = [
        ("EV-001", "AC-001", "okta", {"total_users": 148, "active_users": 142, "users_without_mfa": 0}, "approved", 92),
        ("EV-003", "ENC-001", "aws", {"ebs_encryption_enabled": True, "s3_public_access_blocked": True, "kms_keys": 3}, "approved", 95),
        ("EV-004", "LOG-001", "aws", {"cloudtrail_enabled": True, "trails": 1, "multi_region": True}, "approved", 90),
        ("EV-007", "SAM-001", "manual", {"backup_restore_test": "missing"}, "rejected", 35),
    ]
    for eid, cid, connector, payload, status, quality in evidence:
        add_obj(db, "evidence", f"{connector} evidence (demo)", status, {
            "control_id": cid, "connector": connector, "payload": payload, "hash": sha256_json(payload),
            "quality_score": quality, "source": "demo", **demo}, tenant_id, eid)
    add_obj(db, "policy", "Sample Information Security Policy", "approved", {"policy_type": "security", "version": "1.0", **demo}, tenant_id, "POL-001")
    add_obj(db, "policy_version", "v1.0", "approved", {"policy_id": "POL-001", "version": "1.0", "content": "Sample policy content.", "provider": "demo"}, tenant_id, "PV-001")
    add_obj(db, "questionnaire", "Northwind Enterprise", "imported", {
        "customer": "Northwind Enterprise", "total_questions": 4, "auto_answered": 0, "needs_review": 0,
        "questions": ["Do you encrypt customer data at rest?", "Do you enforce multi-factor authentication?",
                      "Do you test backup restoration?", "Do you have a SOC 2 Type II report?"], **demo}, tenant_id, "QST-001")
    due = (utcnow() + timedelta(days=30)).date().isoformat()
    add_obj(db, "dsar", "Access request", "received", {"request_type": "access", "requester_email": "privacy.access@example.com", "due_date": due, **demo}, tenant_id, "DSAR-001")
    add_obj(db, "workflow_satisfaction", "gdpr_dsar_v1", "in_progress", {"dsar_id": "DSAR-001", "workflow": "gdpr_dsar_v1",
            "required_steps": GDPR_STEPS, "completed_steps": [], "sla_met": True}, tenant_id, "WS-001")
    for idx, step in enumerate(GDPR_STEPS):
        add_obj(db, "workflow_step", step, "pending", {"satisfaction_id": "WS-001", "name": step, "order": idx, "output": {}}, tenant_id, f"STEP-{idx+1:03d}")
    add_obj(db, "audit_engagement", "SOC 2 Type II", "active", {"framework": "SOC 2 Type II", "auditor": "Sample Assurance LLP", **demo}, tenant_id, "AUD-001")
    add_obj(db, "trust_document", "Sample Privacy Policy", "active", {"access_level": "public", **demo}, tenant_id, "TD-003")
    add_obj(db, "nda_request", "Northwind Enterprise", "pending", {"company": "Northwind Enterprise", "requester_email": "procurement@northwind.example", **demo}, tenant_id, "NDA-001")
    add_obj(db, "vendor", "Sample DataStore Inc", "new", {"category": "infrastructure", "risk_tier": "high", "data_access": "customer_pii", "has_soc2": True, **demo}, tenant_id, "VEN-001")
    add_obj(db, "vendor", "Sample EmailProvider", "new", {"category": "communications", "risk_tier": "medium", "data_access": "contact_data", "has_soc2": False, **demo}, tenant_id, "VEN-003")
    add_obj(db, "incident", "Suspicious login from new geography", "open", {"severity": "high", "affected_states": ["TX", "CA"], "obligations": {}, **demo}, tenant_id, "INC-001")


def seed_if_empty():
    db = SessionLocal()
    try:
        if db.query(Obj).filter(Obj.type == "control").count() > 0:
            return
        tenant_id = get_default_tenant(db)
        _seed_reference(db, tenant_id)
        if SEED_DEMO_DATA:
            _seed_demo(db, tenant_id)
        db.commit()
    finally:
        db.close()
