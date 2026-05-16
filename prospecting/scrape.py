#!/usr/bin/env python3
"""
Scrape commercial prospects (hotels, shopping centres, storefronts) from
OpenStreetMap via the Overpass API for a given city. No API key required.

Usage:
    python scrape.py --city "Brisbane" --types hotels,shopping,storefronts
    python scrape.py --city "Brisbane" --types hotels --out output/brisbane_hotels.csv
"""
from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path

import click
import requests

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "ParerPressureWashing-Prospecting/1.0 (contact: louis@parerpressurewashing.com)"

CATEGORY_QUERIES: dict[str, list[tuple[str, str]]] = {
    "hotels": [
        ("tourism", "hotel"),
        ("tourism", "motel"),
        ("tourism", "guest_house"),
        ("tourism", "apartment"),
        ("tourism", "hostel"),
        ("tourism", "resort"),
    ],
    "shopping": [
        ("shop", "mall"),
        ("shop", "department_store"),
        ("shop", "supermarket"),
        ("amenity", "marketplace"),
        ("building", "retail"),
    ],
    "storefronts": [
        ("amenity", "restaurant"),
        ("amenity", "fast_food"),
        ("amenity", "cafe"),
        ("amenity", "pub"),
        ("amenity", "bar"),
    ],
}

CSV_COLUMNS = [
    "name",
    "category",
    "subcategory",
    "address",
    "suburb",
    "postcode",
    "state",
    "phone",
    "website",
    "email",
    "contact_name",
    "contact_role",
    "lat",
    "lon",
    "osm_id",
    "osm_url",
    "source",
]


def lookup_area_id(city: str) -> int | None:
    """Resolve a city name to its OSM relation id via Nominatim."""
    params = {
        "q": city,
        "format": "json",
        "limit": 5,
        "addressdetails": 0,
        "extratags": 0,
    }
    r = requests.get(
        NOMINATIM_URL,
        params=params,
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    r.raise_for_status()
    for hit in r.json():
        if hit.get("osm_type") == "relation":
            return int(hit["osm_id"])
    return None


def build_overpass_query(area_id: int, kv_pairs: list[tuple[str, str]], timeout: int = 180) -> str:
    """Build an Overpass QL query for the given area and tag filters.

    Overpass area ids are OSM relation ids + 3600000000.
    """
    overpass_area = area_id + 3_600_000_000
    selectors = "".join(
        f'nwr["{k}"="{v}"](area.searchArea);' for k, v in kv_pairs
    )
    return (
        f"[out:json][timeout:{timeout}];"
        f"area({overpass_area})->.searchArea;"
        f"({selectors});"
        f"out tags center;"
    )


def fetch_overpass(query: str) -> dict:
    r = requests.post(
        OVERPASS_URL,
        data={"data": query},
        headers={"User-Agent": USER_AGENT},
        timeout=240,
    )
    r.raise_for_status()
    return r.json()


def normalise_element(el: dict, category: str) -> dict:
    """Flatten one OSM element into our CSV schema."""
    tags = el.get("tags", {})
    # Identify subcategory from whichever tag actually matched
    subcat = ""
    for k in ("tourism", "shop", "amenity", "building"):
        if k in tags:
            subcat = f"{k}={tags[k]}"
            break

    address_parts = [
        tags.get("addr:housenumber", ""),
        tags.get("addr:street", ""),
    ]
    address = " ".join(p for p in address_parts if p).strip()

    osm_type = el["type"]
    osm_id = el["id"]
    if osm_type == "node":
        lat, lon = el.get("lat", ""), el.get("lon", "")
    else:
        center = el.get("center", {})
        lat, lon = center.get("lat", ""), center.get("lon", "")

    return {
        "name": tags.get("name", "").strip(),
        "category": category,
        "subcategory": subcat,
        "address": address,
        "suburb": tags.get("addr:suburb", "") or tags.get("addr:city", ""),
        "postcode": tags.get("addr:postcode", ""),
        "state": tags.get("addr:state", ""),
        "phone": tags.get("contact:phone", "") or tags.get("phone", ""),
        "website": tags.get("contact:website", "") or tags.get("website", ""),
        "email": tags.get("contact:email", "") or tags.get("email", ""),
        "contact_name": "",
        "contact_role": "",
        "lat": lat,
        "lon": lon,
        "osm_id": f"{osm_type}/{osm_id}",
        "osm_url": f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
        "source": "openstreetmap",
    }


def dedupe(rows: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for r in rows:
        # Same name + same coords (rounded) => dupe
        key = (
            r["name"].lower(),
            round(float(r["lat"]), 4) if r["lat"] else None,
            round(float(r["lon"]), 4) if r["lon"] else None,
        )
        if key in seen or not r["name"]:
            continue
        seen.add(key)
        out.append(r)
    return out


@click.command()
@click.option("--city", required=True, help='City name, e.g. "Brisbane".')
@click.option(
    "--types",
    default="hotels,shopping,storefronts",
    help="Comma-separated category list. Choose from: hotels, shopping, storefronts.",
)
@click.option(
    "--out",
    "out_path",
    default=None,
    help="Output CSV path. Defaults to output/<city>_<types>.csv",
)
@click.option(
    "--require-website/--no-require-website",
    default=False,
    help="Only keep prospects that have a website (improves enrichment hit rate).",
)
@click.option("--limit", type=int, default=0, help="Cap rows per category (0 = no limit).")
def main(city: str, types: str, out_path: str | None, require_website: bool, limit: int) -> None:
    categories = [c.strip() for c in types.split(",") if c.strip()]
    unknown = [c for c in categories if c not in CATEGORY_QUERIES]
    if unknown:
        click.echo(f"Unknown categories: {unknown}. Valid: {list(CATEGORY_QUERIES)}", err=True)
        sys.exit(2)

    click.echo(f"Resolving area id for {city!r} via Nominatim...")
    area_id = lookup_area_id(city)
    if not area_id:
        click.echo(f"Could not resolve city {city!r} to an OSM relation. Try a more specific name.", err=True)
        sys.exit(1)
    click.echo(f"  -> OSM relation id {area_id}")

    all_rows: list[dict] = []
    for cat in categories:
        click.echo(f"Querying Overpass for {cat}...")
        query = build_overpass_query(area_id, CATEGORY_QUERIES[cat])
        try:
            data = fetch_overpass(query)
        except requests.HTTPError as e:
            click.echo(f"  ! Overpass error for {cat}: {e}. Sleeping 10s and retrying once.", err=True)
            time.sleep(10)
            data = fetch_overpass(query)

        elements = data.get("elements", [])
        rows = [normalise_element(el, cat) for el in elements]
        if require_website:
            rows = [r for r in rows if r["website"]]
        if limit:
            rows = rows[:limit]
        click.echo(f"  -> {len(rows)} rows")
        all_rows.extend(rows)
        # Polite pause between large queries
        time.sleep(2)

    before = len(all_rows)
    all_rows = dedupe(all_rows)
    click.echo(f"Deduped {before} -> {len(all_rows)} rows.")

    if not out_path:
        slug = city.lower().replace(" ", "_").replace(",", "")
        cat_slug = "_".join(categories)
        out_path = f"output/{slug}_{cat_slug}.csv"

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(all_rows)
    click.echo(f"Wrote {len(all_rows)} rows to {out_path}")


if __name__ == "__main__":
    main()
