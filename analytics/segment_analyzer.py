"""
analytics/segment_analyzer.py

Deterministic analysis of device, time-of-day/day-of-week, and location
performance data from google_ads/segment_data.py.

Same ground-truth-first approach as the rest of the project — this file
only aggregates and compares; no AI involved.
"""

from typing import List, Dict
from collections import defaultdict


MIN_COST_TO_CONSIDER = 10.0  # ignore segments with too little spend to judge fairly


def _aggregate_by_key(rows: List[Dict], key_field: str) -> Dict[str, Dict]:
    """
    Groups rows by a key field (e.g. "device", "country") and sums their
    metrics, then computes CPA/CTR for each group.
    """
    grouped = defaultdict(lambda: {"impressions": 0, "clicks": 0, "cost": 0.0, "conversions": 0.0})

    for row in rows:
        key = row[key_field]
        grouped[key]["impressions"] += row["impressions"]
        grouped[key]["clicks"] += row["clicks"]
        grouped[key]["cost"] += row["cost"]
        grouped[key]["conversions"] += row["conversions"]

    results = {}
    for key, totals in grouped.items():
        cpa = (totals["cost"] / totals["conversions"]) if totals["conversions"] else None
        ctr = (totals["clicks"] / totals["impressions"] * 100) if totals["impressions"] else None
        results[key] = {
            "impressions": totals["impressions"],
            "clicks": totals["clicks"],
            "cost": round(totals["cost"], 2),
            "conversions": round(totals["conversions"], 2),
            "cpa": round(cpa, 2) if cpa is not None else None,
            "ctr_pct": round(ctr, 2) if ctr is not None else None,
        }
    return results


def analyze_device_performance(device_rows: List[Dict]) -> Dict:
    """
    Aggregates across all campaigns by device, and identifies which
    device converts most/least efficiently.
    """
    by_device = _aggregate_by_key(device_rows, "device")

    eligible = {d: v for d, v in by_device.items() if v["cost"] >= MIN_COST_TO_CONSIDER and v["cpa"] is not None}
    best_device = min(eligible, key=lambda d: eligible[d]["cpa"]) if eligible else None
    worst_device = max(eligible, key=lambda d: eligible[d]["cpa"]) if eligible else None

    return {
        "performance_by_device": by_device,
        "best_performing_device": {"device": best_device, **by_device[best_device]} if best_device else None,
        "worst_performing_device": {"device": worst_device, **by_device[worst_device]} if worst_device and worst_device != best_device else None,
    }


def analyze_location_performance(location_rows: List[Dict], top_n: int = 5) -> Dict:
    """
    Aggregates across all campaigns by country, and surfaces the best
    and worst-performing locations by CPA (among locations with
    meaningful spend).
    """
    by_country = _aggregate_by_key(location_rows, "country")

    eligible = {c: v for c, v in by_country.items() if v["cost"] >= MIN_COST_TO_CONSIDER and v["cpa"] is not None}
    sorted_by_cpa = sorted(eligible.items(), key=lambda item: item[1]["cpa"])

    best = [{"country": c, **v} for c, v in sorted_by_cpa[:top_n]]
    worst = [{"country": c, **v} for c, v in sorted_by_cpa[-top_n:]] if len(sorted_by_cpa) > top_n else []

    return {
        "performance_by_country": by_country,
        "best_performing_locations": best,
        "worst_performing_locations": worst,
    }


def analyze_city_performance(city_rows: List[Dict], top_n: int = 5) -> Dict:
    """
    Aggregates by (city, country) pair — since city names alone aren't
    unique (many countries have a city of the same name) — and surfaces
    the best/worst performing cities by CPA among those with meaningful
    spend. City-level data is typically sparser than country-level, so
    the MIN_COST_TO_CONSIDER filter matters more here to avoid drawing
    conclusions from a handful of clicks.
    """
    keyed_rows = [{**row, "_city_country": f"{row['city']}, {row['country']}"} for row in city_rows]
    by_city = _aggregate_by_key(keyed_rows, "_city_country")

    eligible = {c: v for c, v in by_city.items() if v["cost"] >= MIN_COST_TO_CONSIDER and v["cpa"] is not None}
    sorted_by_cpa = sorted(eligible.items(), key=lambda item: item[1]["cpa"])

    best = [{"city": c, **v} for c, v in sorted_by_cpa[:top_n]]
    worst = [{"city": c, **v} for c, v in sorted_by_cpa[-top_n:]] if len(sorted_by_cpa) > top_n else []

    return {
        "performance_by_city": by_city,
        "best_performing_cities": best,
        "worst_performing_cities": worst,
    }


def analyze_time_performance(time_rows: List[Dict]) -> Dict:
    """
    Aggregates by day-of-week and by hour separately (rather than every
    day+hour combination, which is usually too granular to act on),
    and identifies the best and worst-converting windows.
    """
    by_day = _aggregate_by_key(time_rows, "day_of_week")
    by_hour = _aggregate_by_key(time_rows, "hour")

    day_eligible = {d: v for d, v in by_day.items() if v["cost"] >= MIN_COST_TO_CONSIDER and v["cpa"] is not None}
    hour_eligible = {h: v for h, v in by_hour.items() if v["cost"] >= MIN_COST_TO_CONSIDER and v["cpa"] is not None}

    best_day = min(day_eligible, key=lambda d: day_eligible[d]["cpa"]) if day_eligible else None
    worst_day = max(day_eligible, key=lambda d: day_eligible[d]["cpa"]) if day_eligible else None
    best_hour = min(hour_eligible, key=lambda h: hour_eligible[h]["cpa"]) if hour_eligible else None
    worst_hour = max(hour_eligible, key=lambda h: hour_eligible[h]["cpa"]) if hour_eligible else None

    return {
        "performance_by_day_of_week": by_day,
        "performance_by_hour": by_hour,
        "best_performing_day": {"day_of_week": best_day, **by_day[best_day]} if best_day else None,
        "worst_performing_day": {"day_of_week": worst_day, **by_day[worst_day]} if worst_day and worst_day != best_day else None,
        "best_performing_hour": {"hour": best_hour, **by_hour[best_hour]} if best_hour is not None else None,
        "worst_performing_hour": {"hour": worst_hour, **by_hour[worst_hour]} if worst_hour is not None and worst_hour != best_hour else None,
    }


def build_segment_findings(device_rows: List[Dict] = None, location_rows: List[Dict] = None, city_rows: List[Dict] = None, time_rows: List[Dict] = None) -> Dict:
    """
    Combines whichever segment breakdowns are provided into one findings
    dict for the LLM analyst layer. Any input may be None if that
    breakdown wasn't requested/available.
    """
    findings = {}
    if device_rows:
        findings["device_findings"] = analyze_device_performance(device_rows)
    if location_rows:
        findings["location_findings"] = analyze_location_performance(location_rows)
    if city_rows:
        findings["city_findings"] = analyze_city_performance(city_rows)
    if time_rows:
        findings["time_findings"] = analyze_time_performance(time_rows)
    return findings


if __name__ == "__main__":
    import json
    from google_ads.segment_data import get_device_performance, get_location_performance

    CUSTOMER_ID = "6485531233"
    CAMPAIGN_ID = "18731997084"

    device_rows = get_device_performance(CUSTOMER_ID, CAMPAIGN_ID, period="last_28_days")
    location_rows = get_location_performance(CUSTOMER_ID, CAMPAIGN_ID, period="last_28_days")

    findings = build_segment_findings(device_rows=device_rows, location_rows=location_rows)
    print(json.dumps(findings, indent=2))