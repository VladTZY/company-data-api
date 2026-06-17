"""Coverage and fill-rate analysis of scraped data.

Usage: python -m analysis.analyze data/scraped.json
"""
import json
import sys


def main(path: str) -> None:
    with open(path) as f:
        rows = json.load(f)

    total = len(rows)
    crawled = [r for r in rows if r["crawled"]]
    n = len(crawled)

    def fill(pred):
        c = sum(1 for r in crawled if pred(r))
        return c, c / n * 100, c / total * 100

    print(f"=== Coverage ===")
    print(f"Total domains:    {total}")
    print(f"Crawled OK:       {n} ({n/total*100:.1f}%)")
    print(f"Failed/dead:      {total-n} ({(total-n)/total*100:.1f}%)")

    metrics = {
        "phone numbers": lambda r: r["phones"],
        "any social link": lambda r: r["socials"],
        "  facebook": lambda r: "facebook" in r["socials"],
        "  instagram": lambda r: "instagram" in r["socials"],
        "  twitter/x": lambda r: "twitter" in r["socials"],
        "  linkedin": lambda r: "linkedin" in r["socials"],
        "  youtube": lambda r: "youtube" in r["socials"],
        "address": lambda r: r["addresses"],
    }
    print(f"\n=== Fill rates ===")
    print(f"{'datapoint':<18}{'count':>6}{'% of crawled':>14}{'% of total':>12}")
    for label, pred in metrics.items():
        c, pc, pt = fill(pred)
        print(f"{label:<18}{c:>6}{pc:>13.1f}%{pt:>11.1f}%")

    all3 = sum(1 for r in crawled if r["phones"] and r["socials"] and r["addresses"])
    any1 = sum(1 for r in crawled if r["phones"] or r["socials"] or r["addresses"])
    total_dp = sum(len(r["phones"]) + sum(len(v) for v in r["socials"].values()) + len(r["addresses"]) for r in crawled)
    print(f"\nAll 3 datapoint types: {all3} ({all3/n*100:.1f}% of crawled)")
    print(f"At least 1 datapoint:  {any1} ({any1/n*100:.1f}% of crawled)")
    print(f"Total datapoints extracted: {total_dp}")


if __name__ == "__main__":
    main(sys.argv[1])
