# WhatsApp Web bridge

Minimal local HTTP service that sends one text message through WhatsApp Web by
using `whatsapp-web.js`. It intentionally does not generate replies, poll the
filter database, retry failures, or manage concurrent jobs.

It can also (when `WA_INBOUND_ENABLED=true`) forward `whatsapp-web.js` incoming
`message` events to the local filter-agent queue.  This uses the WAJS API and
does not inspect WhatsApp Web DOM selectors.  The forwarder is disabled by
default during the phased migration.

## Install and start

```powershell
Set-Location .\wa-bridge
npm install
Copy-Item .env.example .env
npm start
```

The first run opens a dedicated Chromium window and prints a QR code in the
terminal. In WhatsApp on the phone, open **Linked devices**, link the device,
and wait until the terminal prints `WhatsApp is ready`.

On Windows the bridge automatically finds the installed Google Chrome program.
It uses only the browser executable; the WhatsApp login data remains in the
dedicated `.wa-session/` directory. Set `WA_CHROME_PATH` in `.env` if Chrome is
installed somewhere else.

The login session is stored under `.wa-session/` and is excluded from version
control. Do not copy or publish this directory.

Check the service:

```powershell
Invoke-RestMethod http://127.0.0.1:3010/health
```

Expected ready response:

```json
{"status":"ready"}
```

## Send one explicitly approved test message

Run this from the repository root:

```powershell
.\scripts\send-whatsapp.ps1 `
  -Phone "+8613800000000" `
  -Text "您好，这是一条测试回复。"
```

`status: sent` means the WhatsApp Web send operation completed and returned a
native message ID. If the operation may have been accepted but WAJS returns no
ID, the bridge reports `status: submitted_unknown`; this is not a delivery/read
receipt and must not be blindly retried. Responses include
`delivery_status: "unknown"` because this bridge does not claim delivery or
read status.

## API

### `GET /health`

Returns one of `starting`, `qr_required`, `authenticated`, `ready`,
`auth_failure`, or `disconnected`.

### `POST /messages/send`

```json
{
  "phone": "+8613800000000",
  "text": "您好，这是一条测试回复。"
}
```

The phone must use international `+` format. The bridge verifies that the
number is registered on WhatsApp before sending.

### `POST /messages/check`

Checks registration and returns the current WAJS chat JID without sending:

```json
{"phone": "+8613800000000"}
```

Possible responses have `status: "registered"` with a `chat_jid`, or
`status: "not_registered"`. A JID may be an `@lid`; it must not be treated as
the business-layer E.164 phone number.
