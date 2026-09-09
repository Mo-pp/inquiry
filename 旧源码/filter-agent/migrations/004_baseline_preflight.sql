-- Read-only report to run before 004_mark_existing_leads_baseline.sql.
-- It intentionally returns counts only, never phone numbers or message text.

USE lintratek_chat;

SELECT COUNT(*) AS total_rows FROM customer_leads;
SELECT
    SUM(customer_added = TRUE) AS customer_added_true,
    SUM(customer_added = FALSE) AS customer_added_false,
    SUM(customer_added IS NULL) AS customer_added_null,
    SUM(whatsapp_number IS NOT NULL AND whatsapp_number <> '') AS rows_with_phone
FROM customer_leads;

SELECT COUNT(*) AS rows_to_mark
FROM customer_leads
WHERE customer_added IS NOT TRUE;

SELECT COUNT(*) AS outreach_table_present
FROM information_schema.tables
WHERE table_schema = DATABASE() AND table_name = 'lead_outreach';

SELECT COUNT(*) AS direct_customer_phone_overlap
FROM customer_leads AS l
JOIN customers AS c ON c.customer_phone = l.whatsapp_number;
