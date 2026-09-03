"""
google_ads/ad_data.py

Sprint 5 — Ad-level data retrieval.

Pulls individual ads (headlines, descriptions, and their performance
metrics) for ONE campaign at a time, identified by campaign_id.

TWO BUGS FIXED here based on real account review:

1. STATUS: a campaign can be ENABLED (live) while individual ad groups
   or ads inside it are PAUSED — e.g. testing new copy in one ad group
   while an older one is paused, within the same live campaign. The
   original query only excluded REMOVED, so paused ads were still being
   analyzed and flagged even when "live campaigns only" was selected
   elsewhere in the dashboard. enabled_only now defaults to True and
   filters ad_group_ad.status = 'ENABLED' to match.

2. DATE RANGE: the original query had no date filter at all, so it
   always returned LIFETIME totals — regardless of which time period
   (Last 7 days, Last 28 days, etc.) was selected in the dashboard. This
   meant ad-level numbers never actually matched the period stated in
   the report. period now works the same way as campaign_data.py's,
   using the same named periods (last_7_days, last_28_days, etc.).

Only Responsive Search Ads (RSAs) are handled here, since they're the
dominant ad type in most modern accounts and expose multiple headlines/
descriptions per ad.
"""

from google.ads.googleads.client import GoogleAdsClient
from google_ads.campaign_data import _date_range_for_period


def get_ad_data(
    customer_id: str,
    campaign_id: str,
    enabled_only: bool = True,
    period: str = "last_28_days",
):
    """
    Returns a list of dicts, one per ad, within the given campaign, for
    the given time period.

    enabled_only: defaults to True — only ENABLED ads/ad groups are
        included, matching the "live only" behavior used elsewhere.
    period: one of campaign_data.PERIOD_DAYS' keys (e.g. "last_7_days",
        "last_28_days", "last_3_months"). Metrics are scoped to this
        date range, not lifetime totals.
    """
    client = GoogleAdsClient.load_from_storage("google-ads.yaml")
    google_ads_service = client.get_service("GoogleAdsService")

    start_date, end_date = _date_range_for_period(period, offset_periods=0)
    status_filter = "ad_group_ad.status = 'ENABLED'" if enabled_only else "ad_group_ad.status != 'REMOVED'"

    query = f"""
        SELECT
            ad_group_ad.ad.id,
            ad_group_ad.ad.responsive_search_ad.headlines,
            ad_group_ad.ad.responsive_search_ad.descriptions,
            ad_group_ad.status,
            ad_group.id,
            ad_group.name,
            campaign.id,
            campaign.name,
            metrics.impressions,
            metrics.clicks,
            metrics.cost_micros,
            metrics.ctr,
            metrics.conversions,
            metrics.cost_per_conversion
        FROM ad_group_ad
        WHERE campaign.id = {campaign_id}
            AND {status_filter}
            AND segments.date BETWEEN '{start_date}' AND '{end_date}'
        ORDER BY metrics.impressions DESC
    """

    response = google_ads_service.search(customer_id=customer_id, query=query)

    ads = []
    for row in response:
        ad = row.ad_group_ad.ad

        # Responsive Search Ads store headlines/descriptions as repeated
        # AdTextAsset objects — extract just the text from each.
        headlines = [asset.text for asset in ad.responsive_search_ad.headlines]
        descriptions = [asset.text for asset in ad.responsive_search_ad.descriptions]

        cost = row.metrics.cost_micros / 1_000_000
        cost_per_conversion = (
            row.metrics.cost_per_conversion / 1_000_000
            if row.metrics.cost_per_conversion
            else 0
        )

        ads.append({
            "ad_id": ad.id,
            "ad_group_id": row.ad_group.id,
            "ad_group_name": row.ad_group.name,
            "campaign_id": row.campaign.id,
            "campaign_name": row.campaign.name,
            "status": row.ad_group_ad.status.name,
            "headlines": headlines,
            "descriptions": descriptions,
            "impressions": row.metrics.impressions,
            "clicks": row.metrics.clicks,
            "cost": round(cost, 2),
            "ctr": round(row.metrics.ctr * 100, 2) if row.metrics.ctr else 0.0,
            "conversions": round(row.metrics.conversions, 2),
            "cost_per_conversion": round(cost_per_conversion, 2),
        })

    return ads


if __name__ == "__main__":
    import json

    CUSTOMER_ID = "6485531233"
    CAMPAIGN_ID = "18731997084"

    ads = get_ad_data(CUSTOMER_ID, CAMPAIGN_ID, period="last_7_days")

    print(f"\nFound {len(ads)} live, enabled ad(s) in campaign {CAMPAIGN_ID} for last_7_days\n")
    print(json.dumps(ads, indent=2))