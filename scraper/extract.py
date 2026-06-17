"""Datapoint extraction from raw HTML: phones, social links, addresses."""
from __future__ import annotations

import re

import phonenumbers
from phonenumbers import PhoneNumberMatcher, PhoneNumberFormat
from selectolax.parser import HTMLParser

SOCIAL_PATTERNS = {
    "facebook": re.compile(r"(?:https?:)?//(?:[\w-]+\.)?facebook\.com/(?!sharer|share|plugins|tr\b|dialog)[\w\-./?=%&]+", re.I),
    "instagram": re.compile(r"(?:https?:)?//(?:[\w-]+\.)?instagram\.com/(?!p/|share)[\w\-./?=%&]+", re.I),
    "twitter": re.compile(r"(?:https?:)?//(?:[\w-]+\.)?(?:twitter|x)\.com/(?!intent|share|home)[\w\-./?=%&]+", re.I),
    "linkedin": re.compile(r"(?:https?:)?//(?:[\w-]+\.)?linkedin\.com/(?:company|in|school)/[\w\-./?=%&]+", re.I),
    "youtube": re.compile(r"(?:https?:)?//(?:[\w-]+\.)?youtube\.com/(?:channel|c|user|@)[\w\-./?=%&@]+", re.I),
}

# number + street words + optional city/state/zip
ADDRESS_RE = re.compile(
    r"\b\d{1,5}\s+(?:[A-Z][\w.'-]*\s+){0,4}"
    r"(?:Street|St\.?|Avenue|Ave\.?|Boulevard|Blvd\.?|Road|Rd\.?|Drive|Dr\.?|Lane|Ln\.?|Court|Ct\.?|Place|Pl\.?|Way|Highway|Hwy\.?|Suite|Ste\.?|Parkway|Pkwy\.?|Circle|Cir\.?|Square|Sq\.?)"
    r"(?:[,\s]+(?:Suite|Ste\.?|Unit|#)\s*[\w-]+)?"
    r"(?:[,\s]+[A-Z][a-zA-Z.\s]{2,25})?"
    r"(?:[,\s]+[A-Z]{2})?"
    r"(?:[,\s]+\d{5}(?:-\d{4})?)?",
)
ZIP_STATE_RE = re.compile(r"\b[A-Z]{2}[,\s]+\d{5}(?:-\d{4})?\b")

PHONE_HREF_RE = re.compile(r'href=["\']tel:([^"\']+)["\']', re.I)

CONTACT_HINTS = ("contact", "about", "kontakt", "impressum", "location", "find-us", "reach")


def _clean_social(url: str) -> str:
    url = url.strip().rstrip("/.")
    if url.startswith("//"):
        url = "https:" + url
    return url.split("?")[0].split("#")[0]


def extract_phones(html: str, text: str) -> list[str]:
    # tel: links are the most reliable, so take those first, then scan the text
    found: dict[str, None] = {}
    for raw in PHONE_HREF_RE.findall(html):
        raw = raw.replace("%20", " ").strip()
        try:
            num = phonenumbers.parse(raw, "US")
            if phonenumbers.is_valid_number(num):
                found[phonenumbers.format_number(num, PhoneNumberFormat.E164)] = None
        except phonenumbers.NumberParseException:
            pass
    # cap how much text we scan so big pages don't blow up the runtime
    for match in PhoneNumberMatcher(text[:60000], "US"):
        if phonenumbers.is_valid_number(match.number):
            found[phonenumbers.format_number(match.number, PhoneNumberFormat.E164)] = None
        if len(found) >= 8:
            break
    return list(found)


def extract_socials(html: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for network, pat in SOCIAL_PATTERNS.items():
        links = {_clean_social(m.group(0)) for m in pat.finditer(html)}
        # drop bare domain links like facebook.com/ with nothing after
        links = {l for l in links if len(l.split(".com/", 1)[-1]) > 1}
        if links:
            out[network] = sorted(links)[:3]
    return out


def extract_addresses(text: str) -> list[str]:
    found: dict[str, None] = {}
    for m in ADDRESS_RE.finditer(text[:80000]):
        candidate = re.sub(r"\s+", " ", m.group(0)).strip(" ,")
        if 8 <= len(candidate) <= 120:
            found[candidate] = None
        if len(found) >= 5:
            break
    return list(found)


def parse_page(html: str) -> dict:
    tree = HTMLParser(html)
    for sel in ("script", "style", "noscript", "svg"):
        for node in tree.css(sel):
            node.decompose()
    text = tree.body.text(separator=" ") if tree.body else ""
    text = re.sub(r"\s+", " ", text)
    return {
        "phones": extract_phones(html, text),
        "socials": extract_socials(html),
        "addresses": extract_addresses(text),
    }


def find_contact_links(html: str, base_url: str) -> list[str]:
    """Return up to 2 same-site links that look like contact/about pages."""
    from urllib.parse import urljoin, urlparse

    tree = HTMLParser(html)
    base_host = urlparse(base_url).netloc.removeprefix("www.")
    seen: dict[str, None] = {}
    for a in tree.css("a[href]"):
        href = a.attributes.get("href") or ""
        if not any(h in href.lower() for h in CONTACT_HINTS):
            continue
        full = urljoin(base_url, href)
        p = urlparse(full)
        if p.scheme not in ("http", "https"):
            continue
        if p.netloc.removeprefix("www.") != base_host:
            continue
        seen[full.split("#")[0]] = None
        if len(seen) >= 2:
            break
    return list(seen)
