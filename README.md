# Veridion SWE Challenge — Company Data Extraction & Match API

This project crawls ~1000 company websites for phone numbers, social links and
addresses, merges what it finds with the company-names dataset into Elasticsearch,
and exposes a REST API that takes any combination of name, website, phone and
facebook and returns the single best-matching company profile.

## Results

| Metric | Result |
|---|---|
| Full crawl time (997 domains) | 110 s (budget was 10 min) |
| Crawl coverage | 644/997 (64.6%), the rest are dead or parked domains |
| Phone fill rate | 59.5% of crawled sites |
| Social-link fill rate | 56.2% (Facebook 50.0%) |
| Address fill rate | 44.3% |
| API match rate | 32/32 (100%) on `API-input-sample.csv` |

## Architecture

```
sample-websites.csv ──> scraper/crawl.py ──> data/scraped.json
                                                  │  merge
sample-websites-company-names.csv ───────> indexer/build_index.py
                                                  │  bulk index
                                           Elasticsearch (companies)
                                                  │  retrieve top-10
POST /match ──> normalize ──> ES bool/should ──> re-score (weighted fuzzy) ──> best profile
```

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
docker compose up -d                                   # Elasticsearch on :9200

.venv/bin/python -W ignore -m scraper.crawl data/sample-websites.csv data/scraped.json
.venv/bin/python -m analysis.analyze data/scraped.json # coverage + fill rates
.venv/bin/python -m indexer.build_index                # merge + index 997 profiles

.venv/bin/uvicorn api.main:app --port 8000             # the API
.venv/bin/python -m analysis.evaluate                  # match rate on the input sample
```

Example request:

```bash
curl -X POST localhost:8000/match -H 'content-type: application/json' \
  -d '{"name": "MAZ Auto Glass", "phone": "(415) 626-4474"}'
```

The response includes the matched profile, a 0-100 confidence `score`, the
per-field `signals` breakdown, and the normalized input, so the caller can see
why a given profile was returned.

## 1. Scraping

`scraper/crawl.py` is the crawler, `scraper/extract.py` does the parsing.

The workload is almost entirely I/O wait, so I used `httpx` with asyncio and a
concurrency of 100. One process gets through all 997 domains in about 110 seconds,
well inside the 10-minute budget, so there was no reason to reach for a heavier
framework like Scrapy at this scale.

A few details:

- Each domain is tried as `https://`, then `http://`, then `https://www.` with an
  8s timeout, redirects followed and TLS verification turned off. A lot of
  small-business sites have broken certificates and I cared about getting the data,
  not about cert validity.
- If the homepage is missing a phone, address or facebook link, the crawler follows
  up to 2 same-site links whose href looks like a contact/about page
  (`contact`, `about`, `impressum`, etc.). That is where contact data usually lives,
  so it is a cheap way to improve coverage.

Extraction is in `scraper/extract.py`:

- Phones: `tel:` hrefs first since those are basically always real, then Google's
  `libphonenumber` matcher over the visible text with an `is_valid_number` filter.
  This avoids the usual problem where a naive regex treats zip codes and prices as
  phone numbers. Numbers are stored in E.164.
- Socials: one URL pattern per network, with share/sharer/plugin/widget links
  excluded.
- Addresses: a street-pattern heuristic (number + street-type word + optional
  city/state/zip). Precision here is decent but not perfect. A production version
  would use something like libpostal or an NER model.

### Scaling past 1000

The design scales linearly. Shard the domain list across N workers (processes or
machines), each running the same async loop. 1M domains works out to roughly 35
worker-hours, so about 20 machines for a two-hour wall clock. The crawler keeps no
state, so the production version is a queue (SQS/Kafka) feeding autoscaled workers
that bulk-write to ES. DNS resolution becomes the first bottleneck, so I would
pre-resolve and cache it in bulk.

## 2. Analysis

`analysis/analyze.py` prints coverage and fill rates from `data/scraped.json`:

```
Total domains: 997   Crawled OK: 644 (64.6%)   Failed/dead: 353 (35.4%)
phone numbers   383  59.5% of crawled
any social      362  56.2%   (fb 50.0%, ig 31.7%, li 14.0%, tw 13.8%, yt 11.3%)
address         285  44.3%
At least 1 datapoint: 79.0% of crawled · Total datapoints: 1,926
```

I spot-checked the 35% that failed. Almost all of them were NXDOMAIN, parked
domains, or connection timeouts, so they are dead companies rather than crawler
misses. Retrying those with a headless browser only recovers a handful of JS-only
sites, which is not worth roughly 10x the cost at this scale.

## 3. Storage (Elasticsearch)

One `companies` index, with the document id set to the domain since that is a
natural unique key. The mapping (`indexer/build_index.py`) has a few choices worth
pointing out:

- A custom `company_name` analyzer with a legal-suffix stopword filter
  (`inc`, `llc`, `ltd`, `corp`, ...) so that "Total Seal Inc." and "Total Seal"
  tokenize the same way.
- `domain.text` uses a letter tokenizer, which splits `acornlawpc.com` into
  searchable fragments. That lets a name query hit the domain even when there is no
  scraped name to match against.
- `facebook_handles` are pulled out of the scraped facebook URLs and stored as
  exact-match keywords. The API input gives full FB URLs, and handle-vs-handle is
  the reliable way to compare them.
- Phones are stored as E.164 keywords for exact `term` lookups.

## 4. Matching algorithm

The API (`api/main.py`) works in two stages.

Retrieve (Elasticsearch, top 10): a `bool/should` query that combines exact `term`
clauses on the strong identifiers (domain x50, phone x40, fb handle x35) with a
fuzzy `multi_match` over the three name fields plus a name-to-domain-text match. Any
single signal is enough to surface the right candidate.

Re-score (Python): per-field similarity (exact for domain/phone/fb, RapidFuzz
`token_set_ratio` for names, including a name-vs-domain comparison), combined as a
weighted average that is normalized over only the fields actually provided in the
request (domain 100, phone 70, facebook 60, name 45). Two rules matter here:

- Strong-identifier override: an exact hit on domain, phone, or fb handle floors the
  score at 90. These are unique keys, so a conflicting fuzzy name should not veto
  them. Sample row 8 is a deliberate trap: the name is "Denham's Florist Inc" but the
  website is `dreamservicesoftware.com`. We return the website's profile because the
  website is the real identifier and the name is noise, and the signal breakdown
  shows the caller exactly where the conflict is.
- Threshold of 30: below that we return `match: null` instead of guessing. Returning
  something always maximizes raw match rate, but returning nothing below a confidence
  floor keeps precision honest. This threshold is the one knob trading those off.

The result is 32/32 matched. Input normalization does a lot of the work, since the
sample contains malformed URLs (`https://https//acornlawpc.com/`), phones in four
different formats, names that are pure noise (`..`, `Inc.`, `&AWL`), and
conflicting field combinations.

## 5. Bonus: measuring match accuracy

Match rate only tells you a profile was returned. Accuracy asks whether it was the
right one. There are no ground-truth labels in the dataset, so here are ways to
measure accuracy, one of which I implemented:

1. Signal-agreement score (implemented). Every response carries per-field `signals`
   in [0,1]. The fraction of provided input fields that agree at >=0.8 with the
   returned profile is a per-match accuracy proxy. On the sample it is 69%, which
   correctly flags that several matches rest on one strong field while another field
   disagrees.
2. Held-out identifier test. Query with a subset of fields (say name only) and check
   whether a withheld field (the known phone) shows up on the returned profile. Fully
   automatic, no labeling, and it measures correctness directly.
3. Consistency check. Matching on `{name}` and on `{website}` from the same input row
   should return the same document. The disagreement rate is an error bound.
4. Human-labeled sample. Label ~100 random (input, returned profile) pairs as
   correct or incorrect and report precision with a confidence interval. Most
   reliable, and cheap at this volume.
5. Score calibration. Bucket matches by confidence score and check that labeled
   precision per bucket rises monotonically. If it does, the score itself becomes a
   trustworthy accuracy statement.

## Repo layout

```
scraper/crawl.py        async crawler (concurrency 100, contact-page follow-up)
scraper/extract.py      phone/social/address extraction
analysis/analyze.py     coverage + fill rates
analysis/evaluate.py    match-rate harness for API-input-sample.csv
indexer/build_index.py  merge + ES mapping + bulk index
api/main.py             FastAPI /match endpoint (normalize -> retrieve -> re-score)
data/                   input CSVs + scraped.json
```

---

AI was used during development to help with efficiency and to double-check the
correctness of the code. The actions taken and all of the design decisions were
made by me.
