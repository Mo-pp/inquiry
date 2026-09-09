from dataclasses import dataclass
from datetime import datetime
from typing import Any
@dataclass(frozen=True, slots=True)
class LeadRecord:
    source_lead_id:str; created_time:datetime|None; ad_id:Any; ad_name:Any; adset_id:Any; adset_name:Any; campaign_id:Any; campaign_name:Any; form_id:Any; form_name:Any; is_organic:bool; platform:Any; business_role_answer:Any; target_country_answer:Any; coverage_area_answer:Any; purchase_purpose_answer:Any; frequency_budget_quantity_answer:Any; email:Any; whatsapp_number:Any; full_name:Any; company_name:Any; website:Any; job_title:Any; lead_status:Any; raw_data:dict[str,Any]
