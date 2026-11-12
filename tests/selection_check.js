"use strict";

// Exercise the real UI handlers without a browser or network dependencies.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

class Node {
  constructor(tag = "div") {
    this.tagName = tag; this.children = []; this.listeners = {}; this.attributes = {};
    this.hidden = false; this.disabled = false; this.value = ""; this.scrollTop = 0;
    this.classList = { add() {}, remove() {}, toggle() {} };
  }
  append(...nodes) { this.children.push(...nodes.flatMap((node) => node.tagName === "fragment" ? node.children : [node])); }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  setAttribute(key, value) { this.attributes[key] = value; }
  removeAttribute(key) { delete this.attributes[key]; }
  addEventListener(type, callback) { this.listeners[type] = callback; }
  querySelector() { return this.span || (this.span = new Node("span")); }
  focus() {}
}

const nodes = new Map();
const node = (id) => { if (!nodes.has(id)) nodes.set(id, new Node()); return nodes.get(id); };
const radio = new Node("input"); radio.value = "mp3"; radio.checked = true;
const events = [];
const tracks = [
  { id: "one", title: "Café Dreams", artist: "Alpha", duration: 120 },
  { id: "two", title: "Nightfall", artist: "Beta", duration: 180 },
  { id: "three", title: "Cafe Dawn", artist: "Gamma", duration: 240, previously: true },
];
const preview = { id: "preview", title: "Set", kind: "playlist", track_count: 3, tracks };
let requests = [];
const context = {
  document: {
    getElementById: node, body: new Node(),
    createElement: (tag) => new Node(tag), createElementNS: (_, tag) => new Node(tag),
    createDocumentFragment: () => new Node("fragment"), createTextNode: (text) => ({ textContent: text }),
    querySelectorAll: () => [radio], querySelector: () => radio,
    addEventListener() {}, dispatchEvent: (event) => events.push(event.type),
  },
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  URL, AbortController, Event, setTimeout: () => 1, clearTimeout() {},
  CrateSettings: { get: () => ({ format: "mp3", folder: "", autostart: false }), onChange() {} },
  CrateWave: { draw() {} }, addEventListener() {},
  fetch: async (url, options) => {
    requests.push({ url, options });
    if (url === "/api/health") return { ok: true, json: async () => ({ capabilities: { mp3_available: true } }) };
    if (url === "/api/preview") return { ok: true, json: async () => preview };
    return { ok: false, status: 503, json: async () => ({ error: "Please retry" }) };
  },
};
context.window = context;
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "..", "static", "app.js"), "utf8"), context);
const flush = async () => { for (let index = 0; index < 20; index++) await Promise.resolve(); };
const click = (id) => node(id).listeners.click({ target: node(id) });
const search = (value) => { node("track-search").value = value; node("track-search").listeners.input(); };

(async () => {
  assert.ok(events.includes("crate:ready"), "successful startup announces readiness");
  node("playlist-url").value = "https://soundcloud.com/test/sets/example";
  node("playlist-form").listeners.submit({ preventDefault() {} });
  await flush();
  assert.equal(node("track-list").children.length, 3);
  assert.match(node("track-list-summary").textContent, /^2 of 3 selected · 5 min$/);
  search("CAFE");
  assert.equal(node("track-list").children.length, 2, "title filtering ignores case and accents");
  assert.equal(node("select-toggle").textContent, "Select shown");
  click("select-toggle");
  assert.match(node("track-list-summary").textContent, /^3 of 3 selected/);
  click("select-toggle");
  assert.match(node("track-list-summary").textContent, /^1 of 3 selected · 3 min$/);
  search("beta night");
  assert.equal(node("track-list").children.length, 1, "search words may match both artist and title");
  assert.equal(node("track-list").children[0].children[0].children[0].checked, true, "hidden selections survive bulk changes");
  search("does not exist");
  assert.equal(node("track-list").children.length, 0);
  assert.equal(node("track-empty").hidden, false);
  assert.equal(node("select-toggle").disabled, true);
  assert.equal(node("download-button").disabled, false, "filtering never removes the hidden selection");
  click("track-search-clear");
  assert.equal(node("track-list").children.length, 3);
  assert.equal(node("track-empty").hidden, true);
  click("download-button");
  await flush();
  const request = requests.find(({ url }) => url === "/api/jobs");
  assert.deepEqual(JSON.parse(request.options.body).skip_ids, ["one", "three"]);
  click("select-toggle"); // select all
  click("select-toggle"); // select none
  assert.equal(node("download-button").disabled, true);
  click("download-button"); // guard remains effective for programmatic clicks
  await flush();
  assert.equal(node("download-button").disabled, true, "start cleanup preserves zero-selection disabled state");
  assert.equal(requests.filter(({ url }) => url === "/api/jobs").length, 1);
  console.log("Selection checks passed: search, visible bulk actions, hidden selections, empty results, request payload, disabled state, startup event.");
})().catch((error) => { console.error(error); process.exitCode = 1; });
