"""
google_ads/segment_data.py

Pulls three additional breakdowns requested during PPC review:

1. DEVICE performance (Mobile / Desktop / Tablet) — straightforward,
   uses the `campaign` resource segmented by segments.device.

2. DAY-OF-WEEK / HOUR performance ("best time and date") — uses the
   `campaign` resource segmented by segments.day_of_week and segments.hour.

3. LOCATION performance — more complex than the other two. Google's
   geographic_view/user_location_view resources report metrics keyed by
   a numeric location ID (country_criterion_id), NOT a human-readable
   city/country name. To show "UAE" instead of "2784", a second lookup
   against the geo_target_constant resource is required to resolve IDs
   to names. This file does that resolution automatically.

   NOTE: geographic_view/user_location_view report at COUNTRY level by
   default. True CITY-level breakdowns require segmenting by
   segments.geo_target_city, which needs the same ID-to-name resolution
   applied to a different geo target type. This file currently
   implements country-level location reporting; city-level can be added
   the same way if needed later.
"""

from google.ads.googleads.client import GoogleAdsClient
from google_ads.campaign_data import _date_range_for_period


def get_device_performance(customer_id: str, campaign_id: str = None, period: str = "last_28_days"):
    """
    Returns a list of dicts, one per (campaign, device) combination:
        {
            "campaign_name": "...",
            "device": "MOBILE" / "DESKTOP" / "TABLET" / "CONNECTED_TV" / "OTHER",
            "impressions": int, "clicks": int, "cost": float,
            "conversions": float, "cost_per_conversion": float,
        }
    """
    client = GoogleAdsClient.load_from_storage("google-ads.yaml")
    service = client.get_service("GoogleAdsService")
    start_date, end_date = _date_range_for_period(period, offset_periods=0)
    campaign_filter = f"AND campaign.id = {campaign_id}" if campaign_id else ""

    query = f"""
        SELECT
            campaign.name,
            segments.device,
            metrics.impressions,
            metrics.clicks,
            metrics.cost_micros,
            metrics.conversions,
            metrics.cost_per_conversion
        FROM campaign
        WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'
            {campaign_filter}
        ORDER BY metrics.cost_micros DESC
    """

    response = service.search(customer_id=customer_id, query=query)
    results = []
    for row in response:
        cost = row.metrics.cost_micros / 1_000_000
        cpc_conv = row.metrics.cost_per_conversion / 1_000_000 if row.metrics.cost_per_conversion else 0
        results.append({
            "campaign_name": row.campaign.name,
            "device": row.segments.device.name,
            "impressions": row.metrics.impressions,
            "clicks": row.metrics.clicks,
            "cost": round(cost, 2),
            "conversions": round(row.metrics.conversions, 2),
            "cost_per_conversion": round(cpc_conv, 2),
        })
    return results


def get_time_performance(customer_id: str, campaign_id: str = None, period: str = "last_28_days"):
    """
    Returns a list of dicts, one per (campaign, day_of_week, hour):
        {
            "campaign_name": "...",
            "day_of_week": "MONDAY" / ... / "SUNDAY",
            "hour": 0-23,
            "impressions": int, "clicks": int, "cost": float,
            "conversions": float, "cost_per_conversion": float,
        }
    NOTE: this can produce a large number of rows (up to 7 days x 24
    hours x campaigns) — the analyzer aggregates this down to the
    meaningful patterns rather than the caller needing to.
    """
    client = GoogleAdsClient.load_from_storage("google-ads.yaml")
    service = client.get_service("GoogleAdsService")
    start_date, end_date = _date_range_for_period(period, offset_periods=0)
    campaign_filter = f"AND campaign.id = {campaign_id}" if campaign_id else ""

    query = f"""
        SELECT
            campaign.name,
            segments.day_of_week,
            segments.hour,
            metrics.impressions,
            metrics.clicks,
            metrics.cost_micros,
            metrics.conversions,
            metrics.cost_per_conversion
        FROM campaign
        WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'
            {campaign_filter}
    """

    response = service.search(customer_id=customer_id, query=query)
    results = []
    for row in response:
        cost = row.metrics.cost_micros / 1_000_000
        cpc_conv = row.metrics.cost_per_conversion / 1_000_000 if row.metrics.cost_per_conversion else 0
        results.append({
            "campaign_name": row.campaign.name,
            "day_of_week": row.segments.day_of_week.name,
            "hour": row.segments.hour,
            "impressions": row.metrics.impressions,
            "clicks": row.metrics.clicks,
            "cost": round(cost, 2),
            "conversions": round(row.metrics.conversions, 2),
            "cost_per_conversion": round(cpc_conv, 2),
        })
    return results


def _resolve_geo_target_names(client, geo_target_ids: list) -> dict:
    """
    Looks up human-readable names for a list of geo_target_constant IDs
    (e.g. 2784 -> "United Arab Emirates"). Google Ads does not return
    location names directly in geographic_view results — only numeric
    IDs — so this second query is required to make the output readable.
    """
    if not geo_target_ids:
        return {}

    service = client.get_service("GoogleAdsService")
    ids_str = ", ".join(str(i) for i in geo_target_ids)

    query = f"""
        SELECT geo_target_constant.id, geo_target_constant.name
        FROM geo_target_constant
        WHERE geo_target_constant.id IN ({ids_str})
    """
    # geo_target_constant is a customer-agnostic resource; querying it
    # against any valid customer_id under the manager account works.
    response = service.search(customer_id=client.login_customer_id, query=query)

    names = {}
    for row in response:
        names[row.geo_target_constant.id] = row.geo_target_constant.name
    return names


def get_location_performance(customer_id: str, campaign_id: str = None, period: str = "last_28_days"):
    """
    Returns a list of dicts, one per (campaign, country):
        {
            "campaign_name": "...",
            "country": "United Arab Emirates",   # resolved from ID automatically
            "impressions": int, "clicks": int, "cost": float,
            "conversions": float, "cost_per_conversion": float,
        }
    Country-level only — see module docstring for city-level notes.
    """
    client = GoogleAdsClient.load_from_storage("google-ads.yaml")
    service = client.get_service("GoogleAdsService")
    start_date, end_date = _date_range_for_period(period, offset_periods=0)
    campaign_filter = f"AND campaign.id = {campaign_id}" if campaign_id else ""

    query = f"""
        SELECT
            campaign.id,
            campaign.name,
            geographic_view.country_criterion_id,
            metrics.impressions,
            metrics.clicks,
            metrics.cost_micros,
            metrics.conversions,
            metrics.cost_per_conversion
        FROM geographic_view
        WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'
            {campaign_filter}
        ORDER BY metrics.cost_micros DESC
    """

    response = service.search(customer_id=customer_id, query=query)
    raw_rows = []
    country_ids = set()
    for row in response:
        country_id = row.geographic_view.country_criterion_id
        country_ids.add(country_id)
        cost = row.metrics.cost_micros / 1_000_000
        cpc_conv = row.metrics.cost_per_conversion / 1_000_000 if row.metrics.cost_per_conversion else 0
        raw_rows.append({
            "campaign_name": row.campaign.name,
            "_country_id": country_id,
            "impressions": row.metrics.impressions,
            "clicks": row.metrics.clicks,
            "cost": round(cost, 2),
            "conversions": round(row.metrics.conversions, 2),
            "cost_per_conversion": round(cpc_conv, 2),
        })

    id_to_name = _resolve_geo_target_names(client, list(country_ids))

    results = []
    for row in raw_rows:
        country_id = row.pop("_country_id")
        row["country"] = id_to_name.get(country_id, f"Unknown (ID {country_id})")
        results.append(row)

    return results


def _resolve_geo_target_names_by_resource_name(client, resource_names: list) -> dict:
    """
    Resolves city-level geo target names, which come back as RESOURCE_NAME
    strings (e.g. "geoTargetConstants/1023191") rather than plain integer
    IDs like the country field. This needs a different filter format
    (resource_name IN (...) with quoted strings) than the ID-based country
    lookup above.
    """
    if not resource_names:
        return {}

    service = client.get_service("GoogleAdsService")
    names_str = ", ".join(f"'{rn}'" for rn in resource_names)

    query = f"""
        SELECT geo_target_constant.resource_name, geo_target_constant.name
        FROM geo_target_constant
        WHERE geo_target_constant.resource_name IN ({names_str})
    """
    response = service.search(customer_id=client.login_customer_id, query=query)

    names = {}
    for row in response:
        names[row.geo_target_constant.resource_name] = row.geo_target_constant.name
    return names


def get_city_performance(customer_id: str, campaign_id: str = None, period: str = "last_28_days"):
    """
    Returns a list of dicts, one per (campaign, city, country):
        {
            "campaign_name": "...",
            "city": "Dubai",             # resolved automatically
            "country": "United Arab Emirates",
            "impressions": int, "clicks": int, "cost": float,
            "conversions": float, "cost_per_conversion": float,
        }

    NOTE: city-level data can be sparse/noisy on smaller accounts — many
    rows may represent only a handful of clicks. The analyzer applies a
    minimum-spend threshold before drawing conclusions, same as the
    other segment breakdowns.
    """
    client = GoogleAdsClient.load_from_storage("google-ads.yaml")
    service = client.get_service("GoogleAdsService")
    start_date, end_date = _date_range_for_period(period, offset_periods=0)
    campaign_filter = f"AND campaign.id = {campaign_id}" if campaign_id else ""

    query = f"""
        SELECT
            campaign.id,
            campaign.name,
            geographic_view.country_criterion_id,
            segments.geo_target_city,
            metrics.impressions,
            metrics.clicks,
            metrics.cost_micros,
            metrics.conversions,
            metrics.cost_per_conversion
        FROM geographic_view
        WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'
            {campaign_filter}
        ORDER BY metrics.cost_micros DESC
    """

    response = service.search(customer_id=customer_id, query=query)
    raw_rows = []
    country_ids = set()
    city_resource_names = set()

    for row in response:
        country_id = row.geographic_view.country_criterion_id
        city_resource_name = row.segments.geo_target_city
        country_ids.add(country_id)
        if city_resource_name:
            city_resource_names.add(city_resource_name)

        cost = row.metrics.cost_micros / 1_000_000
        cpc_conv = row.metrics.cost_per_conversion / 1_000_000 if row.metrics.cost_per_conversion else 0
        raw_rows.append({
            "campaign_name": row.campaign.name,
            "_country_id": country_id,
            "_city_resource_name": city_resource_name,
            "impressions": row.metrics.impressions,
            "clicks": row.metrics.clicks,
            "cost": round(cost, 2),
            "conversions": round(row.metrics.conversions, 2),
            "cost_per_conversion": round(cpc_conv, 2),
        })

    country_id_to_name = _resolve_geo_target_names(client, list(country_ids))
    city_name_lookup = _resolve_geo_target_names_by_resource_name(client, list(city_resource_names))

    results = []
    for row in raw_rows:
        country_id = row.pop("_country_id")
        city_resource_name = row.pop("_city_resource_name")
        row["country"] = country_id_to_name.get(country_id, f"Unknown (ID {country_id})")
        row["city"] = city_name_lookup.get(city_resource_name, "Unknown city") if city_resource_name else "Unknown city"
        results.append(row)

    return results


if __name__ == "__main__":
    import json

    CUSTOMER_ID = "6485531233"
    CAMPAIGN_ID = "18731997084"

    print("=== Device performance ===")
    print(json.dumps(get_device_performance(CUSTOMER_ID, CAMPAIGN_ID, period="last_28_days"), indent=2))

    print("\n=== Location performance (country) ===")
    print(json.dumps(get_location_performance(CUSTOMER_ID, CAMPAIGN_ID, period="last_28_days"), indent=2))

    print("\n=== Location performance (city) ===")
    print(json.dumps(get_city_performance(CUSTOMER_ID, CAMPAIGN_ID, period="last_28_days"), indent=2))