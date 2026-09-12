"use strict";

class TurnManager {
  constructor({ python, sendText, silenceMs = 20000, logger = console,
    clock = { now: () => Date.now(), setTimeout, clearTimeout } }) {
    this.python = python;
    this.sendText = sendText;
    this.silenceMs = silenceMs;
    this.logger = logger;
    this.clock = clock;
    this.customers = new Map();
    this.stopped = false;
  }

  receive({ phone, chatId, messageId, text, receivedAt = this.clock.now() }) {
    if (this.stopped) return;
    let customer = this.customers.get(phone);
    if (!customer) {
      customer = { phone, collecting: null, queue: [], sending: false, nextSequence: 1 };
      this.customers.set(phone, customer);
    }
    // 即使计时器回调尚未执行，跨过静默边界的新消息也属于下一轮。
    if (customer.collecting && receivedAt >= customer.collecting.deadline) {
      this.seal(customer, customer.collecting);
    }
    let turn = customer.collecting;
    if (!turn) {
      turn = { sequence: customer.nextSequence++, chatId, messages: [], turnId: null,
        sealed: false, submitted: false, reply: null, failed: false, timer: null };
      customer.collecting = turn;
      customer.queue.push(turn);
      // 先占队列位置，再异步申请 ID；ID 返回顺序不影响轮次顺序。
      this.start(customer, turn);
    }
    turn.messages.push({ message_id: messageId, text });
    turn.deadline = receivedAt + this.silenceMs;
    this.clock.clearTimeout(turn.timer);
    turn.timer = this.clock.setTimeout(() => this.seal(customer, turn),
      Math.max(0, turn.deadline - this.clock.now()));
  }

  async start(customer, turn) {
    try {
      const id = await this.python.startTurn(customer.phone);
      if (this.stopped) return;
      if (customer.queue.some(other => other !== turn && other.turnId === id)) {
        throw new Error("Python reused an active turn_id");
      }
      turn.turnId = id;
      this.submit(customer, turn);
    } catch (error) { this.fail(customer, turn, error); }
  }

  seal(customer, turn) {
    if (this.stopped || turn.sealed) return;
    this.clock.clearTimeout(turn.timer);
    turn.timer = null;
    turn.sealed = true;
    if (customer.collecting === turn) customer.collecting = null;
    this.submit(customer, turn);
  }

  async submit(customer, turn) {
    if (this.stopped || !turn.sealed || !turn.turnId || turn.submitted || turn.failed) return;
    turn.submitted = true;
    try {
      turn.reply = await this.python.processTurn(customer.phone, turn.turnId, turn.messages);
      if (!this.stopped) this.drain(customer);
    } catch (error) { this.fail(customer, turn, error); }
  }

  async drain(customer) {
    if (this.stopped || customer.sending) return;
    customer.sending = true;
    try {
      while (!this.stopped && customer.queue.length) {
        const turn = customer.queue[0];
        if (turn.failed || turn.reply === null) break;
        try {
          await this.sendText(turn.chatId, turn.reply);
        } catch (error) {
          this.fail(customer, turn, error);
          break;
        }
        customer.queue.shift();
      }
    } finally {
      customer.sending = false;
      if (!customer.queue.length && !customer.collecting) this.customers.delete(customer.phone);
    }
  }

  fail(customer, turn, error) {
    turn.failed = true;
    if (!this.stopped) this.logger.error(
      `[wa-bridge] ${customer.phone} turn ${turn.turnId || turn.sequence} blocked: ${error.message}`);
  }

  stop() {
    this.stopped = true;
    for (const customer of this.customers.values()) {
      for (const turn of customer.queue) this.clock.clearTimeout(turn.timer);
    }
    this.customers.clear();
  }
}

module.exports = { TurnManager };
