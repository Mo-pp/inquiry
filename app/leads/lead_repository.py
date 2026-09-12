from typing import Iterable
import json
from mysql.connector.errors import IntegrityError
from .models import LeadRecord
from dataclasses import dataclass
from datetime import datetime
@dataclass(frozen=True, slots=True)
class LeadInsertStats: inserted:int; skipped:int
class LeadRepository:
    columns=('source_lead_id','created_time','ad_id','ad_name','adset_id','adset_name','campaign_id','campaign_name','form_id','form_name','is_organic','platform','business_role_answer','target_country_answer','coverage_area_answer','purchase_purpose_answer','frequency_budget_quantity_answer','email','whatsapp_number','full_name','company_name','website','job_title','lead_status','customer_added','raw_data')
    def __init__(self, connection_factory): self.connection_factory=connection_factory
    def get_by_id(self, source_lead_id: str) -> dict | None:
        """只读查询一条线索，供首次联系预览使用。"""
        conn = self.connection_factory()
        cur = None
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute(
                "SELECT * FROM leads WHERE source_lead_id = %s",
                (source_lead_id,),
            )
            return cur.fetchone()
        finally:
            try:
                if cur is not None:
                    cur.close()
            finally:
                conn.close()

    def save_first_contact(self, *, source_lead_id: str, phone: str, chat_jid: str,
                           message_id: str, body: str, sent_at: datetime) -> int:
        """发送成功后，在同一事务中保存客户、out 消息和 lead 标记。

        此方法不发送消息。调用方保持单进程串行；事务不跨越 WhatsApp HTTP 请求。
        """
        conn = self.connection_factory()
        cur = None
        try:
            conn.start_transaction()
            cur = conn.cursor(dictionary=True)
            cur.execute(
                "SELECT customer_added, full_name FROM leads WHERE source_lead_id = %s FOR UPDATE",
                (source_lead_id,),
            )
            lead = cur.fetchone()
            if lead is None or lead["customer_added"]:
                raise ValueError("线索不存在或已联系，未保存此次发送结果")
            cur.execute("SELECT customer_id FROM customers WHERE phone_e164 = %s FOR UPDATE", (phone,))
            customer = cur.fetchone()
            if customer is None:
                cur.execute(
                    "INSERT INTO customers (phone_e164, wa_chat_id, full_name, contacted_at) "
                    "VALUES (%s, %s, %s, %s)",
                    (phone, chat_jid, lead["full_name"], sent_at),
                )
                customer_id = cur.lastrowid
            else:
                customer_id = customer["customer_id"]
                cur.execute(
                    "UPDATE customers SET wa_chat_id = %s, full_name = COALESCE(full_name, %s), "
                    "contacted_at = COALESCE(contacted_at, %s) WHERE customer_id = %s",
                    (chat_jid, lead["full_name"], sent_at, customer_id),
                )
            cur.execute(
                "INSERT INTO messages (customer_id, platform_message_id, direction, body, send_status) "
                "VALUES (%s, %s, 'out', %s, 'sent')",
                (customer_id, message_id, body),
            )
            cur.execute(
                "UPDATE leads SET customer_id = %s, customer_added = TRUE WHERE source_lead_id = %s",
                (customer_id, source_lead_id),
            )
            conn.commit()
            return customer_id
        except Exception:
            conn.rollback()
            raise
        finally:
            try:
                if cur is not None:
                    cur.close()
            finally:
                conn.close()

    def list_uncontacted(self, limit: int = 20, after_lead_id: str | None = None) -> list[dict]:
        """扫描未联系线索，不加锁、不持有跨网络请求的事务。"""
        conn = self.connection_factory()
        cur = None
        try:
            cur = conn.cursor(dictionary=True)
            sql = "SELECT source_lead_id FROM leads WHERE customer_added = FALSE "
            params = []
            if after_lead_id is not None:
                sql += "AND source_lead_id > %s "
                params.append(after_lead_id)
            cur.execute(sql + "ORDER BY source_lead_id LIMIT %s", (*params, limit))
            return cur.fetchall()
        finally:
            try:
                if cur is not None:
                    cur.close()
            finally:
                conn.close()

    def append(self, leads:Iterable[LeadRecord]):
        conn=self.connection_factory(); cur=None; inserted=skipped=0
        sql='INSERT INTO leads ('+','.join(self.columns)+') VALUES ('+','.join(['%s']*26)+')'
        try:
            conn.start_transaction()
            cur=conn.cursor()
            for lead in leads:
                params=tuple(getattr(lead,c) for c in self.columns[:24])+(False,json.dumps(lead.raw_data,ensure_ascii=False))
                try:
                    cur.execute(sql,params)
                    inserted += 1
                except IntegrityError as exc:
                    if exc.errno != 1062:
                        raise
                    skipped += 1
            conn.commit(); return LeadInsertStats(inserted,skipped)
        except Exception:
            conn.rollback(); raise
        finally:
            try:
                if cur is not None:
                    cur.close()
            finally:
                conn.close()
