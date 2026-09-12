"""手动联系指定 lead：会真实发送模板并写数据库，每次只处理一条。"""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mysql.connector

from app.leads.first_contact_service import FirstContactService
from app.leads.lead_repository import LeadRepository
from app.messages.wa_bridge_client import WaBridgeClient
from app.settings import load_settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_lead_id", help="要真实联系的 leads.source_lead_id")
    args = parser.parse_args()
    settings = load_settings()
    repository = LeadRepository(lambda: mysql.connector.connect(**settings.mysql.connect_kwargs()))
    bridge = WaBridgeClient(settings.wa_bridge)
    try:
        result = FirstContactService(repository, bridge).process_one(args.source_lead_id)
        print(json.dumps(asdict(result), ensure_ascii=False))
        return 0 if result.status in {"sent", "already_contacted"} else 1
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        bridge.close()


if __name__ == "__main__":
    raise SystemExit(main())
