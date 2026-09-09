-- Phase-4 baseline operation.  REVIEW ONLY; do not run until the operator
-- confirms the row counts and has a backup.  This script performs no WhatsApp
-- calls and does not claim that any historical lead was contacted.

USE lintratek_chat;

SET @baseline_marker = 'baseline_20260903';

START TRANSACTION;

INSERT INTO lead_outreach (
    source_lead_id,
    idempotency_key,
    phone_e164,
    status,
    template_version,
    baseline_marker,
    baseline_previous_customer_added,
    baseline_marked_at
)
SELECT
    l.source_lead_id,
    SHA2(CONCAT('lead:', l.source_lead_id, CHAR(31), 'template:baseline'), 256),
    l.whatsapp_number,
    'skipped_existing',
    'baseline',
    @baseline_marker,
    l.customer_added,
    UTC_TIMESTAMP()
FROM customer_leads AS l
WHERE l.customer_added IS NOT TRUE
ON DUPLICATE KEY UPDATE
    baseline_marker = COALESCE(baseline_marker, VALUES(baseline_marker)),
    baseline_previous_customer_added = COALESCE(
        baseline_previous_customer_added,
        VALUES(baseline_previous_customer_added)
    ),
    baseline_marked_at = COALESCE(baseline_marked_at, VALUES(baseline_marked_at));

UPDATE customer_leads AS l
JOIN lead_outreach AS o
  ON o.source_lead_id = l.source_lead_id
 AND o.baseline_marker = @baseline_marker
SET l.customer_added = TRUE
WHERE l.customer_added IS NOT TRUE;

COMMIT;

-- Optional guarded rollback (run separately, after checking no later outreach
-- state changed):
-- START TRANSACTION;
-- UPDATE customer_leads AS l
-- JOIN lead_outreach AS o ON o.source_lead_id = l.source_lead_id
-- SET l.customer_added = o.baseline_previous_customer_added
-- WHERE o.baseline_marker = 'baseline_20260903'
--   AND o.status = 'skipped_existing'
--   AND o.attempt_count = 0
--   AND o.wa_message_id IS NULL
--   AND o.contacted_at IS NULL
--   AND l.customer_added = TRUE;
-- COMMIT;
