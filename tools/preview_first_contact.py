"""按 source_lead_id 只读查询并打印首次联系消息，不启动服务或发送消息。"""

import argparse
from pathlib import Path
import sys

# 支持从任意工作目录直接运行此脚本。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mysql.connector

from app.leads.first_contact_template import build_first_contact_message
from app.leads.lead_repository import LeadRepository
from app.settings import load_settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_lead_id", help="leads 表中的线索 ID")
    args = parser.parse_args()

    settings = load_settings()
    repository = LeadRepository(
        lambda: mysql.connector.connect(**settings.mysql.connect_kwargs())
    )
    lead = repository.get_by_id(args.source_lead_id)
    if lead is None:
        print(f"线索不存在：{args.source_lead_id}", file=sys.stderr)
        return 1

    print(build_first_contact_message(lead))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
