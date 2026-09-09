"""Process approved lead-outreach jobs behind explicit phase-5 gates.

The default command performs one read-only dry-run.  Real execution requires
``--execute``, two environment gates, and exactly one explicitly supplied
allowlisted phone number.  This script is intentionally never started by the
normal application launcher.
"""

from __future__ import annotations

import argparse
from collections import Counter
import os
import time
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
    LeadOutreachExecutor,
    LeadOutreachService,
    LeadOutreachWorker,
    RetryPolicy,
)
from filter_agent.services.whatsapp_bridge import HttpWhatsAppBridge


class _DisabledBridge:
    """Guard object proving that dry-run never reaches the HTTP adapter."""

    def check_number(self, _phone):
        raise AssertionError("registration lookup is disabled in dry-run")

    def send_text(self, _phone, _text):
        raise AssertionError("message sending is disabled in dry-run")


def _status_counts(results) -> dict[str, int]:
    return dict(Counter(str(item.get("status") or "unknown") for item in results))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Process approved lead outreach jobs with an explicit send gate"
    )
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--interval", type=float, default=10.0)
    parser.add_argument("--repeat", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--allow-phone", action="append", default=[])
    args = parser.parse_args()

    if args.repeat:
        if os.getenv("OUTREACH_WORKER_AUTOSTART", "").lower() != "true":
            parser.error("OUTREACH_WORKER_AUTOSTART=true is required with --repeat")
        if args.interval <= 0:
            parser.error("--interval must be positive")
    if args.execute:
        if os.getenv("OUTREACH_WORKER_EXECUTE", "").lower() != "true":
            parser.error("OUTREACH_WORKER_EXECUTE=true is required with --execute")
        if os.getenv("OUTREACH_REAL_SEND_ENABLED", "").lower() != "true":
            parser.error("OUTREACH_REAL_SEND_ENABLED=true is required with --execute")
        if len(args.allow_phone) != 1:
            parser.error("--execute requires exactly one --allow-phone test number")

    settings = AppSettings.from_env()
    bridge = (
        HttpWhatsAppBridge(
            base_url=settings.wa_bridge_url,
            timeout_seconds=settings.wa_bridge_timeout_seconds,
        )
        if args.execute
        else _DisabledBridge()
    )
    service = LeadOutreachService(
        dry_run=not args.execute,
        real_send_enabled=args.execute,
        allowlist=args.allow_phone,
        known_phone_checker=MySQLKnownPhoneChecker(settings),
        retry_policy=RetryPolicy(
            max_attempts=settings.outreach_max_attempts,
            delays_seconds=settings.outreach_retry_delays_seconds,
        ),
    )
    store = MySQLOutreachRepository(settings)
    executor = LeadOutreachExecutor(
        service,
        store,
        registration_checker=bridge,
        sender=bridge,
    )
    worker = LeadOutreachWorker(LeadRepository(settings), store, service, executor)

    try:
        while True:
            run = worker.run_once(limit=args.limit, dry_run=not args.execute)
            print(
                {
                    "status": "dry_run" if not args.execute else "executed",
                    "checked": run.checked,
                    "status_counts": _status_counts(run.results),
                }
            )
            if not args.repeat:
                return 0
            time.sleep(args.interval)
    finally:
        close = getattr(bridge, "close", None)
        if callable(close):
            close()


if __name__ == "__main__":
    raise SystemExit(main())

