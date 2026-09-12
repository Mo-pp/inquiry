"use strict";

const express = require("express");

function createApp(bridge) {
  const app = express();
  app.disable("x-powered-by");
  app.get("/health", (_request, response) => response.json(bridge.health()));
  return app;
}

module.exports = { createApp };
