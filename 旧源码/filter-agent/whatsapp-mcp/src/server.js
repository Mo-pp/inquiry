import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import crypto from "node:crypto";
import mysql from "mysql2/promise";
import { BridgeClient, BridgeClientError, loadBridgeConfig } from "./bridge-client.js";
import { loadDbConfig } from "./db.js";

const PHONE_PATTERN = /^\+[1-9]\d{5,14}$/;
const AGENT_SENDER = "ai-agent";
const MAX_MESSAGES_PER_REPLY = 4;
const MAX_MESSAGE_LENGTH = 1000;

const config = loadBridgeConfig();
const bridge = new BridgeClient(config);
const dbConfig = loadDbConfig();
const repliedPhones = new Set();

const server = new McpServer({
  name: "lintratek-whatsapp",
  version: "0.2.0",
});

function result(data) {
  return {
    content: [{ type: "text", text: JSON.stringify(data) }],
    structuredContent: data,
  };
}

function messageKey(phone, messageId, sentAtMs, index) {
  const source = messageId
    ? `agent\u001f${messageId}`
    : `agent\u001f${phone}\u001f${sentAtMs}\u001f${index}`;
  return crypto.createHash("sha256").update(source).digest("hex");
}

server.registerTool(
  "whatsapp_health",
  {
    description: "Check whether the local WhatsApp bridge and the chat database are ready. Call this before replying.",
    inputSchema: {},
  },
  async () => {
    const bridgeHealth = await bridge.health();
    const connection = await mysql.createConnection(dbConfig);
    try {
      await connection.execute("SELECT 1");
    } finally {
      await connection.end();
    }
    return result({ bridge: bridgeHealth, database: "ready" });
  },
);

server.registerTool(
  "whatsapp_send_reply",
  {
    description:
      "Send the reply to one customer as 1-4 short consecutive WhatsApp messages. " +
      "Each array item becomes one separate WhatsApp bubble, like a human support agent typing several short messages in a row. " +
      "Split long content into short readable messages instead of sending one long wall of text. " +
      "Call this exactly once per customer reply; only call again if the previous result reported failed messages, " +
      "and then resend only the failed ones. An 'submitted_unknown' item means the operation may have been accepted; " +
      "do not blindly resend it until it is reconciled. " +
      "Every successfully sent message is automatically recorded in the chat database.",
    inputSchema: {
      phone: z.string().regex(PHONE_PATTERN, "Use international format, e.g. +8613800000000"),
      messages: z
        .array(z.string().trim().min(1).max(MAX_MESSAGE_LENGTH))
        .min(1)
        .max(MAX_MESSAGES_PER_REPLY),
    },
  },
  async ({ phone, messages }) => {
    if (repliedPhones.has(phone)) {
      return result({
        status: "already_replied",
        phone,
        error: "This customer already received a complete reply in this run. Do not send again.",
      });
    }

    const sent = [];
    const submittedUnknown = [];
    const failed = [];
    for (const [index, text] of messages.entries()) {
      try {
        const sendResult = await bridge.sendText(phone, text);
        const item = {
          index,
          text,
          message_id: sendResult.message_id ?? null,
          delivery_status: sendResult.delivery_status || "unknown",
        };
        if (sendResult.status === "submitted_unknown" || !item.message_id) {
          // The bridge may have accepted the operation but failed to return a
          // native id.  Treat this as an ambiguous submission, never as a
          // normal failure eligible for blind resend.
          submittedUnknown.push(item);
        } else {
          sent.push(item);
        }
      } catch (error) {
        failed.push({
          index,
          text,
          error: error instanceof BridgeClientError ? error.message : String(error),
        });
      }
    }

    let recorded = 0;
    let recordError = null;
    const recordedItems = [...sent, ...submittedUnknown];
    if (recordedItems.length) {
      const connection = await mysql.createConnection(dbConfig);
      try {
        await connection.beginTransaction();
        await connection.execute(
          "INSERT INTO customers (customer_phone, customer_name, first_message_at, last_message_at) VALUES (?, ?, NOW(), NOW()) ON DUPLICATE KEY UPDATE last_message_at = NOW()",
          [phone, phone],
        );
        for (const item of recordedItems) {
          const sentAt = new Date();
          const key = messageKey(phone, item.message_id, sentAt.getTime(), item.index);
          const [insertResult] = await connection.execute(
            "INSERT IGNORE INTO customer_messages (customer_phone, message_key, sender, direction, message_at, message_text) VALUES (?, ?, ?, 'out', ?, ?)",
            [phone, key, AGENT_SENDER, sentAt, item.text],
          );
          recorded += insertResult.affectedRows;
        }
        await connection.commit();
      } catch (error) {
        await connection.rollback();
        recordError = error instanceof Error ? error.message : String(error);
      } finally {
        await connection.end();
      }
    }

    if (failed.length === 0) repliedPhones.add(phone);

    return result({
      status: recordError
        ? "sent_with_record_error"
        : failed.length
          ? "partial"
          : submittedUnknown.length
            ? "submitted_unknown"
            : "ok",
      phone,
      sent: sent.map(({ index, message_id, delivery_status }) => ({
        index,
        message_id,
        delivery_status,
      })),
      recorded,
      ...(submittedUnknown.length
        ? {
            submitted_unknown: submittedUnknown.map(({ index, message_id, delivery_status }) => ({
              index,
              message_id,
              delivery_status,
            })),
          }
        : {}),
      ...(failed.length ? { failed } : {}),
      ...(recordError
        ? {
            record_error: recordError,
            hint: "The bridge operation completed or may have completed, but database recording failed. Do not resend until reconciled.",
          }
        : {}),
    });
  },
);

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch((error) => {
  const detail = error instanceof BridgeClientError ? error.message : String(error);
  console.error(`[lintratek-whatsapp-mcp] ${detail}`);
  process.exitCode = 1;
});
