from datetime import datetime, timezone
from .models import LeadRecord
EXPECTED_HEADERS = ["id","created_time","ad_id","ad_name","adset_id","adset_name","campaign_id","campaign_name","form_id","form_name","is_organic","platform","do_you_fall_into_the_category？","_in_which_country_you_want_to_use_the_signal_booster?","2、what_is_the_square_area_of_the_signal_you_want_to_cover?_over_500_sqm_or_less_than_500_sqm","_you_want_to_purchase_our_products_for_home_use_or_for_resell?","4、what_signal_frequency_band_do_you_need_to_purchase?_what_is_your_budget_and_quantity?","email","whatsapp_number","full_name","company_name","website","job_title","lead_status"]
def map_row(headers, values):
    if [str(x).strip() for x in headers][:24] != EXPECTED_HEADERS: raise ValueError("表头不一致")
    v=list(values)+[None]*max(0,24-len(values))
    if not str(v[0] or '').strip(): return None
    d=datetime.fromisoformat(str(v[1]).replace('Z','+00:00')) if v[1] else None
    if d and d.tzinfo: d=d.astimezone(timezone.utc).replace(tzinfo=None)
    fields=[str(x).strip() or None if x is not None else None for x in v[2:24]]
    organic = str(v[10]).strip().lower() if v[10] is not None else ''
    if organic not in {'', 'true', 'false', '1', '0', 'yes', 'no', 'y', 'n'}:
        raise ValueError('is_organic 无效')
    fields[8] = organic in {'true', '1', 'yes', 'y'}
    return LeadRecord(str(v[0]).strip(),d,*fields,{"headers":list(headers),"values":list(values)})
