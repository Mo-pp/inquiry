"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { InboundForwarder, serializeInboundMessage } = require("../src/inbound-forwarder");

function makeMessage(overrides = {}) {
  return {
    id: { _serialized: "wamid-1" },
    from: "218086426841304@lid",
    fromMe: false,
    body: "Hello",
    timestamp: 1_756_863_723,
    type: "chat",
    async getChat() {
      return { id: { _serialized: "218086426841304@lid" }, isGroup: false };
    },
    async getContact() {
      return { number: "15555550100", pushname: "Prospect" };
    },
    ...overrides,
  };
}

test("serializes a one-to-one WAJS message without DOM data", async () => {
  const payload = await serializeInboundMessage(makeMessage());
  assert.equal(payload.chat_jid, "218086426841304@lid");
  assert.equal(payload.customer_phone, "+15555550100");
  assert.equal(payload.sender, "Prospect");
  assert.equal(payload.text, "Hello");
  assert.match(payload.message_key, /^[0-9a-f]{64}$/);
  assert.equal(payload.is_group, false);
});

test("uses the native WAJS message id when _serialized is absent", async () => {
  const payload = await serializeInboundMessage(
    makeMessage({ id: { id: "native-message-1" } }),
  );
  assert.equal(payload.message_id, "native-message-1");
});

test("prefers the full WAJS native id exposed as $1", async () => {
  const payload = await serializeInboundMessage(
    makeMessage({ id: { id: "native-message-1", $1: "true_chat_native-message-1" } }),
  );
  assert.equal(payload.message_id, "true_chat_native-message-1");
});

test("serializes group metadata so the receiver can ignore it", async () => {
  const payload = await serializeInboundMessage(
    makeMessage({ from: "123@g.us", async getChat() { return { isGroup: true }; } }),
  );
  assert.equal(payload.is_group, true);
});

test("uses WAJS LID-to-phone mapping when contact metadata has no number", async () => {
  const message = makeMessage({
    async getContact() { return { pushname: "Prospect" }; },
  });
  const payload = await serializeInboundMessage(message, {
    client: {
      async getContactLidAndPhone(ids) {
        assert.deepEqual(ids, ["218086426841304@lid"]);
        return [{ lid: "218086426841304@lid", pn: "15555550100@c.us" }];
      },
    },
  });
  assert.equal(payload.customer_phone, "+15555550100");
});

test("does not treat the numeric LID as a phone when mapping is available", async () => {
  const message = makeMessage({
    async getContact() {
      return { number: "218086426841304", pushname: "Prospect" };
    },
  });
  const payload = await serializeInboundMessage(message, {
    client: {
      async getContactLidAndPhone(ids) {
        assert.deepEqual(ids, ["218086426841304@lid"]);
        return [{ lid: "218086426841304@lid", pn: "8618664612668@c.us" }];
      },
    },
  });
  assert.equal(payload.customer_phone, "+8618664612668");
});

test("leaves an unresolved LID unmapped instead of inventing a country code", async () => {
  const message = makeMessage({
    async getContact() {
      return { number: "218086426841304", pushname: "Prospect" };
    },
  });
  const payload = await serializeInboundMessage(message, { client: {} });
  assert.equal(payload.customer_phone, null);
});

test("forwarder posts JSON and propagates the queue response", async () => {
  let request;
  const forwarder = new InboundForwarder({
    url: "http://127.0.0.1:8000/api/v1/whatsapp/inbound",
    fetchImpl: async (url, options) => {
      request = { url, options };
      return {
        ok: true,
        async json() { return { status: "accepted", job_id: 7 }; },
      };
    },
    logger: { error() {} },
  });
  const result = await forwarder.forward(makeMessage());
  assert.deepEqual(result, { status: "accepted", job_id: 7 });
  assert.equal(request.url, "http://127.0.0.1:8000/api/v1/whatsapp/inbound");
  assert.equal(request.options.method, "POST");
  assert.equal(JSON.parse(request.options.body).message_id, "wamid-1");
});

test("forwarder retries the same idempotent event after a temporary failure", async () => {
  let calls = 0;
  const forwarder = new InboundForwarder({
    url: "http://127.0.0.1:8000/api/v1/whatsapp/inbound",
    maxAttempts: 2,
    retryDelayMs: 0,
    fetchImpl: async (_url, options) => {
      calls += 1;
      if (calls === 1) throw new Error("temporary network failure");
      return {
        ok: true,
        async json() { return { status: "accepted" }; },
      };
    },
    logger: { error() {} },
  });

  const result = await forwarder.forward(makeMessage());
  assert.deepEqual(result, { status: "accepted" });
  assert.equal(calls, 2);
});
