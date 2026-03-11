"""Candidate scoring and stale-context evaluation."""

from __future__ import annotations

from datetime import datetime, timezone

from menu_context_mcp.schemas import ContextQuery, PageCandidate, ScoredCandidate


def score_candidate(
    *,
    query: ContextQuery,
    candidate: PageCandidate,
    system_match_score: float,
    freshness_hours: int,
    now: datetime | None = None,
) -> ScoredCandidate:
    """Compute unified retrieval score for a page candidate."""

    now = now or datetime.now(timezone.utc)

    page_text_match = _page_text_match_score(query=query, candidate=candidate)
    route_match = _route_match_score(query=query, candidate=candidate)
    freshness_score = _freshness_score(candidate=candidate, freshness_hours=freshness_hours, now=now)
    locator_stability = _locator_stability_score(candidate=candidate)

    total = round(system_match_score + page_text_match + route_match + freshness_score + locator_stability, 4)

    return ScoredCandidate(
        candidate=candidate,
        total_score=total,
        system_match_score=round(system_match_score, 4),
        page_text_match_score=round(page_text_match, 4),
        route_match_score=round(route_match, 4),
        freshness_score=round(freshness_score, 4),
        locator_stability_score=round(locator_stability, 4),
        stale_context=_is_stale(candidate=candidate, freshness_hours=freshness_hours, now=now),
    )


def _normalize(value: str | None) -> str:
    return (value or "").strip().lower()


def _page_text_match_score(*, query: ContextQuery, candidate: PageCandidate) -> float:
    keyword = _normalize(query.page_keyword)
    if not keyword:
        # Route-only intent: keep this component neutral but stage-aware.
        return {"exact": 0.5, "fuzzy": 0.35, "semantic": 0.2}[candidate.stage]

    haystacks = [
        _normalize(candidate.title),
        _normalize(candidate.page_title),
        _normalize(candidate.text_breadcrumb),
    ]

    if any(value == keyword for value in haystacks):
        return 1.0
    if any(value.startswith(keyword) for value in haystacks if value):
        return 0.85
    if any(keyword in value for value in haystacks if value):
        return 0.7

    # Stage still carries retrieval confidence for weak textual matches.
    return {"exact": 0.45, "fuzzy": 0.3, "semantic": 0.15}[candidate.stage]


def _route_match_score(*, query: ContextQuery, candidate: PageCandidate) -> float:
    route = _normalize(query.route_hint)
    if not route:
        return 0.0

    route_values = [
        _normalize(candidate.route_path),
        _normalize(candidate.url_pattern),
        _normalize(candidate.target_url),
    ]

    if any(value == route for value in route_values if value):
        return 1.0
    if any(route in value for value in route_values if value):
        return 0.6
    return 0.0


def _freshness_score(*, candidate: PageCandidate, freshness_hours: int, now: datetime) -> float:
    timestamp = candidate.page_crawled_at
    if timestamp is None:
        return 0.0

    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)

    age_hours = max(0.0, (now - timestamp).total_seconds() / 3600)
    if age_hours <= freshness_hours:
        return 1.0 - 0.4 * (age_hours / freshness_hours)

    if age_hours <= freshness_hours * 2:
        overflow = (age_hours - freshness_hours) / freshness_hours
        return max(0.0, 0.6 - 0.6 * overflow)

    return 0.0


def _locator_stability_score(*, candidate: PageCandidate) -> float:
    max_score = min(max(candidate.max_locator_stability, 0.0), 1.0)
    avg_score = min(max(candidate.avg_locator_stability, 0.0), 1.0)
    return max_score * 0.6 + avg_score * 0.4


def _is_stale(*, candidate: PageCandidate, freshness_hours: int, now: datetime) -> bool:
    timestamp = candidate.page_crawled_at
    if timestamp is None:
        return True

    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)

    age_hours = max(0.0, (now - timestamp).total_seconds() / 3600)
    return age_hours > freshness_hours


def explain_score(candidate: ScoredCandidate) -> list[str]:
    """Human-readable reasons for observability/debug trace."""

    reasons: list[str] = [f"score={candidate.total_score:.3f}"]
    if candidate.route_match_score > 0:
        reasons.append("route_hit")
    if candidate.page_text_match_score >= 0.7:
        reasons.append("page_text_strong")
    if candidate.freshness_score >= 0.8:
        reasons.append("fresh_data")
    if candidate.locator_stability_score >= 0.7:
        reasons.append("stable_locator")
    if candidate.stale_context:
        reasons.append("stale_context")
    return reasons
