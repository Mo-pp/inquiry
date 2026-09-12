-- 消息流水：一行 = 一条 WhatsApp 消息（in/out）。入站存档与出站记录共用本表。
CREATE TABLE IF NOT EXISTS messages (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    -- 关联客户；删除测试客户时消息级联删除。
    customer_id BIGINT UNSIGNED NOT NULL,
    -- 平台消息 ID，同一账户内唯一，用于重复到达去重（允许 NULL，NULL 不参与唯一约束）。
    platform_message_id VARCHAR(128) NULL,
    direction ENUM('in', 'out') NOT NULL,
    body TEXT NOT NULL,
    -- 平台消息时间与本地落库时间分开保存。
    platform_time DATETIME NULL,
    received_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- 所属回复轮次；回复轮次表以后建，先留空。
    reply_run_id BIGINT UNSIGNED NULL,
    -- 出站发送状态；入站消息为 NULL。成功、失败、未知要能区分。
    send_status ENUM('pending', 'sent', 'failed', 'unknown') NULL,
    UNIQUE KEY uq_messages_platform_id (platform_message_id),
    KEY idx_messages_customer (customer_id, id),
    CONSTRAINT fk_messages_customer FOREIGN KEY (customer_id)
      REFERENCES customers(customer_id) ON UPDATE CASCADE ON DELETE CASCADE
) ENGINE=InnoDB
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;
