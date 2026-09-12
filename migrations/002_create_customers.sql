-- 客户主档：一行 = 一个 WhatsApp 客户。首次联系客户时建档。
-- 电话和 WhatsApp chat ID 分开保存（chat ID 可能不是电话号码）。
CREATE TABLE IF NOT EXISTS customers (
    -- 稳定客户号，其他表统一用它关联客户，不拿电话当主键。
    customer_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    -- 规范化后的电话（保留国际区号）；允许为空，非空时唯一。
    phone_e164 VARCHAR(32) NULL,
    -- WhatsApp 会话 ID；允许为空，非空时唯一。
    wa_chat_id VARCHAR(64) NULL,
    full_name VARCHAR(255) NULL,
    -- 客户类别与等级分开记录，不混用一个字段。
    customer_type VARCHAR(32) NULL,
    grade VARCHAR(16) NULL,
    score INT NOT NULL DEFAULT 0,
    -- 首次联系模板的发送时间。
    contacted_at DATETIME NULL,
    -- Claude CLI 会话，用于 --resume 续接；时间戳供会话过期判断。
    session_id VARCHAR(64) NULL,
    session_updated_at DATETIME NULL,
    -- 最后一条入站消息的时间。
    last_inbound_at DATETIME NULL,
    -- 已处理入站消息水位：记录处理到 messages 的哪一条，用于避免遗漏生成回复期间到达的新消息。
    last_processed_inbound_id BIGINT UNSIGNED NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_customers_phone (phone_e164),
    UNIQUE KEY uq_customers_chat (wa_chat_id)
) ENGINE=InnoDB
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;
