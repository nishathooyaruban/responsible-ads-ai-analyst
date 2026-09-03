"""
analytics/ad_performance_analyzer.py

Sprint 5 — Deterministic ad-level performance analysis.

Consumes the list of ad dicts from google_ads/ad_data.py and flags:
  - low-CTR ads (relative to the ad group / ad-set average, not account-wide —
    comparing a Sri Lanka ad's CTR to an unrelated market wouldn't be meaningful)
  - high-cost-per-conversion ads
  - ads with no headlines/descriptions — but only genuinely flags this as an
    issue for Responsive Search Ads; Dynamic Search Ads (DSAs) legitimately
    have empty headlines/descriptions because their text is auto-generated
    from the website, so those are excluded from this specific check.

No AI involved — this is the same "ground truth first" approach used in
waste_detector.py. The LLM (Sprint 6 layer) will explain these findings,
not invent new ones.
"""

from typing import List, Dict, Optional


MIN_IMPRESSIONS_TO_FLAG = 100     # ignore ads with too little data to judge fairly
HIGH_CPA_MULTIPLIER = 1.3         # same threshold convention as waste_detector.py
LOW_CTR_MULTIPLIER = 0.7          # flag ads performing 30%+ below their peer average


def _is_dsa(ad: Dict) -> bool:
    """
    A Dynamic Search Ad has no manually-written headlines/descriptions —
    Google generates them from the website automatically. We detect this
    heuristically: no headlines AND no descriptions present at all.
    """
    return not ad.get("headlines") and not ad.get("descriptions")


def account_ad_summary(ads: List[Dict]) -> Dict:
    """Aggregate stats across all ads in the set being analyzed (one campaign's worth)."""
    total_impressions = sum(a["impressions"] for a in ads)
    total_clicks = sum(a["clicks"] for a in ads)
    total_cost = sum(a["cost"] for a in ads)
    total_conversions = sum(a["conversions"] for a in ads)

    avg_ctr = (total_clicks / total_impressions * 100) if total_impressions else None
    avg_cpa = (total_cost / total_conversions) if total_conversions else None

    return {
        "total_ads": len(ads),
        "total_impressions": total_impressions,
        "total_clicks": total_clicks,
        "total_cost": round(total_cost, 2),
        "total_conversions": round(total_conversions, 2),
        "avg_ctr_pct": round(avg_ctr, 2) if avg_ctr is not None else None,
        "avg_cpa": round(avg_cpa, 2) if avg_cpa is not None else None,
    }


def find_low_ctr_ads(ads: List[Dict], summary: Optional[Dict] = None) -> List[Dict]:
    """
    Flags ads whose CTR is notably below the average CTR across this
    ad set (e.g. all ads within one campaign). Ignores ads with too
    few impressions to judge fairly.
    """
    if summary is None:
        summary = account_ad_summary(ads)

    avg_ctr = summary["avg_ctr_pct"]
    if avg_ctr is None:
        return []

    threshold = avg_ctr * LOW_CTR_MULTIPLIER
    flagged = []
    for ad in ads:
        if ad["impressions"] < MIN_IMPRESSIONS_TO_FLAG:
            continue
        if ad["ctr"] < threshold:
            pct_below = ((avg_ctr - ad["ctr"]) / avg_ctr) * 100
            flagged.append({
                "ad_id": ad["ad_id"],
                "ad_group_name": ad["ad_group_name"],
                "campaign_name": ad.get("campaign_name"),
                "ctr": ad["ctr"],
                "avg_ctr_pct": round(avg_ctr, 2),
                "pct_below_average": round(pct_below, 1),
                "impressions": ad["impressions"],
                "reason": f"CTR is {round(pct_below, 1)}% below the ad set average",
            })
    return sorted(flagged, key=lambda x: x["pct_below_average"], reverse=True)


def find_high_cpa_ads(ads: List[Dict], summary: Optional[Dict] = None) -> List[Dict]:
    """
    Flags ads whose cost-per-conversion is significantly above the
    average across this ad set. Only considers ads that have at least
    one conversion — zero-conversion ads are a different problem
    (handled by find_zero_conversion_ads below).
    """
    if summary is None:
        summary = account_ad_summary(ads)

    avg_cpa = summary["avg_cpa"]
    if avg_cpa is None:
        return []

    threshold = avg_cpa * HIGH_CPA_MULTIPLIER
    flagged = []
    for ad in ads:
        if ad["conversions"] > 0 and ad["cost_per_conversion"] > threshold:
            pct_above = ((ad["cost_per_conversion"] - avg_cpa) / avg_cpa) * 100
            flagged.append({
                "ad_id": ad["ad_id"],
                "ad_group_name": ad["ad_group_name"],
                "campaign_name": ad.get("campaign_name"),
                "cost_per_conversion": ad["cost_per_conversion"],
                "avg_cpa": round(avg_cpa, 2),
                "pct_above_average": round(pct_above, 1),
                "reason": f"Cost per conversion is {round(pct_above, 1)}% above the ad set average",
            })
    return sorted(flagged, key=lambda x: x["pct_above_average"], reverse=True)


def find_zero_conversion_ads(ads: List[Dict]) -> List[Dict]:
    """Ads that spent money but produced zero conversions — the ad-level equivalent
    of waste_detector.find_wasted_spend()."""
    flagged = []
    for ad in ads:
        if ad["conversions"] == 0 and ad["cost"] > 0:
            flagged.append({
                "ad_id": ad["ad_id"],
                "ad_group_name": ad["ad_group_name"],
                "campaign_name": ad.get("campaign_name"),
                "cost": ad["cost"],
                "clicks": ad["clicks"],
                "reason": f"Spent {ad['cost']} with 0 conversions",
            })
    return sorted(flagged, key=lambda x: x["cost"], reverse=True)


def find_top_performing_ads(ads: List[Dict], summary: Optional[Dict] = None, top_n: int = 3) -> List[Dict]:
    """
    Surfaces the best-performing ads (by CTR, among ads with real conversions)
    as a reference point — these are the headlines/descriptions that are
    actually working, useful for the LLM to point to as "what's working" rather
    than only flagging problems.
    """
    eligible = [a for a in ads if a["impressions"] >= MIN_IMPRESSIONS_TO_FLAG and a["conversions"] > 0]
    top = sorted(eligible, key=lambda a: a["ctr"], reverse=True)[:top_n]
    return [{
        "ad_id": a["ad_id"],
        "ad_group_name": a["ad_group_name"],
        "campaign_name": a.get("campaign_name"),
        "ctr": a["ctr"],
        "cost_per_conversion": a["cost_per_conversion"],
        "headlines": a["headlines"],
        "descriptions": a["descriptions"],
    } for a in top]


def find_missing_ad_copy(ads: List[Dict]) -> List[Dict]:
    """
    Flags ads with empty headlines/descriptions that are NOT DSAs — this
    would indicate a genuine setup problem (e.g. a Responsive Search Ad
    that failed to save its assets), which is worth a human's attention.
    DSAs are correctly excluded since empty copy is expected for them.
    """
    flagged = []
    for ad in ads:
        if _is_dsa(ad):
            continue  # expected and normal for DSAs — not a real issue
        if not ad.get("headlines") or not ad.get("descriptions"):
            flagged.append({
                "ad_id": ad["ad_id"],
                "ad_group_name": ad["ad_group_name"],
                "campaign_name": ad.get("campaign_name"),
                "reason": "Missing headlines or descriptions on a non-DSA ad — check ad setup",
            })
    return flagged


def build_ad_findings(ads: List[Dict]) -> Dict:
    """
    Runs all ad-level detectors and packages everything for the LLM layer,
    exactly mirroring waste_detector.build_findings()'s pattern.
    """
    summary = account_ad_summary(ads)
    return {
        "ad_set_summary": summary,
        "low_ctr_ads": find_low_ctr_ads(ads, summary),
        "high_cpa_ads": find_high_cpa_ads(ads, summary),
        "zero_conversion_ads": find_zero_conversion_ads(ads),
        "top_performing_ads": find_top_performing_ads(ads, summary),
        "missing_ad_copy": find_missing_ad_copy(ads),
    }


if __name__ == "__main__":
    import json
    from google_ads.ad_data import get_ad_data

    CUSTOMER_ID = "6485531233"
    CAMPAIGN_ID = "18731997084"  # the campaign you already pulled ad data for

    ads = get_ad_data(CUSTOMER_ID, CAMPAIGN_ID)
    findings = build_ad_findings(ads)

    print(json.dumps(findings, indent=2))