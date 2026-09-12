from typing import Iterable
import json
from mysql.connector.errors import IntegrityError
from .models import LeadRecord
from dataclasses import dataclass
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
