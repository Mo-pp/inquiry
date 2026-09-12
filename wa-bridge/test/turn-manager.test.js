"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { TurnManager } = require("../src/turn-manager");

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const flush = () => new Promise(resolve => setImmediate(resolve));
function fixture() {
  let now = 0, nextTimer = 0;
  const timers = new Map();
  const clock = {
    now: () => now,
    setTimeout(fn, ms) { const id = ++nextTimer; timers.set(id, { fn, at: now + ms }); return id; },
    clearTimeout(id) { timers.delete(id); },
    tick(ms) {
      const end = now + ms;
      while (true) {
        const due = [...timers].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        timers.delete(due[0]); now = due[1].at; due[1].fn();
      }
      now = end;
    },
  };
  const starts = [], processes = [], sent = [], errors = [];
  const manager = new TurnManager({ clock, logger: { error: error => errors.push(error) },
    python: {
      startTurn(phone) { const result = deferred(); starts.push({ phone, ...result }); return result.promise; },
      processTurn(phone, turnId, messages) {
        const result = deferred(); processes.push({ phone, turnId, messages, ...result }); return result.promise;
      },
    },
    sendText: async (chatId, text) => { sent.push({ chatId, text }); },
  });
  let message = 0;
  const receive = (text, phone = "+123456789") => manager.receive({ phone,
    chatId: `${phone.slice(1)}@c.us`, messageId: `msg-${++message}`, text });
  return { manager, clock, starts, processes, sent, errors, receive };
}

test("111/222/333 reset silence and are submitted together exactly once", async () => {
  const f = fixture();
  f.receive("111"); f.starts[0].resolve("t1"); await flush();
  f.clock.tick(19000); f.receive("222");
  f.clock.tick(19000); f.receive("333");
  f.clock.tick(19999); assert.equal(f.processes.length, 0);
  f.clock.tick(1);
  assert.deepEqual(f.processes[0].messages.map(m => m.text), ["111", "222", "333"]);
  assert.equal(f.starts.length, 1);
  f.clock.tick(60000); assert.equal(f.processes.length, 1);
  f.manager.stop();
});

test("out-of-order replies wait per customer; other customers and new turns proceed", async () => {
  const f = fixture();
  f.receive("a1"); f.starts[0].resolve("a1"); await flush(); f.clock.tick(20000);
  f.receive("a2"); f.starts[1].resolve("a2"); await flush(); f.clock.tick(20000);
  f.processes[1].resolve("reply a2"); await flush(); assert.equal(f.sent.length, 0);
  f.receive("b1", "+987654321"); f.starts[2].resolve("b1"); await flush(); f.clock.tick(20000);
  f.processes[2].resolve("reply b1"); await flush();
  assert.deepEqual(f.sent.map(m => m.text), ["reply b1"]);
  f.receive("a3"); f.starts[3].resolve("a3"); await flush(); f.clock.tick(20000);
  assert.equal(f.processes.length, 4);
  f.processes[0].resolve("reply a1"); await flush();
  assert.deepEqual(f.sent.map(m => m.text), ["reply b1", "reply a1", "reply a2"]);
  f.manager.stop();
});

test("slow and reversed turn ID responses do not mix sealed turns", async () => {
  const f = fixture();
  f.receive("111"); f.clock.tick(1000); f.receive("222");
  assert.equal(f.starts.length, 1);
  f.clock.tick(20000); f.receive("333"); f.clock.tick(20000);
  f.starts[1].resolve("later"); await flush();
  f.processes[0].resolve("reply later"); await flush(); assert.equal(f.sent.length, 0);
  f.starts[0].resolve("earlier"); await flush();
  assert.deepEqual(f.processes[1].messages.map(m => m.text), ["111", "222"]);
  assert.deepEqual(f.processes[0].messages.map(m => m.text), ["333"]);
  f.processes[1].resolve("reply earlier"); await flush();
  assert.deepEqual(f.sent.map(m => m.text), ["reply earlier", "reply later"]);
  f.manager.stop();
});

test("a customer's sends are serialized even when replies arrive during a send", async () => {
  const f = fixture(), firstSend = deferred();
  f.manager.sendText = (chatId, text) => {
    f.sent.push({ chatId, text });
    return text === "one" ? firstSend.promise : Promise.resolve();
  };
  f.receive("a1"); f.starts[0].resolve("1"); await flush(); f.clock.tick(20000);
  f.receive("a2"); f.starts[1].resolve("2"); await flush(); f.clock.tick(20000);
  f.processes[0].resolve("one"); await flush();
  f.processes[1].resolve("two"); await flush();
  assert.deepEqual(f.sent.map(m => m.text), ["one"]);
  firstSend.resolve(); await flush();
  assert.deepEqual(f.sent.map(m => m.text), ["one", "two"]);
  assert.equal(f.manager.customers.size, 0);
});

test("a failed earlier turn stays blocked while later turns still process", async () => {
  const f = fixture();
  f.receive("a1"); f.starts[0].resolve("1"); await flush(); f.clock.tick(20000);
  f.processes[0].reject(new Error("unavailable")); await flush();
  f.receive("a2"); f.starts[1].resolve("2"); await flush(); f.clock.tick(20000);
  f.processes[1].resolve("two"); await flush();
  assert.equal(f.sent.length, 0); assert.equal(f.errors.length, 1);
  f.manager.stop();
});

test("stopping clears timers and prevents late Python replies from sending", async () => {
  const f = fixture();
  f.receive("a1"); f.starts[0].resolve("1"); await flush(); f.clock.tick(20000);
  f.receive("a2"); f.manager.stop();
  f.starts[1].resolve("2"); f.processes[0].resolve("one"); await flush(); f.clock.tick(20000);
  assert.equal(f.processes.length, 1); assert.equal(f.sent.length, 0);
});

test("ambiguous or failed send blocks subsequent replies without automatic retry", async () => {
  const f = fixture(); let attempts = 0;
  f.manager.sendText = async () => { attempts++; throw new Error("no message ID"); };
  f.receive("a1"); f.starts[0].resolve("1"); await flush(); f.clock.tick(20000);
  f.receive("a2"); f.starts[1].resolve("2"); await flush(); f.clock.tick(20000);
  f.processes[0].resolve("one"); await flush();
  f.processes[1].resolve("two"); await flush();
  assert.equal(attempts, 1); assert.equal(f.manager.customers.get("+123456789").queue.length, 2);
  f.manager.stop();
});
