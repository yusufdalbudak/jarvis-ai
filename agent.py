#!/usr/bin/env python3
"""Bounded uGig revenue agent.

The agent intentionally optimizes for quality over application volume:
- fetch current hiring gigs,
- reject seller ads mistakenly posted as hiring gigs,
- filter for safe/credible work,
- rank against a focused delivery profile,
- avoid duplicates,
- submit at most a small configured number of applications.

No third-party dependencies are required.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable

API_BASE = "https://ugig.net/api"
PORTFOLIO_URL = "https://github.com/yusufdalbudak"

MIN_BUDGET_USD = float(os.getenv("MIN_BUDGET_USD", "25"))
MAX_APPLICATIONS_PER_RUN = max(0, int(os.getenv("MAX_APPLICATIONS_PER_RUN", "1")))
AUTO_APPLY = os.getenv("AUTO_APPLY", "true").strip().lower() in {"1", "true", "yes", "on"}
API_KEY = os.getenv("UGIG_API_KEY", "").strip()

FIT_TERMS: dict[str, int] = {
    "python": 6,
    "csv": 7,
    "json": 7,
    "data cleaning": 6,
    "data-cleaning": 6,
    "data analysis": 5,
    "pandas": 4,
    "research": 6,
    "source verification": 6,
    "fact check": 5,
    "fact-check": 5,
    "technical writing": 5,
    "documentation": 5,
    "competitive intelligence": 6,
    "market research": 6,
    "vendor research": 6,
    "cybersecurity": 6,
    "security review": 5,
    "security audit": 4,
    "code review": 5,
    "bug fix": 5,
    "debugging": 5,
    "api integration": 6,
    "rest api": 5,
    "automation": 5,
    "typescript": 5,
    "javascript": 4,
    "node.js": 3,
    "next.js": 3,
    "react": 2,
    "spreadsheet": 6,
    "excel": 6,
    "report": 3,
    "technical seo": 3,
}

MISMATCH_TERMS = {
    "full-time",
    "full time",
    "on-site",
    "onsite",
    "native ios",
    "swiftui",
    "android app",
    "kotlin",
    "unity",
    "unreal engine",
    "blender",
    "3d modeling",
    "graphic design",
    "logo design",
    "cold calling",
    "appointment setter",
    "door to door",
    "solidity",
    "smart contract audit",
}

RISK_TERMS = {
    "captcha bypass",
    "anti-bot bypass",
    "antibot bypass",
    "residential proxy",
    "credential stuffing",
    "steal credentials",
    "account takeover",
    "mass dm",
    "mass messaging",
    "fake review",
    "fake reviews",
    "review farming",
    "spam campaign",
    "phishing kit",
    "ransomware",
    "malware loader",
    "bypass paywall",
    "scrape linkedin",
    "scrape facebook",
    "scrape instagram",
}

SELLER_TITLE_PREFIXES = (
    "i will ",
    "i can ",
    "i offer ",
    "my service",
    "hire me",
)
SELLER_AD_PHRASES = (
    "what i do",
    "what you get",
    "what i deliver",
    "best for:",
    "perfect for:",
    "my services",
    "fast turnaround",
    "delivery:",
    "payment: platform escrow",
    "payment preferred",
    "log in to hire",
)
BUYER_INTENT_PHRASES = (
    "we need",
    "i need",
    "looking for",
    "seeking",
    "need help",
    "need someone",
    "looking to hire",
    "required deliverable",
    "the task is",
    "task:",
    "requirements:",
)


@dataclass
class Candidate:
    gig: dict[str, Any]
    score: float
    budget: float
    applications: int | None
    matched_terms: list[str]


def _request(path: str, *, method: str = "GET", payload: dict[str, Any] | None = None, auth: bool = False) -> Any:
    url = path if path.startswith("http") else f"{API_BASE}{path}"
    headers = {"Accept": "application/json", "User-Agent": "yusuf-revenue-agent/1.1"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if auth:
        if not API_KEY:
            raise RuntimeError("UGIG_API_KEY is required for this operation")
        headers["X-API-Key"] = API_KEY

    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"uGig HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"uGig network error: {exc.reason}") from exc


def _extract_list(obj: Any, preferred_keys: Iterable[str]) -> list[dict[str, Any]]:
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    if not isinstance(obj, dict):
        return []
    for key in preferred_keys:
        value = obj.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    for value in obj.values():
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    return []


def fetch_gigs() -> list[dict[str, Any]]:
    params = urllib.parse.urlencode(
        {
            "listing_type": "hiring",
            "sort": "newest",
            "page": 1,
            "limit": 100,
        }
    )
    try:
        obj = _request(f"/gigs?{params}")
    except RuntimeError as first_error:
        fallback = urllib.parse.urlencode({"page": 1, "limit": 100})
        try:
            obj = _request(f"/gigs?{fallback}")
        except RuntimeError:
            raise first_error
    return _extract_list(obj, ("gigs", "items", "data", "results"))


def fetch_existing_gig_ids() -> set[str]:
    if not API_KEY:
        return set()
    obj = _request("/applications/my", auth=True)
    apps = _extract_list(obj, ("applications", "items", "data", "results"))
    ids: set[str] = set()
    for app in apps:
        gig = app.get("gig")
        value = app.get("gig_id")
        if not value and isinstance(gig, dict):
            value = gig.get("id")
        if value:
            ids.add(str(value))
    return ids


def _text(gig: dict[str, Any]) -> str:
    skills = gig.get("skills_required") or gig.get("skills") or []
    ai_tools = gig.get("ai_tools_preferred") or []
    pieces = [
        gig.get("title", ""),
        gig.get("description", ""),
        gig.get("category", ""),
        " ".join(map(str, skills)) if isinstance(skills, list) else str(skills),
        " ".join(map(str, ai_tools)) if isinstance(ai_tools, list) else str(ai_tools),
    ]
    return " ".join(str(p) for p in pieces if p).lower()


def _looks_like_seller_ad(gig: dict[str, Any], text: str) -> bool:
    title = str(gig.get("title", "")).strip().lower()
    description = str(gig.get("description", "")).strip().lower()
    if title.startswith(SELLER_TITLE_PREFIXES):
        return True
    seller_hits = sum(1 for phrase in SELLER_AD_PHRASES if phrase in description)
    buyer_hits = sum(1 for phrase in BUYER_INTENT_PHRASES if phrase in text)
    return seller_hits >= 2 and buyer_hits == 0


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("$", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _budget(gig: dict[str, Any]) -> float:
    vals = [
        _number(gig.get("budget_max")),
        _number(gig.get("budget_min")),
        _number(gig.get("budget_amount")),
        _number(gig.get("budget")),
    ]
    vals = [v for v in vals if v is not None and v >= 0]
    return max(vals) if vals else 0.0


def _application_count(gig: dict[str, Any]) -> int | None:
    for key in ("applications_count", "application_count", "num_applications", "applicants_count"):
        val = gig.get(key)
        if isinstance(val, int):
            return val
        if isinstance(val, str) and val.isdigit():
            return int(val)
    return None


def _age_hours(gig: dict[str, Any]) -> float | None:
    raw = gig.get("created_at") or gig.get("posted_at") or gig.get("published_at")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        when = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
        return max(0.0, (dt.datetime.now(dt.timezone.utc) - when).total_seconds() / 3600)
    except ValueError:
        return None


def evaluate(gig: dict[str, Any]) -> Candidate | None:
    gig_id = gig.get("id")
    if not gig_id:
        return None

    text = _text(gig)
    if _looks_like_seller_ad(gig, text):
        return None
    if any(term in text for term in RISK_TERMS):
        return None
    if any(term in text for term in MISMATCH_TERMS):
        return None

    status = str(gig.get("status", "active")).lower()
    if status not in {"active", "open", "published"}:
        return None

    listing_type = str(gig.get("listing_type", "hiring")).lower()
    if listing_type not in {"hiring", "job", ""}:
        return None

    location_type = str(gig.get("location_type", "remote")).lower()
    if location_type and location_type not in {"remote", "anywhere", "worldwide", "global"}:
        return None

    budget = _budget(gig)
    if budget < MIN_BUDGET_USD:
        return None

    age = _age_hours(gig)
    if age is not None and age > 14 * 24:
        return None

    applications = _application_count(gig)
    if applications is not None and applications > 15:
        return None

    matched = [term for term in FIT_TERMS if term in text]
    if not matched:
        return None

    score = float(sum(FIT_TERMS[t] for t in matched))
    if budget >= 300:
        score += 6
    elif budget >= 100:
        score += 4
    elif budget >= 50:
        score += 2
    else:
        score += 1

    if applications is not None:
        if applications <= 2:
            score += 4
        elif applications <= 5:
            score += 2
        elif applications > 10:
            score -= 4

    if age is not None:
        if age <= 24:
            score += 4
        elif age <= 72:
            score += 2
        elif age > 7 * 24:
            score -= 2

    description = str(gig.get("description", ""))
    if 50 <= len(description) <= 1800:
        score += 1
    elif len(description) > 5000:
        score -= 3

    return Candidate(gig=gig, score=score, budget=budget, applications=applications, matched_terms=matched)


def proposed_rate(gig: dict[str, Any], budget: float) -> float | None:
    minimum = _number(gig.get("budget_min"))
    maximum = _number(gig.get("budget_max"))
    budget_type = str(gig.get("budget_type", "fixed")).lower()
    if budget_type in {"fixed", "project", "hourly", "per_hour", "per hour"}:
        if minimum is not None and minimum >= MIN_BUDGET_USD:
            return round(minimum, 2)
        if maximum is not None:
            return round(max(MIN_BUDGET_USD, maximum * 0.75), 2)
        if budget > 0:
            return round(max(MIN_BUDGET_USD, budget * 0.75), 2)
    return None


def proposed_timeline(budget: float) -> str:
    if budget <= 100:
        return "1-3 days after receiving the required inputs"
    if budget <= 300:
        return "3-5 days after scope and inputs are confirmed"
    return "5-10 days after scope, access and acceptance criteria are confirmed"


def cover_letter(candidate: Candidate) -> str:
    gig = candidate.gig
    title = str(gig.get("title", "this task")).strip()
    matches = ", ".join(candidate.matched_terms[:4])
    return (
        f"Hello — I can take on ‘{title}’ through an AI-assisted technical delivery workflow operated by Yusuf Dalbudak. "
        f"The strongest fit in your brief is {matches}. I keep work scoped and verifiable: first I confirm the provided inputs and acceptance criteria, "
        "then produce the requested implementation/research/output, validate it with the appropriate checks, and deliver a concise handoff with evidence. "
        "I do not invent results or claim access I have not been given. Where code is involved, I keep the diff focused and test the change; where research/data is involved, I keep sources or validation steps traceable. "
        f"Portfolio: {PORTFOLIO_URL}."
    )


def submit(candidate: Candidate) -> Any:
    gig = candidate.gig
    payload: dict[str, Any] = {
        "gig_id": str(gig["id"]),
        "cover_letter": cover_letter(candidate),
        "proposed_timeline": proposed_timeline(candidate.budget),
        "portfolio_items": [PORTFOLIO_URL],
        "ai_tools_to_use": ["ChatGPT"],
    }
    rate = proposed_rate(gig, candidate.budget)
    if rate is not None:
        payload["proposed_rate"] = rate
    return _request("/applications", method="POST", payload=payload, auth=True)


def main() -> int:
    gigs = fetch_gigs()
    print(f"Fetched {len(gigs)} marketplace gigs.")

    existing = fetch_existing_gig_ids() if API_KEY else set()
    candidates: list[Candidate] = []
    for gig in gigs:
        if str(gig.get("id", "")) in existing:
            continue
        candidate = evaluate(gig)
        if candidate:
            candidates.append(candidate)

    candidates.sort(key=lambda c: (c.score, c.budget), reverse=True)
    print(f"Qualified {len(candidates)} gigs after seller/safety/fit/ROI filters.")

    for c in candidates[:5]:
        print(
            "CANDIDATE",
            json.dumps(
                {
                    "id": c.gig.get("id"),
                    "title": c.gig.get("title"),
                    "score": c.score,
                    "budget": c.budget,
                    "applications": c.applications,
                    "matches": c.matched_terms[:6],
                },
                ensure_ascii=False,
            ),
        )

    if not candidates:
        return 0
    if not AUTO_APPLY:
        print("AUTO_APPLY is disabled; scan completed without submitting applications.")
        return 0
    if not API_KEY:
        print("UGIG_API_KEY is not configured; scan completed without submitting applications.")
        return 0

    submitted = 0
    for candidate in candidates:
        if submitted >= MAX_APPLICATIONS_PER_RUN:
            break
        try:
            result = submit(candidate)
            print(
                "APPLIED",
                json.dumps(
                    {
                        "gig_id": candidate.gig.get("id"),
                        "title": candidate.gig.get("title"),
                        "score": candidate.score,
                        "budget": candidate.budget,
                        "application_id": result.get("id") if isinstance(result, dict) else None,
                    },
                    ensure_ascii=False,
                ),
            )
            submitted += 1
        except RuntimeError as exc:
            print(f"Application failed for {candidate.gig.get('id')}: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
