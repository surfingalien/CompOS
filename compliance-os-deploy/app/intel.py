"""External intelligence: Federal Register API, RSS/Atom feeds, internet search providers,
web page text extraction, and the LLM layer used by Legal AI / Policy AI / Questionnaire AI."""
import html
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import List, Optional

import httpx

from . import config

FEDERAL_REGISTER_API = "https://www.federalregister.gov/api/v1/documents.json"


class IntelError(Exception):
    pass


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=config.HTTP_TIMEOUT, follow_redirects=True,
                             headers={"User-Agent": config.HTTP_USER_AGENT})


# ------------------------------------------------------------------ search
def search_live() -> bool:
    return config.SEARCH_PROVIDER in ("tavily", "brave", "serpapi", "perplexity") and bool(_search_key())


def _search_key() -> str:
    return {"tavily": config.TAVILY_API_KEY, "brave": config.BRAVE_API_KEY, "serpapi": config.SERPAPI_API_KEY,
            "perplexity": config.PERPLEXITY_API_KEY}.get(config.SEARCH_PROVIDER, "")


def _mock_search(query: str, max_results: int) -> List[dict]:
    return [{"title": f"[MOCK] result {i+1} for: {query}", "url": f"https://example.com/mock/{i+1}",
             "snippet": "SEARCH_PROVIDER=mock. Set SEARCH_PROVIDER and its API key for real results.",
             "published_date": None, "source": "mock"} for i in range(max_results)]


async def internet_search(query: str, max_results: int = 5) -> List[dict]:
    """Returns real results, raises IntelError on provider failure, or [] when no provider is configured."""
    p = config.SEARCH_PROVIDER
    if p == "mock":
        return _mock_search(query, max_results)
    if not search_live():
        return []
    async with _client() as c:
        try:
            if p == "tavily":
                r = await c.post("https://api.tavily.com/search", json={
                    "api_key": config.TAVILY_API_KEY, "query": query, "max_results": max_results, "search_depth": "advanced"})
                r.raise_for_status()
                return [{"title": x.get("title"), "url": x.get("url"), "snippet": x.get("content"),
                         "published_date": x.get("published_date"), "source": "tavily"} for x in r.json().get("results", [])]
            if p == "brave":
                r = await c.get("https://api.search.brave.com/res/v1/web/search",
                                headers={"X-Subscription-Token": config.BRAVE_API_KEY, "Accept": "application/json"},
                                params={"q": query, "count": max_results})
                r.raise_for_status()
                return [{"title": x.get("title"), "url": x.get("url"), "snippet": re.sub(r"<[^>]+>", "", x.get("description") or ""),
                         "published_date": x.get("page_age") or x.get("age"), "source": "brave"}
                        for x in r.json().get("web", {}).get("results", [])]
            if p == "serpapi":
                r = await c.get("https://serpapi.com/search.json",
                                params={"engine": "google", "q": query, "num": max_results, "api_key": config.SERPAPI_API_KEY})
                r.raise_for_status()
                return [{"title": x.get("title"), "url": x.get("link"), "snippet": x.get("snippet"),
                         "published_date": x.get("date"), "source": "serpapi"} for x in r.json().get("organic_results", [])[:max_results]]
            if p == "perplexity":
                r = await c.post("https://api.perplexity.ai/chat/completions",
                                 headers={"Authorization": f"Bearer {config.PERPLEXITY_API_KEY}"},
                                 json={"model": config.PERPLEXITY_MODEL, "messages": [
                                     {"role": "system", "content": "Find the most recent official regulatory publications. Be concise."},
                                     {"role": "user", "content": query}]})
                r.raise_for_status()
                data = r.json()
                answer = data["choices"][0]["message"]["content"]
                urls = data.get("citations") or [s.get("url") for s in data.get("search_results", []) if s.get("url")]
                return [{"title": f"Perplexity source {i+1}: {u}", "url": u, "snippet": answer[:500],
                         "published_date": None, "source": "perplexity"} for i, u in enumerate(urls[:max_results])]
        except httpx.HTTPStatusError as e:
            raise IntelError(f"{p} search failed: HTTP {e.response.status_code} {e.response.text[:200]}")
        except httpx.HTTPError as e:
            raise IntelError(f"{p} search failed: {e}")
    return []


# ------------------------------------------------------------------ Federal Register
async def federal_register_documents(agency_slug: str, per_page: int = 5, doc_types=("RULE", "PRORULE")) -> List[dict]:
    params = [("conditions[agencies][]", agency_slug), ("order", "newest"), ("per_page", str(per_page))]
    params += [("conditions[type][]", t) for t in doc_types]
    params += [("fields[]", f) for f in ("title", "html_url", "abstract", "type", "publication_date", "effective_on",
                                         "document_number", "comments_close_on", "action")]
    async with _client() as c:
        try:
            r = await c.get(FEDERAL_REGISTER_API, params=params)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise IntelError(f"Federal Register API failed for {agency_slug}: {e}")
        return r.json().get("results", [])


# ------------------------------------------------------------------ RSS / Atom
def _text(el: Optional[ET.Element]) -> str:
    return (el.text or "").strip() if el is not None else ""


def _parse_date(value: str) -> Optional[str]:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except Exception:
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except Exception:
        return value[:10]


def strip_html(value: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value or ""))).strip()


def parse_feed(xml_text: str) -> List[dict]:
    root = ET.fromstring(xml_text)
    items = []
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        fields = {c.tag.split("}")[-1]: c for c in el}
        link = _text(fields.get("link"))
        if not link and fields.get("link") is not None:
            link = fields["link"].get("href", "")
        if tag == "entry":
            for c in el:
                if c.tag.split("}")[-1] == "link" and c.get("rel", "alternate") == "alternate":
                    link = c.get("href", link)
        desc = _text(fields.get("description")) or _text(fields.get("summary")) or _text(fields.get("content"))
        date = _text(fields.get("pubDate")) or _text(fields.get("updated")) or _text(fields.get("published")) or _text(fields.get("date"))
        items.append({"title": strip_html(_text(fields.get("title"))), "url": link.strip(),
                      "snippet": strip_html(desc)[:600], "published_date": _parse_date(date)})
    return items


async def fetch_feed(url: str, limit: int = 10) -> List[dict]:
    async with _client() as c:
        try:
            r = await c.get(url, headers={"Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"})
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise IntelError(f"feed {url} failed: {e}")
    try:
        return parse_feed(r.text)[:limit]
    except ET.ParseError as e:
        raise IntelError(f"feed {url} is not valid RSS/Atom: {e}")


# ------------------------------------------------------------------ page text
async def fetch_page_text(url: str) -> str:
    async with _client() as c:
        try:
            r = await c.get(url)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise IntelError(f"could not fetch {url}: {e}")
    ctype = r.headers.get("content-type", "")
    if "pdf" in ctype:
        raise IntelError("PDF documents are not supported for URL import; paste the text instead")
    body = r.text
    if "html" in ctype or body.lstrip().startswith("<"):
        body = re.sub(r"(?is)<(script|style|noscript|nav|footer|header)[^>]*>.*?</\1>", " ", body)
        body = strip_html(body)
    return body


# ------------------------------------------------------------------ LLM
FALLBACK_MODELS = {"claude-opus-5", "claude-fable-5-1"}


def llm_live() -> bool:
    return (config.LLM_PROVIDER == "anthropic" and bool(config.ANTHROPIC_API_KEY)) or \
           (config.LLM_PROVIDER == "openai" and bool(config.OPENAI_API_KEY))


def llm_name() -> str:
    if config.LLM_PROVIDER == "anthropic":
        return f"anthropic:{config.ANTHROPIC_MODEL}"
    if config.LLM_PROVIDER == "openai":
        return f"openai:{config.OPENAI_MODEL}"
    return config.LLM_PROVIDER


def _anthropic_complete(system: str, prompt: str, schema: Optional[dict]) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    kwargs = {"model": config.ANTHROPIC_MODEL, "max_tokens": 16000, "system": system,
              "messages": [{"role": "user", "content": prompt}]}
    if not config.ANTHROPIC_MODEL.startswith("claude-haiku"):
        kwargs["thinking"] = {"type": "adaptive"}
    if schema:
        kwargs["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
    try:
        if config.ANTHROPIC_MODEL in FALLBACK_MODELS:
            resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
        else:
            resp = client.messages.create(**kwargs)
    except anthropic.AuthenticationError:
        raise IntelError("Anthropic rejected ANTHROPIC_API_KEY")
    except anthropic.RateLimitError:
        raise IntelError("Anthropic rate limit reached; retry shortly")
    except anthropic.BadRequestError as e:
        raise IntelError(f"Anthropic bad request: {e.message}")
    except anthropic.APIStatusError as e:
        raise IntelError(f"Anthropic API error {e.status_code}: {e.message}")
    except anthropic.APIConnectionError:
        raise IntelError("Could not reach the Anthropic API")
    if resp.stop_reason == "refusal":
        raise IntelError("The model declined this request")
    if resp.stop_reason == "max_tokens":
        raise IntelError("The model response was cut off (max_tokens); try a shorter document")
    return "".join(b.text for b in resp.content if b.type == "text")


def _openai_complete(system: str, prompt: str, schema: Optional[dict]) -> str:
    body = {"model": config.OPENAI_MODEL, "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
    if schema:
        body["response_format"] = {"type": "json_object"}
    try:
        r = httpx.post(f"{config.OPENAI_BASE_URL}/chat/completions", json=body, timeout=180,
                       headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"})
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise IntelError(f"OpenAI API error {e.response.status_code}: {e.response.text[:200]}")
    except httpx.HTTPError as e:
        raise IntelError(f"Could not reach the OpenAI API: {e}")
    return r.json()["choices"][0]["message"]["content"]


def llm_complete(system: str, prompt: str, schema: Optional[dict] = None) -> str:
    if config.LLM_PROVIDER == "anthropic":
        return _anthropic_complete(system, prompt, schema)
    if config.LLM_PROVIDER == "openai":
        return _openai_complete(system, prompt, schema)
    raise IntelError("No LLM provider configured")


def llm_json(system: str, prompt: str, schema: dict) -> dict:
    text = llm_complete(system, prompt + "\n\nRespond with JSON only, matching this JSON schema:\n" + json.dumps(schema), schema)
    match = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(match.group(0) if match else text)
    except (json.JSONDecodeError, AttributeError):
        raise IntelError("LLM returned invalid JSON")


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


_STR_LIST = {"type": "array", "items": {"type": "string"}}

LEGAL_SCHEMA = _obj({
    "summary": {"type": "string"},
    "jurisdictions": _STR_LIST,
    "effective_dates": _STR_LIST,
    "deadlines": _STR_LIST,
    "obligations": {"type": "array", "items": _obj({
        "text": {"type": "string"}, "type": {"type": "string", "enum": ["mandatory", "conditional", "recommended"]},
        "deadline": {"type": "string"}, "confidence": {"type": "number"}})},
    "risks": {"type": "array", "items": _obj({"risk": {"type": "string"}, "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                                              "mitigation": {"type": "string"}})},
    "mapped_controls": _STR_LIST,
    "policy_updates": _STR_LIST,
    "evidence_requirements": _STR_LIST,
    "penalties": _STR_LIST,
    "affected_frameworks": _STR_LIST,
    "plan": _obj({"phases": {"type": "array", "items": _obj({
        "horizon": {"type": "string"}, "objective": {"type": "string"},
        "tasks": {"type": "array", "items": _obj({"title": {"type": "string"}, "owner_role": {"type": "string"}})}})}}),
    "confidence": {"type": "number"},
})

LEGAL_SYSTEM = (
    "You are a senior regulatory compliance analyst. Extract concrete, source-grounded obligations from the "
    "document provided. Quote or closely paraphrase the document for each obligation; do not invent requirements "
    "that are not in the text. Use an empty string for an unknown deadline. Map obligations to control areas "
    "from this catalog where relevant: {controls}. Produce a phased implementation plan with horizons "
    "'0-30 days', '30-90 days' and '90-180 days'. Your output is reviewed by legal counsel before use."
)


def heuristic_legal_analysis(text: str, title: str, jurisdiction: str) -> dict:
    """Rule-based extraction used when no LLM is configured. Every output is derived from the text."""
    obligations = []
    for sentence in re.split(r"(?<=[.!?;])\s+", text or ""):
        s = sentence.strip()
        if len(s) < 15 or not re.search(r"\b(must|shall|required|within|no later than|notify|retain|encrypt|report|disclose|assess|document|review|test|train)\b", s, re.I):
            continue
        dl = re.search(r"(?:within|no later than)\s+(\d+\s*(?:business\s+)?(?:hours|days|months|years))", s, re.I)
        obligations.append({"text": s[:600], "type": "mandatory" if re.search(r"\b(must|shall|are required to|is required to)\b", s, re.I) else "conditional",
                            "deadline": dl.group(1) if dl else "", "confidence": 0.6})
    deadlines = sorted({m.group(1) for m in re.finditer(r"(?:within|no later than)\s+(\d+\s*(?:business\s+)?(?:hours|days|months|years))", text or "", re.I)})
    effective = sorted({m.group(0) for m in re.finditer(r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}\b", text or "")})
    risks = []
    if deadlines:
        risks.append({"risk": "Deadline-driven non-compliance", "severity": "high", "mitigation": "Create dated tasks and legal review gates."})
    if re.search(r"penalt|fine|sanction|civil money", text or "", re.I):
        risks.append({"risk": "Regulatory penalty exposure", "severity": "high", "mitigation": "Prioritise controls, evidence and management reporting."})
    controls = []
    if re.search(r"privacy|personal (data|information)|gdpr|consumer data", text or "", re.I):
        controls += ["Privacy", "DSAR", "Data Retention", "Breach Notification"]
    if re.search(r"security|cyber|incident|encrypt|authentication|mfa", text or "", re.I):
        controls += ["Access Control", "Logging", "Change Management", "Risk Assessment"]
    if re.search(r"\bAI\b|artificial intelligence|machine learning|algorithm", text or ""):
        controls += ["AI Usage Policy", "Model Risk", "Data Lineage", "Human Oversight"]
    frameworks = [f for f, pat in (("GDPR", r"gdpr|personal data"), ("HIPAA", r"hipaa|health information"), ("SOC2", r"security|availability"),
                                   ("ISO27001", r"information security"), ("AI Governance", r"artificial intelligence|\bAI\b")) if re.search(pat, text or "", re.I)]
    return {
        "summary": f"Rule-based extraction of {len(obligations)} candidate obligation(s) from '{title}'. "
                   "Configure LLM_PROVIDER for a full legal analysis.",
        "jurisdictions": [jurisdiction], "effective_dates": effective, "deadlines": deadlines,
        "obligations": obligations or [{"text": "No obligation language detected; manual review required.", "type": "recommended", "deadline": "", "confidence": 0.3}],
        "risks": risks, "mapped_controls": sorted(set(controls)),
        "policy_updates": ["Update policy language for each extracted obligation."] if obligations else [],
        "evidence_requirements": ["Approved policy version", "Control test evidence", "Training records", "Legal review approval"],
        "penalties": [m.group(0).strip() for m in re.finditer(r"[^.]*\b(?:penalt\w*|fine[sd]?)\b[^.]*\.", text or "", re.I)][:5],
        "affected_frameworks": frameworks,
        "plan": {"phases": [
            {"horizon": "0-30 days", "objective": "Assess, validate, and triage", "tasks": [{"title": "Validate legal interpretation", "owner_role": "Legal Counsel"}, {"title": "Identify affected systems and processes", "owner_role": "Security Engineer"}]},
            {"horizon": "30-90 days", "objective": "Implement controls, policies, and evidence", "tasks": [{"title": o["text"][:140], "owner_role": "Compliance Owner"} for o in obligations[:6]] or [{"title": "Map to unified controls", "owner_role": "Compliance Architect"}]},
            {"horizon": "90-180 days", "objective": "Operate, test, train, and audit", "tasks": [{"title": "Run control effectiveness tests", "owner_role": "Security Operations"}, {"title": "Prepare auditor evidence pack", "owner_role": "Compliance Manager"}]},
        ]},
        "confidence": 0.45 if obligations else 0.2,
    }


MAX_LLM_CHARS = 600_000


def legal_analysis(text: str, title: str, jurisdiction: str, control_names: List[str]) -> dict:
    if not llm_live():
        return {**heuristic_legal_analysis(text, title, jurisdiction), "provider": "heuristic", "legal_review_required": True}
    truncated = len(text) > MAX_LLM_CHARS
    prompt = f"Document title: {title}\nJurisdiction: {jurisdiction}\n\n<document>\n{text[:MAX_LLM_CHARS]}\n</document>"
    data = llm_json(LEGAL_SYSTEM.format(controls=", ".join(control_names)), prompt, LEGAL_SCHEMA)
    for o in data.get("obligations", []):
        o["deadline"] = o.get("deadline") or None
    return {**data, "provider": llm_name(), "legal_review_required": True, "input_truncated": truncated}


POLICY_SYSTEM = (
    "You are a GRC policy writer. Draft a clear, auditable company policy in Markdown with sections: Purpose, Scope, "
    "Roles and Responsibilities, Policy Statements (numbered, each testable), Exceptions, Enforcement, Review Cadence. "
    "Reference the provided controls by code where they apply. Do not claim certifications the company has not stated."
)


def draft_policy(policy_type: str, company: str, frameworks: List[str], controls: List[dict]) -> dict:
    control_lines = "\n".join(f"- {c['id']}: {c.get('title')} ({c.get('category')})" for c in controls)
    if llm_live():
        content = llm_complete(POLICY_SYSTEM, f"Company: {company}\nPolicy type: {policy_type}\nFrameworks: {', '.join(frameworks)}\nControls:\n{control_lines}")
        return {"content": content, "provider": llm_name()}
    statements = "\n".join(f"{i+1}. **{c['id']} – {c.get('title')}.** {company} shall implement and evidence this control; "
                           f"evidence is collected and reviewed at least quarterly." for i, c in enumerate(controls))
    content = (f"# {company} {policy_type.title()} Policy\n\n## Purpose\nDefine {company}'s requirements for {policy_type} "
               f"aligned to {', '.join(frameworks)}.\n\n## Scope\nAll employees, contractors, systems and data owned or processed by {company}.\n\n"
               f"## Roles and Responsibilities\n- Policy owner: Security/Compliance lead\n- Control owners: as assigned in CompOS\n\n"
               f"## Policy Statements\n{statements}\n\n## Exceptions\nExceptions require documented risk acceptance approved by the policy owner.\n\n"
               f"## Enforcement\nViolations may result in disciplinary action.\n\n## Review Cadence\nReviewed at least annually.\n\n"
               f"_Template generated from the control catalog (no LLM configured)._")
    return {"content": content, "provider": "template"}


def draft_answer(question: str, library: List[dict]) -> Optional[str]:
    if not llm_live():
        return None
    kb = "\n".join(f"Q: {a.get('question')}\nA: {a.get('answer')}" for a in library)
    return llm_complete("You answer customer security questionnaires using ONLY the approved answer library provided. "
                        "If the library does not support an answer, reply exactly: INSUFFICIENT_SOURCE.",
                        f"Approved answer library:\n{kb}\n\nQuestion: {question}\nAnswer in 1-3 sentences.").strip()


REG_CHANGE_SCHEMA = _obj({"state_code": {"type": "string"}, "individual_days": {"type": "string"},
                          "regulator_threshold": {"type": "string"}, "regulator_notice": {"type": "string"},
                          "summary": {"type": "string"}})


def extract_breach_change(text: str, state_code: str) -> Optional[dict]:
    if not llm_live():
        return None
    data = llm_json("You extract changes to US state data-breach notification requirements. Use empty strings for anything "
                    "the text does not state. individual_days is the number of days to notify individuals, regulator_threshold "
                    "is like '500_residents'.", f"State: {state_code}\n\n{text[:200_000]}", REG_CHANGE_SCHEMA)
    change = {}
    if (data.get("individual_days") or "").isdigit():
        change["individual_days"] = int(data["individual_days"])
    for k in ("regulator_threshold", "regulator_notice"):
        if data.get(k):
            change[k] = data[k]
    return {"proposed_change": change, "summary": data.get("summary", "")}
