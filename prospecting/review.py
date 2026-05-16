#!/usr/bin/env python3
"""
Interactive review for generated drafts.

For each row: show the prospect + draft, ask approve/edit/skip/back/quit.
Saves progress on every action so you can quit and resume.
Writes a separate "approved-only" CSV ready for MailerLite import.

Usage:
    python review.py --in output/brisbane_drafts.csv \
                     --out output/brisbane_reviewed.csv

    # Also emit a clean import file with only approved rows:
    python review.py --in output/brisbane_drafts.csv \
                     --out output/brisbane_reviewed.csv \
                     --approved-out output/brisbane_for_mailerlite.csv

Resumable: rerun the same --in/--out and rows already marked approved
or skipped are passed over.

Status column values:
    pending   - not yet reviewed (default for new rows)
    approved  - draft is good as-is or edited; included in --approved-out
    edited    - draft was modified; included in --approved-out
    skipped   - excluded from --approved-out
"""
from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

import click


STATUS_COL = "status"
TERMINAL_STATUSES = {"approved", "edited", "skipped"}
KEEP_STATUSES = {"approved", "edited"}


def _ensure_status(rows: list[dict], fieldnames: list[str]) -> list[str]:
    if STATUS_COL not in fieldnames:
        fieldnames = fieldnames + [STATUS_COL]
    for r in rows:
        r.setdefault(STATUS_COL, "")
        if not r[STATUS_COL]:
            r[STATUS_COL] = "pending"
    return fieldnames


def _write_csv(path: str, rows: list[dict], fieldnames: list[str]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _render_row(row: dict, idx: int, total: int, remaining: int) -> None:
    click.clear()
    click.secho(f"[{idx + 1}/{total}]  remaining to review: {remaining}", fg="cyan", bold=True)
    click.echo("-" * 70)
    click.secho(f"  {row.get('name', '(no name)')}", fg="white", bold=True)
    click.echo(f"  Category : {row.get('category', '')}  ({row.get('subcategory', '')})")
    click.echo(f"  Suburb   : {row.get('suburb', '')}  {row.get('state', '')}")
    contact = row.get("contact_name") or "(no contact found)"
    role = row.get("contact_role") or ""
    contact_line = f"  Contact  : {contact}" + (f", {role}" if role else "")
    click.echo(contact_line)
    click.echo(f"  Email    : {row.get('email') or '(none)'}")
    click.echo(f"  Phone    : {row.get('phone') or '(none)'}")
    click.echo(f"  Website  : {row.get('website') or '(none)'}")
    click.echo(f"  OSM      : {row.get('osm_url') or ''}")
    click.echo("-" * 70)
    click.secho("DRAFT", fg="yellow", bold=True)
    click.echo(row.get("draft_message", ""))
    click.echo("-" * 70)
    if row.get(STATUS_COL) in TERMINAL_STATUSES:
        click.secho(f"  (already {row[STATUS_COL]})", fg="green")


def _prompt_action() -> str:
    click.echo()
    click.echo(
        click.style("[a]", fg="green") + "pprove  "
        + click.style("[e]", fg="yellow") + "dit  "
        + click.style("[s]", fg="red") + "kip  "
        + click.style("[b]", fg="blue") + "ack  "
        + click.style("[r]", fg="magenta") + "estart row  "
        + click.style("[q]", fg="cyan") + "uit & save"
    )
    return click.prompt("Action", default="a", show_default=False).strip().lower()[:1]


def _edit_draft(current: str) -> str | None:
    """Open $EDITOR with the current draft. Returns new text or None if unchanged/cancelled."""
    edited = click.edit(current, require_save=True)
    if edited is None:
        return None
    edited = edited.rstrip("\n")
    if edited == current or not edited.strip():
        return None
    return edited


@click.command()
@click.option("--in", "in_path", required=True, help="Drafts CSV (from generate_drafts.py).")
@click.option("--out", "out_path", required=True, help="Reviewed CSV (includes all rows + status).")
@click.option(
    "--approved-out",
    default=None,
    help="Optional path for a separate CSV containing only approved/edited rows.",
)
@click.option(
    "--skip-no-email",
    is_flag=True,
    help="Auto-skip rows that have no email address (can't be reached anyway).",
)
def main(in_path: str, out_path: str, approved_out: str | None, skip_no_email: bool) -> None:
    # If resuming, prefer reading from out_path if it exists, otherwise read in_path
    source = out_path if Path(out_path).exists() else in_path
    with open(source, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    if "draft_message" not in fieldnames:
        click.echo("ERROR: input CSV has no 'draft_message' column. Run generate_drafts.py first.", err=True)
        sys.exit(2)

    fieldnames = _ensure_status(rows, fieldnames)

    if skip_no_email:
        auto = 0
        for r in rows:
            if r[STATUS_COL] == "pending" and not (r.get("email") or "").strip():
                r[STATUS_COL] = "skipped"
                auto += 1
        if auto:
            click.echo(f"Auto-skipped {auto} rows with no email.")
            _write_csv(out_path, rows, fieldnames)

    total = len(rows)
    idx = 0
    # Jump to first pending row
    while idx < total and rows[idx][STATUS_COL] in TERMINAL_STATUSES:
        idx += 1

    while idx < total:
        remaining = sum(1 for r in rows if r[STATUS_COL] == "pending")
        _render_row(rows[idx], idx, total, remaining)
        action = _prompt_action()

        if action == "a":
            rows[idx][STATUS_COL] = "approved"
            _write_csv(out_path, rows, fieldnames)
            idx += 1
        elif action == "e":
            new = _edit_draft(rows[idx]["draft_message"])
            if new is not None:
                rows[idx]["draft_message"] = new
                rows[idx][STATUS_COL] = "edited"
                _write_csv(out_path, rows, fieldnames)
                idx += 1
            else:
                click.echo("No changes - row left as pending.")
                click.pause()
        elif action == "s":
            rows[idx][STATUS_COL] = "skipped"
            _write_csv(out_path, rows, fieldnames)
            idx += 1
        elif action == "b":
            idx = max(0, idx - 1)
            # On back-step, allow re-reviewing already-terminal rows
            rows[idx][STATUS_COL] = "pending"
            _write_csv(out_path, rows, fieldnames)
        elif action == "r":
            rows[idx][STATUS_COL] = "pending"
            _write_csv(out_path, rows, fieldnames)
        elif action == "q":
            break
        else:
            click.echo("Unknown action.")
            click.pause()

    # Skip past any newly-terminal rows at end
    _write_csv(out_path, rows, fieldnames)

    approved = [r for r in rows if r[STATUS_COL] in KEEP_STATUSES]
    skipped = sum(1 for r in rows if r[STATUS_COL] == "skipped")
    pending = sum(1 for r in rows if r[STATUS_COL] == "pending")

    click.echo()
    click.secho(
        f"Done. approved/edited: {len(approved)}  skipped: {skipped}  still pending: {pending}",
        fg="green" if pending == 0 else "yellow",
    )

    if approved_out:
        # MailerLite-friendly subset: drop status column from the approved file
        approved_fields = [c for c in fieldnames if c != STATUS_COL]
        for r in approved:
            r.pop(STATUS_COL, None)
        _write_csv(approved_out, approved, approved_fields)
        click.echo(f"Wrote {len(approved)} approved rows -> {approved_out}")


if __name__ == "__main__":
    main()
