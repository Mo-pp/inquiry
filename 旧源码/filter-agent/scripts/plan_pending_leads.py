"""Detect new leads and persist outreach plans without contacting WhatsApp.

The default mode is read-only.  ``--persist`` may be enabled later to create
``lead_outreach`` rows, but it still never calls ``getNumberId`` or
``sendMessage``.  A separate, explicitly approved phase is required for
outreach execution.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from filter_agent.config import AppSettings
from filter_agent.persistence import (
    LeadRepository,
    MySQLKnownPhoneChecker,
    MySQLOutreachRepository,
)
from filter_agent.services.lead_outreach import (
    LeadOutreachPlanner,
    LeadOutreachService,
    RetryPolicy,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Plan new customer_leads without sending WhatsApp messages"
    )
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument(
        "--persist",
        action="store_true",
        help="write idempotent lead_outreach rows (still never sends WhatsApp)",
    )
    parser.add_argument("--approved", action="store_true")
    parser.add_argument("--opted-in", action="store_true")
    parser.add_argument("--allow-phone", action="append", default=[])
    parser.add_argument("--question", action="append", default=[])
    args = parser.parse_args()

    if args.persist and os.getenv("OUTREACH_PLANNER_PERSIST", "").lower() != "true":
        parser.error("OUTREACH_PLANNER_PERSIST=true is required with --persist")

    settings = AppSettings.from_env()
    service = LeadOutreachService(
        # Planning remains a dry-run even when its durable plan row is written.
        dry_run=True,
        allowlist=args.allow_phone,
        known_phone_checker=MySQLKnownPhoneChecker(settings),
        retry_policy=RetryPolicy(
            max_attempts=settings.outreach_max_attempts,
            delays_seconds=settings.outreach_retry_delays_seconds,
        ),
    )
    planner = LeadOutreachPlanner(
        LeadRepository(settings),
        MySQLOutreachRepository(settings),
        service,
    )
    run = planner.run_once(
        limit=args.limit,
        approved=args.approved,
        opted_in=args.opted_in,
        custom_questions=args.question,
        dry_run=not args.persist,
    )
    print(
        json.dumps(
            {
                "status": "dry_run" if not args.persist else "plans_persisted",
                "scanned": run.scanned,
                "persisted": run.persisted,
                "status_counts": run.status_counts,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

