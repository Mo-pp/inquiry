"use strict";

const http = require("node:http");
const https = require("node:https");

class PythonClient {
  constructor(baseUrl) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.requests = new Set();
  }

  post(route, body) {
    const url = new URL(this.baseUrl + route);
    const transport = url.protocol === "https:" ? https : http;
    return new Promise((resolve, reject) => {
      // 每轮独立请求，不设置处理时长上限，也不自动重试。
      const request = transport.request(url, {
        method: "POST", agent: false, headers: { "Content-Type": "application/json" },
      }, response => {
        let text = "";
        response.setEncoding("utf8");
        response.on("data", chunk => { text += chunk; });
        response.on("error", reject);
        response.on("end", () => {
          try {
            if (response.statusCode < 200 || response.statusCode >= 300) {
              throw new Error(`Python returned HTTP ${response.statusCode}`);
            }
            resolve(JSON.parse(text));
          } catch (error) { reject(error); }
        });
      });
      this.requests.add(request);
      request.on("close", () => this.requests.delete(request));
      request.on("error", reject);
      request.end(JSON.stringify(body));
    });
  }

  async startTurn(phone) {
    const result = await this.post("/turns/start", { phone });
    if (result?.phone !== phone || typeof result.turn_id !== "string" || !result.turn_id.trim()) {
      throw new Error("Python start response must contain matching phone and a non-empty turn_id");
    }
    return result.turn_id;
  }

  async processTurn(phone, turnId, messages) {
    const result = await this.post("/turns/process", { phone, turn_id: turnId, messages });
    if (result?.phone !== phone || result.turn_id !== turnId ||
        typeof result.reply_text !== "string" || !result.reply_text.trim()) {
      throw new Error("Python reply must contain matching phone/turn_id and non-empty reply_text");
    }
    return result.reply_text;
  }

  close() {
    for (const request of this.requests) request.destroy(new Error("Bridge stopped"));
  }
}

module.exports = { PythonClient };
