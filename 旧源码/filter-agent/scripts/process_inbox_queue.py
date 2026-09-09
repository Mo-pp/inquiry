"""Process the WAJS -> MySQL inbound queue.

The command is intentionally inert by default.  Without ``--execute`` it
doesn't connect to MySQL or claim a row.  Real processing requires both the
flag and ``WHATSAPP_INBOUND_WORKER_EXECUTE=true`` so starting a terminal by
mistake cannot consume production messages.
"""

from __future__ import annotations

import argparse
import os
import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from filter_agent.classification.classifier import RelevanceClassifier
from filter_agent.config import AppSettings
from filter_agent.persistence import MySQLCustomerChatRepository, MySQLInboxRepository
from filter_agent.services.inbound_ingestion import InboundWorker
from filter_agent.services.process_unread_batch import ProcessUnreadBatch


def main() -> int:
    parser = argparse.ArgumentParser(description="Process persisted WAJS inbound jobs")
    parser.add_argument("--once", action="store_true", help="process at most one job")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--execute", action="store_true", help="enable real queue processing")
    args = parser.parse_args()

    if not args.execute:
        print(
            "dry-run: no MySQL connection or queue claim was made; "
            "pass --execute plus WHATSAPP_INBOUND_WORKER_EXECUTE=true after approval"
        )
        return 0
    if os.getenv("WHATSAPP_INBOUND_WORKER_EXECUTE", "").strip().lower() != "true":
        parser.error("WHATSAPP_INBOUND_WORKER_EXECUTE=true is required with --execute")
    if args.interval <= 0:
        parser.error("--interval must be positive")

    settings = AppSettings.from_env()
    queue = MySQLInboxRepository(settings)
    processor = ProcessUnreadBatch(
        RelevanceClassifier(settings=settings),
        MySQLCustomerChatRepository(settings),
    )
    worker = InboundWorker(queue, processor)
    while True:
        result = worker.run_once(dry_run=False)
        if result is not None:
            print(result)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())

