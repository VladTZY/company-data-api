"""Evaluate the match API against API-input-sample.csv.

Usage: python -m analysis.evaluate  (API must be running on :8000)

Reports match rate and an accuracy proxy: how many of the provided input
fields agree with the returned profile.
"""
import csv

import httpx
from rapidfuzz import fuzz

API = "http://localhost:8000/match"


def main() -> None:
    rows = list(csv.DictReader(open("data/API-input-sample.csv")))
    matched = 0
    results = []
    for row in rows:
        payload = {
            "name": row.get("input name") or None,
            "phone": (row.get("input phone") or "").strip() or None,
            "website": row.get("input website") or None,
            "facebook": row.get("input_facebook") or None,
        }
        r = httpx.post(API, json=payload, timeout=30).json()
        m = r.get("match")
        if m:
            matched += 1
        results.append((payload, r))

    print(f"Match rate: {matched}/{len(rows)} ({matched/len(rows)*100:.0f}%)\n")
    print(f"{'input name':<42}{'matched domain':<32}{'score':>6}  signals")
    for payload, r in results:
        m = r.get("match")
        name = (payload["name"] or payload["website"] or payload["phone"] or "?")[:40]
        if m:
            print(f"{name:<42}{m['domain']:<32}{r['score']:>6}  {r.get('signals')}")
        else:
            print(f"{name:<42}{'-- NO MATCH --':<32}{r['score']:>6}  {r.get('reason')}")

    # accuracy proxy: input-vs-output field agreement on matched rows
    agree, checks = 0, 0
    for payload, r in results:
        m = r.get("match")
        if not m:
            continue
        sig = r.get("signals", {})
        for k, v in sig.items():
            checks += 1
            if v >= 0.8:
                agree += 1
    if checks:
        print(f"\nAccuracy proxy: {agree}/{checks} input signals agree >=0.8 "
              f"({agree/checks*100:.0f}%) on matched rows")


if __name__ == "__main__":
    main()
