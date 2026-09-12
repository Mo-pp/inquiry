"""可选 MySQL 集成验证：仅写会话临时表，不修改实际客户/线索/消息。

运行前设置 FIRST_CONTACT_TEST_MYSQL=1；HTTP 使用 MockTransport，不发送 WhatsApp。
"""

from datetime import datetime
import json
import os
import unittest

import httpx
import mysql.connector

from app.settings import load_settings
from app.messages.wa_bridge_client import WaBridgeClient
from app.leads.lead_repository import LeadRepository
from app.leads.first_contact_service import FirstContactService


class SessionConnection:
    """让 Repository 使用同一测试会话，看见临时表；真实连接由测试统一关闭。"""
    def __init__(self, connection):
        self.connection = connection

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def close(self):
        self.connection.rollback()


@unittest.skipUnless(os.environ.get("FIRST_CONTACT_TEST_MYSQL") == "1", "需显式开启 MySQL 临时表验证")
class FirstContactMySQLTests(unittest.TestCase):
    def setUp(self):
        settings = load_settings()
        self.conn = mysql.connector.connect(**settings.mysql.connect_kwargs())
        self.addCleanup(self.conn.close)
        self.cur = self.conn.cursor(dictionary=True)
        self.addCleanup(self.cur.close)
        # 同名临时表仅覆盖当前连接的名称解析，实际业务表不受影响。
        for table in ("leads", "customers", "messages"):
            self.cur.execute(f"CREATE TEMPORARY TABLE first_contact_source_{table} LIKE {table}")
            self.cur.execute(f"CREATE TEMPORARY TABLE {table} LIKE first_contact_source_{table}")
        self.conn.commit()
        self.repo = LeadRepository(lambda: SessionConnection(self.conn))
        self.sent = []
        def handler(request):
            data = json.loads(request.content)
            if request.url.path == "/messages/check":
                return httpx.Response(200, json={"status": "registered", "phone": data["phone"], "chat_jid": "999999@lid"})
            self.sent.append(data)
            return httpx.Response(200, json={"status": "sent", "phone": data["phone"],
                "chat_jid": "999999@lid", "message_id": f"test-message-{len(self.sent)}"})
        self.bridge = WaBridgeClient(settings.wa_bridge, transport=httpx.MockTransport(handler))
        self.addCleanup(self.bridge.close)
        self.service = FirstContactService(self.repo, self.bridge)

    def add_lead(self, id, phone="+8613800000000"):
        self.cur.execute("INSERT INTO leads (source_lead_id, whatsapp_number, full_name, platform, raw_data) "
                         "VALUES (%s, %s, 'Test name', 'fb', '{}')", (id, phone))
        self.conn.commit()

    def rows(self, table):
        self.cur.execute(f"SELECT * FROM {table}")
        rows = self.cur.fetchall()
        self.conn.commit()
        return rows

    def test_scan_saves_all_three_tables_and_next_round_does_not_send_again(self):
        self.add_lead("test-lead-1")
        self.service.scan_once()
        self.service.scan_once()
        self.assertEqual(len(self.sent), 1)
        customers, messages, leads = self.rows("customers"), self.rows("messages"), self.rows("leads")
        self.assertEqual(len(customers), 1)
        self.assertEqual(customers[0]["phone_e164"], "+8613800000000")
        self.assertEqual(customers[0]["wa_chat_id"], "999999@lid")
        self.assertEqual(messages[0]["body"], self.sent[0]["text"])
        self.assertEqual(messages[0]["send_status"], "sent")
        self.assertEqual(messages[0]["direction"], "out")
        self.assertEqual(leads[0]["customer_added"], 1)
        self.assertEqual(leads[0]["customer_id"], customers[0]["customer_id"])
        # 同号码第二条表单复用客户，但各 lead 各发送一次。
        self.add_lead("test-lead-2")
        result = self.service.process_one("test-lead-2")
        self.assertEqual(result.customer_id, customers[0]["customer_id"])
        self.assertEqual(len(self.rows("customers")), 1)
        self.assertEqual(len(self.rows("messages")), 2)
        self.assertEqual(self.rows("customers")[0]["contacted_at"], customers[0]["contacted_at"])

    def test_message_insert_failure_rolls_back_customer_and_lead_together(self):
        self.add_lead("original")
        self.service.process_one("original")
        self.add_lead("rollback", "+8613900000000")
        with self.assertRaises(mysql.connector.IntegrityError):
            self.repo.save_first_contact(source_lead_id="rollback", phone="+8613900000000",
                chat_jid="888888@lid", message_id="test-message-1", body="duplicate message ID",
                sent_at=datetime(2026, 9, 12))
        self.assertEqual(len(self.rows("customers")), 1)
        self.assertEqual(len(self.rows("messages")), 1)
        lead = self.repo.get_by_id("rollback")
        self.assertFalse(lead["customer_added"])
        self.assertIsNone(lead["customer_id"])


if __name__ == "__main__":
    unittest.main()
