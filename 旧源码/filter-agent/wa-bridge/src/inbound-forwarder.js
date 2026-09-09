"use strict";

const crypto = require("node:crypto");

function clean(value) {
  if (value === undefined || value === null) return null;
  const text = String(value).trim();
  return text || null;
}

function normalizePhone(value) {
  let text = clean(value);
  if (!text) return null;
  if (text.toLowerCase().startsWith("p:")) text = text.slice(2).trim();
  if (text.startsWith("00")) text = `+${text.slice(2)}`;
  if (!text.startsWith("+")) text = `+${text}`;
  const digits = `+${text.slice(1).replace(/\D/g, "")}`;
  return /^\+[1-9]\d{5,14}$/.test(digits) ? digits : null;
}

function serializedId(value) {
  if (!value) return null;
  if (typeof value === "string") return clean(value);
  return clean(value._serialized || value.serialized);
}

function messageIdentifier(value) {
  if (!value) return null;
  if (typeof value === "string") return clean(value);
  return clean(value._serialized || value.serialized || value.$1 || value.id);
}

async function safeCall(target, method, ...args) {
  if (!target || typeof target[method] !== "function") return null;
  try {
    return await target[method](...args);
  } catch (_error) {
    return null;
  }
}

function mappedPhoneValue(mapping) {
  if (!mapping || typeof mapping !== "object") return null;
  return normalizePhone(mapping.pn || mapping.phone);
}

async function resolveLidPhone(client, chatJid) {
  const mapped = await safeCall(client, "getContactLidAndPhone", [chatJid]);
  const entries = Array.isArray(mapped) ? mapped : [mapped];
  const matching = entries.find(
    (entry) => clean(entry?.lid) === chatJid,
  );
  return mappedPhoneValue(matching || entries[0]);
}

function timestampIso(value) {
  if (value === undefined || value === null || value === "") {
    return new Date().toISOString();
  }
  if (typeof value === "number") {
    const milliseconds = value > 10_000_000_000 ? value : value * 1000;
    return new Date(milliseconds).toISOString();
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? new Date().toISOString() : date.toISOString();
}

function messageKey({ messageId, chatJid, timestamp, sender, text }) {
  const source = messageId || [chatJid, timestamp, sender, text].join("\x1f");
  return crypto.createHash("sha256").update(`wa:${source}`).digest("hex");
}

/**
 * Convert a whatsapp-web.js Message into a JSON-safe event.  The helper is
 * async because contact/chat metadata are exposed through async WAJS methods.
 */
async function serializeInboundMessage(message, { client = null } = {}) {
  const chat = await safeCall(message, "getChat");
  const contact = await safeCall(message, "getContact");
  const chatJid = clean(
    message?.from ||
      serializedId(message?.chatId) ||
      serializedId(chat?.id) ||
      serializedId(chat),
  );
  if (!chatJid) throw new Error("WAJS message has no chat id");

  // Some WAJS LID message models leave ``_serialized`` empty and expose the
  // native id in ``id.id``.  Use it so inbound idempotency remains durable.
  const messageId = messageIdentifier(message?.id);
  const timestamp = timestampIso(message?.timestamp ?? message?.t);
  const sender =
    clean(
      contact?.pushname ||
        contact?.name ||
        contact?.notifyName ||
        message?.notifyName ||
        contact?.number,
    ) || chatJid;
  const text =
    clean(message?.body) || clean(message?.caption) ||
    `[${clean(message?.type) || "non-text"} message]`;
  let customerPhone = null;
  if (chatJid.endsWith("@lid")) {
    // A LID's numeric part is an internal WhatsApp identity, not a country
    // code. Resolve it first; accepting contact.number here would turn e.g.
    // 218086426841304@lid into the unrelated phone +218086426841304.
    customerPhone = await resolveLidPhone(client, chatJid);

    // Only use an explicit non-LID fallback when WAJS could not resolve the
    // mapping. Never reinterpret the LID's own numeric part as a phone.
    const lidDigits = chatJid.slice(0, -"@lid".length);
    for (const candidate of [message?.author, contact?.number]) {
      const raw = clean(candidate);
      if (!raw || raw.endsWith("@lid") || raw.replace(/\D/g, "") === lidDigits) {
        continue;
      }
      const normalized = normalizePhone(raw);
      if (normalized) {
        customerPhone = normalized;
        break;
      }
    }
  } else {
    customerPhone =
      normalizePhone(contact?.number) ||
      normalizePhone(message?.author) ||
      (/^(\d{6,15})@c\.us$/.exec(chatJid)?.[1]
        ? `+${chatJid.split("@")[0]}`
        : null);
  }
  const isGroup =
    Boolean(chat?.isGroup) ||
    chatJid.endsWith("@g.us") ||
    chatJid.endsWith("@broadcast") ||
    chatJid.endsWith("@newsletter") ||
    chatJid === "status@broadcast";

  return {
    event_type: "message",
    message_id: messageId,
    message_key: messageKey({
      messageId,
      chatJid,
      timestamp,
      sender,
      text,
    }),
    chat_jid: chatJid,
    customer_phone: customerPhone,
    sender,
    text,
    timestamp,
    from_me: Boolean(message?.fromMe),
    is_group: isGroup,
    type: clean(message?.type),
  };
}

class InboundForwarder {
  constructor({
    url,
    timeoutMs = 10_000,
    maxAttempts = 3,
    retryDelayMs = 1000,
    fetchImpl = fetch,
    logger = console,
  }) {
    this.url = url;
    this.timeoutMs = timeoutMs;
    if (!Number.isInteger(maxAttempts) || maxAttempts <= 0) {
      throw new Error("maxAttempts must be a positive integer");
    }
    if (!Number.isInteger(retryDelayMs) || retryDelayMs < 0) {
      throw new Error("retryDelayMs must be a non-negative integer");
    }
    this.maxAttempts = maxAttempts;
    this.retryDelayMs = retryDelayMs;
    this.fetchImpl = fetchImpl;
    this.logger = logger;
  }

  async forward(message, { client = null } = {}) {
    if (message?.fromMe) return { status: "ignored", reason: "from_me" };
    const payload = await serializeInboundMessage(message, { client });
    let lastError;
    for (let attempt = 1; attempt <= this.maxAttempts; attempt += 1) {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), this.timeoutMs);
      try {
        const response = await this.fetchImpl(this.url, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(payload),
          signal: controller.signal,
        });
        const data = await response.json().catch(() => null);
        if (!response.ok) {
          throw new Error(data?.detail || `filter-agent returned HTTP ${response.status}`);
        }
        return data || { status: "accepted", message_key: payload.message_key };
      } catch (error) {
        lastError = error;
        if (attempt >= this.maxAttempts) break;
        if (this.retryDelayMs > 0) {
          await new Promise((resolve) => setTimeout(resolve, this.retryDelayMs));
        }
      } finally {
        clearTimeout(timeout);
      }
    }
    this.logger.error(
      `[wa-bridge] failed to forward inbound message after ${this.maxAttempts} attempts: ${
        lastError instanceof Error ? lastError.message : String(lastError)
      }`,
    );
    throw lastError;
  }
}

module.exports = {
  InboundForwarder,
  normalizePhone,
  serializeInboundMessage,
};
