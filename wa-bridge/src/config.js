"use strict";

const fs = require("node:fs");
const path = require("node:path");

function loadConfig(env = process.env) {
  const port = Number(env.WA_BRIDGE_PORT || "3010");
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error("WA_BRIDGE_PORT must be an integer from 1 to 65535");
  }
  const headless = env.WA_HEADLESS || "false";
  if (!["true", "false"].includes(headless)) {
    throw new Error("WA_HEADLESS must be true or false");
  }
  const turnsEnabled = env.WA_TURNS_ENABLED || "false";
  if (!["true", "false"].includes(turnsEnabled)) throw new Error("WA_TURNS_ENABLED must be true or false");
  const silenceSeconds = Number(env.WA_SILENCE_SECONDS || "20");
  if (!Number.isFinite(silenceSeconds) || silenceSeconds <= 0 || silenceSeconds * 1000 > 2147483647) {
    throw new Error("WA_SILENCE_SECONDS must be a positive timer duration");
  }
  const pythonUrl = env.WA_PYTHON_URL || "http://127.0.0.1:8000";
  if (!["http:", "https:"].includes(new URL(pythonUrl).protocol)) throw new Error("WA_PYTHON_URL must use HTTP(S)");
  const candidates = env.WA_CHROME_PATH
    ? [env.WA_CHROME_PATH]
    : [env.ProgramFiles, env["ProgramFiles(x86)"], env.LOCALAPPDATA]
        .filter(Boolean)
        .map(root => path.join(root, "Google", "Chrome", "Application", "chrome.exe"));
  const chromePath = candidates.find(candidate => fs.existsSync(candidate));
  if (!chromePath) throw new Error("Google Chrome not found; set WA_CHROME_PATH in .env");
  return {
    host: "127.0.0.1",
    turnsEnabled: turnsEnabled === "true",
    silenceMs: silenceSeconds * 1000,
    pythonUrl,
    port,
    headless: headless === "true",
    chromePath,
    clientId: "lintratek-ai",
    sessionPath: path.resolve(__dirname, "..", ".wa-session"),
    cachePath: path.resolve(__dirname, "..", ".wwebjs_cache"),
  };
}

module.exports = { loadConfig };
