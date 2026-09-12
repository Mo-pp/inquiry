-- 线索表：Google 表单同步写入的原始获客事实，一行 = 一条表单记录，不合并。
-- 结构与旧项目 customer_leads 一致，另增加 customer_id 一列（见下）。
-- 在新系统专用库 lintratek_AI 中执行。
CREATE TABLE IF NOT EXISTS leads (
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
    -- 首次联系时写入：该表单对应的客户（customers.customer_id）；导入时为 NULL。
    -- 不加外键：leads 是原始事实，客户被清理时不联动删除线索。
    customer_id BIGINT UNSIGNED NULL,
    raw_data JSON NOT NULL,
    imported_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_leads_whatsapp (whatsapp_number),
    KEY idx_leads_created (created_time),
    KEY idx_leads_customer (customer_id)
) ENGINE=InnoDB
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;
