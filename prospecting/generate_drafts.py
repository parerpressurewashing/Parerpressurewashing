#!/usr/bin/env python3
"""
Generate per-prospect outreach drafts that match your tone.

Two modes:
  1. Template (default) - extracts tone features from past_messages.txt and
     fills templates that mirror your phrasing. No API key needed.
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
import random
import re
import statistics
from collections import Counter
from pathlib import Path

import click

CONTRACTIONS = {"don't", "i'll", "we'll", "you'll", "we're", "i'm", "you're",
                "can't", "won't", "it's", "that's", "what's"}
GREETINGS_CASUAL = {"hi", "hey", "g'day", "morning", "afternoon"}
GREETINGS_FORMAL = {"hello", "good morning", "good afternoon", "dear"}
SIGNOFFS_CASUAL = {"cheers", "ta", "talk soon", "thanks heaps", "many thanks"}
SIGNOFFS_FORMAL = {"kind regards", "best regards", "sincerely", "yours"}

# Phrases we'll try to detect because they tend to be load-bearing voice markers.
SOFTENERS = [
    "just wondering", "just wanted to", "just letting you know",
    "just checking", "just want to", "if possible", "if that suits",
    "if that's ok", "if it suits", "no worries", "no pressure",
    "absolutely no stress", "no rush", "if you could please",
    "would be amazing", "would be brilliant", "would be greatly appreciated",
    "if you may also",
]

CLOSERS = [
    "look forward to seeing you", "look forward to speaking with you",
    "look forward to hearing from you", "looking forward to",
    "see you then", "see you soon", "many thanks", "thanks so much",
    "hope you are well", "hope your well", "hope you're well",
    "hope you have an amazing", "hope you have a great",
]

GREET_CHUNK_RE = re.compile(r"^(hi|hey|hello)[^.,!?]*[,.!?]\s*", re.IGNORECASE)
SIGNATURE_RE = re.compile(r"(?:many thanks|cheers|regards)[,.\s]*louis\.?\s*$", re.IGNORECASE)


def _strip_greeting_and_signature(msg: str) -> str:
    """Drop the leading 'Hi X,' and trailing 'Many thanks, Louis.' so we can study the middle."""
    body = GREET_CHUNK_RE.sub("", msg).strip()
    body = SIGNATURE_RE.sub("", body).strip().rstrip(",.")
    return body


def _count_phrases(msgs: list[str], phrases: list[str]) -> list[tuple[str, int]]:
    text = " ".join(msgs).lower()
    counts = [(p, text.count(p)) for p in phrases]
    return sorted([c for c in counts if c[1] > 0], key=lambda x: -x[1])


def _common_first_sentence_after_greeting(msgs: list[str], top_n: int = 5) -> list[str]:
    """What does Louis typically say *first* after 'Hi X,'?"""
    firsts: list[str] = []
    for m in msgs:
        body = GREET_CHUNK_RE.sub("", m).strip()
        if not body:
            continue
        first = re.split(r"[.!?]", body, maxsplit=1)[0].strip()
        if 2 <= len(first.split()) <= 12:
            firsts.append(first.lower())
    counter = Counter(firsts)
    return [phrase for phrase, _ in counter.most_common(top_n)]


def _common_signoff(msgs: list[str]) -> str | None:
    matches = [SIGNATURE_RE.search(m) for m in msgs]
    matches = [m.group(0).strip().rstrip(".") for m in matches if m]
    if not matches:
        return None
    return Counter(matches).most_common(1)[0][0]


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

    formality = "casual" if (casual_greet + casual_sign) >= (formal_greet + formal_sign) else "formal"

    return {
        "message_count": len(msgs),
        "avg_words": round(statistics.mean(lengths), 1),
        "median_words": int(statistics.median(lengths)),
        "formality": formality,
        "contraction_rate": round(contraction_rate, 2),
        "exclamations_per_msg": round(exclamations, 2),
        "uses_emoji": emoji_rate > 0.1,
        "common_first_sentences": _common_first_sentence_after_greeting(msgs),
        "common_softeners": _count_phrases(msgs, SOFTENERS)[:8],
        "common_closers": _count_phrases(msgs, CLOSERS)[:8],
        "signoff": _common_signoff(msgs) or "Many thanks, Louis",
    }


# Templates written to mirror Louis's actual phrasing:
#   - "Hi [name], hope you are well." opener
#   - "Just wondering if..." softener
#   - "no pressure / absolutely no stress" opt-out
#   - "Look forward to hearing from you. Many thanks, Louis." closer

TEMPLATES_NAMED = [
    "Hi {first_name}, hope you are well. Louis here from Parer's Pressure "
    "Washing, a Brisbane exterior cleaning business. Just wondering if "
    "{company} would be open to a free quote on the {target} — happy to come "
    "by whenever suits, absolutely no pressure if not the right time. "
    "Look forward to hearing from you. {signoff}",

    "Hi {first_name}, hope your well. Louis from Parer's Pressure Washing in "
    "Brisbane here. Just wanted to reach out as we look after a fair few "
    "{category_friendly} around town and would love the chance to send "
    "through a no-obligation quote for the {target} at {company}. No worries "
    "at all if the timing isn't right. {signoff}",

    "Hi {first_name}, hope you are well. My name is Louis Parer, I run "
    "Parer's Pressure Washing in Brisbane. Just wondering if you'd be "
    "interested in a quick quote on the {target} at {company} — happy to "
    "come and have a look whenever suits. Look forward to speaking with you "
    "soon. {signoff}",
]

TEMPLATES_NONAME = [
    "Hi there, hope you are well. Louis here from Parer's Pressure Washing, "
    "a Brisbane-based exterior cleaning business. Just wondering if "
    "{company} would be open to a free quote on the {target} — no pressure "
    "at all, happy to swing by whenever suits. Look forward to hearing from "
    "you. {signoff}",

    "Hi there, hope your well. Louis from Parer's Pressure Washing in "
    "Brisbane. Just wanted to reach out as we look after a fair few "
    "{category_friendly} around the area and would love to send through a "
    "no-obligation quote on the {target} at {company}. Absolutely no stress "
    "if the timing isn't right. {signoff}",
]

CATEGORY_FRIENDLY = {
    "hotels": "hotels and motels",
    "shopping": "shopping centres and retail plazas",
    "storefronts": "cafes, restaurants and storefronts",
}

TARGET_FOR_CATEGORY = {
    "hotels": "facade, walkways and carpark",
    "shopping": "storefronts, walkways and carpark",
    "storefronts": "shopfront, footpath and awnings",
}


def first_name(full_name: str) -> str:
    return full_name.strip().split(" ")[0] if full_name else ""


def fill_template_row(row: dict, profile: dict, rng: random.Random) -> str:
    fname = first_name(row.get("contact_name", ""))
    cat = row.get("category", "")
    ctx = {
        "first_name": fname or "there",
        "company": row.get("name", "your business"),
        "category_friendly": CATEGORY_FRIENDLY.get(cat, "local businesses"),
        "target": TARGET_FOR_CATEGORY.get(cat, "building exterior"),
        "signoff": profile.get("signoff", "Many thanks, Louis"),
    }
    pool = TEMPLATES_NAMED if fname else TEMPLATES_NONAME
    template = rng.choice(pool)
    return template.format(**ctx)


def llm_draft(row: dict, profile: dict, samples: list[str], model: str) -> str:
    try:
        from anthropic import Anthropic
    except ImportError:
        raise SystemExit("LLM mode requires: pip install anthropic")

    client = Anthropic()
    system = (
        "You are drafting a single outreach message from Louis Parer of Parer's "
        "Pressure Washing, a Brisbane exterior cleaning business reaching out to a "
        "commercial prospect (hotel, shopping centre, storefront) for the FIRST "
        "time. Mirror Louis's voice EXACTLY using the provided tone profile and "
        "sample messages. Specifically:\n"
        "  - Open with 'Hi [name], hope you are well.' (or 'hope your well' if "
        "    that variant appears in samples).\n"
        "  - Introduce yourself briefly in the first or second sentence.\n"
        "  - Use a soft 'Just wondering if...' or 'Just wanted to reach out as...' "
        "    pattern for the ask.\n"
        "  - Include a soft opt-out ('no pressure', 'absolutely no stress if not "
        "    the right time', etc).\n"
        "  - Close with 'Look forward to hearing from you' (or similar) and the "
        "    signoff from the profile (e.g., 'Many thanks, Louis').\n"
        "  - 3-5 sentences total. Plain text. No emoji unless the profile says "
        "    so.\n"
        "  - Never invent facts about the prospect's business beyond what is "
        "    provided. If contact_name is missing, address them as 'Hi there'.\n"
        "Output the message only, no preamble, no quotes."
    )
    user = json.dumps({
        "tone_profile": profile,
        "sample_messages": samples[:15],
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
               f"avg_words={profile['avg_words']}, samples={profile['message_count']}, "
               f"signoff={profile.get('signoff')!r}")
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
