-- 林创 AI 客服（新系统）专用数据库，与旧系统的 lintratek_chat 完全隔离。
-- 本机 Windows 的 MySQL lower_case_table_names=1，库名实际按小写 lintratek_ai 存储；
-- 配置和连接字符串里写 lintratek_AI 或 lintratek_ai 均可（不区分大小写）。
-- 可重复执行：库已存在时不改动。
CREATE DATABASE IF NOT EXISTS lintratek_AI
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;
