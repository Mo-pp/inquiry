"use strict";

const express = require("express");
const { BridgeError } = require("./whatsapp-client");

function createApp(bridge) {
  const app = express();
  app.disable("x-powered-by");
  app.get("/health", (_request, response) => response.json(bridge.health()));
  app.post("/messages/send", express.json({ limit: "32kb" }), async (request, response, next) => {
    try {
      if (!request.body || typeof request.body !== "object" || Array.isArray(request.body)) {
        throw new BridgeError("request body must be a JSON object", 400);
      }
      response.json(await bridge.sendToPhone(request.body.phone, request.body.text));
    } catch (error) {
      next(error);
    }
  });
  app.post("/messages/check", express.json({ limit: "32kb" }), async (request, response, next) => {
    try {
      if (!request.body || typeof request.body !== "object" || Array.isArray(request.body)) {
        throw new BridgeError("request body must be a JSON object", 400);
      }
      response.json(await bridge.checkNumber(request.body.phone));
    } catch (error) {
      next(error);
    }
  });
  app.use((error, _request, response, _next) => {
    if (error.type === "entity.parse.failed") {
      return response.status(400).json({ detail: "request body must be valid JSON" });
    }
    if (error.type === "entity.too.large") {
      return response.status(413).json({ detail: "request body exceeds 32kb" });
    }
    if (error instanceof BridgeError) {
      return response.status(error.statusCode).json({ detail: error.message });
    }
    console.error("[wa-bridge] Request failed:", error);
    return response.status(500).json({ detail: "WhatsApp operation failed" });
  });
  return app;
}

module.exports = { createApp };
