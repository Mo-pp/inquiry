const DEFAULT_BRIDGE_URL = "http://127.0.0.1:3010";
const DEFAULT_TIMEOUT_MS = 10_000;

export class BridgeClientError extends Error {
  constructor(message, { statusCode = 502, cause } = {}) {
    super(message, { cause });
    this.name = "BridgeClientError";
    this.statusCode = statusCode;
  }
}

function parseTimeout(value) {
  if (value === undefined) return DEFAULT_TIMEOUT_MS;
  const timeout = Number.parseInt(value, 10);
  if (!Number.isInteger(timeout) || timeout < 100 || timeout > 120_000) {
    throw new Error("WA_MCP_TIMEOUT_MS must be an integer from 100 to 120000");
  }
  return timeout;
}

export function loadBridgeConfig(env = process.env) {
  const rawUrl = env.WA_BRIDGE_URL || DEFAULT_BRIDGE_URL;
  let url;
  try {
    url = new URL(rawUrl);
  } catch (error) {
    throw new Error("WA_BRIDGE_URL must be a valid HTTP URL", { cause: error });
  }
  if (!['http:', 'https:'].includes(url.protocol)) {
    throw new Error("WA_BRIDGE_URL must use http or https");
  }

  return {
    baseUrl: url.toString().replace(/\/$/, ""),
    timeoutMs: parseTimeout(env.WA_MCP_TIMEOUT_MS),
  };
}

export class BridgeClient {
  constructor({ baseUrl = DEFAULT_BRIDGE_URL, timeoutMs = DEFAULT_TIMEOUT_MS, fetchImpl = fetch } = {}) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.timeoutMs = timeoutMs;
    this.fetchImpl = fetchImpl;
  }

  async request(path, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      let response;
      try {
        response = await this.fetchImpl(`${this.baseUrl}${path}`, {
          ...options,
          signal: controller.signal,
        });
      } catch (error) {
        const detail = error?.name === "AbortError"
          ? `bridge request timed out after ${this.timeoutMs}ms`
          : `bridge request failed: ${error?.message || String(error)}`;
        throw new BridgeClientError(detail, { cause: error });
      }

      const data = await response.json().catch(() => null);
      if (!response.ok) {
        const detail = data?.detail || `bridge returned HTTP ${response.status}`;
        throw new BridgeClientError(detail, { statusCode: response.status });
      }
      return data;
    } finally {
      clearTimeout(timeout);
    }
  }

  health() {
    return this.request("/health");
  }

  sendText(phone, text) {
    return this.request("/messages/send", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ phone, text }),
    });
  }

  checkNumber(phone) {
    return this.request("/messages/check", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ phone }),
    });
  }
}
