"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const http = require("node:http");
const { once } = require("node:events");
const { PythonClient } = require("../src/python-client");

test("HTTP contract permits a later request to complete first and validates correlation", async t => {
  let held;
  const requests = [];
  const server = http.createServer(async (req, res) => {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    const body = JSON.parse(raw); requests.push({ path: req.url, body });
    if (req.url === "/turns/start") {
      res.end(JSON.stringify({ phone: body.phone, turn_id: "t1" }));
    } else if (body.turn_id === "slow") {
      held = () => res.end(JSON.stringify({ phone: body.phone, turn_id: "slow", reply_text: "slow reply" }));
    } else {
      res.end(JSON.stringify({ phone: body.turn_id === "bad" ? "wrong" : body.phone,
        turn_id: body.turn_id, reply_text: "fast reply" }));
    }
  });
  server.listen(0, "127.0.0.1"); await once(server, "listening");
  const client = new PythonClient(`http://127.0.0.1:${server.address().port}`);
  t.after(() => { client.close(); server.closeAllConnections(); server.close(); });
  assert.equal(await client.startTurn("+123456789"), "t1");
  const slow = client.processTurn("+123456789", "slow", [{ message_id: "m1", text: "111" }]);
  assert.equal(await client.processTurn("+123456789", "fast", []), "fast reply");
  while (!held) await new Promise(resolve => setImmediate(resolve));
  held(); assert.equal(await slow, "slow reply");
  await assert.rejects(client.processTurn("+123456789", "bad", []), /matching/);
  assert.deepEqual(requests[0], { path: "/turns/start", body: { phone: "+123456789" } });
  assert.deepEqual(requests.find(r => r.body.turn_id === "slow").body.messages,
    [{ message_id: "m1", text: "111" }]);
});
