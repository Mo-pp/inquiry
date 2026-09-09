"use strict";

const assert = require("node:assert/strict");
const { afterEach, test } = require("node:test");
const { createApp } = require("../src/app");
const { BridgeError } = require("../src/whatsapp-client");

const servers = [];

afterEach(async () => {
  await Promise.all(
    servers.splice(0).map(
      (server) =>
        new Promise((resolve, reject) =>
          server.close((error) => (error ? reject(error) : resolve())),
        ),
    ),
  );
});

async function start(bridge) {
  const server = createApp(bridge).listen(0, "127.0.0.1");
  servers.push(server);
  await new Promise((resolve) => server.once("listening", resolve));
  const address = server.address();
  return `http://127.0.0.1:${address.port}`;
}

test("GET /health returns bridge state", async () => {
  const baseUrl = await start({
    health: () => ({ status: "ready" }),
    sendText: async () => {},
  });

  const response = await fetch(`${baseUrl}/health`);
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { status: "ready" });
});

test("POST /messages/send forwards phone and text", async () => {
  let received;
  const baseUrl = await start({
    health: () => ({ status: "ready" }),
    sendText: async (phone, text) => {
      received = { phone, text };
      return { status: "sent", phone, message_id: "message-1" };
    },
  });

  const response = await fetch(`${baseUrl}/messages/send`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ phone: "+8613800000000", text: "test reply" }),
  });

  assert.equal(response.status, 200);
  assert.deepEqual(received, {
    phone: "+8613800000000",
    text: "test reply",
  });
  assert.deepEqual(await response.json(), {
    status: "sent",
    phone: "+8613800000000",
    message_id: "message-1",
  });
});

test("POST /messages/check forwards a phone registration lookup", async () => {
  let received;
  const baseUrl = await start({
    health: () => ({ status: "ready" }),
    checkNumber: async (phone) => {
      received = phone;
      return {
        status: "registered",
        phone,
        chat_jid: "123@lid",
        phone_jid: "15555550100@c.us",
      };
    },
    sendText: async () => {},
  });

  const response = await fetch(`${baseUrl}/messages/check`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ phone: "+15555550100" }),
  });

  assert.equal(response.status, 200);
  assert.equal(received, "+15555550100");
  assert.deepEqual(await response.json(), {
    status: "registered",
    phone: "+15555550100",
    chat_jid: "123@lid",
    phone_jid: "15555550100@c.us",
  });
});

test("POST /messages/recent forwards a read-only history lookup", async () => {
  let received;
  const baseUrl = await start({
    health: () => ({ status: "ready" }),
    sendText: async () => {},
    recentMessages: async (phone, limit) => {
      received = { phone, limit };
      return { status: "ok", phone, chat_jid: "123@lid", messages: [] };
    },
  });

  const response = await fetch(`${baseUrl}/messages/recent`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ phone: "+15555550100", limit: 5 }),
  });

  assert.equal(response.status, 200);
  assert.deepEqual(received, { phone: "+15555550100", limit: 5 });
  assert.deepEqual(await response.json(), {
    status: "ok",
    phone: "+15555550100",
    chat_jid: "123@lid",
    messages: [],
  });
});

test("bridge errors keep their HTTP status", async () => {
  const baseUrl = await start({
    health: () => ({ status: "starting" }),
    sendText: async () => {
      throw new BridgeError("WhatsApp is not ready", 503);
    },
  });

  const response = await fetch(`${baseUrl}/messages/send`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ phone: "+8613800000000", text: "test" }),
  });

  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), {
    detail: "WhatsApp is not ready",
  });
});

test("malformed JSON returns 400", async () => {
  const baseUrl = await start({
    health: () => ({ status: "ready" }),
    sendText: async () => {},
  });

  const response = await fetch(`${baseUrl}/messages/send`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: "{",
  });

  assert.equal(response.status, 400);
  assert.deepEqual(await response.json(), {
    detail: "request body must be valid JSON",
  });
});
