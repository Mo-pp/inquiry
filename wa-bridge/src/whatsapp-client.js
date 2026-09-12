"use strict";

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
    const message = await this.client.sendMessage(chatId, text, {
      waitUntilMsgSent: true, sendSeen: false,
    });
    const messageId = message?.id?._serialized || message?.id?.id;
    if (!messageId) throw new Error("Send submitted without a message ID; keeping turn blocked");
    return { message_id: messageId };
  }

  async destroy() {
    // destroy 关闭浏览器；不调用 logout，以便下次恢复登录。
    await this.client.destroy();
  }
}

module.exports = { WhatsAppClient };
