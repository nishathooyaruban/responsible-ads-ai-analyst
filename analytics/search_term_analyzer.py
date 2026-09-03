"""
analytics/search_term_analyzer.py

Deterministic analysis of search term data — the original "which keyword
is wasting budget unnecessarily" feature from the project's day-one spec.

Key distinction this file works with:
    KEYWORD (matched_keyword) = what you bid on, e.g. "sri lanka tour packages"
    SEARCH TERM               = what the user actually typed, e.g.
                                 "sri lanka visa free countries"
A BROAD or PHRASE match keyword can trigger ads for search terms that are
only loosely related — sometimes irrelevant. This file flags:
  - individual search terms that spent money with zero conversions
  - match types (EXACT/PHRASE/BROAD) that are disproportionately
    responsible for wasted spend, which is a more actionable finding than
    any single search term ("tighten your BROAD match keywords" is a
    concrete action; "block this one term" only fixes one leak)

No AI involved — same ground-truth-first approach as the rest of the
project.
"""

from typing import List, Dict, Optional
from collections import defaultdict


MIN_COST_TO_FLAG_TERM = 5.0     # ignore trivially small search-term spend
HIGH_COST_SHARE_THRESHOLD = 40.0  # flag a match type if it accounts for this % or more of wasted spend


def search_term_summary(search_terms: List[Dict]) -> Dict:
    total_impressions = sum(t["impressions"] for t in search_terms)
    total_clicks = sum(t["clicks"] for t in search_terms)
    total_cost = sum(t["cost"] for t in search_terms)
    total_conversions = sum(t["conversions"] for t in search_terms)

    return {
        "total_search_terms": len(search_terms),
        "total_impressions": total_impressions,
        "total_clicks": total_clicks,
        "total_cost": round(total_cost, 2),
        "total_conversions": round(total_conversions, 2),
    }


def find_wasted_search_terms(search_terms: List[Dict]) -> List[Dict]:
    """
    Individual search terms that spent money with zero conversions —
    the most direct "wasted budget" signal available. Sorted by cost so
    the biggest leaks surface first.
    """
    flagged = []
    for t in search_terms:
        if t["conversions"] == 0 and t["cost"] >= MIN_COST_TO_FLAG_TERM:
            flagged.append({
                "search_term": t["search_term"],
                "matched_keyword": t["matched_keyword"],
                "match_type": t["match_type"],
                "campaign_name": t["campaign_name"],
                "ad_group_name": t["ad_group_name"],
                "cost": t["cost"],
                "clicks": t["clicks"],
                "reason": f"Spent {t['cost']} on '{t['search_term']}' with 0 conversions",
            })
    return sorted(flagged, key=lambda x: x["cost"], reverse=True)


def find_wasted_spend_by_match_type(search_terms: List[Dict]) -> List[Dict]:
    """
    Aggregates wasted (zero-conversion) spend by match type. This is the
    more strategically useful finding: if BROAD match keywords account
    for the large majority of wasted spend, the fix is "review/tighten
    BROAD match keywords" — a single, high-leverage action — rather than
    a long list of individual terms to negative-keyword one at a time.
    """
    wasted_by_type = defaultdict(float)
    total_wasted = 0.0

    for t in search_terms:
        if t["conversions"] == 0:
            wasted_by_type[t["match_type"]] += t["cost"]
            total_wasted += t["cost"]

    if total_wasted == 0:
        return []

    results = []
    for match_type, cost in wasted_by_type.items():
        share = (cost / total_wasted) * 100
        results.append({
            "match_type": match_type,
            "wasted_cost": round(cost, 2),
            "pct_of_total_wasted_spend": round(share, 1),
            "flagged_as_high_share": share >= HIGH_COST_SHARE_THRESHOLD,
        })

    return sorted(results, key=lambda x: x["wasted_cost"], reverse=True)


def find_top_converting_search_terms(search_terms: List[Dict], top_n: int = 5) -> List[Dict]:
    """
    Surfaces the best-converting actual search terms — useful for finding
    new exact-match keyword opportunities (a search term converting well
    that ISN'T already an exact-match keyword is a common, concrete
    "add this as a new keyword" recommendation).
    """
    eligible = [t for t in search_terms if t["conversions"] > 0]
    top = sorted(eligible, key=lambda t: t["conversions"], reverse=True)[:top_n]
    return [{
        "search_term": t["search_term"],
        "matched_keyword": t["matched_keyword"],
        "match_type": t["match_type"],
        "campaign_name": t["campaign_name"],
        "conversions": t["conversions"],
        "cost_per_conversion": t["cost_per_conversion"],
        "is_already_exact_match": (
            t["match_type"] == "EXACT" and t["search_term"].lower() == t["matched_keyword"].lower()
        ),
    } for t in top]


def build_search_term_findings(search_terms: List[Dict]) -> Dict:
    """Runs all search-term detectors and packages for the LLM analyst layer."""
    return {
        "search_term_summary": search_term_summary(search_terms),
        "wasted_search_terms": find_wasted_search_terms(search_terms),
        "wasted_spend_by_match_type": find_wasted_spend_by_match_type(search_terms),
        "top_converting_search_terms": find_top_converting_search_terms(search_terms),
    }


if __name__ == "__main__":
    import json
    from google_ads.search_term_data import get_search_term_data

    CUSTOMER_ID = "6485531233"
    CAMPAIGN_ID = "18731997084"

    terms = get_search_term_data(CUSTOMER_ID, campaign_id=CAMPAIGN_ID, period="last_28_days")
    findings = build_search_term_findings(terms)

    print(json.dumps(findings, indent=2))