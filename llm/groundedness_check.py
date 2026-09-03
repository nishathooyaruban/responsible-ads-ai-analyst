"""
llm/groundedness_check.py

This is the safety net that makes the "Responsible AI" claim real rather
than aspirational: after the LLM writes its report, we extract every
number it mentions and check that number actually appears somewhere in
the FINDINGS JSON it was given. If the LLM mentions a number we never
gave it, that's a hallucination — and we flag it instead of silently
showing it to a human reviewer as if it were verified fact.

This is a heuristic, not a perfect proof of correctness (it can't verify
the LLM's *reasoning* is right, only that any concrete numbers it cites
were actually present in the source data). That's still a meaningful and
honest safeguard, and worth being explicit about that limitation in your
CV/portfolio write-up — overclaiming what a groundedness check does is
itself a Responsible AI mistake.
"""

import re
from typing import Dict, List, Set


def _extract_numbers(text: str) -> Set[str]:
    """
    Extracts numeric tokens from text, normalized for comparison
    (e.g. "58.18" and "58.18%" both become "58.18").

    Handles comma-formatted thousands (e.g. "2,233" or "120,301.86"),
    which the LLM commonly uses for readability — without this, a
    number like "2,233" would incorrectly split into "2" and "233"
    and get flagged as an unverified hallucination when it's really
    just a formatting difference, not a false claim.

    Ignores tiny numbers like "1" or "2" (list markers, single digits)
    since those produce too many false positives to be useful signals.
    """
    # Match numbers that may contain comma thousand-separators, e.g. 120,301.86
    raw_matches = re.findall(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.?\d*", text)
    numbers = set()
    for m in raw_matches:
        cleaned = m.replace(",", "")
        try:
            value = float(cleaned)
        except ValueError:
            continue
        if value >= 10:  # skip small numbers — too noisy to be meaningful here
            numbers.add(cleaned.rstrip("0").rstrip(".") if "." in cleaned else cleaned)
    return numbers


def _flatten_findings_numbers(findings: Dict) -> Set[str]:
    """
    Walks the entire findings dict (nested lists/dicts included) and
    collects every numeric value as a string, so we have a ground-truth
    set to compare the LLM's cited numbers against.
    """
    numbers = set()

    def walk(obj):
        nonlocal numbers
        if isinstance(obj, dict):
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)
        elif isinstance(obj, (int, float)):
            # Use abs() here: negative values (e.g. cost_change_pct: -17.0,
            # meaning a 17% DECREASE) are legitimate source data, and a
            # report describing a decrease typically states the magnitude
            # ("costs declined 17.0%") without the negative sign. Checking
            # obj >= 10 alone would silently exclude every negative number
            # from the "known good" set, causing correct citations of a
            # decrease's magnitude to be wrongly flagged as hallucinations.
            if abs(obj) >= 10:
                s = str(abs(obj))
                numbers.add(s.rstrip("0").rstrip(".") if "." in s else s)
        elif isinstance(obj, str):
            numbers = numbers | _extract_numbers(obj)

    walk(findings)
    return numbers


def check_groundedness(report_text: str, findings: Dict, tolerance: float = 0.5) -> Dict:
    """
    Compares numbers mentioned in the LLM's report against numbers present
    in the findings JSON. Numbers within `tolerance` (absolute difference)
    of a real value are treated as a match — this allows for minor
    rounding differences (e.g. LLM writes "54" when findings has "54.39").

    Returns a dict with:
      - is_grounded: bool — True if no unverified numbers were found
      - unverified_numbers: list of numbers cited in the report that don't
        match anything in findings
      - checked_numbers: total count of numeric claims checked
    """
    report_numbers = _extract_numbers(report_text)
    findings_numbers = _flatten_findings_numbers(findings)
    findings_floats = [float(n) for n in findings_numbers]

    unverified = []
    for rn in report_numbers:
        rn_float = float(rn)
        if not any(abs(rn_float - fn) <= tolerance for fn in findings_floats):
            unverified.append(rn)

    return {
        "is_grounded": len(unverified) == 0,
        "unverified_numbers": sorted(unverified, key=float),
        "checked_numbers": len(report_numbers),
    }


def print_groundedness_result(result: Dict) -> None:
    print("\n==============================")
    print("GROUNDEDNESS CHECK")
    print("==============================")
    print(f"Numbers checked: {result['checked_numbers']}")
    if result["is_grounded"]:
        print("✓ PASSED — all cited numbers trace back to source data.")
    else:
        print("⚠ FAILED — the following numbers were NOT found in the source findings:")
        for n in result["unverified_numbers"]:
            print(f"   - {n}")
        print("This report should NOT be sent to a client without human review.")


if __name__ == "__main__":
    # Manual test with a deliberately "bad" report containing a made-up number
    sample_findings = {
        "account_summary": {"account_avg_cpa": 53.83, "total_cost": 120142.56},
        "high_cpa_campaigns": [
            {"campaign_name": "UAE", "cost_per_conversion": 146.21, "pct_above_average": 171.6}
        ],
    }

    good_report = "The account average CPA is 53.83, and UAE is 171.6% above that average."
    bad_report = "The account average CPA is 53.83, but I estimate switching bidding strategy could cut CPA by 65%."

    print("Testing a grounded report:")
    print_groundedness_result(check_groundedness(good_report, sample_findings))

    print("\nTesting a report with a hallucinated number:")
    print_groundedness_result(check_groundedness(bad_report, sample_findings))