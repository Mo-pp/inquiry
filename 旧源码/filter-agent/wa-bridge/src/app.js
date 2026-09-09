"use strict";

const express = require("express");
const { BridgeError } = require("./whatsapp-client");

function createApp(bridge) {
  const app = express();
  app.disable("x-powered-by");
  app.use(express.json({ limit: "32kb" }));

  app.get("/health", (_request, response) => {
    response.json(bridge.health());
  });

  app.post("/messages/send", async (request, response, next) => {
    try {
      const body = request.body;
      if (!body || typeof body !== "object" || Array.isArray(body)) {
        throw new BridgeError("request body must be a JSON object", 400);
      }
      const result = await bridge.sendText(body.phone, body.text);
      response.json(result);
    } catch (error) {
      next(error);
    }
  });

  app.post("/messages/check", async (request, response, next) => {
    try {
      const body = request.body;
      if (!body || typeof body !== "object" || Array.isArray(body)) {
        throw new BridgeError("request body must be a JSON object", 400);
      }
      const result = await bridge.checkNumber(body.phone);
      response.json(result);
    } catch (error) {
      next(error);
    }
  });

  app.post("/messages/recent", async (request, response, next) => {
    try {
      const body = request.body;
      if (!body || typeof body !== "object" || Array.isArray(body)) {
        throw new BridgeError("request body must be a JSON object", 400);
      }
      const result = await bridge.recentMessages(body.phone, body.limit);
      response.json(result);
    } catch (error) {
      next(error);
    }
  });

  app.use((error, _request, response, _next) => {
    if (error instanceof SyntaxError && "body" in error) {
      response.status(400).json({ detail: "request body must be valid JSON" });
      return;
    }

    const statusCode =
      error instanceof BridgeError ? error.statusCode : 500;
    if (!(error instanceof BridgeError)) {
      console.error(
        `[wa-bridge] request failed: ${
          error instanceof Error ? error.stack || error.message : String(error)
        }`,
      );
    }
    const detail =
      error instanceof BridgeError ? error.message : "WhatsApp send failed";
    response.status(statusCode).json({ detail });
  });

  return app;
}

module.exports = { createApp };
