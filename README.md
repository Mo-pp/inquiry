# 林创 AI 客服

项目采用一个 Python 常驻服务处理业务，`wa-bridge` 负责 WhatsApp 收发。以下目录已创建，目前业务模块尚未实现；目录说明用于约定后续代码放在哪里。

已定义线索表：`migrations/001_create_leads.sql` 沿用旧项目 `schema.sql` 中最终版本的 `customer_leads` 字段、类型、默认值及索引列，表名改为 `leads`，对应索引改名，并显式指定 InnoDB。`source_lead_id` 为主键，`customer_added` 保留原字段，默认 `FALSE`。该文件仅包含建表定义，不含删表操作。2026-09-08 已在本机 `lintratek_chat` 库执行，确认 `leads` 使用 InnoDB，包含 27 个字段、主键及两个普通索引。

## 目录结构与用途

```text
林创AI客服/
├── app/                          # Python 主服务：HTTP 接口、调度和业务模块
│   ├── leads/                    # 获客：表格增量同步、扫描未联系客户、发送首次联系模板
│   ├── messages/                 # 消息：接收入站、关联客户、调用收发桥、保存 in/out 消息
│   ├── replies/                  # 回复：静默判断、上下文拼装、Claude CLI 调用、会话与处理进度
│   ├── scoring/                  # 评分：处理及校验评分结果，保存评分日志和客户当前评分
│   └── notifications/            # 通知：达到 80 分推送钉钉、次日 08:55 汇总
├── agent/                        # Claude CLI 的工作目录：以后放身份、聊天策略等配置
│   └── .claude/
│       └── skills/               # 供 Claude 使用的项目技能
│           └── customer-scoring/ # 客户评分技能：以后放评分规则及相关资料
├── templates/                    # 固定消息模板，例如首次联系的英文消息
├── wa-bridge/                    # Node 服务：管理 WhatsApp 登录会话和实际消息收发
│   └── src/                      # 收发桥源码：HTTP 接口、消息监听、入站转发
├── integrations/                 # 外部平台适配资料和部署脚本
│   └── google_sheets/            # Google Apps Script 网关脚本，部署在 Google 端
├── migrations/                   # 数据库建表与后续结构变更，按顺序编号保存
├── tests/                        # 关键业务行为测试，随功能逐步添加
├── docs/                         # 需求、数据库字段含义、配置说明和设计决策
├── tools/                        # 手动执行的维护工具，例如数据检查和迁移核对
├── .idea/                        # PyCharm 工程配置，由 IDE 管理
├── .venv/                        # Python 虚拟环境和已安装依赖，不存放业务代码
├── main.py                       # 当前已有的入口示例；后续正式启动入口再随实现统一
├── test_main.http                # 当前已有的 HTTP 请求示例，可在 IDE 中手动调用接口
└── README.md                     # 本文件：项目入口说明和目录用途
```

## 模块之间怎么配合

Google 表格只读网关已编写：`integrations/google_sheets/google_sheets_lead_gateway.gs`，提供获取末行和分批读取接口。部署步骤见 [Google表格网关部署](docs/Google表格网关部署.md)。Python 接收开关、轮询和入库模块尚未实现。

统一配置层已编写：普通设置位于 `config.toml`，秘密位于本机 `.env`，由 `app/settings.py` 统一读取和校验。配置项说明见 [配置说明](docs/配置说明.md)。当前 `.env` 已填写网关地址，但访问令牌刻意留空；更换 Apps Script 的长令牌并在本机填写相同值后，配置才能通过校验。

这些 `app/` 子目录是同一个 Python 服务中的模块，不是分别启动的独立服务。

1. `leads/` 从表格导入线索，生成固定首次联系模板，通过 `messages/` 发送并记录已联系。
2. `wa-bridge/` 监听 WhatsApp 新消息，将事件转发给 Python 的 `messages/` 接口，保存客户消息。
3. `replies/` 判断未处理消息是否静默满 90 秒，组装上下文，由内部 `ClaudeRunner` 启动 CLI 子进程，通过 stdin 传入 prompt，并传入配置和客户 session ID，读取返回结果。
4. `scoring/` 保存内部评分；`notifications/` 按条件将结果发送给钉钉。客户看不到评分消息。

Claude 是否经 MCP 直接发送消息尚未确定，因此目前没有创建 `whatsapp-mcp/` 目录。

## 后续代码放置约定

- 服务入口、统一配置读取、数据库连接和统一调度放在 `app/` 根目录；定时任务调用各业务模块，不再用 PowerShell 循环运行业务脚本。
- 每个业务模块的数据读写集中在该模块的 repository 文件中。`wa-bridge/` 只做平台收发适配，不直接写客户业务表。
- `replies/` 中的 prompt 拼装负责本轮客户数据；`agent/` 保存稳定的客服身份和策略，避免在多个文件重复维护同一套规则。
- `scoring/` 放程序处理逻辑，`agent/.claude/skills/customer-scoring/` 放评分规则，二者职责不同。
- `integrations/google_sheets/` 放 Google 端的网关脚本；本地定时同步逻辑放在 `app/leads/`。
- `tools/` 只放人工维护工具，正常运行不依赖逐个启动这些工具。
- 普通运行配置与密钥后续统一管理，配置用途记入 `docs/`，真实密钥不写进 README。

## 当前已确认的边界

- 消息方向只有 `in/out`，暂不增加 `stop` 或人工接管回复模式。
- 主动发送首次联系模板就视为该 lead 已处理，暂不设计复杂发送补偿状态。
- Claude CLI 按回复轮次启动和退出，由 Python 服务内部的 `ClaudeRunner` 管理。
- 已记录待修复问题：AI 生成期间到达的新消息可能被后入库的 out 消息挡住。修复思路是在 `replies/` 中按 `last_processed_inbound_id` 记录实际处理进度，而不是根据最后一条 out 推断哪些消息已处理；尚未实现。

目录调整时同步更新本文件，避免结构和说明脱节。
