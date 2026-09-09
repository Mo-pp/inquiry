"use strict";

const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const test = require("node:test");
const {
  BridgeError,
  WhatsAppClient,
} = require("../src/whatsapp-client");

class FakeLocalAuth {
  constructor(options) {
    this.options = options;
  }
}

class FakeClient extends EventEmitter {
  constructor(options) {
    super();
    this.options = options;
    this.numberId = { _serialized: "218086426841304@lid" };
    this.sendResult = { id: { _serialized: "platform-message-1" } };
    this.sent = [];
    this.lidMappings = [{ lid: "218086426841304@lid", pn: "8613800000000@c.us" }];
    this.chat = {
      fetchMessages: async () => [
        {
          id: { _serialized: "message-1" },
          fromMe: true,
          body: "hello",
          timestamp: 123,
          ack: 1,
        },
      ],
    };
  }

  async initialize() {}

  async getNumberId(number) {
    this.checkedNumber = number;
    return this.numberId;
  }

  async sendMessage(chatId, text, options) {
    this.sent.push({ chatId, text, options });
    return this.sendResult;
  }

  async getContactLidAndPhone(ids) {
    this.mappedIds = ids;
    return this.lidMappings;
  }

  async getChatById(chatId) {
    this.chatId = chatId;
    return this.chat;
  }

  async destroy() {}
}

function makeBridge() {
  return new WhatsAppClient({
    Client: FakeClient,
    LocalAuth: FakeLocalAuth,
    qrTerminal: { generate() {} },
    config: {
      clientId: "test",
      sessionPath: "C:\\test-session",
      headless: true,
      chromePath: "C:\\chrome.exe",
    },
    logger: { log() {}, error() {} },
  });
}

test("sendText requires a ready WhatsApp session", async () => {
  const bridge = makeBridge();

  await assert.rejects(
    bridge.sendText("+8613800000000", "hello"),
    (error) =>
      error instanceof BridgeError &&
      error.statusCode === 503 &&
      error.message.includes("not ready"),
  );
});

test("sendText resolves the current WhatsApp id before sending", async () => {
  const bridge = makeBridge();
  bridge.client.emit("ready");

  const result = await bridge.sendText("+8613800000000", "hello");

  assert.equal(bridge.client.checkedNumber, "8613800000000");
  assert.deepEqual(bridge.client.sent, [
    {
      chatId: "8613800000000@c.us",
      text: "hello",
      options: { waitUntilMsgSent: true, sendSeen: false },
    },
  ]);
  assert.deepEqual(result, {
    status: "sent",
    operation_submitted: true,
    message_id_acquired: true,
    delivery_status: "unknown",
    phone: "+8613800000000",
    message_id: "platform-message-1",
    chat_jid: "218086426841304@lid",
    send_jid: "8613800000000@c.us",
    phone_jid: "8613800000000@c.us",
  });
});

test("sendText accepts the native message id when _serialized is absent", async () => {
  const bridge = makeBridge();
  bridge.client.sendResult = { id: { id: "native-message-1" } };
  bridge.client.emit("ready");

  const result = await bridge.sendText("+8613800000000", "hello");

  assert.equal(result.status, "sent");
  assert.equal(result.message_id, "native-message-1");
  assert.equal(result.message_id_acquired, true);
});

test("sendText prefers WAJS full native id when exposed as $1", async () => {
  const bridge = makeBridge();
  bridge.client.sendResult = {
    id: { id: "native-message-1", $1: "true_chat_native-message-1" },
  };
  bridge.client.emit("ready");

  const result = await bridge.sendText("+8613800000000", "hello");

  assert.equal(result.status, "sent");
  assert.equal(result.message_id, "true_chat_native-message-1");
});

test("sendText rejects a number that is not registered", async () => {
  const bridge = makeBridge();
  bridge.client.numberId = null;
  bridge.client.emit("ready");

  await assert.rejects(
    bridge.sendText("+8613800000000", "hello"),
    (error) => error instanceof BridgeError && error.statusCode === 404,
  );
  assert.deepEqual(bridge.client.sent, []);
});

test("sendText does not report failure when WhatsApp omits the message id", async () => {
  const bridge = makeBridge();
  bridge.client.sendResult = undefined;
  bridge.client.emit("ready");

  const result = await bridge.sendText("+8613800000000", "hello");

  assert.deepEqual(result, {
    status: "submitted_unknown",
    operation_submitted: true,
    message_id_acquired: false,
    delivery_status: "unknown",
    phone: "+8613800000000",
    message_id: null,
    chat_jid: "218086426841304@lid",
    send_jid: "8613800000000@c.us",
    phone_jid: "8613800000000@c.us",
  });
});

test("initialize completes ready when the synced event raced initialization", async () => {
  const bridge = makeBridge();
  let evaluations = 0;
  bridge.client.pupPage = {
    evaluate: async () => {
      evaluations += 1;
      if (evaluations === 1) return true;
      bridge.client.emit("ready");
      return undefined;
    },
  };

  await bridge.initialize();

  assert.equal(evaluations, 2);
  assert.equal(bridge.status, "ready");
});

test("recentMessages reads a bounded message sample without sending", async () => {
  const bridge = makeBridge();
  bridge.client.emit("ready");

  const result = await bridge.recentMessages("+8613800000000", 100);

  assert.equal(bridge.client.checkedNumber, "8613800000000");
  assert.equal(bridge.client.chatId, "8613800000000@c.us");
  assert.deepEqual(result, {
      status: "ok",
      phone: "+8613800000000",
      chat_jid: "218086426841304@lid",
      phone_jid: "8613800000000@c.us",
      messages: [
      {
        message_id: "message-1",
        from_me: true,
        body: "hello",
        timestamp: 123,
        ack: 1,
      },
    ],
  });
  assert.deepEqual(bridge.client.sent, []);
});

test("recentMessages accepts the native message id when _serialized is absent", async () => {
  const bridge = makeBridge();
  bridge.client.chat = {
    fetchMessages: async () => [
      {
        id: { id: "native-history-id" },
        fromMe: true,
        body: "hello",
        timestamp: 123,
        ack: 1,
      },
    ],
  };
  bridge.client.emit("ready");

  const result = await bridge.recentMessages("+8613800000000", 20);

  assert.equal(result.messages[0].message_id, "native-history-id");
});
