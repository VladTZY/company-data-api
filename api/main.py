"""Company match API.

POST /match  {"name": ..., "website": ..., "phone": ..., "facebook": ...}
Returns the single best-matching company profile plus a match score breakdown.

Run: uvicorn api.main:app --port 8000
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

import phonenumbers
from elasticsearch import Elasticsearch
from fastapi import FastAPI
from pydantic import BaseModel
from rapidfuzz import fuzz

ES_URL = "http://localhost:9200"
INDEX = "companies"
MATCH_THRESHOLD = 30.0  # minimum confidence (0-100) to return a match

app = FastAPI(title="Veridion Company Match API")
es = Elasticsearch(ES_URL)


class MatchInput(BaseModel):
    name: Optional[str] = None
    website: Optional[str] = None
    phone: Optional[str] = None
    facebook: Optional[str] = None


# Input normalization

def norm_domain(website: str | None) -> str | None:
    if not website:
        return None
    w = website.strip().lower()
    # repair malformed inputs like "https://https//example.com/"
    w = re.sub(r"^(https?:?/?/?)+", "", w)
    w = w.removeprefix("www.")
    w = w.split("/")[0].split("?")[0].strip()
    return w or None


def norm_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    try:
        num = phonenumbers.parse(phone, "US")
        if phonenumbers.is_possible_number(num):
            return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:
        pass
    digits = re.sub(r"\D", "", phone)
    return f"+1{digits}" if len(digits) == 10 else (f"+{digits}" if digits else None)


FB_HANDLE_RE = re.compile(r"facebook\.com/(?:pages/)?([\w.\-]+)", re.I)


def norm_fb(facebook: str | None) -> str | None:
    if not facebook:
        return None
    m = FB_HANDLE_RE.search(facebook)
    if m:
        h = m.group(1).lower().strip(".")
        return h if h not in ("pages", "profile.php") else None
    return facebook.strip().lower().lstrip("@") or None


LEGAL_SUFFIX_RE = re.compile(
    r"\b(inc|llc|ltd|corp|corporation|co|company|pc|pa|llp|lp|plc|the)\b\.?", re.I)


def norm_name(name: str | None) -> str | None:
    if not name:
        return None
    n = LEGAL_SUFFIX_RE.sub(" ", name.lower())
    n = re.sub(r"[^\w\s]", " ", n)
    return re.sub(r"\s+", " ", n).strip() or None


def name_to_domain_token(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name)


# Candidate retrieval from Elasticsearch

def retrieve(domain, phone, fb, name) -> list[dict]:
    should = []
    if domain:
        should.append({"term": {"domain": {"value": domain, "boost": 50}}})
    if phone:
        should.append({"term": {"phones": {"value": phone, "boost": 40}}})
    if fb:
        should.append({"term": {"facebook_handles": {"value": fb, "boost": 35}}})
        should.append({"match": {"company_all_available_names": {"query": fb.replace(".", " ").replace("-", " "), "boost": 2}}})
    if name:
        should.append({"multi_match": {
            "query": name,
            "fields": ["company_commercial_name^3", "company_legal_name^2", "company_all_available_names^2"],
            "type": "most_fields", "fuzziness": "AUTO", "boost": 3,
        }})
        # name collapsed to a domain-like token catches "Acorn Law" -> acornlawpc.com
        should.append({"match": {"domain.text": {"query": name, "boost": 4}}})
    if not should:
        return []
    res = es.search(index=INDEX, query={"bool": {"should": should}}, size=10)
    return [h["_source"] for h in res["hits"]["hits"]]


# Re-scoring of the retrieved candidates

WEIGHTS = {"domain": 100, "phone": 70, "facebook": 60, "name": 45}


def score_candidate(c: dict, domain, phone, fb, name) -> tuple[float, dict]:
    signals: dict[str, float] = {}

    if domain:
        cd = c["domain"]
        if cd == domain:
            signals["domain"] = 1.0
        else:
            signals["domain"] = fuzz.ratio(cd.split(".")[0], domain.split(".")[0]) / 100 * 0.6

    if phone:
        if phone in (c.get("phones") or []):
            signals["phone"] = 1.0
        else:
            # compare national significant numbers (last 10 digits)
            tail = phone[-10:]
            signals["phone"] = 0.9 if any(p.endswith(tail) for p in c.get("phones") or []) else 0.0

    if fb:
        handles = c.get("facebook_handles") or []
        if fb in handles:
            signals["facebook"] = 1.0
        elif handles:
            signals["facebook"] = max(fuzz.ratio(fb, h) for h in handles) / 100 * 0.5
        else:
            # the handle usually contains the company name, so fall back to comparing those
            best = max((fuzz.token_set_ratio(fb.replace(".", " ").replace("-", " "),
                                             norm_name(n) or "")
                        for n in _all_names(c)), default=0)
            signals["facebook"] = best / 100 * 0.45

    if name:
        best = max((fuzz.token_set_ratio(name, norm_name(n) or "") for n in _all_names(c)), default=0)
        # also match name against the domain itself (e.g. "MAZ Auto Glass" vs mazautoglass.com)
        dom_token = c["domain"].split(".")[0]
        best = max(best, fuzz.partial_ratio(name_to_domain_token(name), dom_token))
        signals["name"] = best / 100

    total_weight = sum(WEIGHTS[k] for k in signals)
    score = sum(WEIGHTS[k] * v for k, v in signals.items()) / total_weight * 100 if total_weight else 0.0

    # an exact hit on a unique key (domain/phone/fb) wins regardless of the name fuzz
    if signals.get("domain") == 1.0 or signals.get("phone", 0) >= 0.9 or signals.get("facebook") == 1.0:
        score = max(score, 90.0)
    return score, signals


def _all_names(c: dict) -> list[str]:
    names = [c.get("company_commercial_name"), c.get("company_legal_name")]
    names += c.get("company_all_available_names") or []
    return [n for n in names if n]


@app.post("/match")
def match(inp: MatchInput):
    domain = norm_domain(inp.website)
    phone = norm_phone(inp.phone)
    fb = norm_fb(inp.facebook)
    name = norm_name(inp.name)

    candidates = retrieve(domain, phone, fb, name)
    if not candidates:
        return {"match": None, "score": 0, "reason": "no candidates"}

    scored = [(score_candidate(c, domain, phone, fb, name), c) for c in candidates]
    scored.sort(key=lambda x: x[0][0], reverse=True)
    (best_score, signals), best = scored[0]

    if best_score < MATCH_THRESHOLD:
        return {"match": None, "score": round(best_score, 1), "reason": "below threshold"}

    return {
        "match": best,
        "score": round(best_score, 1),
        "signals": {k: round(v, 3) for k, v in signals.items()},
        "normalized_input": {"domain": domain, "phone": phone, "facebook": fb, "name": name},
    }


@app.get("/health")
def health():
    return {"status": "ok", "es": es.ping()}
