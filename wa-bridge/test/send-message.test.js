"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { once } = require("node:events");
const { WhatsAppClient } = require("../src/whatsapp-client");
const { createApp } = require("../src/app");
const vm = require("node:vm");

function fakeBridge() {
  const bridge = Object.create(WhatsAppClient.prototype);
  bridge.status = "ready";
  bridge.sent = [];
  bridge.client = {
    getNumberId: async () => ({ _serialized: "999999@lid" }),
    getContactLidAndPhone: async () => [{ pn: "8613800000000@c.us" }],
    sendMessage: async (...args) => { bridge.sent.push(args); return { id: { id: "native-id" } }; },
  };
  return bridge;
}

test("current WhatsApp MsgKey alias is restored before send without replacing existing support", async () => {
  const bridge = fakeBridge();
  class MsgKey { constructor() { this.$1 = "true_999999@lid_native-id"; } }
  bridge.client.pupPage = {
    evaluate: fn => vm.runInNewContext(`(${fn.toString()})()`, { window: { require: () => MsgKey } }),
  };
  bridge.client.sendMessage = async () => {
    const key = new MsgKey();
    assert.equal(key._serialized, key.$1);
    return { id: key };
  };
  assert.equal((await bridge.sendToPhone("+8613800000000", "Hello")).message_id, "true_999999@lid_native-id");
  Object.defineProperty(MsgKey.prototype, "_serialized", { configurable: true, get() { return "existing-native-id"; } });
  bridge.client.sendMessage = async () => ({ id: new MsgKey() });
  assert.equal((await bridge.sendToPhone("+8613800000000", "Hello")).message_id, "existing-native-id");
});

test("first contact resolves LID for sending while preserving native identity", async () => {
  const bridge = fakeBridge();
  assert.deepEqual(await bridge.sendToPhone("+8613800000000", "Hello\n{literal}"), {
    status: "sent", phone: "+8613800000000", chat_jid: "999999@lid", message_id: "native-id",
  });
  assert.deepEqual(bridge.sent, [["8613800000000@c.us", "Hello\n{literal}", {
    waitUntilMsgSent: true, sendSeen: false,
  }]]);
});

test("invalid inputs, unready state, unregistered numbers and wrong mapping never send", async () => {
  const bridge = fakeBridge();
  await assert.rejects(bridge.sendToPhone("+8613800000000", " "), {statusCode:400});
  await assert.rejects(bridge.sendToPhone("bad", "Hello"), {statusCode:400});
  bridge.status = "disconnected";
  await assert.rejects(bridge.sendToPhone("+8613800000000", "Hello"), {statusCode:503});
  bridge.status = "ready";
  bridge.client.getContactLidAndPhone = async () => [{pn: "8613900000000@c.us"}];
  await assert.rejects(bridge.sendToPhone("+8613800000000", "Hello"), {statusCode:502});
  bridge.client.getNumberId = async () => null;
  await assert.rejects(bridge.sendToPhone("+8613800000000", "Hello"), {statusCode:404});
  assert.equal(bridge.sent.length, 0);
});

test("HTTP send distinguishes known rejection from an uncertain submitted operation", async t => {
  const bridge = fakeBridge();
  const server = createApp(bridge).listen(0, "127.0.0.1");
  t.after(() => new Promise(resolve => { server.close(resolve); server.closeAllConnections(); }));
  await once(server, "listening");
  const url = `http://127.0.0.1:${server.address().port}/messages/send`;
  const post = body => fetch(url, { method:"POST", headers:{"Content-Type":"application/json"}, body });
  assert.equal((await post("[]")).status, 400);
  assert.equal((await post("{")).status, 400);
  let result = await post('{"phone":"+8613800000000","text":"Hello"}');
  assert.equal(result.status, 200);
  assert.equal((await result.json()).status, "sent");
  bridge.client.sendMessage = async () => ({});
  result = await post('{"phone":"+8613800000000","text":"Hello"}');
  assert.equal(result.status, 200);
  assert.equal((await result.json()).status, "submitted_unknown");
  bridge.client.sendMessage = async () => { throw new Error("connection lost during send"); };
  result = await post('{"phone":"+8613800000000","text":"Hello"}');
  const unknown = await result.json();
  assert.equal(unknown.status, "submitted_unknown");
  assert.equal(unknown.message_id, null);
});
