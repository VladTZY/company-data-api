"""Merge scraped data with company names and index into Elasticsearch.

Usage: python -m indexer.build_index
"""
from __future__ import annotations

import csv
import json
import re

from elasticsearch import Elasticsearch, helpers

ES_URL = "http://localhost:9200"
INDEX = "companies"

MAPPING = {
    "settings": {
        "number_of_shards": 1,
        "analysis": {
            "filter": {
                "company_stop": {
                    "type": "stop",
                    "stopwords": [
                        "inc", "llc", "ltd", "corp", "corporation", "co",
                        "company", "the", "of", "and", "pc", "pa", "llp", "lp", "plc",
                    ],
                },
                "name_shingles": {"type": "shingle", "min_shingle_size": 2, "max_shingle_size": 3},
            },
            "analyzer": {
                "company_name": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase", "asciifolding", "company_stop"],
                },
                "domain_text": {
                    "type": "custom",
                    "tokenizer": "letter",
                    "filter": ["lowercase"],
                },
            },
            "normalizer": {
                "lower": {"type": "custom", "filter": ["lowercase", "asciifolding"]},
            },
        },
    },
    "mappings": {
        "properties": {
            "domain": {"type": "keyword", "normalizer": "lower",
                       "fields": {"text": {"type": "text", "analyzer": "domain_text"}}},
            "company_commercial_name": {"type": "text", "analyzer": "company_name",
                                        "fields": {"raw": {"type": "keyword", "normalizer": "lower"}}},
            "company_legal_name": {"type": "text", "analyzer": "company_name",
                                   "fields": {"raw": {"type": "keyword", "normalizer": "lower"}}},
            "company_all_available_names": {"type": "text", "analyzer": "company_name"},
            "phones": {"type": "keyword"},
            "facebook": {"type": "keyword", "normalizer": "lower",
                         "fields": {"handle": {"type": "keyword", "normalizer": "lower"}}},
            "facebook_handles": {"type": "keyword", "normalizer": "lower"},
            "instagram": {"type": "keyword"},
            "twitter": {"type": "keyword"},
            "linkedin": {"type": "keyword"},
            "youtube": {"type": "keyword"},
            "addresses": {"type": "text"},
            "crawled": {"type": "boolean"},
        }
    },
}

FB_HANDLE_RE = re.compile(r"facebook\.com/(?:pages/)?([\w.\-]+)", re.I)


def fb_handle(url: str) -> str | None:
    m = FB_HANDLE_RE.search(url or "")
    if not m:
        return None
    h = m.group(1).lower().strip(".")
    return h if h not in ("pages", "profile.php", "sharer", "share") else None


def build_docs() -> list[dict]:
    with open("data/scraped.json") as f:
        scraped = {r["domain"]: r for r in json.load(f)}

    docs = []
    with open("data/sample-websites-company-names.csv") as f:
        for row in csv.DictReader(f):
            domain = row["domain"].strip().lower()
            s = scraped.get(domain, {})
            socials = s.get("socials", {})
            fb_urls = socials.get("facebook", [])
            doc = {
                "_index": INDEX,
                "_id": domain,
                "domain": domain,
                "company_commercial_name": row["company_commercial_name"] or None,
                "company_legal_name": row["company_legal_name"] or None,
                "company_all_available_names": (row["company_all_available_names"] or "").split(" | ") or None,
                "phones": s.get("phones", []),
                "facebook": fb_urls,
                "facebook_handles": [h for h in (fb_handle(u) for u in fb_urls) if h],
                "instagram": socials.get("instagram", []),
                "twitter": socials.get("twitter", []),
                "linkedin": socials.get("linkedin", []),
                "youtube": socials.get("youtube", []),
                "addresses": s.get("addresses", []),
                "crawled": s.get("crawled", False),
            }
            docs.append(doc)
    return docs


def main() -> None:
    es = Elasticsearch(ES_URL)
    if es.indices.exists(index=INDEX):
        es.indices.delete(index=INDEX)
    es.indices.create(index=INDEX, **MAPPING)
    docs = build_docs()
    ok, _ = helpers.bulk(es, docs)
    es.indices.refresh(index=INDEX)
    print(f"Indexed {ok} company profiles into '{INDEX}'")


if __name__ == "__main__":
    main()
