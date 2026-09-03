"""
llm/analyst.py

The LLM layer — Sprint 6.

CRITICAL DESIGN RULE: the LLM NEVER sees raw campaign data. It only ever
receives the structured `findings` JSON produced by analytics/waste_detector.py.
This means the LLM's job is strictly to explain, prioritize, and write in
plain English — it cannot invent a number that isn't already in `findings`,
because it never had access to anything else.

This file has two responsibilities:
  1. build_prompt()   — turn the findings JSON into a tightly-scoped prompt
  2. generate_report() — call the LLM and return its written analysis

The groundedness check (verifying the LLM's output doesn't contain numbers
NOT present in findings) lives in a separate file: llm/groundedness_check.py.
Every report should be checked before being shown to a human reviewer.
"""

import os
import json
from typing import Dict

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser


SYSTEM_PROMPT = """You are a Google Ads performance analyst.

You will be given a JSON object called FINDINGS, which contains pre-computed,
verified metrics and flags for one client's Google Ads account, for a
SPECIFIC TIME PERIOD (e.g. last 7 days, last 28 days, last 3 months). The
period being analyzed will be stated explicitly in FINDINGS as "period".
Always mention the period near the start of your report so the reader
knows what timeframe the analysis covers.

FINDINGS may contain two sections: "campaign_findings" (account and
campaign-level allocation, spend, and CPA) and "ad_findings" (ad-level
performance within a specific campaign, including headlines/descriptions
and their CTR/CPA). It may also contain "search_term_findings" (actual
search queries that triggered ads, matched keyword, and match type) and
"segment_findings" (performance broken down by device, location/country,
and day-of-week/hour). Any combination of these sections may be present.

NOTE ON FIELD NAMES: the quoted names below (like "over_target_cpa_campaigns"
or "wasted_spend_by_match_type") are internal data-schema labels used only
so you understand the structure of FINDINGS. NEVER reproduce these exact
snake_case names in your written report — always translate them into
plain English (e.g. "the campaigns exceeding their target CPA" or "the
wasted-spend breakdown by keyword match type").

SEGMENT FINDINGS: "segment_findings" may contain "device_findings"
(performance by MOBILE/DESKTOP/TABLET), "location_findings" (performance
by country), "city_findings" (performance by city), and "time_findings"
(performance by day-of-week and hour). Each includes a
"best_performing_X" and "worst_performing_X" entry where enough data
exists to compare fairly. Use these to make concrete, actionable
observations (e.g. "Desktop converts at a notably lower CPA than
Mobile — consider adjusting device bid adjustments", or "Dubai
significantly outperforms Abu Dhabi — consider shifting location bid
adjustments toward Dubai") — but only when the underlying numbers show
a real, meaningful gap; do not force a finding if the difference is
small or based on very little spend. City-level data in particular can
include many very-low-spend cities — only discuss cities with
meaningful, comparable spend, and do not list every low-activity city
individually. If a "worst_performing_X" entry is missing or null, it
means there wasn't enough distinct, comparable data to identify one —
state this rather than guessing.

SEARCH TERM FINDINGS — IMPORTANT NUANCE: "wasted_search_terms" lists
search terms that spent money with zero conversions. Do NOT assume this
automatically means the search terms are irrelevant or off-target —
check the actual search term text yourself. If the wasted search terms
are clearly ON-TOPIC and relevant to the business (e.g. close variants
of the product/service being advertised), the real issue is NOT keyword
targeting — it's more likely something after the click: landing page
experience, offer clarity, price mismatch, or lead follow-up. Only
describe search terms as "irrelevant traffic" if they are genuinely
off-topic from the business. "wasted_spend_by_match_type" shows which
match type (EXACT/PHRASE/BROAD) accounts for the most wasted spend —
report this factually, but do not assume a high EXACT-match share means
"loosen targeting" (the opposite of what you'd say for BROAD match) —
instead flag it as evidence the problem is likely post-click, not
targeting-related. "top_converting_search_terms" flags search terms
converting well that are not already an exact-match keyword
(is_already_exact_match: false) — these are concrete "add this as a new
keyword" opportunities.

IMPORTANT — TARGET CPA, NOT ACCOUNT AVERAGE: campaign_findings evaluates
each campaign against ITS OWN configured target_cpa (set by the account
manager for that specific market/campaign), NOT against the account-wide
average CPA. This means:
- "over_target_cpa_campaigns" lists campaigns exceeding their own target —
  this is the primary signal of an efficiency problem, and should be
  treated as more meaningful than any account-wide average comparison.
- "under_target_cpa_campaigns" lists campaigns beating their own target —
  genuinely efficient, good candidates for more budget/attention.
- "campaigns_missing_target_cpa" lists campaigns with NO target configured
  at all (e.g. Manual CPC or Target ROAS bidding) — these cannot be
  judged as over/under target, so state this limitation plainly rather
  than guessing whether they're performing well.
- Do NOT use "account_avg_cpa" from account_summary as a benchmark for
  individual campaigns — it is provided only as general account-wide
  context (e.g. total spend, total conversions), not a performance target.

"significant_period_changes" shows campaigns whose cost or conversions
changed by 30% or more compared to the immediately preceding period of
the same length. Treat a significant negative change (e.g. conversions
dropping while cost stays flat or rises) as high priority — this is a
concrete, dated signal of a developing problem, not a static observation.

You may also be given PAST_REPORTS: a list of prior reports for this same
client, each with a date. Treat PAST_REPORTS the same way you treat
FINDINGS — as verified historical fact you may reference, but never as
something to embellish. You may only say an issue is "recurring" or
"worsening" if a PAST_REPORTS entry actually mentions it. If PAST_REPORTS
is empty or not provided, do not mention history at all — do not imply
this is the first report if you don't actually know that either; simply
omit any comparison to prior periods.

IDENTIFYING ADS: when referring to a specific ad, identify it by its
ad_group_name and campaign_name (e.g. "the 'holidays to sri lanka' ad in
the UAE Leads campaign") — NOT by its numeric ad_id. The ad_id exists in
the data only as a technical reference; it is not meaningful to a human
reader and should not appear in the written report.

Your job is ONLY to:
1. Explain what the findings mean in plain English.
2. Prioritize which issues matter most (biggest cost/impact first, and
   campaigns furthest over their own target CPA before smaller deviations).
3. Suggest a general direction for action (e.g. "reduce budget on X",
   "reallocate budget toward Y", "review the landing page for ad Z") — but
   do NOT invent specific bid amounts, percentages, or numbers that are not
   already present in FINDINGS.
4. If both campaign_findings and ad_findings are present, connect them where
   relevant — e.g. if a campaign is over its target CPA in campaign_findings
   AND has a specific low-CTR or high-CPA ad within it in ad_findings, call
   out that connection explicitly, since it points to a more precise cause.
5. If ad_findings includes headlines/descriptions from top_performing_ads,
   you may describe qualitatively what pattern seems to work (e.g. "shorter,
   price-focused headlines" or "ads mentioning a specific duration") — but
   do NOT write brand-new headline or description copy claiming it will
   perform better, since that performance claim cannot be verified from
   the data you have.
6. If PAST_REPORTS shows an issue was already flagged before and still
   appears in the current FINDINGS, call this out explicitly as a
   recurring issue — this is more urgent than a first-time flag and should
   be prioritized accordingly.

STRICT RULES:
- Do NOT introduce any number, statistic, or metric that is not present
  in the FINDINGS JSON you were given.
- If FINDINGS does not contain enough data to answer something, say so
  explicitly instead of guessing.
- Every claim you make must be traceable to a specific field in FINDINGS,
  or to a specific dated entry in PAST_REPORTS — but describe the source
  in plain English, never by pasting the raw JSON field name into the
  report. Say "according to the search term match-type breakdown" or
  "as shown in the wasted-spend analysis" — NOT "(wasted_spend_by_match_type)"
  or "(high_cpa_ads)". A reader should never see snake_case or a raw
  field name anywhere in the written report; it should read like a
  normal business document, not an API response.
- Do not recommend specific new bid amounts or budget figures unless
  that exact figure appears in FINDINGS.
- Write for a marketing consultant audience — clear, concise, no jargon
  padding.
- End with a short "Recommended next steps" list, ranked by priority.
"""

USER_PROMPT_TEMPLATE = """Here is the FINDINGS JSON for this client's account:

{findings_json}

Here is PAST_REPORTS — prior reports for this same client, if any:

{past_reports_json}

Write the analysis now, following the system rules exactly.
"""


def build_prompt() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("user", USER_PROMPT_TEMPLATE),
    ])


def get_llm(model: str = "gpt-5.6-luna", temperature: float = 0.2) -> ChatOpenAI:
    """
    temperature is kept low (0.2) on purpose — this is an analytical task,
    not a creative one. We want consistent, literal reporting, not variety.
    Requires OPENAI_API_KEY to be set as an environment variable.

    NOTE: gpt-5.6-luna is OpenAI's fast/cost-efficient tier — good for
    keeping iteration costs low. If reports feel shallow or miss nuance
    during testing, try swapping to "gpt-5.6-terra" for a step up in
    reasoning depth at a moderate cost increase.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "OPENAI_API_KEY environment variable not set. "
            "Set it before running: setx OPENAI_API_KEY \"your-key-here\" (Windows) "
            "or export OPENAI_API_KEY=your-key-here (Mac/Linux)."
        )
    return ChatOpenAI(model=model, temperature=temperature, api_key=api_key)


def generate_report(
    campaign_findings: Dict = None,
    ad_findings: Dict = None,
    search_term_findings: Dict = None,
    segment_findings: Dict = None,
    customer_id: str = None,
    period: str = None,
    use_history: bool = True,
    auto_store: bool = True,
    return_past_reports: bool = False,
):
    """
    Takes findings from waste_detector.build_findings() (campaign_findings),
    ad_performance_analyzer.build_ad_findings() (ad_findings),
    search_term_analyzer.build_search_term_findings() (search_term_findings),
    and/or segment_analyzer.build_segment_findings() (segment_findings),
    and returns the LLM's plain-English report as a string.

    period: the human-readable period label being analyzed (e.g.
        "last_28_days"). Passed through into the findings JSON so the LLM
        states which timeframe the report covers, rather than presenting
        the data as if it were all-time.

    At least one of the four must be provided. Any combination may be
    provided together for a unified report.

    If customer_id is provided and use_history=True, retrieves relevant past
    reports for this client from the RAG store (Sprint 7) and includes them
    as context. If auto_store=True, the newly generated report is saved back
    to the RAG store afterward so future runs can reference it.

    If return_past_reports=True, returns a tuple (report, past_reports)
    instead of just the report string. This matters for groundedness
    checking: the LLM is allowed to cite numbers/dates from past_reports
    (e.g. "as flagged in the 2026-09-01 report"), so a correct groundedness
    check must validate against past_reports too, not just campaign_findings
    and ad_findings — otherwise legitimate historical citations get wrongly
    flagged as hallucinations.
    """
    if campaign_findings is None and ad_findings is None and search_term_findings is None and segment_findings is None:
        raise ValueError("Provide at least one of campaign_findings, ad_findings, search_term_findings, or segment_findings.")

    combined = {}
    if period:
        combined["period"] = period
    if campaign_findings is not None:
        combined["campaign_findings"] = campaign_findings
    if ad_findings is not None:
        combined["ad_findings"] = ad_findings
    if search_term_findings is not None:
        combined["search_term_findings"] = search_term_findings
    if segment_findings is not None:
        combined["segment_findings"] = segment_findings

    # --- Sprint 7: retrieve relevant history before generating the report ---
    past_reports = []
    if use_history and customer_id:
        from rag.retriever import retrieve_relevant_history, summarize_findings_for_storage

        current_summary = summarize_findings_for_storage(campaign_findings, ad_findings)
        # Build a short query string from the current findings' flagged names,
        # so retrieval pulls the most relevant past reports, not just recent ones.
        query_terms = []
        for v in current_summary.values():
            query_terms.extend(v)
        query_text = " ".join(query_terms) if query_terms else "Google Ads performance report"

        try:
            past_reports = retrieve_relevant_history(customer_id, query_text, k=3)
        except Exception as e:
            # RAG history is a nice-to-have, not critical — if the vector
            # store isn't set up yet (e.g. first-ever run), proceed without it
            # rather than failing the whole report.
            print(f"[rag] Could not retrieve history ({e}); proceeding without it.")
            past_reports = []

    prompt = build_prompt()
    llm = get_llm()
    chain = prompt | llm | StrOutputParser()

    findings_json = json.dumps(combined, indent=2)
    past_reports_json = json.dumps(past_reports, indent=2) if past_reports else "[]"

    report = chain.invoke({
        "findings_json": findings_json,
        "past_reports_json": past_reports_json,
    })

    # --- Sprint 7: store this report for future runs ---
    if auto_store and customer_id:
        from rag.retriever import store_report, summarize_findings_for_storage

        try:
            summary = summarize_findings_for_storage(campaign_findings, ad_findings)
            store_report(customer_id=customer_id, report_text=report, findings_summary=summary)
        except Exception as e:
            print(f"[rag] Could not store this report ({e}); report was still generated successfully.")

    if return_past_reports:
        return report, past_reports
    return report


if __name__ == "__main__":
    # Manual test — combines campaign-level, ad-level, and search-term
    # findings into one unified report, with RAG history included.
    from analytics.waste_detector import build_findings
    from analytics.ad_performance_analyzer import build_ad_findings
    from analytics.search_term_analyzer import build_search_term_findings
    from google_ads.campaign_data import get_campaign_data_with_comparison
    from google_ads.ad_data import get_ad_data
    from google_ads.search_term_data import get_search_term_data
    from llm.groundedness_check import check_groundedness, print_groundedness_result

    CUSTOMER_ID = "6485531233"      # swap for the client account to test
    CAMPAIGN_ID = "18731997084"     # the specific campaign to pull ad-level/search-term data for

    campaigns = get_campaign_data_with_comparison(CUSTOMER_ID, period="last_28_days")
    campaign_findings = build_findings(campaigns)

    ads = get_ad_data(CUSTOMER_ID, CAMPAIGN_ID)
    ad_findings = build_ad_findings(ads)

    search_terms = get_search_term_data(CUSTOMER_ID, campaign_id=CAMPAIGN_ID, period="last_28_days")
    search_term_findings = build_search_term_findings(search_terms)

    report, past_reports_used = generate_report(
        campaign_findings=campaign_findings,
        ad_findings=ad_findings,
        search_term_findings=search_term_findings,
        customer_id=CUSTOMER_ID,
        period="Last 28 days",
        return_past_reports=True,
    )

    print("\n==============================")
    print("AI ANALYST REPORT")
    print("==============================\n")
    print(report)

    combined_findings_for_check = {
        "campaign_findings": campaign_findings,
        "ad_findings": ad_findings,
        "search_term_findings": search_term_findings,
    }
    if past_reports_used:
        combined_findings_for_check["past_reports"] = past_reports_used
    result = check_groundedness(report, combined_findings_for_check)
    print_groundedness_result(result)