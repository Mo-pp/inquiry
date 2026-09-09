# WhatsApp MCP wrapper

This is a deliberately thin MCP adapter. It exposes two tools over STDIO and
forwards calls to the existing local `wa-bridge` HTTP service:

- `whatsapp_health`
- `whatsapp_send_reply`

It does not start WhatsApp Web, read the database, generate replies, or operate
the browser. Keep `wa-bridge` running separately.

## Install and test

```powershell
Set-Location .\whatsapp-mcp
npm install
npm test
```

## Run manually

```powershell
$env:WA_BRIDGE_URL = "http://127.0.0.1:3010"
npm start
```

The server speaks MCP JSON-RPC on stdin/stdout. Logs go to stderr so the MCP
protocol stream stays clean.

## Configure a harness

Configure an MCP server using STDIO:

```text
command: node
arguments: C:\Users\Administrator\Desktop\workspace\filter-agent\whatsapp-mcp\src\server.js
environment:
  WA_BRIDGE_URL: http://127.0.0.1:3010
```

For Codex CLI, the equivalent command is:

```powershell
codex mcp add lintratek-whatsapp -- node C:\Users\Administrator\Desktop\workspace\filter-agent\whatsapp-mcp\src\server.js
```

The bridge must already be running and `/health` must report `ready` before
calling `whatsapp_send_reply`. A bridge response of `submitted_unknown` means
the operation may have been accepted without a native message ID; do not
blindly resend it.
