# WhatsApp 桥：登录、私聊文字归轮与回复发送

从旧项目提取，使用 whatsapp-web.js 1.34.7 和本机 Google Chrome。
HTTP 服务提供 GET /health。可选开启私聊文字归轮：调用外部 Python 接口处理，
再通过 WhatsApp 发送回复。不连接业务数据库，不扫描 leads，不提供首次联系发送接口。

## 安装与启动

需要 Node.js 20 或以上版本、Google Chrome。在本目录运行：

```powershell
$env:PUPPETEER_SKIP_DOWNLOAD = "true"
npm.cmd install
Copy-Item .env.example .env # 仅首次配置时执行，不覆盖已有本机配置
npm.cmd start
```

打开手机 WhatsApp → 已关联设备 → 关联设备，扫描新 Chrome 窗口中的二维码。
首次登录需要人工操作；终端也会显示二维码。等终端显示 WhatsApp ready。

另开终端检查：

```powershell
Invoke-RestMethod http://127.0.0.1:3010/health
```

返回状态包括 starting、qr_required、authenticated、ready、auth_failure、
disconnected、initialization_failed。只有 ready 表示客户端已就绪；HTTP 200 本身不代表已登录。
错误状态可能附带 error 字段。失败后查看日志、修正问题并重启，暂不自动重连。

使用 Ctrl+C 停止，再运行 npm.cmd start 验证恢复登录。
登录保存在本目录 .wa-session/，与旧项目隔离；不复制旧会话。
过期或手机取消关联时需要重新扫码。不要提交或分享会话目录。

## 文件职责

- src/server.js：启动 HTTP 和 WhatsApp，处理正常退出。
- src/config.js：读取 .env；固定监听 127.0.0.1，默认端口 3010，自动查找 Chrome。
- src/whatsapp-client.js：二维码、登录状态、会话恢复和浏览器关闭。
- src/app.js：GET /health。
- src/turn-manager.js：每个客户独立归轮、20 秒静默计时、并行提交、按轮次顺序发送。
- src/python-client.js：申请轮次与提交消息的 HTTP 调用，校验回复的 phone / turn_id。
- .env.example / .env：配置示例 / 本机配置，修改后重启生效。
- package.json / package-lock.json：依赖和启动命令 / 锁定版本。
- .gitignore：排除依赖、本机配置、登录数据及网页缓存。

若端口被旧桥占用，修改新桥 .env 的 WA_BRIDGE_PORT 后重启。

## 私聊文字归轮

先配置 Python 的两个接口，再在 .env 中设置并重启桥：

```dotenv
WA_TURNS_ENABLED=true
WA_PYTHON_URL=http://127.0.0.1:8000
WA_SILENCE_SECONDS=20
```

默认 WA_TURNS_ENABLED=false，仅登录；Python 尚未实现时保持关闭。
`/health` 的 ready 仅代表 WhatsApp 就绪，不代表 Python 接口可用。

仅接收一对一入站文字，忽略自己发的消息、群聊、广播、图片、语音、视频、文件等。
WhatsApp 的 LID 先解析为国际格式电话，不能把 LID 当电话。回复使用入站的实际聊天 ID。

1. 客户新一轮的第一条消息到达，立即占一个本地轮次位置，向 Python 申请 turn_id。
2. 申请期间的消息先缓冲；每条消息重置 20 秒静默计时。
3. 最后一条文字到达后静默 20 秒，封闭该轮。拿到 ID 后提交整轮消息。
4. 封闭后新消息进入下一轮，即使上一轮 ID 或回复仍未返回。
5. 各轮处理请求独立进行。回复先暂存，只按该客户本地创建轮次的顺序发送。
6. 上一轮发送成功才发送下一轮；不同客户互不等待。同一客户旧轮卡住也不妨碍新轮收集和提交。

Python 接口契约（本项目本次不实现 Python）：

### POST /turns/start

请求：
```json
{"phone":"+8613800000000"}
```
响应：
```json
{"phone":"+8613800000000","turn_id":"turn-001"}
```

turn_id 为非空字符串，同一客户的每个轮次使用不同 ID。wa 按本地创建顺序排序，不比较 ID 大小。

### POST /turns/process

请求：
```json
{
  "phone":"+8613800000000",
  "turn_id":"turn-001",
  "messages":[
    {"message_id":"message-1","text":"111"},
    {"message_id":"message-2","text":"222"},
    {"message_id":"message-3","text":"333"}
  ]
}
```
Python 处理完成后直接在该 HTTP 请求中返回 JSON：
```json
{"phone":"+8613800000000","turn_id":"turn-001","reply_text":"回复内容"}
```

无需回调接口。不设置处理秒数上限，不自动重试、超时跳过或恢复轮次。
请求断开、错误响应、关联字段不匹配、发送失败或发送未取得消息 ID 时，记录错误并阻挡该客户后续回复。
消息 ID 只表示发送操作返回了 ID，不表示已送达或已读。
所有轮次仅存在内存中，重启会丢失未处理消息和待发送回复。

## 验证

运行 `npm.cmd test`。测试用模拟 WhatsApp 和本机 HTTP 模拟 Python，不联系真实客户。
覆盖静默合并、慢 ID 返回、回复乱序、跨客户独立、发送串行、失败阻挡和停止服务。
