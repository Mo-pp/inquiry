CREATE DATABASE IF NOT EXISTS lintratek_chat
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE lintratek_chat;

DROP TABLE IF EXISTS customer_score_logs;
DROP TABLE IF EXISTS customer_messages;
DROP TABLE IF EXISTS lead_outreach;
DROP TABLE IF EXISTS wa_inbox_jobs;
DROP TABLE IF EXISTS customer_leads;
DROP TABLE IF EXISTS customers;
DROP TABLE IF EXISTS customer_chats;

CREATE TABLE customers (
    customer_phone VARCHAR(32) NOT NULL PRIMARY KEY,
    customer_name VARCHAR(255) NOT NULL,
    first_message_at DATETIME NOT NULL,
    last_message_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    agent_session_id VARCHAR(64) NULL,
    session_updated_at DATETIME NULL,
    score INT NOT NULL DEFAULT 0,
    level_code VARCHAR(16) NULL,
    score_updated_at DATETIME NULL
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE TABLE customer_messages (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    customer_phone VARCHAR(32) NOT NULL,
    message_key CHAR(64) NOT NULL,
    sender VARCHAR(255) NOT NULL,
    direction ENUM('in', 'out') NOT NULL,
    message_at DATETIME NOT NULL,
    message_text TEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_message_key (message_key),
    KEY idx_customer_message_time (customer_phone, message_at, id),
    CONSTRAINT fk_customer_messages_customer FOREIGN KEY (customer_phone)
      REFERENCES customers(customer_phone) ON UPDATE CASCADE ON DELETE CASCADE
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE TABLE customer_score_logs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    customer_phone VARCHAR(32) NOT NULL,
    trigger_message_key CHAR(64) NOT NULL,
    score_before INT NOT NULL,
    score_delta INT NOT NULL,
    score_after INT NOT NULL,
    level_before VARCHAR(16) NULL,
    level_after VARCHAR(16) NULL,
    matched_points JSON NULL,
    user_evidence JSON NULL,
    scoring_version VARCHAR(32) NOT NULL DEFAULT 'v1',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_customer_score_event (customer_phone, trigger_message_key, scoring_version),
    KEY idx_customer_score_logs_time (customer_phone, created_at),
    CONSTRAINT fk_customer_score_logs_customer FOREIGN KEY (customer_phone)
      REFERENCES customers(customer_phone) ON UPDATE CASCADE ON DELETE CASCADE
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE TABLE customer_leads (
    source_lead_id VARCHAR(128) NOT NULL PRIMARY KEY,
    created_time DATETIME NULL,
    ad_id VARCHAR(255) NULL,
    ad_name VARCHAR(255) NULL,
    adset_id VARCHAR(255) NULL,
    adset_name VARCHAR(255) NULL,
    campaign_id VARCHAR(255) NULL,
    campaign_name VARCHAR(255) NULL,
    form_id VARCHAR(255) NULL,
    form_name VARCHAR(255) NULL,
    is_organic BOOLEAN NOT NULL DEFAULT FALSE,
    platform VARCHAR(32) NULL,
    business_role_answer TEXT NULL,
    target_country_answer TEXT NULL,
    coverage_area_answer TEXT NULL,
    purchase_purpose_answer TEXT NULL,
    frequency_budget_quantity_answer TEXT NULL,
    email VARCHAR(320) NULL,
    whatsapp_number VARCHAR(32) NULL,
    full_name VARCHAR(255) NULL,
    company_name VARCHAR(255) NULL,
    website VARCHAR(1024) NULL,
    job_title VARCHAR(255) NULL,
    lead_status VARCHAR(64) NULL,
    customer_added BOOLEAN NOT NULL DEFAULT FALSE,
    raw_data JSON NOT NULL,
    imported_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_customer_leads_whatsapp (whatsapp_number),
    KEY idx_customer_leads_created (created_time)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- Service-side ingestion and proactive outreach tables.  This full schema
-- file is suitable only for a fresh/test database; use the additive migration
-- files for an existing database.
CREATE TABLE wa_inbox_jobs (
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
    UNIQUE KEY uq_wa_inbox_wa_message_id (wa_message_id),
    KEY idx_wa_inbox_claim (status, available_at, lease_until, id),
    KEY idx_wa_inbox_chat_time (chat_jid, message_at, id)
) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE TABLE lead_outreach (
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
