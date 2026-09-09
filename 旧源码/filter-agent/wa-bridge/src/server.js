"use strict";

const path = require("node:path");
const dotenv = require("dotenv");
const qrTerminal = require("qrcode-terminal");
const { Client, LocalAuth } = require("whatsapp-web.js");
const { createApp } = require("./app");
const { loadConfig } = require("./config");
const { WhatsAppClient } = require("./whatsapp-client");
const { InboundForwarder } = require("./inbound-forwarder");

dotenv.config({ path: path.resolve(__dirname, "..", ".env"), quiet: true });

const config = loadConfig();
const inboundForwarder = config.inboundEnabled
  ? new InboundForwarder({
      url: config.inboundUrl,
      timeoutMs: config.inboundTimeoutMs,
      maxAttempts: config.inboundForwardAttempts,
      retryDelayMs: config.inboundForwardRetryDelayMs,
    })
  : null;
let compensationTimer = null;
let compensationRunning = false;
async function runCompensationScan() {
  if (!inboundForwarder || compensationRunning) return;
  compensationRunning = true;
  try {
    await bridge.scanRecentMessages({
      limitPerChat: config.compensationMessagesPerChat,
      onMessage: (message) =>
        inboundForwarder.forward(message, { client: bridge.client }),
    });
  } catch (error) {
    console.error(
      `[wa-bridge] compensation scan failed: ${
        error instanceof Error ? error.message : String(error)
      }`,
    );
  } finally {
    compensationRunning = false;
  }
}
const bridge = new WhatsAppClient({
  Client,
  LocalAuth,
  qrTerminal,
  config,
  onInboundMessage: inboundForwarder
    ? (message) => inboundForwarder.forward(message, { client: bridge.client })
    : null,
  onReady:
    inboundForwarder &&
    (config.compensationScanOnReady || config.compensationScanIntervalSeconds > 0)
      ? () =>
          runCompensationScan().then(() => {
            if (
              config.compensationScanIntervalSeconds > 0 &&
              compensationTimer === null
            ) {
              compensationTimer = setInterval(
                runCompensationScan,
                config.compensationScanIntervalSeconds * 1000,
              );
            }
          })
      : null,
});
const app = createApp(bridge);

const server = app.listen(config.port, config.host, () => {
  console.log(
    `[wa-bridge] HTTP server listening on http://${config.host}:${config.port}`,
  );
});

bridge.initialize().catch((error) => {
  console.error(
    `[wa-bridge] WhatsApp initialization failed: ${
      error instanceof Error ? error.message : String(error)
    }`,
  );
});

let shuttingDown = false;
async function shutdown(signal) {
  if (shuttingDown) return;
  shuttingDown = true;
  console.log(`[wa-bridge] Received ${signal}; shutting down.`);
  if (compensationTimer !== null) clearInterval(compensationTimer);
  server.close();
  await bridge.destroy();
  process.exit(0);
}

process.on("SIGINT", () => shutdown("SIGINT"));
process.on("SIGTERM", () => shutdown("SIGTERM"));
