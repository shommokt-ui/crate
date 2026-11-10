const fs = require("fs");
let rects = [], queue = [], now = 0, hidden = false;
const ctx = { setTransform() {}, clearRect() { rects = []; }, fillRect(x, y, w, h) { rects.push([x, y, w, h]); }, save() {}, restore() {}, set fillStyle(v) {}, set shadowColor(v) {}, set shadowBlur(v) {} };
const canvas = { width: 0, height: 0, getBoundingClientRect: () => ({ width: 764, height: 88 }), getContext: () => ctx };
global.devicePixelRatio = 2;
global.requestAnimationFrame = (fn) => { queue.push(fn); return queue.length; };
global.matchMedia = () => ({ matches: false });
global.ResizeObserver = class { observe() {} };
global.MutationObserver = class { observe() {} };
const listeners = {};
global.document = { get hidden() { return hidden; }, documentElement: { dataset: { theme: "dark" } },
  getElementById: (id) => (id === "wave-canvas" ? canvas : {}), addEventListener: (t, f) => { listeners[t] = f; } };
global.window = global;
eval(fs.readFileSync(require("path").join(__dirname, "..", "static", "wave.js"), "utf8"));

const heights = () => rects.filter((r) => r[2] === 1).map((r) => Math.round(r[3] * 10) / 10).join(",");
const frames = (ms) => { for (let t = 0; t < ms; t += 16) { now += 16; const q = queue; queue = []; q.forEach((fn) => fn(now)); } };

CrateWave.draw(55, "active"); const first = heights();
frames(400); const later = heights();
console.log("active: loop running =", !!CrateWave.state.loop, "| bars shake (heights change) =", first !== later);
// Columns: 1 device pixel wide, every other pixel, mirrored on one centre line, never overlapping.
const cols = rects.filter((r) => r[2] === 1 && r[3] > 2 && r[3] < 176); // skip the full-height beat-grid hairlines
const centreOf = new Map();
for (const r of cols) { const key = r[0]; const mid = r[1] + r[3] / 2; if (!centreOf.has(key)) centreOf.set(key, []); centreOf.get(key).push([r[1], r[1] + r[3], mid]); }
let overlap = 0, offCentre = 0;
for (const parts of centreOf.values()) {
  parts.sort((p, q) => p[0] - q[0]);
  for (let i = 1; i < parts.length; i++) if (parts[i][0] < parts[i - 1][1] - 0.001) overlap++;
  const top = parts[0][0], bottom = parts[parts.length - 1][1];
  if (Math.abs((top + bottom) / 2 - 88) > 0.01) offCentre++;   // canvas is 88 CSS px * 2 = 176 device px; centre 88
}
const xs = [...centreOf.keys()].sort((p, q) => p - q);
console.log("columns:", xs.length, "| spacing =", [...new Set(xs.map((x, i) => i ? x - xs[i - 1] : 0).filter(Boolean))], "| overlapping segments =", overlap, "| off-centre columns =", offCentre);
hidden = true; frames(200); console.log("window hidden -> loop stopped =", !CrateWave.state.loop);
hidden = false; listeners.visibilitychange(); console.log("window visible again -> loop resumes =", !!CrateWave.state.loop);
CrateWave.draw(100, "done"); frames(200); const done1 = heights(); frames(300); const done2 = heights();
console.log("done: loop stopped =", !CrateWave.state.loop && queue.length === 0, "| static =", done1 === done2);
