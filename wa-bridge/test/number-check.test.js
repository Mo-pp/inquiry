"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { once } = require("node:events");
const { WhatsAppClient } = require("../src/whatsapp-client");
const { createApp } = require("../src/app");

test("number lookup preserves LID and distinguishes absence from lookup errors", async () => {
  const bridge = Object.create(WhatsAppClient.prototype);
  bridge.status = "ready";
  const calls = [];
  bridge.client = { getNumberId: async phone => {
    calls.push(phone);
    return { _serialized: "12345@lid" };
  } };
  assert.deepEqual(await bridge.checkNumber("+8613800000000"), {
    status: "registered", phone: "+8613800000000", chat_jid: "12345@lid",
  });
  assert.deepEqual(calls, ["8613800000000"]);
  for (const phone of [null, 8613800000000, "8613800000000", "+0123456", "+86 13800000000", "+123", "+1234567890123456"]) {
    await assert.rejects(bridge.checkNumber(phone), { statusCode: 400 });
  }
  bridge.status = "qr_required";
  await assert.rejects(bridge.checkNumber("+8613800000000"), { statusCode: 503 });
  assert.equal(calls.length, 1);
  bridge.status = "ready";
  bridge.client.getNumberId = async () => null;
  assert.deepEqual(await bridge.checkNumber("+8613800000000"), {
    status: "not_registered", phone: "+8613800000000", chat_jid: null,
  });
  bridge.client.getNumberId = async () => { throw new Error("lookup failed"); };
  await assert.rejects(bridge.checkNumber("+8613800000000"), /lookup failed/);
});

test("HTTP check endpoint validates JSON and returns registration and readiness results", async t => {
  const bridge = Object.create(WhatsAppClient.prototype);
  bridge.status = "ready";
  let calls = 0;
  bridge.client = { getNumberId: async () => { calls++; return { _serialized: "8613800000000@c.us" }; } };
  const server = createApp(bridge).listen(0, "127.0.0.1");
  t.after(() => new Promise(resolve => { server.close(resolve); server.closeAllConnections(); }));
  await once(server, "listening");
  const url = `http://127.0.0.1:${server.address().port}`;
  const post = body => fetch(`${url}/messages/check`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body,
  });
  assert.deepEqual(await (await fetch(`${url}/health`)).json(), { status: "ready" });
  for (const body of ["{", "[]", "null", "{}", '{"phone":123}']) {
    const response = await post(body);
    assert.equal(response.status, 400);
    assert.equal(typeof (await response.json()).detail, "string");
  }
  assert.equal(calls, 0);
  let response = await post('{"phone":"+8613800000000"}');
  assert.equal(response.status, 200);
  assert.equal((await response.json()).status, "registered");
  bridge.client.getNumberId = async () => null;
  response = await post('{"phone":"+8613800000000"}');
  assert.equal(response.status, 200);
  assert.equal((await response.json()).chat_jid, null);
  bridge.status = "disconnected";
  response = await post('{"phone":"+8613800000000"}');
  assert.equal(response.status, 503);
  response = await post(JSON.stringify({phone: "x".repeat(33000)}));
  assert.equal(response.status, 413);
});
