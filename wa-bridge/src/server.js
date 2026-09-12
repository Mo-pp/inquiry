"use strict";

const path = require("node:path");
require("dotenv").config({ path: path.resolve(__dirname, "..", ".env"), quiet: true });
const { Client, LocalAuth } = require("whatsapp-web.js");
const qrTerminal = require("qrcode-terminal");
const { loadConfig } = require("./config");
const { WhatsAppClient } = require("./whatsapp-client");
const { createApp } = require("./app");
const { PythonClient } = require("./python-client");
const { TurnManager } = require("./turn-manager");

const config = loadConfig();
const python = new PythonClient(config.pythonUrl);
let turns;
const bridge = new WhatsAppClient({ Client, LocalAuth, qrTerminal, config,
  onMessage: config.turnsEnabled ? message => turns.receive(message) : null });
turns = new TurnManager({ python, silenceMs: config.silenceMs,
  sendText: (chatId, text) => bridge.sendText(chatId, text) });
console.log(`[wa-bridge] Private text turns ${config.turnsEnabled ? "enabled" : "disabled"}`);
const server = createApp(bridge).listen(config.port, config.host, () => {
  console.log(`[wa-bridge] Listening on http://${config.host}:${config.port}`);
  bridge.initialize().catch(error => console.error("[wa-bridge] Initialization failed:", error));
});
server.on("error", error => {
  console.error("[wa-bridge] HTTP startup failed:", error.message);
  process.exitCode = 1;
});

let shuttingDown = false;
async function shutdown(signal) {
  if (shuttingDown) return;
  shuttingDown = true;
  console.log(`[wa-bridge] ${signal}: shutting down`);
  server.close();
  turns.stop();
  python.close();
  try {
    await bridge.destroy();
    process.exitCode = 0;
  } catch (error) {
    console.error("[wa-bridge] Shutdown failed:", error.message);
    process.exitCode = 1;
  }
}
process.on("SIGINT", () => shutdown("SIGINT"));
process.on("SIGTERM", () => shutdown("SIGTERM"));
