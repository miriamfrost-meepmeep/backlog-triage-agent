"""A pretend Azure DevOps. Stores work items in data/ado_store.json.

TICKETS=down   every create call fails (the service is down)
TICKETS=flaky  the first attempt for each item fails, the next one works
"""
import json
import os
from pathlib import Path

STORE = Path(__file__).resolve().parent / "data" / "ado_store.json"


class TicketError(Exception):
    pass


class TicketSystem:
    def __init__(self, mode=None, store=STORE):
        self.mode = mode or os.environ.get("TICKETS", "up")
        self.store = store
        self.attempts = {}

    def _load(self):
        return json.loads(self.store.read_text()) if self.store.exists() else []

    def reset(self):
        self.store.write_text("[]")

    def items(self):
        return self._load()

    def create(self, story):
        key = story["title"]
        self.attempts[key] = self.attempts.get(key, 0) + 1
        if self.mode == "down":
            raise TicketError("503 Service Unavailable")
        if self.mode == "flaky" and self.attempts[key] == 1:
            raise TicketError("504 Gateway Timeout")
        items = self._load()
        item = {"id": f"ADO-{200 + len(items)}", "title": story["title"],
                "source_feedback": story["feedback_ids"], "priority_score": story.get("score")}
        items.append(item)
        self.store.write_text(json.dumps(items, indent=1))
        return item["id"]
