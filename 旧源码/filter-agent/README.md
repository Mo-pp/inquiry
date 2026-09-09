# 林创未读客户消息筛选服务

本地 FastAPI 服务接收 `wa-bridge` 从 WhatsApp Web/Chromium 会话转发的入站消息，
用 LangChain 和 DeepSeek 判断是否与林创手机信号放大器客服业务有关。相关消息按
客户电话增量写入 MySQL；上下文只参与判断，不写入数据库。

## 初始化

```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# 仅用于全新/测试数据库；schema.sql 会删除并重建表，生产库不要执行
mysql -u root -p < schema.sql
python -m filter_agent
```

## Google Sheets 获客同步

获客同步使用 Google Sheets API 将表格数据追加到 MySQL 的
`customer_leads` 表。它不会读取或写入 `customers`；只有 WhatsApp 建联并
产生对话后，客户才进入聊天客户流程。

### 当前状态：生产同步已暂停

为便于清空数据库并手动放入测试 lead，当前项目把暂停点放在本地 Python
同步服务：

1. 本地 Python 服务读取 `GOOGLE_SHEETS_SYNC_ENABLED`，默认值和当前 `.env`
   值均为 `false`。关闭时不会读取 Google Sheets、请求 Apps Script，也不会调用
   `LeadRepository.append()` 写入 `customer_leads`；`--once` 返回 0 条同步结果，
   持续模式记录日志后退出。
2. Google Sheets 和已经部署的 Apps Script 保持原样，表单仍可正常写入表格。源码
   `scripts\google_sheets_lead_gateway.gs` 里的 `PRODUCTION_SYNC_ENABLED=false`
   只是可选的远程保险开关；不重新部署它不会影响线上表单或网关。本地 Python 闸门
   在进程启动时读取配置，重启本地进程后立即生效。

因此现在可以在不触发 Google 读取和 WhatsApp 操作的情况下，手动准备测试数据库。
请不要启动同步任务，也不要把现有生产客户号码复制到测试数据。测试阶段应只使用你
明确提供并授权的测试 lead。

只有在确认恢复本地生产同步时，才完成以下操作：

```text
# 项目根目录 .env
GOOGLE_SHEETS_SYNC_ENABLED=true
```

如果当前使用的是已经正常工作的 Apps Script 部署，不需要修改或重新部署它；只有
你以后明确想关闭网关本身时，才部署源码中的远程保险开关。恢复前建议先用
`python -m filter_agent.lead_sync --once` 做一次受控检查。

数据库迁移目前不要执行。`migrations\002_rebuild_customer_leads_for_facebook_2026_09.sql`
会重建并清空获客表，只有在你完成备份、确认清空范围并明确批准后才可运行；本次暂停
不会自动清空数据库，也不会自动标记或触达任何 lead。

在 Google Cloud Console 启用 Google Sheets API，创建 Desktop OAuth Client，
下载 `credentials.json` 到项目目录，并在 `.env` 中设置：

```text
GOOGLE_SPREADSHEET_ID=107j9EZZY5GFEUL_6o83bsO32o978_-ro5kpi0PJ-GWw
GOOGLE_SHEET_GID=0
GOOGLE_CREDENTIALS_FILE=credentials.json
GOOGLE_TOKEN_FILE=google-token.json
GOOGLE_SHEETS_POLL_INTERVAL_SECONDS=60
```

如果无法使用 Google Cloud 付款验证，可以改用项目内的
`scripts\google_sheets_lead_gateway.gs`：在目标表格打开“扩展程序 > Apps Script”，
粘贴脚本，将 `ACCESS_TOKEN` 替换为随机长字符串，运行 `setup()` 并完成授权；然后
选择“部署 > 新部署 > Web 应用”，设置为“以我身份执行”，访问权限选择“任何人”。
本地服务不带 Google 登录态，因此必须选择允许匿名访问，再由脚本中的长 token
保护数据。把部署得到的 `/exec` 地址和同一个 token 配置到 `.env`：

```text
GOOGLE_SHEETS_SOURCE=apps_script
GOOGLE_APPS_SCRIPT_URL=https://script.google.com/macros/s/你的部署ID/exec
GOOGLE_APPS_SCRIPT_TOKEN=与脚本相同的随机字符串
```

此方式不需要 Google Cloud 项目或银行卡。Apps Script 端只返回表格数据给持有 token
的本地同步服务，不要使用“发布到网络”或公开表格链接；获客表含有电话号码和邮箱。
如果使用 API/OAuth 方式，则设置 `GOOGLE_SHEETS_SOURCE=api`。

第一次运行会打开浏览器完成 Google 账号授权，token 会写入本地的
`google-token.json`。单次同步和持续轮询分别使用：

```powershell
python -m filter_agent.lead_sync --once
python -m filter_agent.lead_sync
```

当前同步按 `（2026.09）Facebook 潜在客户表单.xlsx` 的 24 列固定映射，首次同步导入
现有历史行，之后按表格第一列 `id` 只追加新记录；重复 `id` 不会覆盖已有获客资料。
`customer_added` 默认是 `FALSE`，同步服务不读取或修改 `customers`。可将
`scripts\start_google_sheets_sync.ps1` 配置为 Windows
Task Scheduler 的开机任务，任务的“启动于”目录设为项目根目录，并选择“用户登录
时运行”或“开机时运行”。

建表脚本会删除旧测试表并创建：

- `customers`：每个客户一行，记录首次和最新消息时间，以及当前评分快照（`score`、`level_code`、`score_updated_at`）；
- `customer_messages`：每条消息一行，`message_key` 唯一去重。
- `customer_score_logs`：每次 Claude 评分一行，记录分数变化、等级、命中的评分项和用户原话证据；同一批入站消息按唯一键防止重复计分。

首次客户的相关批次保存 `all_messages` 完整双方历史；后续相关批次只保存
`unread_messages`，最多 5 条 `context_messages` 仅用于分类，不写入数据库。

默认监听 `http://127.0.0.1:8000`。健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

## API

### 服务化 WhatsApp 入站（阶段 3，默认关闭）

`wa-bridge` 可以在保持同一个 WhatsApp Web/Chromium 登录会话的前提下，把
`whatsapp-web.js` 的 `message` 事件转发到本地 `/api/v1/whatsapp/inbound`。
这条路径不读取 DOM，也不依赖页面上的未读角标。消息先进入 MySQL
`wa_inbox_jobs` 持久队列，再由后续 worker 分类和写入现有客户表；重复的
`message_key` 会幂等去重。群聊和自己发送的消息会被忽略，LID 与 E.164 手机号
分开保存。

需要先执行新增式迁移 `migrations\003_service_inbox_and_outreach.sql`，并在确认
阶段后同时显式设置（两个服务各自的环境文件）：

```text
# 项目根目录 .env
WHATSAPP_INBOUND_ENABLED=true

# wa-bridge/.env
WA_INBOUND_ENABLED=true
```

`WA_COMPENSATION_SCAN_ON_READY=true` 才会在 WAJS ready 后对当前本地可取得的聊天
历史做有界补偿扫描；默认关闭。设置
`WA_COMPENSATION_SCAN_INTERVAL_SECONDS` 为正数可以在 ready 期间周期性重复该有界
扫描。每次转发失败还会按 `WA_INBOUND_FORWARD_ATTEMPTS` 做短暂重试；这些机制都不
替代服务端消息水位，扫描仍不是服务器端完整历史保证。

队列 worker 同样默认空操作：

```powershell
python scripts\process_inbox_queue.py --once
```

只有在后续阶段明确批准后，才同时使用 `--execute` 和环境变量
`WHATSAPP_INBOUND_WORKER_EXECUTE=true`；这一步会把队列交给现有分类和客户写库
流程，但不会改变 MCP → wa-bridge 的回复链路。

`wa_inbox_jobs` 还可以用 `migrations\005_wa_message_id_idempotency.sql` 增加原生
`wa_message_id` 唯一约束。执行前应先检查非空重复值；该迁移不会自动清理或合并消息。

### 自动发现新 lead（阶段 3，只规划不发送）

`scripts\plan_pending_leads.py` 会只读取 `customer_leads` 中
`customer_added = FALSE` 且尚未有 `lead_outreach` 计划的记录。默认是只读 dry-run：

```powershell
python scripts\plan_pending_leads.py --limit 100
```

如需只创建幂等的 `lead_outreach` 计划，必须显式设置
`OUTREACH_PLANNER_PERSIST=true` 并传入 `--persist`。这一步仍然不会调用
`getNumberId()`、`sendMessage()` 或其他 WhatsApp 接口。计划默认进入
`pending_approval`；`--approved` 和 `--opted-in` 只用于受控模拟，不能替代阶段 5
的单号码授权。数据库号码安全检查失败时会直接阻断，而不是按“未命中”放行。

`scripts\process_lead_outreach.py` 只处理已经在 `lead_outreach` 中同时具备审批和
opt-in 时间戳的计划。默认也是一次 dry-run；它不在 `start_all.ps1` 中自动启动。
真实执行必须同时满足 `--execute`、`OUTREACH_WORKER_EXECUTE=true`、
`OUTREACH_REAL_SEND_ENABLED=true` 和恰好一个 `--allow-phone`，留给阶段 5 的单号码
授权测试。

### 主动触达 dry-run（阶段 3）

`scripts\plan_lead_outreach.py` 只读取你明确提供的一条 JSON lead，不读取生产
数据库，也不会调用 WhatsApp：

```powershell
python scripts\plan_lead_outreach.py --input .\one-test-lead.json `
  --approved --opted-in --allow-phone "+15555550100"
```

同一 `source_lead_id` 与英文模板版本会生成固定幂等键。首条消息模板支持表单
五项答案和最多三个定制问题；没有覆盖问题时，会按 A/B/C/D/E/K 类别提示选择首轮
三个英文正向问题，并在明显异常的表单答案旁追加澄清问题。阶段 3 始终输出 `dry_run`。
真实发送必须经过后续
单号码授权阶段，不能用 `customer_added` 作为发送成功标记。

主动消息在 WAJS 返回可靠 `message_id` 后，还会以 `out` 方向幂等写入
`customers`/`customer_messages`。这样客户的第一条回复进入调度器时，历史中包含
首条模板，调度提示也会读取同手机号的五项表单答案。后续轮次先处理表单中不清楚
或客户指出错误的字段，再从评分规则中选择最多三个尚未问过的正向问题；客户只回答
其中一部分时，下一轮保留未回答的问题。当前测试目标分数为 80，但信息已足以确定
唯一等级或命中垃圾信号时会提前结束追问。

代码中的 `LeadOutreachExecutor` 已把 `getNumberId`（通过 bridge 的
`/messages/check`）→ `sendMessage`（通过 `/messages/send`）→ 状态审计串起来，
但默认 `real_send_enabled=false`，阶段 3 不会执行这条路径。

暂定重试策略为：网络/超时等明确临时错误最多自动尝试 3 次，间隔 30 秒、2 分钟、
10 分钟；没有设置“每天最多 8 人”的业务上限。响应不确定或缺少 WhatsApp 消息
ID 时记为 `submitted_unknown`，先人工核对，不自动盲目重发。

阶段 4 的历史基线脚本为 `migrations\004_mark_existing_leads_baseline.sql`，在
执行前先运行只读报告 `004_baseline_preflight.sql`，并等待人工确认；脚本只更新
数据库标记，不调用 WhatsApp。

`POST /api/v1/unread-batches` 接收：

```json
{
  "customer_name": "张三",
  "customer_phone": "+8613800000000",
  "context_messages": [
    {
      "message_key": "context-1",
      "sender": "客服",
      "direction": "out",
      "timestamp": "10:00, 29/08/2026",
      "text": "请问您哪里没有信号？"
    }
  ],
  "unread_messages": [
    {
      "message_key": "unread-1",
      "sender": "张三",
      "direction": "in",
      "timestamp": "10:01, 29/08/2026",
      "text": "地下室没有手机信号"
    }
  ]
}
```

相关消息返回：

```json
{"status":"stored","is_relevant":true,"appended_count":1}
```

无关消息返回：

```json
{"status":"filtered","is_relevant":false,"appended_count":0}
```

- `422`：请求结构或电话格式错误。
- `502`：DeepSeek 分类失败，调用方应重试。
- `503`：MySQL 写入失败，调用方应重试。

客户电话必须是带国家区号的 `+` 加数字格式。`unread_messages` 只允许入站消息，
`context_messages` 最多 5 条。同一个 `message_key` 重复提交不会重复写入。

## 代码结构

```text
filter_agent/
  api/             # FastAPI 路由和错误映射
  classification/  # LangChain 分类、Prompt、模型与安全序列化
  persistence/     # MySQL 客户/消息事务与时间解析
  services/        # 分类后按结果写入的业务用例
```

聊天记录始终作为不可信 JSON 数据传给模型。模型没有工具、memory 或数据库访问权。

## 测试

```powershell
python -m unittest discover -s tests -v
```

## WhatsApp 回复链路 MVP

`wa-bridge/` 是独立的本地 Node.js 服务，使用 `whatsapp-web.js` 将明确提供的
国际电话号码和文本发送到 WhatsApp Web。它不参与消息分类，也不会自动读取
数据库或生成回复。

首次扫码、启动方式和 Codex 可手工执行的 PowerShell 调用脚本见
[`wa-bridge/README.md`](wa-bridge/README.md)。
