-- Destructive migration requested for replacing the previous lead schema.
DROP TABLE IF EXISTS customer_leads;

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
