-- Additive phase-3 migration for service-side ingestion and lead outreach.
--
-- This file intentionally contains no DROP/DELETE and does not mark existing
-- leads as contacted.  Run only after the phase gate and a database backup.

USE lintratek_chat;

CREATE TABLE IF NOT EXISTS wa_inbox_jobs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    message_key CHAR(64) NOT NULL,
    wa_message_id VARCHAR(255) NULL,
    chat_jid VARCHAR(128) NOT NULL,
    customer_phone VARCHAR(32) NULL,
    sender VARCHAR(255) NOT NULL,
    direction VARCHAR(8) NOT NULL DEFAULT 'in',
    message_at DATETIME NOT NULL,
    message_text TEXT NOT NULL,
    raw_payload JSON NOT NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'pending',
    attempts INT UNSIGNED NOT NULL DEFAULT 0,
    available_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lease_until DATETIME NULL,
    lease_token CHAR(36) NULL,
    last_error TEXT NULL,
    received_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_at DATETIME NULL,
    UNIQUE KEY uq_wa_inbox_message_key (message_key),
    KEY idx_wa_inbox_claim (status, available_at, lease_until, id),
    KEY idx_wa_inbox_chat_time (chat_jid, message_at, id)
) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS lead_outreach (
    source_lead_id VARCHAR(128) NOT NULL PRIMARY KEY,
    idempotency_key CHAR(64) NOT NULL,
    phone_e164 VARCHAR(32) NULL,
    chat_jid VARCHAR(128) NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending_approval',
    template_version VARCHAR(64) NOT NULL DEFAULT 'en-v1',
    message_text_hash CHAR(64) NULL,
    wa_message_id VARCHAR(255) NULL,
    attempt_count INT UNSIGNED NOT NULL DEFAULT 0,
    attempt_history JSON NULL,
    opt_in_at DATETIME NULL,
    opt_in_source VARCHAR(255) NULL,
    approved_at DATETIME NULL,
    next_attempt_at DATETIME NULL,
    last_attempt_at DATETIME NULL,
    contacted_at DATETIME NULL,
    last_error TEXT NULL,
    lease_until DATETIME NULL,
    lease_token CHAR(36) NULL,
    baseline_marker VARCHAR(64) NULL,
    baseline_previous_customer_added BOOLEAN NULL,
    baseline_marked_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_lead_outreach_idempotency (idempotency_key),
    KEY idx_lead_outreach_status (status, next_attempt_at, lease_until),
    KEY idx_lead_outreach_phone (phone_e164)
) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
