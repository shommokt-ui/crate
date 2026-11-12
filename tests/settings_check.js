"use strict";

// Deterministic persistence checks: no server or browser dependency required.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "..", "static", "settings.js"), "utf8");

function harness() {
  const timers = new Map(), requests = [];
  let nextId = 0;
  const context = {
    AbortController,
    setTimeout(callback, delay) { const id = ++nextId; timers.set(id, { callback, delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
    matchMedia: () => ({ addEventListener() {} }),
    document: { addEventListener() {}, getElementById() { return {}; } },
    CrateTheme: { apply() {}, resolve: (theme) => theme, current: () => "light", transition: (change) => change() },
    fetch(url, options) {
      return new Promise((resolve, reject) => requests.push({ url, body: JSON.parse(options.body), resolve, reject }));
    },
  };
  context.window = context;
  vm.runInNewContext(source, context);
  return {
    settings: context.CrateSettings, requests,
    runSave() {
      const next = [...timers].find(([, timer]) => timer.delay !== 15000);
      assert.ok(next, "Expected a pending save or retry");
      timers.delete(next[0]); next[1].callback();
    },
    scheduled: () => timers.size,
  };
}

async function settle() { for (let i = 0; i < 5; i++) await Promise.resolve(); }

(async () => {
  const serial = harness();
  serial.settings.set({ quality: 192 });
  serial.runSave();
  serial.settings.set({ quality: 320, folder: "/Music/New folder" });
  serial.runSave();
  assert.equal(serial.requests.length, 1, "Only one save may be in flight");
  assert.equal(serial.settings.get().quality, 320, "UI preferences update immediately");
  serial.requests[0].resolve({ ok: true }); await settle();
  serial.runSave();
  assert.deepEqual(serial.requests[1].body, { quality: 320, folder: "/Music/New folder" });
  serial.requests[1].resolve({ ok: true }); await settle();
  assert.equal(serial.scheduled(), 0);

  const retry = harness();
  retry.settings.set({ phone: true, quality: 192 }); retry.runSave();
  retry.settings.set({ quality: 256 });
  retry.requests[0].reject(new TypeError("Network offline")); await settle();
  retry.runSave();
  assert.deepEqual(retry.requests[1].body, { phone: true, quality: 256 }, "Failed patches survive with newer values taking precedence");
  retry.requests[1].resolve({ ok: true }); await settle();
  assert.equal(retry.scheduled(), 0);

  const limited = harness();
  limited.settings.set({ numbering: false });
  for (let index = 0; index < 4; index++) {
    limited.runSave();
    limited.requests[index].resolve({ ok: false, status: 503 }); await settle();
  }
  assert.equal(limited.scheduled(), 0, "Automatic retries must stop after three retries");
  limited.settings.set({ tags: false }); limited.runSave();
  assert.deepEqual(limited.requests[4].body, { numbering: false, tags: false }, "A later change retries all unsaved preferences");
  limited.requests[4].resolve({ ok: true }); await settle();
  assert.equal(limited.scheduled(), 0);
  console.log("Settings persistence checks passed: serialization, latest values, network/HTTP retries, bounded backoff.");
})().catch((error) => { console.error(error); process.exitCode = 1; });
