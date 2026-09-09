"use strict";

const path = require("node:path");
const fs = require("node:fs");

function parseBoolean(value, fallback) {
  if (value === undefined) return fallback;
  if (value === "true") return true;
  if (value === "false") return false;
  throw new Error("WA_HEADLESS must be either true or false");
}

function parsePositiveInteger(value, fallback, name) {
  if (value === undefined || value === "") return fallback;
  const parsed = Number.parseInt(value, 10);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    throw new Error(`${name} must be a positive integer`);
  }
  return parsed;
}

function parseNonNegativeInteger(value, fallback, name) {
  if (value === undefined || value === "") return fallback;
  const parsed = Number.parseInt(value, 10);
  if (!Number.isInteger(parsed) || parsed < 0) {
    throw new Error(`${name} must be a non-negative integer`);
  }
  return parsed;
}

function findChromePath(env) {
  if (env.WA_CHROME_PATH) return env.WA_CHROME_PATH;
  if (process.platform !== "win32") return undefined;

  const candidates = [
    env.ProgramFiles &&
      path.join(env.ProgramFiles, "Google", "Chrome", "Application", "chrome.exe"),
    env["ProgramFiles(x86)"] &&
      path.join(
        env["ProgramFiles(x86)"],
        "Google",
        "Chrome",
        "Application",
        "chrome.exe",
      ),
    env.LOCALAPPDATA &&
      path.join(
        env.LOCALAPPDATA,
        "Google",
        "Chrome",
        "Application",
        "chrome.exe",
      ),
  ].filter(Boolean);

  return candidates.find((candidate) => fs.existsSync(candidate));
}

function loadConfig(env = process.env) {
  const port = Number.parseInt(env.WA_BRIDGE_PORT || "3010", 10);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error("WA_BRIDGE_PORT must be an integer from 1 to 65535");
  }

  return {
    host: env.WA_BRIDGE_HOST || "127.0.0.1",
    port,
    headless: parseBoolean(env.WA_HEADLESS, false),
    clientId: env.WA_CLIENT_ID || "filter-agent",
    chromePath: findChromePath(env),
    sessionPath: path.resolve(
      __dirname,
      "..",
      env.WA_SESSION_PATH || ".wa-session",
    ),
    // Disabled by default during the phased migration.  Enabling this only
    // forwards inbound events to the local queue; it never sends replies.
    inboundEnabled: parseBoolean(env.WA_INBOUND_ENABLED, false),
    inboundUrl:
      env.WA_INBOUND_URL ||
      "http://127.0.0.1:8000/api/v1/whatsapp/inbound",
    inboundTimeoutMs: parsePositiveInteger(
      env.WA_INBOUND_TIMEOUT_MS,
      10_000,
      "WA_INBOUND_TIMEOUT_MS",
    ),
    compensationScanOnReady: parseBoolean(
      env.WA_COMPENSATION_SCAN_ON_READY,
      false,
    ),
    compensationMessagesPerChat: parsePositiveInteger(
      env.WA_COMPENSATION_MESSAGES_PER_CHAT,
      50,
      "WA_COMPENSATION_MESSAGES_PER_CHAT",
    ),
    compensationScanIntervalSeconds: parseNonNegativeInteger(
      env.WA_COMPENSATION_SCAN_INTERVAL_SECONDS,
      0,
      "WA_COMPENSATION_SCAN_INTERVAL_SECONDS",
    ),
    inboundForwardAttempts: parsePositiveInteger(
      env.WA_INBOUND_FORWARD_ATTEMPTS,
      3,
      "WA_INBOUND_FORWARD_ATTEMPTS",
    ),
    inboundForwardRetryDelayMs: parseNonNegativeInteger(
      env.WA_INBOUND_FORWARD_RETRY_DELAY_MS,
      1000,
      "WA_INBOUND_FORWARD_RETRY_DELAY_MS",
    ),
  };
}

module.exports = { loadConfig, parsePositiveInteger, parseNonNegativeInteger };
