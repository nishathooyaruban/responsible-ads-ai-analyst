"""
rag/retriever.py

Sprint 7 — RAG over historical client reports.

WHY THIS EXISTS: without this, every report is generated with zero memory
of what was said last time. A campaign flagged as "high CPA" last month
and still high CPA this month looks like a fresh discovery, when really
it's a recurring, worsening problem the client should know about.

DESIGN RULE (same principle as the rest of this project): retrieved past
context is treated as verified historical fact, not something the LLM can
embellish. The LLM may say "this was also flagged last month" only if a
retrieved record actually says so — it cannot claim recurrence that isn't
present in the retrieved documents.

Storage: Chroma, a lightweight local vector database — no external
infrastructure needed, everything persists to a folder on disk.
Embeddings: OpenAI's embedding model, since we're already using OpenAI
for the LLM analyst (Sprint 6), so no second provider/API key is needed.
"""

import os
import json
from datetime import datetime
from typing import Dict, List, Optional

from langchain_openai import OpenAIEmbeddings
from langchain_chroma import Chroma
from langchain_core.documents import Document


PERSIST_DIRECTORY = "rag/chroma_store"
COLLECTION_NAME = "client_reports"


def _get_vectorstore() -> Chroma:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "OPENAI_API_KEY environment variable not set — required for "
            "generating embeddings, not just the LLM report itself."
        )
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small", api_key=api_key)
    return Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=embeddings,
        persist_directory=PERSIST_DIRECTORY,
    )


def store_report(customer_id: str, report_text: str, findings_summary: Dict, report_date: Optional[str] = None) -> None:
    """
    Saves a generated report so future runs can reference it.

    findings_summary should be a SMALL summary dict, not the full findings
    JSON — e.g. just the flagged campaign/ad names and their key numbers.
    This keeps what gets embedded and retrieved focused and relevant,
    rather than re-embedding huge raw JSON blobs.
    """
    report_date = report_date or datetime.utcnow().strftime("%Y-%m-%d")

    metadata = {
        "customer_id": customer_id,
        "report_date": report_date,
        "findings_summary_json": json.dumps(findings_summary),
    }

    doc = Document(page_content=report_text, metadata=metadata)

    vectorstore = _get_vectorstore()
    vectorstore.add_documents([doc])


def retrieve_relevant_history(customer_id: str, query_text: str, k: int = 3) -> List[Dict]:
    """
    Retrieves the top-k most relevant past reports for this customer,
    given a query (typically a short description of the current findings,
    e.g. "high CPA campaigns UAE Maldives Bahrain wasted spend").

    Returns a list of dicts with report_date, report_text, and
    findings_summary — this is what gets fed into the LLM prompt as
    historical context, NOT the raw vectorstore Document objects.
    """
    vectorstore = _get_vectorstore()

    # Filter to only this customer's past reports — we never want to leak
    # one client's history into another client's report.
    results = vectorstore.similarity_search(
        query_text,
        k=k,
        filter={"customer_id": customer_id},
    )

    history = []
    for doc in results:
        history.append({
            "report_date": doc.metadata.get("report_date"),
            "report_text": doc.page_content,
            "findings_summary": json.loads(doc.metadata.get("findings_summary_json", "{}")),
        })
    return history


def summarize_findings_for_storage(campaign_findings: Optional[Dict] = None, ad_findings: Optional[Dict] = None) -> Dict:
    """
    Builds a compact summary of the current findings — just the flagged
    campaign/ad names and their key numbers — suitable for storing
    alongside a report. Keeping this small (rather than the full findings
    JSON) keeps embeddings focused on what's actually comparable
    period-to-period.
    """
    summary = {}
    if campaign_findings:
        summary["high_cpa_campaigns"] = [
            c["campaign_name"] for c in campaign_findings.get("high_cpa_campaigns", [])
        ]
        summary["wasted_spend_campaigns"] = [
            c["campaign_name"] for c in campaign_findings.get("wasted_spend", [])
        ]
    if ad_findings:
        summary["high_cpa_ads"] = [
            a["ad_group_name"] for a in ad_findings.get("high_cpa_ads", [])
        ]
        summary["low_ctr_ads"] = [
            a["ad_group_name"] for a in ad_findings.get("low_ctr_ads", [])
        ]
    return summary


if __name__ == "__main__":
    # Manual test — store a fake past report, then retrieve it
    CUSTOMER_ID = "6485531233"

    fake_past_summary = {
        "high_cpa_campaigns": ["UAE", "Maldives", "Bahrain"],
        "wasted_spend_campaigns": ["Netherland 2026", "Australia 2026"],
    }
    fake_past_report = (
        "Last period, UAE and Maldives were flagged as high-CPA campaigns, "
        "significantly above the account average. Netherland 2026 and "
        "Australia 2026 had spend with zero conversions."
    )

    print("Storing a test past report...")
    store_report(
        customer_id=CUSTOMER_ID,
        report_text=fake_past_report,
        findings_summary=fake_past_summary,
        report_date="2026-08-01",
    )

    print("Retrieving relevant history for a query about UAE and high CPA...")
    history = retrieve_relevant_history(
        customer_id=CUSTOMER_ID,
        query_text="high CPA campaigns UAE Maldives Bahrain",
        k=2,
    )

    for h in history:
        print(f"\n--- Report from {h['report_date']} ---")
        print(h["report_text"])
        print("Summary:", h["findings_summary"])