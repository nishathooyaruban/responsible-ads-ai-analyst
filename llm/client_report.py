"""
llm/client_report.py

Client-facing report generator.

This is a SECOND report format, distinct from llm/analyst.py's detailed
technical report. Same underlying grounded data, same strict rules
(no invented numbers, no raw field names) — but restructured into a
simple, non-technical format suitable for sending directly to a client:

    Executive Summary
    What Worked
    What Didn't Work
    Resolved Since Last Report   (data-only — see note below)
    Next Month Strategy

"ACTIONS TAKEN" DESIGN NOTE: the original request was for an "Actions
Taken" section, but the system has no way to verify what a human
actually DID (e.g. "we rewrote the landing page") — only what changed
in the data. So this uses "Resolved Since Last Report" instead: it
compares the current findings against the most recent stored report
(via RAG) and reports only issues that were flagged before and no
longer appear now. This is a real, verifiable data change — but the
LLM is explicitly instructed NOT to claim a specific action caused it,
since that would be an unverified assumption.
"""

import json
from typing import Dict, Optional

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from llm.analyst import get_llm  # reuse the same LLM config (model, temperature, API key handling)


SYSTEM_PROMPT = """You are writing a monthly Google Ads performance summary
for a client who is NOT a digital marketing expert. This is a client-facing
document, not an internal technical report — write in plain, warm,
confident business language. No jargon, no raw data-field names, no
snake_case, no acronyms without a one-word explanation the first time
they're used (e.g. "cost per lead (CPA)").

You will be given a JSON object called FINDINGS with the same structure
used elsewhere in this system: campaign_findings (including
account_period_comparison, a current-vs-previous-period comparison),
ad_findings, search_term_findings, and segment_findings. You may also be
given PAST_REPORTS — prior reports for this same client.

STRUCTURE — use exactly these five headings, in this order:

## Executive Summary
One to three sentences. Lead with the headline number from
account_period_comparison if it is present (e.g. "Leads increased X%
while cost per lead decreased Y%"). If account_period_comparison fields
are None, do not state a percentage — say plainly that this period
doesn't have a comparable prior period yet.

## What Worked
2-4 bullet points on what performed well this period — draw from
under_target_cpa_campaigns, top_performing_ads, and the best-performing
segments (device/city/day) in segment_findings. Name the campaign, ad,
or segment plainly (e.g. "Your UAE Leads campaign" not "campaign_findings").

## What Didn't Work
2-4 bullet points on the clearest problems — draw from
over_target_cpa_campaigns, wasted_spend, high_cpa_ads, low_ctr_ads, and
the weakest segments. Keep this constructive, not alarming — describe
what happened, not blame.

## Resolved Since Last Report
Only include this section if PAST_REPORTS is provided and non-empty.
Compare the issues in the current FINDINGS against what PAST_REPORTS
described. List anything that was flagged before and no longer appears
now (e.g. a campaign that was previously above its target CPA and is
not anymore). State this as an observed change only — e.g. "X is no
longer showing as a concern this period" — and do NOT claim a specific
action or cause, since the data does not show what was actually done.
If nothing has resolved, or PAST_REPORTS is empty/not provided, omit
this section entirely rather than writing "nothing changed."

## Next Month Strategy
2-4 forward-looking bullet points in plain business language — general
direction only (e.g. "consider shifting some budget toward your
higher-performing UAE Leads campaign"), never specific bid amounts,
budget figures, or percentages that are not already in FINDINGS.

STRICT RULES (same as the technical report):
- CURRENCY: FINDINGS may include an "account_currency" field (e.g. "USD",
  "GBP", "AED") — this is the ONE fixed currency for the entire account,
  and every cost figure is already in it, regardless of which country
  a campaign name mentions (a campaign named "United Kingdom" in a USD
  account still reports costs in USD, not GBP). Mention the currency
  once near the start if account_currency is present, and use it
  consistently. NEVER guess a currency from a campaign or country name.
  If account_currency is absent, state figures as plain numbers with no
  currency symbol.
- Never state a number, statistic, or metric not present in FINDINGS.
- Never use a raw JSON field name (like "over_target_cpa_campaigns") —
  always describe it in plain English.
- If FINDINGS doesn't have enough information for a section, say so
  briefly rather than guessing, or omit that bullet.
- Keep the whole report to roughly 300-450 words — this is a client
  summary, not the full technical analysis.
"""

USER_PROMPT_TEMPLATE = """Here is the FINDINGS JSON for this client's account:

{findings_json}

Here is PAST_REPORTS, if any:

{past_reports_json}

Write the client-facing report now, following the structure and rules exactly.
"""


def build_client_report_prompt() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("user", USER_PROMPT_TEMPLATE),
    ])


def generate_client_report(
    campaign_findings: Dict = None,
    ad_findings: Dict = None,
    search_term_findings: Dict = None,
    segment_findings: Dict = None,
    past_reports: Optional[list] = None,
) -> str:
    """
    Generates the client-facing report. Unlike llm/analyst.py's
    generate_report(), this does NOT manage RAG retrieval/storage itself —
    pass in past_reports (e.g. from the same retrieval already done for
    the technical report) so the same historical context is reused
    rather than fetched twice.
    """
    if campaign_findings is None and ad_findings is None and search_term_findings is None and segment_findings is None:
        raise ValueError("Provide at least one findings section.")

    combined = {}
    if campaign_findings is not None:
        combined["campaign_findings"] = campaign_findings
    if ad_findings is not None:
        combined["ad_findings"] = ad_findings
    if search_term_findings is not None:
        combined["search_term_findings"] = search_term_findings
    if segment_findings is not None:
        combined["segment_findings"] = segment_findings

    prompt = build_client_report_prompt()
    llm = get_llm()
    chain = prompt | llm | StrOutputParser()

    findings_json = json.dumps(combined, indent=2)
    past_reports_json = json.dumps(past_reports, indent=2) if past_reports else "[]"

    report = chain.invoke({
        "findings_json": findings_json,
        "past_reports_json": past_reports_json,
    })
    return report


if __name__ == "__main__":
    from analytics.waste_detector import build_findings
    from google_ads.campaign_data import get_campaign_data_with_comparison
    from llm.groundedness_check import check_groundedness, print_groundedness_result

    CUSTOMER_ID = "YOUR_CUSTOMER_ID"

    campaigns = get_campaign_data_with_comparison(CUSTOMER_ID, period="last_28_days")
    campaign_findings = build_findings(campaigns)

    report = generate_client_report(campaign_findings=campaign_findings)

    print("\n==============================")
    print("CLIENT-FACING REPORT")
    print("==============================\n")
    print(report)

    result = check_groundedness(report, {"campaign_findings": campaign_findings})
    print_groundedness_result(result)