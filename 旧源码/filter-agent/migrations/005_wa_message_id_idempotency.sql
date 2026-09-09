-- Add a second idempotency boundary for native WhatsApp message ids.
-- Run only after checking for duplicate non-NULL wa_message_id values.

USE lintratek_chat;

ALTER TABLE wa_inbox_jobs
    ADD UNIQUE KEY uq_wa_inbox_wa_message_id (wa_message_id);

