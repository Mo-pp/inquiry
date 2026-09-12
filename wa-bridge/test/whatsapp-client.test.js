"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { WhatsAppClient } = require("../src/whatsapp-client");
const flush = () => new Promise(resolve => setImmediate(resolve));

test("only private inbound text is forwarded; LID is resolved to phone and reply uses original chat", async () => {
  const received = [], sent = [];
  class FakeClient extends EventEmitter {
    async getContactLidAndPhone() { return [{ pn: "123456789@c.us" }]; }
    async sendMessage(...args) { sent.push(args); return { id: { id: "sent-id" } }; }
  }
  const bridge = new WhatsAppClient({ Client: FakeClient, LocalAuth: class {},
    qrTerminal: {}, config: {}, onMessage: message => received.push(message) });
  const msg = { from: "987654321@lid", type: "chat", body: "111", id: { _serialized: "m1" } };
  for (const overrides of [{fromMe:true}, {from:"123@g.us"}, {type:"image",hasMedia:true},
    {type:"ptt"}, {from:"status@broadcast"}, {body:" "}]) {
    bridge.client.emit("message", {...msg, ...overrides});
  }
  bridge.client.emit("message", msg);
  bridge.client.emit("message", {...msg, body:"222", id:{_serialized:"m2"}});
  await flush();
  assert.deepEqual(received.map(m => m.text), ["111", "222"]);
  assert.equal(received[0].phone, "+123456789");
  assert.equal(received[0].chatId, "987654321@lid");
  bridge.status = "ready";
  await bridge.sendText(received[0].chatId, "reply");
  assert.equal(sent[0][0], "987654321@lid");
  assert.equal(sent[0][1], "reply");
  bridge.client.sendMessage = async () => ({});
  await assert.rejects(bridge.sendText(received[0].chatId, "reply"), /without a message ID/);
});
