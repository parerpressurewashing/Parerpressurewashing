# Prospecting Pipeline

Tools for building a commercial prospect list (hotels, shopping centres,
storefronts) for Parer's Pressure Washing and generating tone-matched
outreach drafts for import into MailerLite.

```
scrape.py            -> public OSM data       -> prospects.csv
enrich.py            -> visits websites       -> prospects_enriched.csv
generate_drafts.py   -> learns your tone      -> prospects_drafts.csv
review.py            -> interactive approval  -> prospects_for_mailerlite.csv  -> MailerLite
```

---

## ⚠️ Read this before sending anything

Australian law (**Spam Act 2003**, enforced by ACMA) governs commercial
electronic messages — email and SMS.

| | Email (B2B) | SMS / MMS |
|---|---|---|
| Consent | Inferred consent OK if the address is **conspicuously published** by the business, the message is **relevant to that business function**, and there's **no statement against marketing** | **Express consent required** — opt-in. Scraped numbers do **not** count. |
| Required in every message | Sender identity, accurate contact info, **functional unsubscribe** | Same |
| Penalty risk | Up to ~$2.2M/day for a body corporate | Same |

**Practical rule of thumb for this pipeline:**

- **Email channel:** send to `info@`, `manager@`, `gm@`, named `firstname@`
  addresses found on the prospect's website. Always include sender details
  and an unsubscribe link. MailerLite handles the unsubscribe footer
  automatically.
- **SMS channel:** **do not** cold-text scraped phone numbers. Only SMS
  after a prospect replies to email and opts in.
- Honor any "no marketing" / "do not contact" notices on a prospect's
  website. The scraper does not check for these — you must.
- Don't message government, healthcare, or political organisations.
- Keep a suppression list of anyone who unsubscribes or asks to be removed.

If unsure about a specific prospect, skip them. The cost of a complaint
to ACMA is far higher than the cost of one lost lead.

---

## Setup

```bash
cd prospecting
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run the offline tests to confirm everything's wired up:

```bash
python tests/test_pipeline.py
```

---

## 1. Scrape prospects from OpenStreetMap

Uses the free [Overpass API](https://overpass-api.de/) — no key required.
Categories: `hotels`, `shopping`, `storefronts`.

```bash
# Brisbane, all three categories
python scrape.py --city "Brisbane" --types hotels,shopping,storefronts

# Just hotels with a website (better enrichment hit rate)
python scrape.py --city "Brisbane" --types hotels --require-website \
                 --out output/brisbane_hotels.csv

# Try a smaller area for testing
python scrape.py --city "Fortitude Valley" --types storefronts --limit 50
```

Output columns: `name, category, subcategory, address, suburb, postcode,
state, phone, website, email, contact_name, contact_role, lat, lon,
osm_id, osm_url, source`.

**Notes on coverage:** OSM is community-maintained. Coverage varies. For
Brisbane it's strong on hotels and shopping centres, decent on cafes &
restaurants. Expect partial fields — that's what enrichment fixes.

---

## 2. Enrich with website data

Walks each prospect's website (`/`, `/about`, `/contact`, `/team`, etc.)
and extracts:

- Best-guess **contact name** + **role** (looks for "General Manager",
  "Property Manager", "Owner", etc. near a capitalised 2-3 word name)
- Emails (prefers same-domain matches over generic ones)

Respects `robots.txt`. Default 1.5s delay between requests.

```bash
python enrich.py --in  output/brisbane_hotels.csv \
                 --out output/brisbane_hotels_enriched.csv

# Test on a small slice first
python enrich.py --in  output/brisbane_hotels.csv \
                 --out output/test.csv --limit 20
```

**Expect ~30-60% enrichment hit rate.** Larger businesses publish team
pages; small storefronts often don't. Rows without a contact_name are
still useful — you can address them generically or skip them.

---

## 3. Generate tone-matched drafts

### Prepare your tone file

Export your sent iMessages (see main chat — `imessage-exporter` is the
easiest path on Mac), strip phone numbers and client names, keep one
message per line. Save as `past_messages.txt` in this folder (gitignored).

A small example is at `sample_data/past_messages_example.txt`.

### Generate drafts (template mode — no API key)

```bash
python generate_drafts.py \
  --csv  output/brisbane_hotels_enriched.csv \
  --tone past_messages.txt \
  --out  output/brisbane_hotels_drafts.csv
```

Writes a tone profile to `tone_profile.json` so you can sanity-check what
it picked up (formality, avg length, etc.) before generating.

### Generate drafts (Claude mode — better quality)

Needs `pip install anthropic` and `ANTHROPIC_API_KEY` in env.

```bash
pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...
python generate_drafts.py \
  --csv  output/brisbane_hotels_enriched.csv \
  --tone past_messages.txt \
  --out  output/brisbane_hotels_drafts.csv \
  --llm
```

Defaults to `claude-haiku-4-5-20251001` (fast & cheap). Pass `--model`
for Sonnet/Opus if you want higher quality.

---

## 4. Review drafts interactively

Walk through every draft one-by-one, approve/edit/skip each. Saves
progress after every action, so you can quit and resume.

```bash
python review.py --in  output/brisbane_hotels_drafts.csv \
                 --out output/brisbane_hotels_reviewed.csv \
                 --approved-out output/brisbane_for_mailerlite.csv
```

For each row you'll see the prospect (name, contact, email, OSM link)
and the draft. Actions:

| Key | Action |
|---|---|
| `a` | Approve as-is, move to next |
| `e` | Open `$EDITOR` to edit the draft, then move to next |
| `s` | Skip this prospect (excluded from `--approved-out`) |
| `b` | Go back to the previous row (reverts its status to pending) |
| `r` | Restart this row (revert to pending so you can decide later) |
| `q` | Quit and save progress |

Tips:

- Set your editor with `export EDITOR=nano` (or `vim`, `code -w`, etc).
- Add `--skip-no-email` to auto-skip rows that have no email address —
  those can't be reached anyway.
- Rerun the same command to resume — already-reviewed rows are passed
  over automatically.
- Status values written to the reviewed CSV: `approved`, `edited`,
  `skipped`, `pending`. The `--approved-out` file contains only
  `approved` + `edited` rows and drops the status column for a clean
  MailerLite import.

---

## 5. Import to MailerLite

MailerLite import works best when columns match its field names.
Recommended manual rename / mapping in the import UI:

| Pipeline column | MailerLite field |
|---|---|
| `email` | Email *(required)* |
| `contact_name` | Name |
| `name` | company *(custom field)* |
| `phone` | phone *(custom field — do not use for SMS)* |
| `suburb` | city |
| `category` | segment_tag *(custom field — used for segmentation)* |
| `draft_message` | message_draft *(custom field — for templating in campaign)* |

Then in MailerLite:

1. Create a **Group** per category (e.g. "Brisbane Hotels", "Brisbane
   Shopping Centres") and import each CSV slice into its group.
2. Build an **email campaign** that pulls the per-recipient
   `{{message_draft}}` custom field into the body.
3. **Manually review** drafts before sending — the LLM/template will get
   some wrong. Spot-check the first 20 in any send.
4. Set a **conservative send rate** for the first batch (50-100 emails)
   to monitor bounce/complaint rates before scaling up.

---

## Files

```
prospecting/
├── README.md               (this file)
├── requirements.txt
├── scrape.py               OSM Overpass scraper
├── enrich.py               website -> contact name/email extractor
├── generate_drafts.py      tone profile + draft generator
├── review.py               interactive approve/edit/skip TUI
├── sample_data/
│   ├── overpass_fixture.json
│   └── past_messages_example.txt
├── tests/
│   └── test_pipeline.py    offline unit tests (no network needed)
└── output/                 generated CSVs land here (gitignored)
```

---

## Limitations & honest caveats

- **Overpass coverage varies** by region and category. For dense Brisbane
  suburbs you'll get hundreds of hits; for outer suburbs maybe dozens.
- **Manager names are best-guess.** The regex catches "Jane Smith,
  General Manager" patterns. It will miss names buried in JS-rendered
  team pages or PDFs.
- **Email accuracy is not verified.** Pipe the CSV through a verifier
  (NeverBounce, ZeroBounce, MailerLite's own verification) before bulk
  sending or your sender reputation will tank.
- **No CRM dedupe.** If you already have a CSV of existing clients,
  dedupe externally before importing.
- **The pipeline does not handle SMS sending.** Don't try to wire it in.
  See the compliance section above.
