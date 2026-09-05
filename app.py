"""
app.py

Sprint 8 — Streamlit dashboard.

This is the client-facing (or interview-demo-facing) layer that ties every
prior sprint together into one interactive tool:

  Google Ads API → deterministic analytics → LLM analyst (+ RAG history)
  → groundedness check → human-reviewable report → export

IMPORTANT: this dashboard is deliberately READ-ONLY. It never writes back
to the Google Ads account — no bid changes, no budget changes, no pausing
or enabling campaigns. It only reads data and produces a report for a
human to act on.

Includes, per PPC review feedback:
  - Time period selector (last 7/14/28 days, last 3/6 months) with
    period-over-period comparison
  - Target-CPA-based findings (per campaign, not account-average)
  - Live-only filtering, correctly applied at campaign, ad, and
    search-term level
  - Search term analysis (wasted spend by search term / match type,
    new keyword opportunities)
  - Device, location (country), and day-of-week/hour breakdowns

Run with:
    streamlit run app.py
"""

import os
import streamlit as st

# --- Secrets bootstrap for cloud deployment ---
# Locally, google-ads.yaml is a real file on disk (never committed to
# GitHub — see .gitignore) and OPENAI_API_KEY is set as an environment
# variable. On Streamlit Community Cloud, neither of those exist by
# default; instead, credentials are entered securely via the app's
# "Secrets" settings in the Streamlit dashboard (TOML format), and
# Streamlit exposes them through st.secrets at runtime.
#
# This block writes google-ads.yaml from st.secrets if it doesn't
# already exist on disk, and sets OPENAI_API_KEY as an environment
# variable from st.secrets if it isn't already set. This means the
# exact same codebase runs unchanged whether it's on your local machine
# or deployed — nothing below this block needs to know which environment
# it's in.
#
# NOTE: if no secrets have been configured at all yet (e.g. right after
# first deploying, before adding them in the app's Settings), accessing
# st.secrets raises StreamlitSecretNotFoundError rather than behaving
# like an empty dict — so this is wrapped in a try/except to fail
# gracefully with a clear message instead of a confusing traceback.
try:
    if not os.path.exists("google-ads.yaml") and "google_ads" in st.secrets:
        ga = st.secrets["google_ads"]
        with open("google-ads.yaml", "w") as f:
            f.write(f"""developer_token: "{ga['developer_token']}"
client_id: "{ga['client_id']}"
client_secret: "{ga['client_secret']}"
refresh_token: "{ga['refresh_token']}"
login_customer_id: "{ga['login_customer_id']}"
use_proto_plus: True
""")

    if "OPENAI_API_KEY" not in os.environ and "OPENAI_API_KEY" in st.secrets:
        os.environ["OPENAI_API_KEY"] = st.secrets["OPENAI_API_KEY"]
except Exception:
    # No secrets configured yet — only a problem once the user actually
    # tries to run an analysis, so don't crash the whole app on load.
    # The relevant functions will raise their own clear errors (e.g.
    # "OPENAI_API_KEY environment variable not set") when actually called.
    pass

from google_ads.campaign_data import get_campaign_data_with_comparison
from google_ads.ad_data import get_ad_data
from google_ads.search_term_data import get_search_term_data
from google_ads.segment_data import (
    get_device_performance, get_location_performance, get_city_performance, get_time_performance,
)
from analytics.waste_detector import build_findings
from analytics.ad_performance_analyzer import build_ad_findings
from analytics.search_term_analyzer import build_search_term_findings
from analytics.segment_analyzer import build_segment_findings
from llm.analyst import generate_report
from llm.groundedness_check import check_groundedness


st.set_page_config(page_title="Responsible Ads AI Analyst", layout="wide")

st.title("Responsible Ads AI Analyst")
st.caption(
    "Read-only Google Ads analysis. Every recommendation is grounded in "
    "computed data and checked for hallucinated numbers before display."
)

PERIOD_LABELS = {
    "last_7_days": "Last 7 days",
    "last_14_days": "Last 14 days",
    "last_28_days": "Last 28 days",
    "last_3_months": "Last 3 months",
    "last_6_months": "Last 6 months",
}

with st.sidebar:
    st.header("Account details")
    customer_id = st.text_input("Customer ID", placeholder="Enter your Google Ads Customer ID")
    campaign_id = st.text_input(
        "Campaign ID (optional — scopes the entire analysis to one campaign)",
        placeholder="Enter a Campaign ID (optional)",
        help="Leave blank to analyze all live campaigns in the account. "
             "Enter a Campaign ID to scope the campaign overview, ads, "
             "search terms, and segment breakdowns to just that one campaign.",
    )

    st.header("Time period")
    period_key = st.selectbox(
        "Analyze performance for:",
        options=list(PERIOD_LABELS.keys()),
        format_func=lambda k: PERIOD_LABELS[k],
        index=2,
    )
    show_comparison = st.checkbox(
        "Compare to previous period",
        value=True,
        help="Shows change vs. the immediately preceding period of the same length.",
    )

    st.header("Options")
    use_history = st.checkbox("Include historical context (RAG)", value=True)
    live_only = st.checkbox(
        "Live campaigns only",
        value=True,
        help="When checked, only ENABLED campaigns/ads/ad groups are analyzed.",
    )
    include_search_terms = st.checkbox(
        "Include search term analysis",
        value=True,
        help="Requires a Campaign ID above.",
    )
    include_segments = st.checkbox(
        "Include device / location / time breakdown",
        value=True,
        help="Analyzes performance by device, country, and day/hour. "
             "Requires a Campaign ID above.",
    )
    run_button = st.button("Run analysis", type="primary")

if run_button:
    if not customer_id:
        st.error("Please enter a Customer ID.")
        st.stop()

    with st.spinner(f"Pulling campaign data for {PERIOD_LABELS[period_key]}..."):
        try:
            campaigns = get_campaign_data_with_comparison(
                customer_id,
                campaign_id=campaign_id if campaign_id else None,
                enabled_only=live_only,
                period=period_key,
            )
        except Exception as e:
            st.error(f"Failed to fetch campaign data: {e}")
            st.stop()

    campaign_findings = build_findings(campaigns)

    if not show_comparison:
        campaign_findings.pop("significant_period_changes", None)

    ad_findings = None
    search_term_findings = None
    segment_findings = None

    if campaign_id:
        with st.spinner("Pulling ad-level data..."):
            try:
                ads = get_ad_data(customer_id, campaign_id, enabled_only=live_only, period=period_key)
                ad_findings = build_ad_findings(ads)
            except Exception as e:
                st.warning(f"Ad-level data could not be fetched, continuing without it: {e}")

        if include_search_terms:
            with st.spinner("Pulling search term data..."):
                try:
                    search_terms = get_search_term_data(
                        customer_id, campaign_id=campaign_id, period=period_key, enabled_only=live_only
                    )
                    search_term_findings = build_search_term_findings(search_terms)
                except Exception as e:
                    st.warning(f"Search term data could not be fetched, continuing without it: {e}")

        if include_segments:
            with st.spinner("Pulling device / location / time data..."):
                try:
                    device_rows = get_device_performance(customer_id, campaign_id, period=period_key)
                    location_rows = get_location_performance(customer_id, campaign_id, period=period_key)
                    city_rows = get_city_performance(customer_id, campaign_id, period=period_key)
                    time_rows = get_time_performance(customer_id, campaign_id, period=period_key)
                    segment_findings = build_segment_findings(
                        device_rows=device_rows,
                        location_rows=location_rows,
                        city_rows=city_rows,
                        time_rows=time_rows,
                    )
                except Exception as e:
                    st.warning(f"Segment data could not be fetched, continuing without it: {e}")
    else:
        if include_search_terms:
            st.info("Search term analysis requires a Campaign ID — skipping it for this run.")
        if include_segments:
            st.info("Device/location/time analysis requires a Campaign ID — skipping it for this run.")

    with st.spinner("Generating analyst report..."):
        try:
            report, past_reports_used = generate_report(
                campaign_findings=campaign_findings,
                ad_findings=ad_findings,
                search_term_findings=search_term_findings,
                segment_findings=segment_findings,
                customer_id=customer_id,
                period=PERIOD_LABELS[period_key],
                use_history=use_history,
                return_past_reports=True,
            )
        except Exception as e:
            st.error(f"Failed to generate report: {e}")
            st.stop()

    combined_findings_for_check = {"campaign_findings": campaign_findings}
    if ad_findings:
        combined_findings_for_check["ad_findings"] = ad_findings
    if search_term_findings:
        combined_findings_for_check["search_term_findings"] = search_term_findings
    if segment_findings:
        combined_findings_for_check["segment_findings"] = segment_findings
    if past_reports_used:
        combined_findings_for_check["past_reports"] = past_reports_used
    groundedness_result = check_groundedness(report, combined_findings_for_check)

    if groundedness_result["is_grounded"]:
        st.success(
            f"✓ Groundedness check passed — all {groundedness_result['checked_numbers']} "
            f"cited numbers trace back to source data."
        )
    else:
        st.error(
            "⚠ Groundedness check FAILED — the following numbers could not be verified "
            "against source data. Do not send this report to a client without manual review:\n\n"
            + ", ".join(groundedness_result["unverified_numbers"])
        )

    summary = campaign_findings["account_summary"]
    st.caption(f"Showing: **{PERIOD_LABELS[period_key]}**" + (" (vs. previous period)" if show_comparison else ""))
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Live campaigns", summary["total_campaigns"])
    col2.metric("Total spend", f"{summary['total_cost']:,}")
    col3.metric("Total conversions", f"{summary['total_conversions']:,}")
    col4.metric(
        "Campaigns over target CPA",
        len(campaign_findings.get("over_target_cpa_campaigns", [])),
    )

    if search_term_findings:
        st.divider()
        st_summary = search_term_findings["search_term_summary"]
        wasted_terms = search_term_findings.get("wasted_search_terms", [])
        wasted_terms_count = len(wasted_terms)
        wasted_total = sum(t["cost"] for t in wasted_terms)
        col1, col2, col3 = st.columns(3)
        col1.metric("Search terms analyzed", st_summary["total_search_terms"])
        col2.metric("Wasted search terms", wasted_terms_count)
        col3.metric("Wasted spend (search terms)", f"{round(wasted_total, 2):,}")

        if wasted_terms:
            st.markdown("**Wasted search terms** — spent money with zero conversions:")
            wasted_table = [
                {
                    "Search term": t["search_term"],
                    "Matched keyword": t["matched_keyword"],
                    "Match type": t["match_type"],
                    "Cost": t["cost"],
                    "Clicks": t["clicks"],
                }
                for t in wasted_terms
            ]
            st.dataframe(wasted_table, use_container_width=True, hide_index=True)

    if segment_findings:
        st.divider()
        col1, col2, col3 = st.columns(3)
        best_device = segment_findings.get("device_findings", {}).get("best_performing_device")

        best_city = None
        best_cities = segment_findings.get("city_findings", {}).get("best_performing_cities", [])
        if best_cities:
            best_city = best_cities[0]

        best_day = segment_findings.get("time_findings", {}).get("best_performing_day")

        col1.metric("Best device (by CPA)", best_device["device"] if best_device else "—")
        col2.metric("Best city (by CPA)", best_city["city"] if best_city else "—")
        col3.metric("Best day (by CPA)", best_day["day_of_week"] if best_day else "—")

    st.subheader("Analyst Report")
    st.markdown(report)

    with st.expander("View raw findings data (what the LLM was given)"):
        st.json(combined_findings_for_check)

    st.download_button(
        label="Download report (Markdown)",
        data=report,
        file_name=f"ads_report_{customer_id}_{period_key}.md",
        mime="text/markdown",
    )

else:
    st.info("Enter a Customer ID in the sidebar, choose a time period, and click **Run analysis** to begin.")