"""Render one proactive-contact plan without reading MySQL or sending WhatsApp.

This is the phase-3 test harness.  The input must be a user-supplied JSON
object matching the lead fields; it is never populated from the production
database automatically.  Keep ``--dry-run`` (the default) while validating a
new form mapping.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from filter_agent.services.lead_outreach import LeadOutreachService, RetryPolicy


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Plan one lead outreach message (always dry-run in phase 3)"
    )
    parser.add_argument("--input", required=True, help="path to a user-provided JSON lead")
    parser.add_argument("--approved", action="store_true")
    parser.add_argument("--opted-in", action="store_true")
    parser.add_argument("--allow-phone", action="append", default=[])
    parser.add_argument("--question", action="append", default=[])
    args = parser.parse_args()

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        parser.error("input JSON must contain one object, not a list")

    service = LeadOutreachService(
        dry_run=True,
        allowlist=args.allow_phone,
        retry_policy=RetryPolicy(),
    )
    plan = service.plan(
        payload,
        approved=args.approved,
        opted_in=args.opted_in,
        custom_questions=args.question,
    )
    output = service.simulate(plan)
    output["status_detail"] = plan.status.value
    output["message_text"] = plan.message_text
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

