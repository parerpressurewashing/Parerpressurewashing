#!/usr/bin/env python3
"""
Generate per-prospect outreach drafts that match your tone.

Two modes:
  1. Template (default) - extracts tone features from past_messages.txt and
     fills templates. No API key needed.
  2. LLM (--llm)        - sends tone samples + prospect details to Claude.
     Requires ANTHROPIC_API_KEY env var and `pip install anthropic`.

Tone input format (past_messages.txt):
  One message per line. Only YOUR sent messages, not the client's replies.
  Strip phone numbers and client names before sharing.

Usage:
    python generate_drafts.py --csv output/brisbane_hotels_enriched.csv \
                              --tone past_messages.txt \
                              --out output/brisbane_hotels_drafts.csv
    python generate_drafts.py --csv ... --tone ... --out ... --llm
"""
from __future__ import annotations

import csv
import json
import os
import random
import re
import statistics
import sys
from pathlib import Path

import click

CONTRACTIONS = {"don't", "i'll", "we'll", "you'll", "we're", "i'm", "you're", "can't", "won't", "it's", "that's"}
GREETINGS_CASUAL = {"hi", "hey", "g'day", "morning", "afternoon"}
GREETINGS_FORMAL = {"hello", "good morning", "good afternoon", "dear"}
SIGNOFFS_CASUAL = {"cheers", "thanks", "ta", "talk soon", "thanks heaps"}
SIGNOFFS_FORMAL = {"kind regards", "regards", "best regards", "sincerely"}


def build_tone_profile(text: str) -> dict:
    msgs = [m.strip() for m in text.splitlines() if m.strip() and not m.startswith("#")]
    if not msgs:
        raise ValueError("No messages found in tone file.")

    lengths = [len(m.split()) for m in msgs]
    lowered = [m.lower() for m in msgs]
    all_text = " ".join(lowered)

    contraction_rate = sum(1 for c in CONTRACTIONS if c in all_text) / max(len(CONTRACTIONS), 1)
    casual_greet = sum(1 for g in GREETINGS_CASUAL if any(m.startswith(g) for m in lowered))
    formal_greet = sum(1 for g in GREETINGS_FORMAL if any(m.startswith(g) for m in lowered))
    casual_sign = sum(1 for s in SIGNOFFS_CASUAL if s in all_text)
    formal_sign = sum(1 for s in SIGNOFFS_FORMAL if s in all_text)
    exclamations = sum(m.count("!") for m in msgs) / len(msgs)
    emoji_rate = sum(1 for m in msgs if re.search(r"[\U0001F300-\U0001FAFF]|:\)|:\(", m)) / len(msgs)

    # Sample openers and closers
    openers = [m.split(".")[0][:60] for m in msgs[:30] if len(m) > 10]
    closers = [m.split(".")[-1].strip()[-60:] for m in msgs[:30] if len(m) > 10]

    formality = "casual" if (casual_greet + casual_sign) >= (formal_greet + formal_sign) else "formal"

    return {
        "message_count": len(msgs),
        "avg_words": round(statistics.mean(lengths), 1),
        "median_words": int(statistics.median(lengths)),
        "formality": formality,
        "contraction_rate": round(contraction_rate, 2),
        "exclamations_per_msg": round(exclamations, 2),
        "uses_emoji": emoji_rate > 0.1,
        "sample_openers": openers[:10],
        "sample_closers": closers[:10],
    }


TEMPLATES_CASUAL = [
    "Hi {first_name}, Louis here from Parer's Pressure Washing in Brisbane. "
    "Saw {company} and reckon your {target} would scrub up really well with a "
    "professional clean. Happy to swing by for a quick quote whenever suits — "
    "no pressure either way. Cheers, Louis",

    "Hey {first_name}, Louis from Parer's Pressure Washing. We look after a "
    "fair few {category_friendly} around Brisbane and noticed {company}. "
    "Keen to drop a quick quote your way if you're after a refresh on the "
    "{target}. Let me know — Louis",
]

TEMPLATES_FORMAL = [
    "Hello {first_name}, my name is Louis Parer from Parer's Pressure Washing, "
    "a Brisbane-based exterior cleaning business. I'd like to offer {company} a "
    "complimentary quote for {target} cleaning at your convenience. "
    "Kind regards, Louis Parer",

    "Hello {first_name}, I'm writing on behalf of Parer's Pressure Washing in "
    "Brisbane. We provide professional exterior cleaning to {category_friendly} "
    "and would welcome the opportunity to quote on the {target} at {company}. "
    "Regards, Louis",
]

# Fallback when no contact_name is available
TEMPLATES_NONAME_CASUAL = [
    "Hi there, Louis from Parer's Pressure Washing in Brisbane. We do "
    "exterior cleaning for {category_friendly} and thought {company}'s "
    "{target} could do with a refresh. Happy to send a quick no-obligation "
    "quote — just let me know. Cheers, Louis",
]
TEMPLATES_NONAME_FORMAL = [
    "Hello, my name is Louis Parer of Parer's Pressure Washing, Brisbane. "
    "I'd like to offer {company} a complimentary quote for exterior "
    "cleaning of the {target}. Regards, Louis Parer",
]

CATEGORY_FRIENDLY = {
    "hotels": "hotels and motels",
    "shopping": "shopping centres and retail plazas",
    "storefronts": "cafes, restaurants and storefronts",
}

TARGET_FOR_CATEGORY = {
    "hotels": "facade, walkways and carpark",
    "shopping": "storefront, walkways and carpark",
    "storefronts": "shopfront, footpath and awnings",
}


def first_name(full_name: str) -> str:
    return full_name.strip().split(" ")[0] if full_name else ""


def fill_template_row(row: dict, profile: dict, rng: random.Random) -> str:
    formality = profile["formality"]
    fname = first_name(row.get("contact_name", ""))
    cat = row.get("category", "")
    ctx = {
        "first_name": fname or "there",
        "company": row.get("name", "your business"),
        "category_friendly": CATEGORY_FRIENDLY.get(cat, "local businesses"),
        "target": TARGET_FOR_CATEGORY.get(cat, "building exterior"),
    }
    if fname:
        pool = TEMPLATES_CASUAL if formality == "casual" else TEMPLATES_FORMAL
    else:
        pool = TEMPLATES_NONAME_CASUAL if formality == "casual" else TEMPLATES_NONAME_FORMAL
    template = rng.choice(pool)
    return template.format(**ctx)


def llm_draft(row: dict, profile: dict, samples: list[str], model: str) -> str:
    try:
        from anthropic import Anthropic
    except ImportError:
        raise SystemExit("LLM mode requires: pip install anthropic")

    client = Anthropic()
    system = (
        "You write short outreach messages (SMS or email opener length, 2-4 sentences) "
        "from Louis Parer of Parer's Pressure Washing, a Brisbane exterior cleaning "
        "business. Match the writer's tone exactly using the provided tone profile and "
        "sample messages. Be specific to the prospect. Never invent facts about the "
        "prospect's business beyond what is provided. Always include a soft opt-out "
        "line at the end (e.g., 'no worries if not the right time'). Output the message "
        "only, no preamble."
    )
    user = json.dumps({
        "tone_profile": profile,
        "sample_messages": samples[:20],
        "prospect": {
            "name": row.get("name"),
            "category": row.get("category"),
            "contact_name": row.get("contact_name"),
            "contact_role": row.get("contact_role"),
            "suburb": row.get("suburb"),
        },
    }, indent=2)

    resp = client.messages.create(
        model=model,
        max_tokens=400,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return resp.content[0].text.strip()


@click.command()
@click.option("--csv", "csv_path", required=True, help="Prospects CSV (enriched).")
@click.option("--tone", "tone_path", required=True, help="Path to past_messages.txt")
@click.option("--out", "out_path", required=True, help="Output CSV with drafts column.")
@click.option("--llm", is_flag=True, help="Use Claude API for drafts (needs ANTHROPIC_API_KEY).")
@click.option("--model", default="claude-haiku-4-5-20251001", help="Anthropic model id for --llm.")
@click.option("--seed", type=int, default=42, help="Template selection seed for reproducibility.")
@click.option(
    "--profile-out",
    default="tone_profile.json",
    help="Write the extracted tone profile to this path (review-then-tweak).",
)
def main(csv_path: str, tone_path: str, out_path: str, llm: bool, model: str, seed: int, profile_out: str) -> None:
    tone_text = Path(tone_path).read_text(encoding="utf-8")
    profile = build_tone_profile(tone_text)
    Path(profile_out).write_text(json.dumps(profile, indent=2), encoding="utf-8")
    click.echo(f"Tone profile: formality={profile['formality']}, "
               f"avg_words={profile['avg_words']}, samples={profile['message_count']}")
    click.echo(f"  -> wrote {profile_out}")

    samples = [m.strip() for m in tone_text.splitlines() if m.strip() and not m.startswith("#")]

    rng = random.Random(seed)
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    if "draft_message" not in fieldnames:
        fieldnames.append("draft_message")

    for i, row in enumerate(rows, 1):
        if llm:
            row["draft_message"] = llm_draft(row, profile, samples, model)
        else:
            row["draft_message"] = fill_template_row(row, profile, rng)
        if i % 25 == 0:
            click.echo(f"  drafted {i}/{len(rows)}")

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    click.echo(f"Wrote {len(rows)} drafts to {out_path}")


if __name__ == "__main__":
    main()
