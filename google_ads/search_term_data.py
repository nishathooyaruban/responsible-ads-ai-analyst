"""
google_ads/search_term_data.py

Pulls SEARCH TERM data — the actual queries real users typed that
triggered your ads to show. This is different from "keywords" (what you
bid on) — a search term is what someone actually searched, which Google
matched to one of your keywords based on match type (EXACT, PHRASE, or
BROAD).

WHY THIS MATTERS: this is the most direct way to answer "which keyword
is wasting budget on irrelevant traffic."

FIX (per real account review): a search term's spend is attributed to
the ad group it belongs to. If that ad group is PAUSED, its historical
search-term spend shouldn't be analyzed as a "current" issue any more
than a paused ad's performance should — enabled_only now filters to
ad_group.status = 'ENABLED' by default, matching the same fix applied
to ad_data.py.

Supports the same period system as campaign_data.py and pulls the
matched keyword text + match type, so match-type-level patterns can be
analyzed too.
"""

from google.ads.googleads.client import GoogleAdsClient
from google_ads.campaign_data import _date_range_for_period


def get_search_term_data(
    customer_id: str,
    campaign_id: str = None,
    period: str = "last_28_days",
    min_impressions: int = 1,
    enabled_only: bool = True,
):
    """
    Returns a list of dicts, one per search term, for the given period.

    campaign_id: optional — restrict to one campaign. If None, pulls
        search terms across the whole account for the given period.
    min_impressions: filters out extremely low-volume search terms.
    enabled_only: defaults to True — only search terms attributed to an
        ENABLED ad group are included, so paused ad groups' historical
        spend isn't analyzed as a current issue.
    """
    client = GoogleAdsClient.load_from_storage("google-ads.yaml")
    google_ads_service = client.get_service("GoogleAdsService")

    start_date, end_date = _date_range_for_period(period, offset_periods=0)

    campaign_filter = f"AND campaign.id = {campaign_id}" if campaign_id else ""
    status_filter = "AND ad_group.status = 'ENABLED'" if enabled_only else ""

    query = f"""
        SELECT
            search_term_view.search_term,
            segments.keyword.info.text,
            segments.keyword.info.match_type,
            campaign.id,
            campaign.name,
            ad_group.id,
            ad_group.name,
            ad_group.status,
            metrics.impressions,
            metrics.clicks,
            metrics.cost_micros,
            metrics.conversions,
            metrics.cost_per_conversion
        FROM search_term_view
        WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'
            {campaign_filter}
            {status_filter}
        ORDER BY metrics.cost_micros DESC
    """

    response = google_ads_service.search(customer_id=customer_id, query=query)

    search_terms = []
    for row in response:
        if row.metrics.impressions < min_impressions:
            continue

        cost = row.metrics.cost_micros / 1_000_000
        cost_per_conversion = (
            row.metrics.cost_per_conversion / 1_000_000
            if row.metrics.cost_per_conversion
            else 0
        )

        search_terms.append({
            "search_term": row.search_term_view.search_term,
            "matched_keyword": row.segments.keyword.info.text,
            "match_type": row.segments.keyword.info.match_type.name,
            "campaign_id": row.campaign.id,
            "campaign_name": row.campaign.name,
            "ad_group_id": row.ad_group.id,
            "ad_group_name": row.ad_group.name,
            "impressions": row.metrics.impressions,
            "clicks": row.metrics.clicks,
            "cost": round(cost, 2),
            "conversions": round(row.metrics.conversions, 2),
            "cost_per_conversion": round(cost_per_conversion, 2),
        })

    return search_terms


if __name__ == "__main__":
    import json

    CUSTOMER_ID = "6485531233"
    CAMPAIGN_ID = "18731997084"

    terms = get_search_term_data(CUSTOMER_ID, campaign_id=CAMPAIGN_ID, period="last_7_days")

    print(f"\nFound {len(terms)} search term(s) from ENABLED ad groups, last_7_days\n")
    print(json.dumps(terms, indent=2))