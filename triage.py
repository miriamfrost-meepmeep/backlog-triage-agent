"""Backlog triage agent: feedback in, ranked user stories out, nothing created without approval.

    python triage.py            # draft, show, ask for approval, then create
    TICKETS=down python triage.py
"""
import json
import time
from pathlib import Path

import anthropic

from ticket_system import TicketSystem, TicketError

ROOT = Path(__file__).resolve().parent
MODEL = "claude-sonnet-5"
FEEDBACK = json.load(open(ROOT / "data" / "feedback.json"))
BACKLOG = json.load(open(ROOT / "data" / "backlog.json"))
SYSTEM = (ROOT / "prompts" / "system.md").read_text()
RETRIES = 3

# My prioritization criteria from isolved, as weights. Change these to change the ranking.
WEIGHTS = {"frequency": 3, "contract": 4, "deadline": 3, "revenue": 2, "complexity": 2}

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["stories", "unassigned"],
    "properties": {
        "stories": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["title", "as_a", "i_want", "so_that", "acceptance_criteria",
                         "feedback_ids", "duplicate_of", "complexity", "deadline"],
            "properties": {
                "title": {"type": "string"}, "as_a": {"type": "string"},
                "i_want": {"type": "string"}, "so_that": {"type": "string"},
                "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                "feedback_ids": {"type": "array", "items": {"type": "string"}},
                "duplicate_of": {"type": ["string", "null"]},
                "complexity": {"type": "string", "enum": ["S", "M", "L"]},
                "deadline": {"type": ["string", "null"]}}}},
        "unassigned": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["feedback_id", "reason", "note"],
            "properties": {
                "feedback_id": {"type": "string"},
                "reason": {"type": "string", "enum": ["needs_info", "praise", "suspicious"]},
                "note": {"type": "string"}}}}}}


def draft():
    """One Claude call: group feedback and draft stories. Returns (plan, cost)."""
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL, max_tokens=8000, system=SYSTEM,
        messages=[{"role": "user", "content":
                   "Existing backlog:\n" + json.dumps(BACKLOG, indent=1) +
                   "\n\nNew feedback:\n" + json.dumps(FEEDBACK, indent=1)}],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
    )
    text = next(b.text for b in response.content if b.type == "text")
    cost = (response.usage.input_tokens * 3 + response.usage.output_tokens * 15) / 1e6
    return json.loads(text), round(cost, 4)


def score(plan):
    """Rank stories with my weights. The math is in code, not in the model, so it's explainable."""
    by_id = {f["id"]: f for f in FEEDBACK}
    for s in plan["stories"]:
        items = [by_id[i] for i in s["feedback_ids"] if i in by_id]
        customers = {f["customer"]: f["customer_arr"] for f in items}
        parts = {
            "frequency": min(len(items), 5) / 5,
            "contract": 1.0 if any(f["contract_commitment"] for f in items) else 0.0,
            "deadline": 1.0 if s["deadline"] else 0.0,
            "revenue": min(sum(customers.values()), 300000) / 300000,
            "complexity": {"S": 1.0, "M": 0.5, "L": 0.0}[s["complexity"]],
        }
        s["score"] = round(sum(WEIGHTS[k] * v for k, v in parts.items()), 2)
        s["arr_affected"] = sum(customers.values())
    plan["stories"].sort(key=lambda s: s["score"], reverse=True)
    return plan


def show(plan):
    print(f"\n{len(plan['stories'])} stories drafted. Nothing has been created.\n")
    for n, s in enumerate(plan["stories"], 1):
        dup = f"  DUPLICATE of {s['duplicate_of']}" if s["duplicate_of"] else ""
        print(f"{n}. [{s['score']}] {s['title']}{dup}")
        print(f"   As a {s['as_a']}, I want {s['i_want']}, so that {s['so_that']}")
        print(f"   {len(s['feedback_ids'])} reports, ${s['arr_affected']:,} ARR, size {s['complexity']}"
              + (f", deadline: {s['deadline']}" if s["deadline"] else ""))
    if plan["unassigned"]:
        print("\nNot turned into stories:")
        for u in plan["unassigned"]:
            print(f"   {u['feedback_id']} [{u['reason']}] {u['note']}")


def create_approved(plan, approved_numbers, tickets):
    """Create only what a person approved. Duplicates are linked, not created. Retries on failure."""
    report = {"created": [], "linked": [], "failed": []}
    for n in approved_numbers:
        s = plan["stories"][n - 1]
        if s["duplicate_of"]:
            report["linked"].append((s["title"], s["duplicate_of"]))
            continue
        for attempt in range(1, RETRIES + 1):
            try:
                report["created"].append((s["title"], tickets.create(s)))
                break
            except TicketError as e:
                if attempt == RETRIES:
                    report["failed"].append((s["title"], str(e)))
                else:
                    time.sleep(0.2 * attempt)
    return report


def ask_approval(plan):
    answer = input("\nApprove which stories? (e.g. 1,3  or  all  or  none): ").strip().lower()
    if answer == "all":
        return list(range(1, len(plan["stories"]) + 1))
    if answer in ("", "none", "n", "no"):
        return []
    return [int(x) for x in answer.replace(" ", "").split(",") if x.isdigit()
            and 1 <= int(x) <= len(plan["stories"])]


if __name__ == "__main__":
    plan, cost = draft()
    plan = score(plan)
    show(plan)
    print(f"\n   est. cost: ${cost}")
    approved = ask_approval(plan)
    report = create_approved(plan, approved, TicketSystem())
    print()
    for title, wid in report["created"]:
        print(f"   created {wid}: {title}")
    for title, dup in report["linked"]:
        print(f"   linked to existing {dup}: {title}")
    for title, err in report["failed"]:
        print(f"   NOT created after {RETRIES} tries ({err}): {title}")
    if not approved:
        print("   Nothing approved, nothing created.")
