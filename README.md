# 林创 AI 客服

项目采用一个 Python 常驻服务处理业务，`wa-bridge` 负责 WhatsApp 收发。以下目录已创建，目前业务模块尚未实现；目录说明用于约定后续代码放在哪里。

新系统使用专用数据库 `lintratek_AI`（2026-09-12 新建），与旧系统的 `lintratek_chat` 完全隔离，旧库中的在用表不受影响。`migrations/` 按编号执行：`000_create_database.sql` 建库；`001_create_leads.sql` 线索表，沿用旧项目 `schema.sql` 中最终版本的 `customer_leads` 字段、类型、默认值及索引列，表名改为 `leads`，另增加 `customer_id`（首次联系时写入对应的 `customers.customer_id`，导入时为 NULL）；`002_create_customers.sql` 客户主档；`003_create_messages.sql` 消息流水（in/out）。所有文件仅包含非破坏性定义，不含删表操作。建库建表当天已核验：三张表均为 InnoDB、utf8mb4，字段、索引与字符集符合预期。

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

统一配置层已编写：普通设置位于 `config.toml`，秘密位于本机 `.env`，由 `app/settings.py` 统一读取和校验。配置项说明见 [配置说明](docs/配置说明.md)。`.env` 已填写网关地址与 32 位访问令牌（与 Apps Script 脚本属性 `ACCESS_TOKEN` 相同）。

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

## 首次联系模板预览

首次联系消息拼装已实现，也已供手动处理和自动扫描复用。预览相关三个文件：

- `templates/first_contact.toml`：英文话术、渠道名称和空答案提示。修改保存后，下次拼装即生效，无需重启。保留 `{channel}` 和五个答案占位符名称；正文中的普通花括号写成 `{{` / `}}`。
- `app/leads/first_contact_template.py`：`build_first_contact_message(lead)` 接收数据库行字典，返回纯文本。项目询问使用固定问句，五个答案保留原文，空答案显示 `Not provided`。
- `tools/preview_first_contact.py`：通过现有 Repository 按 `source_lead_id` 只读查询并打印消息。使用已有 `config.toml` / `.env` 配置，不启动同步、不发送、不修改客户或线索。该工具可独立删除，不影响拼装功能。

在项目根目录运行（把示例 ID 替换为实际线索 ID）：

```powershell
.\.venv\Scripts\python.exe tools\preview_first_contact.py "实际的source_lead_id"
```

线索不存在时打印提示并以退出码 1 结束；配置或数据库错误直接报错，便于修正后再次预览。

## 当前服务启动方式

WhatsApp 桥的独立启动入口已实现，详见 `wa-bridge/README.md`。
支持扫码登录、保存独立会话和 `GET /health` 状态查询。
wa 侧私聊文字归轮及回复发送已实现：每个客户静默 20 秒封闭一轮，并行请求外部 Python，按客户轮次顺序发送。
`WA_TURNS_ENABLED` 默认关闭，Python 轮次接口尚未实现，尚未进行真实收发联调。
首次联系号码检查、发送和 leads 扫描已接入 Python；默认关闭自动首次联系。
接口契约与文件职责见桥的 README。
在 `wa-bridge/` 下运行 `npm.cmd start`；默认监听本机端口 3010。

## 首次联系处理与自动扫描

文件职责：

| 文件 | 职责 |
| --- | --- |
| `app/messages/wa_bridge_client.py` | 通过 HTTP 检查号码和发送文本，校验桥的返回结果；不重试发送 |
| `app/leads/first_contact_service.py` | `process_one` 处理一条 lead；后台线程每轮完成后等 20 秒，默认不开启 |
| `app/leads/lead_repository.py` | 查线索、扫描未联系线索；发送成功后统一事务保存客户、消息、lead 标记 |
| `tools/process_first_contact.py` | 按指定 lead ID 手动执行一次真实联系 |
| `config.toml` / `app/settings.py` | 桥地址、请求超时、首次联系开关、扫描间隔及批量大小 |
| `main.py` | 启停同步与首次联系线程；先等待业务结束，再关闭 HTTP 客户端 |

单条处理顺序：读取 lead → 已联系则跳过 → 整理号码并拼模板 → 检查注册 → 发送 →
成功后同一事务创建/复用 customers、写入 out messages、回写 leads.customer_id 和 customer_added=true。
同号码复用客户，一条新表单仍发送一次；不覆盖已有客户姓名和首次联系时间，不修改 lead_status。
号码仅清理空格、括号和连字符，必须已含 `+` 和国家区号；不猜区号，不修改 lead 原始号码。
contacted_at 使用发送成功后的 UTC 时间；未返回平台时间时 messages.platform_time 留空。

手动执行（这条命令会真实发送，预览请使用上面的 preview 工具）：

```powershell
.\.venv\Scripts\python.exe tools\process_first_contact.py "实际的source_lead_id"
```

自动执行：确保桥 ready，设置 `[first_contact].enabled_on_startup = true`，再启动/重启 main.py。
该开关与 `[google_sheets].enabled_on_startup` 独立；Google 接收保持关闭也可处理手工入库线索。
开启后会处理库中所有 customer_added=false 的线索，不限新插入的测试线索。
每轮最多 20 条，按 source_lead_id 分批向后扫描，到末尾再从头查，避免无效号码挡住后面的线索。
未注册或发送前明确失败：不建客户、不标记已联系，记日志并继续后续线索，以后扫描还会检查。
正常退出会等当前线索处理及入库结束，然后停止，不再开始下一条。

发送结果不确定或发送成功后数据库保存未确认：报错并停止该次自动扫描，需人工核对再重启。
暂停原因仅在内存和日志中，重启会重新扫描；未核对前不要重启或手动重发该 lead。
尚无跨进程防重复/补偿机制；只运行一个 main.py，不和手动发送工具并行。
进程恰好在发送后、标记前崩溃仍可能导致重复，第一版暂不引入复杂状态机。

测试：`python -m unittest discover -s tests` 和 `npm.cmd --prefix wa-bridge test`。
设置 `FIRST_CONTACT_TEST_MYSQL=1` 可运行 MySQL 集成测试，只写本连接的临时表，HTTP 模拟发送。
已验证模拟完整联系、下一轮跳过、同号码复用客户和事务失败回滚。
2026-09-12 已完成用户指定号码的真实测试：首次发送发现 WhatsApp MsgKey 字段兼容问题，
核对实际消息后补记发送；修正后另建复测 lead，完整自动完成发送、客户复用、消息保存及已联系标记。
复测等待超过 20 秒后确认没有重复发送。两条测试 lead 和两条发送记录保留在本机数据库。
20 项 Python 测试（含 MySQL 临时表事务验证）及 15 项 Node 测试通过。
测试扫描已停止；Google 接收和常驻服务的自动首次联系开关仍为 false。

`main.py` 已接入统一配置、网关客户端、MySQL Repository 和同步线程的启动/停止。运行 `.\.venv\Scripts\python.exe main.py`。接收默认关闭，通过 `config.toml` 的 `enabled_on_startup` 控制，修改后重启生效，无 HTTP 开关接口。MySQL 普通配置在 `[mysql]`，密码在 `.env`。使用单进程，详见 [配置说明](docs/配置说明.md)。此前“尚未实现”的描述为历史规划，当前已完成入口接线，并于 2026-09-12 完成真实同步验证（表格新增整行成功入库）；随后按测试约定关闭接收并清空 `leads`。
