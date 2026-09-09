import assert from "node:assert/strict";
import test from "node:test";
import { BridgeClient, BridgeClientError, loadBridgeConfig } from "../src/bridge-client.js";

function response(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() { return body; },
  };
}

test("loadBridgeConfig uses localhost defaults", () => {
  assert.deepEqual(loadBridgeConfig({}), {
    baseUrl: "http://127.0.0.1:3010",
    timeoutMs: 10_000,
  });
});

test("BridgeClient forwards health requests", async () => {
  const calls = [];
  const client = new BridgeClient({
    baseUrl: "http://127.0.0.1:3010/",
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      return response({ status: "ready" });
    },
  });

  assert.deepEqual(await client.health(), { status: "ready" });
  assert.equal(calls[0].url, "http://127.0.0.1:3010/health");
  assert.equal(calls[0].options.method, undefined);
});

test("BridgeClient forwards a text send request", async () => {
  let call;
  const client = new BridgeClient({
    fetchImpl: async (url, options) => {
      call = { url, options };
      return response({ status: "sent", message_id: null });
    },
  });

  const data = await client.sendText("+8613531360238", "测试");

  assert.deepEqual(data, { status: "sent", message_id: null });
  assert.equal(call.url, "http://127.0.0.1:3010/messages/send");
  assert.equal(call.options.method, "POST");
  assert.deepEqual(JSON.parse(call.options.body), {
    phone: "+8613531360238",
    text: "测试",
  });
});

test("BridgeClient forwards a number registration check", async () => {
  let call;
  const client = new BridgeClient({
    fetchImpl: async (url, options) => {
      call = { url, options };
      return response({ status: "registered", chat_jid: "123@lid" });
    },
  });

  assert.deepEqual(await client.checkNumber("+15555550100"), {
    status: "registered",
    chat_jid: "123@lid",
  });
  assert.equal(call.url, "http://127.0.0.1:3010/messages/check");
  assert.deepEqual(JSON.parse(call.options.body), { phone: "+15555550100" });
});

test("BridgeClient preserves bridge errors", async () => {
  const client = new BridgeClient({
    fetchImpl: async () => response({ detail: "WhatsApp is not ready" }, 503),
  });

  await assert.rejects(
    client.sendText("+8613531360238", "测试"),
    (error) => error instanceof BridgeClientError && error.statusCode === 503 && error.message === "WhatsApp is not ready",
  );
});

test("BridgeClient reports request timeouts", async () => {
  const client = new BridgeClient({
    timeoutMs: 100,
    fetchImpl: (_url, { signal }) => new Promise((_resolve, reject) => {
      signal.addEventListener("abort", () => {
        const error = new Error("aborted");
        error.name = "AbortError";
        reject(error);
      });
    }),
  });

  await assert.rejects(
    client.health(),
    (error) => error instanceof BridgeClientError && error.message.includes("timed out"),
  );
});
