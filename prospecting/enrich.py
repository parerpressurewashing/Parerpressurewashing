#!/usr/bin/env python3
"""
Enrich a prospect CSV by visiting each row's website and extracting:
  - emails (regex)
  - likely manager name + role (e.g. "Jane Smith, General Manager")

Respects robots.txt for the homepage. Skips rows that already have a contact_name.

Usage:
    python enrich.py --in output/brisbane_hotels.csv --out output/brisbane_hotels_enriched.csv
"""
from __future__ import annotations

import csv
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import click
import requests
from bs4 import BeautifulSoup

USER_AGENT = "ParerPressureWashing-Prospecting/1.0 (contact: louis@parerpressurewashing.com)"

CANDIDATE_PATHS = [
    "/", "/about", "/about-us", "/contact", "/contact-us",
    "/our-team", "/team", "/management", "/staff", "/people",
]

ROLE_KEYWORDS = [
    # ordered roughly by seniority / relevance for property decisions
    "General Manager", "Hotel Manager", "Property Manager",
    "Operations Manager", "Centre Manager", "Center Manager",
    "Facilities Manager", "Maintenance Manager", "Building Manager",
    "Owner", "Proprietor", "Director", "Managing Director",
    "Manager",
]

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

# Words that get capitalised at the start of headings but aren't names.
NAME_BLOCKLIST = {
    "our", "about", "meet", "contact", "welcome", "team", "hello", "hi", "hey",
    "dear", "see", "view", "visit", "read", "learn", "find", "get", "call",
    "email", "phone", "the", "this", "that", "these", "those", "all", "more",
    "join", "follow", "click", "book", "request", "free", "new",
}

# Names: 2-3 Capitalised words with optional apostrophes/hyphens.
NAME = r"[A-Z][a-zA-Z'\-]+(?:\s+[A-Z][a-zA-Z'\-]+){1,2}"


def _role_alt() -> str:
    return "|".join(re.escape(r) for r in ROLE_KEYWORDS)


def _compile_role_patterns() -> list[tuple[re.Pattern, str]]:
    role_alt = _role_alt()
    return [
        (re.compile(rf"({NAME})\s*[,\-–—:]\s*({role_alt})"), "name_first"),
        (re.compile(rf"({role_alt})\s*[:,\-–—]\s*({NAME})"), "role_first"),
    ]


ROLE_PATTERNS = _compile_role_patterns()


def _clean_candidate_name(raw: str) -> str:
    """Drop leading words that look like headers (Our, Meet, Team, etc.)."""
    words = raw.split()
    while words and words[0].lower() in NAME_BLOCKLIST:
        words.pop(0)
    if len(words) < 2:
        return ""
    return " ".join(words)


def robots_allows(url: str, session: requests.Session) -> bool:
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    rp = RobotFileParser()
    try:
        r = session.get(urljoin(base, "/robots.txt"), timeout=10)
        if r.status_code >= 400:
            return True
        rp.parse(r.text.splitlines())
        return rp.can_fetch(USER_AGENT, url)
    except Exception:
        return True


def fetch(url: str, session: requests.Session) -> str | None:
    try:
        r = session.get(url, timeout=15, allow_redirects=True)
        if r.status_code != 200 or "text/html" not in r.headers.get("Content-Type", ""):
            return None
        return r.text
    except requests.RequestException:
        return None


def extract_contacts(html: str, domain: str) -> tuple[str, str, str]:
    """Return (name, role, email) best-guess from a page's HTML."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)

    name, role = "", ""
    for pattern, order in ROLE_PATTERNS:
        for m in pattern.finditer(text):
            if order == "name_first":
                cand_name, cand_role = m.group(1).strip(), m.group(2).strip().title()
            else:
                cand_role, cand_name = m.group(1).strip().title(), m.group(2).strip()
            cleaned = _clean_candidate_name(cand_name)
            if cleaned:
                name, role = cleaned, cand_role
                break
        if name:
            break

    # Prefer emails on the prospect's own domain
    emails = EMAIL_RE.findall(text)
    same_domain = [e for e in emails if domain and domain in e.lower()]
    email = (same_domain or emails or [""])[0]

    return name, role, email


def enrich_row(row: dict, session: requests.Session, delay: float) -> dict:
    site = (row.get("website") or "").strip()
    if not site or row.get("contact_name"):
        return row
    if not site.startswith("http"):
        site = "https://" + site

    parsed = urlparse(site)
    domain = parsed.netloc.lower().replace("www.", "")

    if not robots_allows(site, session):
        return row

    for path in CANDIDATE_PATHS:
        url = urljoin(site, path)
        html = fetch(url, session)
        if not html:
            continue
        name, role, email = extract_contacts(html, domain)
        if name or email:
            row["contact_name"] = row.get("contact_name") or name
            row["contact_role"] = row.get("contact_role") or role
            row["email"] = row.get("email") or email
            if name and email:
                break
        time.sleep(delay)
    return row


@click.command()
@click.option("--in", "in_path", required=True, help="Input prospects CSV (from scrape.py).")
@click.option("--out", "out_path", required=True, help="Output enriched CSV path.")
@click.option("--delay", type=float, default=1.5, help="Seconds between requests (be polite).")
@click.option("--limit", type=int, default=0, help="Only enrich the first N rows (0 = all).")
def main(in_path: str, out_path: str, delay: float, limit: int) -> None:
    rows: list[dict] = []
    with open(in_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        for r in reader:
            rows.append(r)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    to_process = rows[:limit] if limit else rows
    skipped = len(rows) - len(to_process)
    enriched = 0

    for i, row in enumerate(to_process, 1):
        before_name = row.get("contact_name", "")
        before_email = row.get("email", "")
        enrich_row(row, session, delay)
        if (row.get("contact_name") and row["contact_name"] != before_name) or (
            row.get("email") and row["email"] != before_email
        ):
            enriched += 1
        if i % 10 == 0:
            click.echo(f"  [{i}/{len(to_process)}] enriched so far: {enriched}")

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    click.echo(f"Done. Enriched {enriched}/{len(to_process)} rows ({skipped} unprocessed). Wrote {out_path}")


if __name__ == "__main__":
    main()
