"""Async crawler. Fetches the domains concurrently and extracts datapoints.

Usage: python -m scraper.crawl data/sample-websites.csv data/scraped.json
"""
from __future__ import annotations

import asyncio
import json
import sys
import time

import httpx

from scraper.extract import parse_page, find_contact_links

CONCURRENCY = 100
TIMEOUT = httpx.Timeout(8.0, connect=5.0)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
MAX_BYTES = 1_500_000


async def fetch(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        resp = await client.get(url)
        if resp.status_code >= 400:
            return None
        ctype = resp.headers.get("content-type", "")
        if "html" not in ctype and "text" not in ctype and ctype:
            return None
        return resp.text[:MAX_BYTES]
    except Exception:
        return None


def merge(a: dict, b: dict) -> dict:
    phones = list(dict.fromkeys(a["phones"] + b["phones"]))
    addresses = list(dict.fromkeys(a["addresses"] + b["addresses"]))
    socials = dict(a["socials"])
    for k, v in b["socials"].items():
        socials[k] = list(dict.fromkeys(socials.get(k, []) + v))[:3]
    return {"phones": phones[:8], "socials": socials, "addresses": addresses[:5]}


async def crawl_domain(client: httpx.AsyncClient, sem: asyncio.Semaphore, domain: str) -> dict:
    async with sem:
        result = {"domain": domain, "crawled": False, "phones": [], "socials": {}, "addresses": []}
        html = None
        used_url = None
        for url in (f"https://{domain}", f"http://{domain}", f"https://www.{domain}"):
            html = await fetch(client, url)
            if html:
                used_url = url
                break
        if not html:
            return result

        result["crawled"] = True
        data = parse_page(html)

        # if the homepage is missing something, check a couple of contact/about pages
        if not data["phones"] or not data["addresses"] or "facebook" not in data["socials"]:
            for link in find_contact_links(html, used_url):
                sub = await fetch(client, link)
                if sub:
                    data = merge(data, parse_page(sub))
                if data["phones"] and data["addresses"]:
                    break

        result.update(data)
        return result


async def main(domains_csv: str, out_json: str) -> None:
    with open(domains_csv) as f:
        domains = [line.strip() for line in f.readlines()[1:] if line.strip()]

    sem = asyncio.Semaphore(CONCURRENCY)
    start = time.time()
    async with httpx.AsyncClient(
        timeout=TIMEOUT, headers=HEADERS, follow_redirects=True, verify=False,
        limits=httpx.Limits(max_connections=CONCURRENCY + 20),
    ) as client:
        tasks = [crawl_domain(client, sem, d) for d in domains]
        results = []
        done = 0
        for coro in asyncio.as_completed(tasks):
            results.append(await coro)
            done += 1
            if done % 100 == 0:
                print(f"  {done}/{len(domains)} done in {time.time()-start:.0f}s", flush=True)

    elapsed = time.time() - start
    with open(out_json, "w") as f:
        json.dump(results, f, indent=1)
    crawled = sum(r["crawled"] for r in results)
    print(f"Crawled {crawled}/{len(domains)} domains in {elapsed:.0f}s -> {out_json}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
