"""
google_ads/campaign_data.py

Pulls campaign-level performance data, with two major additions based on
real PPC review feedback:

1. TARGET CPA (not account average): each campaign can have its own
   configured Target CPA in Google Ads, set by the account manager based
   on what's actually profitable for that market/client. Comparing a
   campaign's performance to the ACCOUNT-WIDE average CPA is misleading —
   a campaign targeting a competitive market might have a target CPA of
   150, while another has a target of 40. The correct comparison is
   "is this campaign hitting ITS OWN target", not "is it above/below
   everyone else's average."

   Target CPA lives in different fields depending on the campaign's
   bidding strategy:
     - Target CPA strategy:        campaign.target_cpa.target_cpa_micros
     - Maximize Conversions (with
       a target set):              campaign.maximize_conversions.target_cpa_micros
   A campaign may have NEITHER (e.g. Manual CPC, Target ROAS, or Maximize
   Conversions with no cap) — in that case target_cpa is None, and the
   analytics layer should say so explicitly rather than inventing one.

2. DATE RANGES + PERIOD COMPARISON: instead of always pulling "all time"
   data, this supports named periods (last 7/14/28 days, last 3/6 months)
   and can also fetch the immediately preceding period of the same length,
   so callers can compute period-over-period change.
"""

from datetime import date, timedelta
from google.ads.googleads.client import GoogleAdsClient


PERIOD_DAYS = {
    "last_7_days": 7,
    "last_14_days": 14,
    "last_28_days": 28,
    "last_3_months": 91,
    "last_6_months": 182,
}


def _date_range_for_period(period: str, offset_periods: int = 0):
    """
    Returns (start_date, end_date) as 'YYYY-MM-DD' strings for the given
    named period. offset_periods=0 is the current/most recent period;
    offset_periods=1 is the immediately preceding period of the same
    length (for period-over-period comparison).
    """
    if period not in PERIOD_DAYS:
        raise ValueError(f"Unknown period '{period}'. Valid options: {list(PERIOD_DAYS.keys())}")

    days = PERIOD_DAYS[period]
    yesterday = date.today() - timedelta(days=1)

    end_date = yesterday - timedelta(days=offset_periods * days)
    start_date = end_date - timedelta(days=days - 1)

    return start_date.isoformat(), end_date.isoformat()


def _extract_target_cpa(campaign_row) -> float:
    """
    Reads target CPA from whichever field applies to this campaign's
    bidding strategy. Returns None if the campaign has no target CPA
    configured (e.g. Manual CPC, Target ROAS, or uncapped Maximize
    Conversions).
    """
    campaign = campaign_row.campaign

    target_cpa_micros = None
    if campaign.target_cpa and campaign.target_cpa.target_cpa_micros:
        target_cpa_micros = campaign.target_cpa.target_cpa_micros
    elif campaign.maximize_conversions and campaign.maximize_conversions.target_cpa_micros:
        target_cpa_micros = campaign.maximize_conversions.target_cpa_micros

    if target_cpa_micros:
        return round(target_cpa_micros / 1_000_000, 2)
    return None


def get_campaign_data(
    customer_id: str,
    campaign_id: str = None,
    enabled_only: bool = True,
    period: str = "last_28_days",
    offset_periods: int = 0,
):
    """
    Pulls campaign-level performance data for a specific date range.

    campaign_id: optional — restrict to just this one campaign. If None,
        returns all campaigns matching the other filters (this was
        previously the only behavior, which meant typing a Campaign ID
        in the dashboard had no effect on the campaign-level overview,
        even though it correctly scoped ad-level/search-term/segment
        data. Now it consistently scopes everything to one campaign
        when provided.
    enabled_only: defaults to True — paused campaigns are excluded by
        default, per review feedback.
    period: one of PERIOD_DAYS' keys.
    offset_periods: 0 for current period, 1 for the immediately preceding
        period of the same length.
    """
    client = GoogleAdsClient.load_from_storage("google-ads.yaml")
    google_ads_service = client.get_service("GoogleAdsService")

    start_date, end_date = _date_range_for_period(period, offset_periods)
    status_filter = "campaign.status = 'ENABLED'" if enabled_only else "campaign.status != 'REMOVED'"
    campaign_filter = f"AND campaign.id = {campaign_id}" if campaign_id else ""

    query = f"""
        SELECT
            campaign.id,
            campaign.name,
            campaign.status,
            campaign.target_cpa.target_cpa_micros,
            campaign.maximize_conversions.target_cpa_micros,
            metrics.impressions,
            metrics.clicks,
            metrics.cost_micros,
            metrics.ctr,
            metrics.average_cpc,
            metrics.conversions,
            metrics.cost_per_conversion
        FROM campaign
        WHERE {status_filter}
            AND segments.date BETWEEN '{start_date}' AND '{end_date}'
            {campaign_filter}
        ORDER BY metrics.impressions DESC
    """

    response = google_ads_service.search(customer_id=customer_id, query=query)

    campaigns = []
    for row in response:
        cost = row.metrics.cost_micros / 1_000_000
        average_cpc = row.metrics.average_cpc / 1_000_000
        cost_per_conversion = (
            row.metrics.cost_per_conversion / 1_000_000
            if row.metrics.cost_per_conversion
            else 0
        )

        campaigns.append({
            "campaign_id": row.campaign.id,
            "campaign_name": row.campaign.name,
            "status": row.campaign.status.name,
            "target_cpa": _extract_target_cpa(row),
            "impressions": row.metrics.impressions,
            "clicks": row.metrics.clicks,
            "cost": round(cost, 2),
            "ctr": round(row.metrics.ctr * 100, 2),
            "average_cpc": round(average_cpc, 2),
            "conversions": round(row.metrics.conversions, 2),
            "cost_per_conversion": round(cost_per_conversion, 2),
        })

    return campaigns


def get_campaign_data_with_comparison(
    customer_id: str,
    campaign_id: str = None,
    enabled_only: bool = True,
    period: str = "last_28_days",
):
    """
    Fetches both the current period and the immediately preceding period
    of the same length, merged by campaign_id so each row includes
    previous-period comparison figures.

    campaign_id: optional — restrict to just this one campaign (see
        get_campaign_data's docstring for why this matters).

    Adds to each campaign dict:
        "previous_cost", "previous_conversions", "previous_cost_per_conversion"
        "cost_change_pct", "conversions_change_pct"
    None is used (not 0) when there's no prior-period data to compare against.
    """
    current = get_campaign_data(customer_id, campaign_id=campaign_id, enabled_only=enabled_only, period=period, offset_periods=0)
    previous = get_campaign_data(customer_id, campaign_id=campaign_id, enabled_only=enabled_only, period=period, offset_periods=1)

    previous_by_id = {c["campaign_id"]: c for c in previous}

    def pct_change(old, new):
        if old is None or old == 0:
            return None
        return round(((new - old) / old) * 100, 1)

    merged = []
    for c in current:
        prev = previous_by_id.get(c["campaign_id"])
        c = dict(c)
        if prev:
            c["previous_cost"] = prev["cost"]
            c["previous_conversions"] = prev["conversions"]
            c["previous_cost_per_conversion"] = prev["cost_per_conversion"]
            c["cost_change_pct"] = pct_change(prev["cost"], c["cost"])
            c["conversions_change_pct"] = pct_change(prev["conversions"], c["conversions"])
        else:
            c["previous_cost"] = None
            c["previous_conversions"] = None
            c["previous_cost_per_conversion"] = None
            c["cost_change_pct"] = None
            c["conversions_change_pct"] = None
        merged.append(c)

    return merged


if __name__ == "__main__":
    import json

    CUSTOMER_ID = "6485531233"

    print("=== Current period (last_28_days) with comparison to previous period ===")
    data = get_campaign_data_with_comparison(CUSTOMER_ID, period="last_28_days")
    print(json.dumps(data, indent=2))