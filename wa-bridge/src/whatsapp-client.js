"use strict";

class BridgeError extends Error {
  constructor(message, statusCode) {
    super(message);
    this.name = "BridgeError";
    this.statusCode = statusCode;
  }
}

class WhatsAppClient {
  constructor({ Client, LocalAuth, qrTerminal, config, onMessage = null }) {
    this.status = "starting";
    this.lastError = null;
    this.onMessage = onMessage;
    this.inboundChains = new Map();
    this.phoneIds = new Map();
    this.client = new Client({
      authStrategy: new LocalAuth({ clientId: config.clientId, dataPath: config.sessionPath }),
      webVersionCache: { type: "local", path: config.cachePath },
      userAgent: false,
      puppeteer: {
        headless: config.headless,
        executablePath: config.chromePath,
        ignoreDefaultArgs: ["--disable-extensions"],
      },
    });
    this.client.on("message", message => this.receiveMessage(message));
    this.client.on("qr", qr => {
      this.status = "qr_required";
      this.lastError = null;
      console.log("[wa-bridge] Scan the QR code using WhatsApp > Linked devices.");
      qrTerminal.generate(qr, { small: true });
    });
    for (const [event, status] of [["authenticated", "authenticated"], ["ready", "ready"]]) {
      this.client.on(event, () => {
        this.status = status;
        this.lastError = null;
        console.log(`[wa-bridge] WhatsApp ${status}`);
      });
    }
    for (const event of ["auth_failure", "disconnected"]) {
      this.client.on(event, reason => {
        this.status = event;
        this.lastError = String(reason || event);
        console.error(`[wa-bridge] ${event}: ${this.lastError}`);
      });
    }
  }

  async initialize() {
    try {
      await this.client.initialize();
      // 保留旧桥的会话恢复兼容处理：已同步会话可能错过库的 ready 回调。
      const page = this.client.pupPage;
      if (page && typeof page.evaluate === "function") {
        const shouldCompleteReady = await page.evaluate(() => {
          const socket = window.require?.("WAWebSocketModel")?.Socket;
          return Boolean(socket?.state === "CONNECTED" && socket?.__x_hasSynced === true &&
            typeof window.WWebJS === "undefined" &&
            typeof window.onAppStateHasSyncedEvent === "function");
        });
        if (shouldCompleteReady) {
          await page.evaluate(() => window.onAppStateHasSyncedEvent());
        }
      }
    } catch (error) {
      this.status = "initialization_failed";
      this.lastError = error.message;
      throw error;
    }
  }

  health() {
    return { status: this.status, ...(this.lastError ? { error: this.lastError } : {}) };
  }

  async checkNumber(phone) {
    if (typeof phone !== "string" || !/^\+[1-9]\d{5,14}$/.test(phone)) {
      throw new BridgeError("phone must be an international number such as +8613800000000", 400);
    }
    if (this.status !== "ready") {
      throw new BridgeError(`WhatsApp is not ready: ${this.status}`, 503);
    }
    const numberId = await this.client.getNumberId(phone.slice(1));
    const chatId = numberId?._serialized || null;
    return {
      status: chatId ? "registered" : "not_registered",
      phone,
      chat_jid: chatId,
    };
  }

  receiveMessage(message) {
    if (!this.onMessage || message.fromMe || message.type !== "chat" || message.hasMedia ||
        typeof message.body !== "string" || !message.body.trim() ||
        !/^\d+@(c\.us|lid)$/.test(message.from)) return;
    const receivedAt = Date.now();
    const chatId = message.from;
    // 号码解析按聊天串行，避免异步 LID 查询颠倒同一聊天消息；不等待 Python。
    const previous = this.inboundChains.get(chatId) || Promise.resolve();
    const pending = previous.then(async () => {
      let phoneId = this.phoneIds.get(chatId) || chatId;
      if (phoneId.endsWith("@lid")) {
        const mappings = await this.client.getContactLidAndPhone([chatId]);
        phoneId = mappings?.[0]?.pn;
      }
      if (typeof phoneId !== "string" || !/^[1-9]\d{5,14}@c\.us$/.test(phoneId)) {
        throw new Error("Unable to resolve incoming WhatsApp phone number");
      }
      this.phoneIds.set(chatId, phoneId);
      const messageId = message.id?._serialized || message.id?.id;
      if (!messageId) throw new Error("Incoming message has no message ID");
      this.onMessage({ phone: `+${phoneId.split("@")[0]}`, chatId,
        messageId, text: message.body, receivedAt });
    }).catch(error => console.error("[wa-bridge] Incoming message failed:", error.message));
    this.inboundChains.set(chatId, pending);
    pending.finally(() => {
      if (this.inboundChains.get(chatId) === pending) this.inboundChains.delete(chatId);
    });
  }

  async sendText(chatId, text) {
    if (this.status !== "ready") throw new Error(`WhatsApp is not ready: ${this.status}`);
    if (!/^\d+@(c\.us|lid)$/.test(chatId) || typeof text !== "string" || !text.trim()) {
      throw new Error("A private chat ID and non-empty reply are required");
    }
    const messageId = await this._sendText(chatId, text);
    if (!messageId) throw new Error("Send submitted without a message ID; keeping turn blocked");
    return { message_id: messageId };
  }

  async _sendText(chatId, text) {
    // 首次联系和私聊回复共用实际发送操作；各自处理不确定结果。
    if (this.client.pupPage) {
      await this.client.pupPage.evaluate(() => {
        // WhatsApp Web 的 MsgKey 现以 $1 保存完整 ID；1.34.7 仍用
        // _serialized 回查刚发出的消息。补同义属性，避免发送成功却返回 undefined。
        const MsgKey = window.require('WAWebMsgKey');
        if (!('_serialized' in MsgKey.prototype)) {
          Object.defineProperty(MsgKey.prototype, '_serialized', {
            configurable: true,
            get() { return this.$1; },
          });
        }
      });
    }
    const message = await this.client.sendMessage(chatId, text, {
      waitUntilMsgSent: true, sendSeen: false,
    });
    const id = message?.id;
    const value = typeof id === "string" ? id : id?._serialized || id?.serialized || id?.$1 || id?.id;
    return typeof value === "string" && value.trim() ? value : null;
  }

  async sendToPhone(phone, text) {
    if (typeof text !== "string" || !text.trim()) {
      throw new BridgeError("text must be a non-empty string", 400);
    }
    const checked = await this.checkNumber(phone);
    if (checked.status !== "registered") {
      throw new BridgeError("phone is not registered on WhatsApp", 404);
    }
    let sendJid = checked.chat_jid;
    // 沿用旧桥：首次联系 LID 号码时，优先使用库解析出的电话 JID。
    if (sendJid.endsWith("@lid") && typeof this.client.getContactLidAndPhone === "function") {
      const mappings = await this.client.getContactLidAndPhone([sendJid]);
      const phoneJid = mappings?.[0]?.pn || mappings?.[0]?.phone;
      if (typeof phoneJid === "string" && /^[1-9]\d{5,14}@c\.us$/.test(phoneJid)) {
        if (phoneJid !== `${phone.slice(1)}@c.us`) {
          throw new BridgeError("WhatsApp phone mapping does not match requested phone", 502);
        }
        sendJid = phoneJid;
      }
    }
    let messageId = null;
    try {
      messageId = await this._sendText(sendJid, text);
    } catch (error) {
      // 进入发送操作后，异常不一定代表未发出；不得自动重试。
      console.error("[wa-bridge] Send outcome unknown:", error.message);
    }
    return {
      status: messageId ? "sent" : "submitted_unknown",
      phone,
      chat_jid: checked.chat_jid,
      message_id: messageId,
    };
  }

  async destroy() {
    // destroy 关闭浏览器；不调用 logout，以便下次恢复登录。
    await this.client.destroy();
  }
}

module.exports = { WhatsAppClient, BridgeError };
