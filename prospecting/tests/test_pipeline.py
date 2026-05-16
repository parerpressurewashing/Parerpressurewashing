"""Offline tests - no network. Run with: python -m pytest tests/  (or python tests/test_pipeline.py)"""
from __future__ import annotations

import csv
import io
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scrape import dedupe, normalise_element, CSV_COLUMNS, build_overpass_query
from enrich import extract_contacts
from generate_drafts import build_tone_profile, fill_template_row


FIXTURE = Path(__file__).resolve().parent.parent / "sample_data" / "overpass_fixture.json"
TONE_SAMPLE = Path(__file__).resolve().parent.parent / "sample_data" / "past_messages_example.txt"


def test_normalise_and_dedupe():
    data = json.loads(FIXTURE.read_text())
    rows = [normalise_element(el, "hotels" if el["tags"].get("tourism") else
                              "shopping" if el["tags"].get("shop") else "storefronts")
            for el in data["elements"]]
    assert len(rows) == 4
    deduped = dedupe(rows)
    assert len(deduped) == 3, "Duplicate Example Cafe rows should collapse"
    hotel = next(r for r in deduped if r["name"] == "Example Brisbane Hotel")
    assert hotel["phone"] == "+61 7 1234 5678"
    assert hotel["website"] == "https://example-hotel.test"
    assert hotel["email"] == "info@example-hotel.test"
    assert hotel["address"] == "100 Queen Street"
    assert hotel["postcode"] == "4000"
    for col in CSV_COLUMNS:
        assert col in hotel
    print("OK test_normalise_and_dedupe")


def test_overpass_query_format():
    q = build_overpass_query(2316593, [("tourism", "hotel"), ("tourism", "motel")])
    assert "area(3602316593)" in q, "OSM relation id must be offset by 3.6e9"
    assert '["tourism"="hotel"]' in q
    assert '["tourism"="motel"]' in q
    assert "out tags center" in q
    print("OK test_overpass_query_format")


def test_extract_contacts():
    html = """
    <html><body>
    <h2>Our Team</h2>
    <p>Sarah Johnson, General Manager</p>
    <p>Contact: sarah.j@example-hotel.test or info@example-hotel.test</p>
    </body></html>
    """
    name, role, email = extract_contacts(html, "example-hotel.test")
    assert name == "Sarah Johnson", f"got {name!r}"
    assert "General Manager" in role, f"got {role!r}"
    assert email.endswith("@example-hotel.test"), f"got {email!r}"
    print("OK test_extract_contacts")


def test_extract_contacts_role_first():
    html = "<p>General Manager: Tom O'Brien</p>"
    name, role, _ = extract_contacts(html, "x.test")
    assert name == "Tom O'Brien", f"got {name!r}"
    assert "General Manager" in role
    print("OK test_extract_contacts_role_first")


def test_tone_profile_and_template():
    text = TONE_SAMPLE.read_text()
    profile = build_tone_profile(text)
    assert profile["formality"] == "casual", profile
    assert profile["message_count"] >= 5
    row = {
        "name": "Example Brisbane Hotel",
        "category": "hotels",
        "contact_name": "Sarah Johnson",
        "contact_role": "General Manager",
    }
    draft = fill_template_row(row, profile, random.Random(0))
    assert "Sarah" in draft
    assert "Example Brisbane Hotel" in draft
    assert "Parer" in draft
    print("OK test_tone_profile_and_template")


def test_tone_template_no_contact_name():
    profile = build_tone_profile(TONE_SAMPLE.read_text())
    row = {"name": "Example Cafe", "category": "storefronts", "contact_name": ""}
    draft = fill_template_row(row, profile, random.Random(0))
    assert "Example Cafe" in draft
    assert "Parer" in draft
    print("OK test_tone_template_no_contact_name")


if __name__ == "__main__":
    test_normalise_and_dedupe()
    test_overpass_query_format()
    test_extract_contacts()
    test_extract_contacts_role_first()
    test_tone_profile_and_template()
    test_tone_template_no_contact_name()
    print("\nAll tests passed.")
