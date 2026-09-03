"""
waste_detector.py

Deterministic waste/opportunity detection on CAMPAIGN-LEVEL data.

MAJOR CHANGE (per PPC review feedback): campaigns are now evaluated
against their OWN configured Target CPA (set by the account manager
based on what's actually profitable for that market), NOT against the
account-wide average CPA. Comparing a campaign targeting a competitive
market (target CPA 150) to one in an easy market (target CPA 40) using
one shared "account average" produces misleading flags — a campaign
could be flagged "high CPA" while comfortably beating its own target,
or missed as "fine" while actually blowing through its target.

Campaigns with no configured target_cpa (Manual CPC, Target ROAS, or
uncapped Maximize Conversions) are excluded from target-based comparison
and explicitly reported as "no target set" rather than silently ignored
or compared against a fallback that isn't really theirs.

Also new: period-over-period findings, using the cost_change_pct /
conversions_change_pct fields produced by
google_ads.campaign_data.get_campaign_data_with_comparison().

No AI involved — every flag here is a plain threshold/comparison rule.
This is the "ground truth" the LLM analyst layer explains, not invents.
"""

from typing import List, Dict, Optional


MIN_SPEND_TO_FLAG_WASTE = 20.0        # ignore tiny-spend campaigns
ZERO_ACTIVITY_IMPRESSION_THRESHOLD = 0
SIGNIFICANT_CHANGE_PCT = 30.0         # flag period-over-period swings of this size or more


def account_summary(campaigns: List[Dict]) -> Dict:
    """
    Account-wide totals — kept for overall context (total spend, total
    conversions, etc.) but NO LONGER used as a per-campaign performance
    benchmark. That role now belongs to each campaign's own target_cpa.
    """
    total_impressions = sum(c["impressions"] for c in campaigns)
    total_clicks = sum(c["clicks"] for c in campaigns)
    total_cost = sum(c["cost"] for c in campaigns)
    total_conversions = sum(c["conversions"] for c in campaigns)

    avg_cpa = (total_cost / total_conversions) if total_conversions else None
    avg_ctr = (total_clicks / total_impressions * 100) if total_impressions else None
    avg_cpc = (total_cost / total_clicks) if total_clicks else None

    return {
        "total_campaigns": len(campaigns),
        "total_impressions": total_impressions,
        "total_clicks": total_clicks,
        "total_cost": round(total_cost, 2),
        "total_conversions": round(total_conversions, 2),
        "account_avg_cpa": round(avg_cpa, 2) if avg_cpa is not None else None,
        "account_avg_ctr_pct": round(avg_ctr, 2) if avg_ctr is not None else None,
        "account_avg_cpc": round(avg_cpc, 2) if avg_cpc is not None else None,
    }


def find_wasted_spend(campaigns: List[Dict]) -> List[Dict]:
    """Campaigns that spent real money but got zero conversions."""
    flagged = []
    for c in campaigns:
        if c["conversions"] == 0 and c["cost"] >= MIN_SPEND_TO_FLAG_WASTE:
            flagged.append({
                "campaign_name": c["campaign_name"],
                "status": c["status"],
                "cost": c["cost"],
                "clicks": c["clicks"],
                "target_cpa": c.get("target_cpa"),
                "reason": f"Spent {c['cost']} with 0 conversions",
            })
    return sorted(flagged, key=lambda x: x["cost"], reverse=True)


def find_campaigns_missing_target_cpa(campaigns: List[Dict]) -> List[Dict]:
    """
    Campaigns with no target_cpa configured at all. Reported explicitly
    so the human reviewer knows these campaigns simply cannot be evaluated
    against a target in this report — not because of a data error, but
    because they use a bidding strategy without one (e.g. Manual CPC,
    Target ROAS, or uncapped Maximize Conversions).
    """
    return [
        {"campaign_name": c["campaign_name"], "status": c["status"]}
        for c in campaigns
        if c.get("target_cpa") is None
    ]


def find_over_target_cpa_campaigns(campaigns: List[Dict]) -> List[Dict]:
    """
    THE CORE CHANGE: flags campaigns whose actual cost_per_conversion
    exceeds THEIR OWN configured target_cpa — not the account average.
    Only considers campaigns that have both a target_cpa set and at
    least one conversion (0-conversion campaigns are wasted_spend, a
    different problem).
    """
    flagged = []
    for c in campaigns:
        target = c.get("target_cpa")
        if target is None or c["conversions"] <= 0:
            continue
        if c["cost_per_conversion"] > target:
            pct_over = ((c["cost_per_conversion"] - target) / target) * 100
            flagged.append({
                "campaign_name": c["campaign_name"],
                "status": c["status"],
                "cost_per_conversion": c["cost_per_conversion"],
                "target_cpa": target,
                "pct_over_target": round(pct_over, 1),
                "reason": f"Actual CPA is {round(pct_over, 1)}% above its own target CPA of {target}",
            })
    return sorted(flagged, key=lambda x: x["pct_over_target"], reverse=True)


def find_under_target_cpa_campaigns(campaigns: List[Dict]) -> List[Dict]:
    """
    Campaigns beating their own target CPA — genuinely efficient
    performance, and candidates for increased budget/attention since
    they're proven to convert within (or under) what the account
    manager decided was an acceptable cost.
    """
    flagged = []
    for c in campaigns:
        target = c.get("target_cpa")
        if target is None or c["conversions"] <= 0:
            continue
        if c["cost_per_conversion"] < target:
            pct_under = ((target - c["cost_per_conversion"]) / target) * 100
            flagged.append({
                "campaign_name": c["campaign_name"],
                "status": c["status"],
                "cost_per_conversion": c["cost_per_conversion"],
                "target_cpa": target,
                "pct_under_target": round(pct_under, 1),
                "reason": f"Actual CPA is {round(pct_under, 1)}% under its own target CPA of {target}",
            })
    return sorted(flagged, key=lambda x: x["pct_under_target"], reverse=True)


def find_dead_campaigns(campaigns: List[Dict]) -> List[Dict]:
    """Campaigns with zero impressions — an account hygiene issue, not a spend problem."""
    flagged = []
    for c in campaigns:
        if c["impressions"] <= ZERO_ACTIVITY_IMPRESSION_THRESHOLD:
            flagged.append({
                "campaign_name": c["campaign_name"],
                "status": c["status"],
                "reason": "Zero impressions — inactive or misconfigured campaign",
            })
    return flagged


def find_significant_period_changes(campaigns: List[Dict]) -> List[Dict]:
    """
    Flags campaigns whose cost or conversions changed significantly
    versus the immediately preceding period (requires campaigns to come
    from get_campaign_data_with_comparison(), which adds cost_change_pct
    and conversions_change_pct). Campaigns without comparison data
    (e.g. new campaigns with no prior period) are silently skipped —
    there's nothing meaningful to compare.
    """
    flagged = []
    for c in campaigns:
        cost_change = c.get("cost_change_pct")
        conv_change = c.get("conversions_change_pct")

        notes = []
        if cost_change is not None and abs(cost_change) >= SIGNIFICANT_CHANGE_PCT:
            direction = "increased" if cost_change > 0 else "decreased"
            notes.append(f"cost {direction} {abs(cost_change)}% vs. the previous period")
        if conv_change is not None and abs(conv_change) >= SIGNIFICANT_CHANGE_PCT:
            direction = "increased" if conv_change > 0 else "decreased"
            notes.append(f"conversions {direction} {abs(conv_change)}% vs. the previous period")

        if notes:
            flagged.append({
                "campaign_name": c["campaign_name"],
                "status": c["status"],
                "cost_change_pct": cost_change,
                "conversions_change_pct": conv_change,
                "reason": "; ".join(notes),
            })
    return flagged


def build_findings(campaigns: List[Dict]) -> Dict:
    """
    Runs all detectors and packages everything for the LLM analyst layer.
    campaigns should come from google_ads.campaign_data.get_campaign_data()
    (target-CPA findings work) or get_campaign_data_with_comparison()
    (adds period-change findings too).
    """
    summary = account_summary(campaigns)
    return {
        "account_summary": summary,
        "wasted_spend": find_wasted_spend(campaigns),
        "over_target_cpa_campaigns": find_over_target_cpa_campaigns(campaigns),
        "under_target_cpa_campaigns": find_under_target_cpa_campaigns(campaigns),
        "campaigns_missing_target_cpa": find_campaigns_missing_target_cpa(campaigns),
        "dead_campaigns": find_dead_campaigns(campaigns),
        "significant_period_changes": find_significant_period_changes(campaigns),
    }


if __name__ == "__main__":
    import json
    from google_ads.campaign_data import get_campaign_data_with_comparison

    CUSTOMER_ID = "6485531233"

    campaigns = get_campaign_data_with_comparison(CUSTOMER_ID, period="last_28_days")
    findings = build_findings(campaigns)
    print(json.dumps(findings, indent=2))