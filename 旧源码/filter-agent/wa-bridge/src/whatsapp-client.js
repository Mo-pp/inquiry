"use strict";

const PHONE_PATTERN = /^\+[1-9]\d{5,14}$/;

function extractMessageId(message) {
  const id = message?.id;
  if (id === undefined || id === null) return null;
  if (typeof id === "string") return id.trim() || null;
  try {
    const value = id._serialized ?? id.serialized ?? id.$1 ?? id.id;
    if (value === undefined || value === null) return null;
    const text = String(value).trim();
    return text || null;
  } catch (_error) {
    return null;
  }
}

class BridgeError extends Error {
  constructor(message, statusCode) {
    super(message);
    this.name = "BridgeError";
    this.statusCode = statusCode;
  }
}

class WhatsAppClient {
  constructor({
    Client,
    LocalAuth,
    qrTerminal,
    config,
    logger = console,
    onInboundMessage = null,
    onReady = null,
  }) {
    this.logger = logger;
    this.qrTerminal = qrTerminal;
    this.onInboundMessage = onInboundMessage;
    this.onReady = onReady;
    this.status = "starting";
    this.lastError = null;

    this.client = new Client({
      authStrategy: new LocalAuth({
        clientId: config.clientId,
        dataPath: config.sessionPath,
      }),
      userAgent: false,
      puppeteer: {
        headless: config.headless,
        ...(config.chromePath ? { executablePath: config.chromePath } : {}),
        ignoreDefaultArgs: ["--disable-extensions"],
      },
    });

    this.bindEvents();
  }

  bindEvents() {
    this.client.on("qr", (qr) => {
      this.status = "qr_required";
      this.lastError = null;
      this.logger.log("[wa-bridge] Scan this QR code with WhatsApp:");
      this.qrTerminal.generate(qr, { small: true });
    });

    this.client.on("authenticated", () => {
      this.status = "authenticated";
      this.lastError = null;
      this.logger.log("[wa-bridge] WhatsApp authenticated; waiting for ready state.");
    });

    this.client.on("ready", () => {
      this.status = "ready";
      this.lastError = null;
      this.logger.log("[wa-bridge] WhatsApp is ready.");
      if (typeof this.onReady === "function") {
        Promise.resolve(this.onReady()).catch((error) => {
          this.logger.error(
            `[wa-bridge] ready callback failed: ${
              error instanceof Error ? error.message : String(error)
            }`,
          );
        });
      }
    });

    // ``message`` is the inbound signal.  ``message_create`` also contains
    // self-authored messages, so it is intentionally not used for intake.
    this.client.on("message", (message) => {
      if (typeof this.onInboundMessage !== "function") return;
      Promise.resolve(this.onInboundMessage(message)).catch((error) => {
        this.logger.error(
          `[wa-bridge] inbound callback failed: ${
            error instanceof Error ? error.message : String(error)
          }`,
        );
      });
    });

    this.client.on("auth_failure", (message) => {
      this.status = "auth_failure";
      this.lastError = String(message || "WhatsApp authentication failed");
      this.logger.error(`[wa-bridge] ${this.lastError}`);
    });

    this.client.on("disconnected", (reason) => {
      this.status = "disconnected";
      this.lastError = String(reason || "WhatsApp disconnected");
      this.logger.error(`[wa-bridge] Disconnected: ${this.lastError}`);
    });
  }

  async initialize() {
    this.status = "starting";
    try {
      await this.client.initialize();

      // whatsapp-web.js wires its ready callback to the WebSocket
      // ``hasSynced`` event. With a persisted LocalAuth session that event
      // can fire before the listener is attached, leaving an already
      // connected page stuck in ``starting`` forever. Kick the library's
      // existing callback once when that state is already true; this only
      // completes WAJS injection and never sends a message.
      const page = this.client.pupPage;
      if (page && typeof page.evaluate === "function") {
        const shouldCompleteReady = await page.evaluate(() => {
          const socket = window.require?.("WAWebSocketModel")?.Socket;
          return Boolean(
            socket?.state === "CONNECTED" &&
              socket?.__x_hasSynced === true &&
              typeof window.WWebJS === "undefined" &&
              typeof window.onAppStateHasSyncedEvent === "function",
          );
        });
        if (shouldCompleteReady) {
          await page.evaluate(() => window.onAppStateHasSyncedEvent());
        }
      }
    } catch (error) {
      this.status = "auth_failure";
      this.lastError = error instanceof Error ? error.message : String(error);
      throw error;
    }
  }

  health() {
    return {
      status: this.status,
      ...(this.lastError ? { error: this.lastError } : {}),
    };
  }

  async sendText(phone, text) {
    if (this.status !== "ready") {
      throw new BridgeError(
        `WhatsApp is not ready (current status: ${this.status})`,
        503,
      );
    }
    if (typeof phone !== "string" || !PHONE_PATTERN.test(phone)) {
      throw new BridgeError(
        "phone must be an international number such as +8613800000000",
        400,
      );
    }
    if (typeof text !== "string" || text.trim().length === 0) {
      throw new BridgeError("text must be a non-empty string", 400);
    }

    const numberId = await this.client.getNumberId(phone.slice(1));
    const chatJid = numberId?._serialized;
    if (!chatJid) {
      throw new BridgeError("phone is not registered on WhatsApp", 404);
    }

    // New multi-device accounts commonly return an ``@lid`` from
    // getNumberId(). WAJS can resolve it back to the phone JID; using that
    // ``@c.us`` JID lets WhatsApp Web create/open a first-time one-to-one
    // chat reliably, while the LID remains the native chat identity we
    // report to callers.
    let sendChatId = chatJid;
    let phoneJid = null;
    if (chatJid.endsWith("@lid") && typeof this.client.getContactLidAndPhone === "function") {
      try {
        const mappings = await this.client.getContactLidAndPhone([chatJid]);
        phoneJid = mappings?.[0]?.pn || mappings?.[0]?.phone || null;
        if (typeof phoneJid === "string" && phoneJid.endsWith("@c.us")) {
          sendChatId = phoneJid;
        }
      } catch (error) {
        this.logger.warn?.(
          `[wa-bridge] LID-to-phone mapping unavailable; using LID: ${
            error instanceof Error ? error.message : String(error)
          }`,
        );
      }
    }

    const message = await this.client.sendMessage(sendChatId, text, {
      waitUntilMsgSent: true,
      sendSeen: false,
    });
    // WAJS 1.34.x can expose the native message id as ``id.id`` while
    // ``id._serialized`` is null for LID chats.  Preserve both shapes.
    const messageId = extractMessageId(message);

    return {
      // This is an operation status, not a delivery/read receipt.  A missing
      // WAJS id is kept explicitly ambiguous so callers do not blindly retry.
      status: messageId ? "sent" : "submitted_unknown",
      operation_submitted: true,
      message_id_acquired: Boolean(messageId),
      delivery_status: "unknown",
      phone,
      message_id: messageId,
      chat_jid: chatJid,
      send_jid: sendChatId,
      phone_jid: phoneJid,
    };
  }

  async checkNumber(phone) {
    if (this.status !== "ready") {
      throw new BridgeError(
        `WhatsApp is not ready (current status: ${this.status})`,
        503,
      );
    }
    if (typeof phone !== "string" || !PHONE_PATTERN.test(phone)) {
      throw new BridgeError(
        "phone must be an international number such as +8613800000000",
        400,
      );
    }
    const numberId = await this.client.getNumberId(phone.slice(1));
    const chatId = numberId?._serialized || null;
    let phoneJid = null;
    if (chatId && chatId.endsWith("@lid") && typeof this.client.getContactLidAndPhone === "function") {
      try {
        const mappings = await this.client.getContactLidAndPhone([chatId]);
        phoneJid = mappings?.[0]?.pn || mappings?.[0]?.phone || null;
      } catch (error) {
        this.logger.warn?.(
          `[wa-bridge] LID-to-phone lookup failed during check: ${
            error instanceof Error ? error.message : String(error)
          }`,
        );
      }
    }
    return {
      status: chatId ? "registered" : "not_registered",
      phone,
      chat_jid: chatId,
      phone_jid: phoneJid,
    };
  }

  /**
   * Read a bounded recent-message sample for reconciliation after an
   * ambiguous send result. This method never sends or changes chat state.
   */
  async recentMessages(phone, limit = 20) {
    if (this.status !== "ready") {
      throw new BridgeError(
        `WhatsApp is not ready (current status: ${this.status})`,
        503,
      );
    }
    if (typeof phone !== "string" || !PHONE_PATTERN.test(phone)) {
      throw new BridgeError(
        "phone must be an international number such as +8613800000000",
        400,
      );
    }
    const boundedLimit = Number.isInteger(limit)
      ? Math.min(Math.max(limit, 1), 50)
      : 20;
    const numberId = await this.client.getNumberId(phone.slice(1));
    const chatJid = numberId?._serialized;
    if (!chatJid) {
      return { status: "not_registered", phone, chat_jid: null, messages: [] };
    }
    let phoneJid = null;
    if (chatJid.endsWith("@lid") && typeof this.client.getContactLidAndPhone === "function") {
      try {
        const mappings = await this.client.getContactLidAndPhone([chatJid]);
        phoneJid = mappings?.[0]?.pn || mappings?.[0]?.phone || null;
      } catch (error) {
        this.logger.warn?.(
          `[wa-bridge] LID-to-phone lookup failed during history read: ${
            error instanceof Error ? error.message : String(error)
          }`,
        );
      }
    }
    // getChatById() asks WAJS to serialize the chat model. On a newly-created
    // LID chat that serialization can hit a WhatsApp IndexedDB edge case even
    // though the raw chat is available. Prefer the raw injected chat and its
    // currently loaded message models for this diagnostic read.
    let messages;
    const rawChat = this.client.pupPage &&
      typeof this.client.pupPage.evaluate === "function"
      ? await this.client.pupPage.evaluate(
          async (chatId, limit) => {
            const chat = await window.WWebJS.getChat(chatId, {
              getAsModel: false,
            });
            if (!chat) return null;
            const models = chat.msgs?.getModelsArray?.() || [];
            return {
              chat_jid: chat.id?._serialized || chatId,
              messages: models.slice(-limit).map((message) => ({
                message_id:
                  message?.id?._serialized ||
                  message?.id?.serialized ||
                  message?.id?.$1 ||
                  message?.id?.id ||
                  null,
                from_me: Boolean(message?.fromMe || message?.id?.fromMe),
                body: typeof message?.body === "string" ? message.body : "",
                timestamp: message?.timestamp ?? message?.t ?? null,
                ack: message?.ack ?? null,
              })),
            };
          },
          phoneJid || chatJid,
          boundedLimit,
        )
      : null;
    if (rawChat) {
      messages = rawChat.messages;
    } else {
      const chat = await this.client.getChatById(phoneJid || chatJid);
      if (!chat || typeof chat.fetchMessages !== "function") {
        return {
          status: "chat_unavailable",
          phone,
          chat_jid: chatJid,
          phone_jid: phoneJid,
          messages: [],
        };
      }
      messages = (await chat.fetchMessages({ limit: boundedLimit })).map(
        (message) => ({
          message_id: extractMessageId(message),
          from_me: Boolean(message?.fromMe),
          body: typeof message?.body === "string" ? message.body : "",
          timestamp: message?.timestamp ?? null,
          ack: message?.ack ?? null,
        }),
      );
    }
    return {
      status: "ok",
      phone,
      chat_jid: chatJid,
      phone_jid: phoneJid,
      messages: messages || [],
    };
  }

  /**
   * Fetch a bounded amount of locally available history for compensation.
   * Nothing invokes this automatically unless the server is explicitly
   * configured with ``WA_COMPENSATION_SCAN_ON_READY=true``.
   */
  async scanRecentMessages({
    limitPerChat = 50,
    onMessage = this.onInboundMessage,
  } = {}) {
    if (this.status !== "ready") {
      throw new BridgeError(
        `WhatsApp is not ready (current status: ${this.status})`,
        503,
      );
    }
    if (typeof this.client.getChats !== "function") {
      return { scanned_chats: 0, scanned_messages: 0 };
    }
    const chats = await this.client.getChats();
    let scannedChats = 0;
    let scannedMessages = 0;
    for (const chat of chats || []) {
      const chatId = chat?.id?._serialized || "";
      if (
        chat?.isGroup ||
        chatId.endsWith("@g.us") ||
        chatId.endsWith("@broadcast") ||
        chatId.endsWith("@newsletter") ||
        chatId === "status@broadcast"
      ) continue;
      if (typeof chat?.fetchMessages !== "function") continue;
      scannedChats += 1;
      const messages = await chat.fetchMessages({ limit: limitPerChat });
      for (const message of messages || []) {
        scannedMessages += 1;
        if (typeof onMessage === "function") await onMessage(message);
      }
    }
    return { scanned_chats: scannedChats, scanned_messages: scannedMessages };
  }

  async destroy() {
    try {
      await this.client.destroy();
    } catch (error) {
      this.logger.error(
        `[wa-bridge] Failed to close WhatsApp client: ${
          error instanceof Error ? error.message : String(error)
        }`,
      );
    }
  }
}

module.exports = { BridgeError, PHONE_PATTERN, WhatsAppClient };
