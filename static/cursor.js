"use strict";

// Little musical notes drift off the cursor as it travels. Sparse on purpose: one note per
// stretch of movement, a handful on screen at most, and no frames at all while idle.
(() => {
  const settings = window.CrateSettings;
  const canvas = document.getElementById("cursor-canvas");
  const context = canvas.getContext("2d");
  const root = document.documentElement;
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  const GLYPHS = ["♪", "♫", "♩", "♬"];
  const SPACING = 90;     // px of travel between notes
  const LIFE_MS = 1100;
  const MAX_NOTES = 6;

  let width = 0, height = 0, frame = 0, lastFrame = 0, notes = [], travelled = 0, previous = null, glyph = 0;
  let colorA = [255, 160, 77], colorB = [242, 84, 27];

  const rgb = (value, fallback) => {
    const hex = /^#([0-9a-f]{6})$/i.exec(value.trim());
    return hex ? [0, 2, 4].map((i) => parseInt(hex[1].slice(i, i + 2), 16)) : fallback;
  };
  function readColors() {
    const style = getComputedStyle(root);
    colorA = rgb(style.getPropertyValue("--a1"), colorA);
    colorB = rgb(style.getPropertyValue("--a2"), colorB);
  }
  const enabled = () => settings.get().visuals && !reduced.matches && !document.hidden;
  const clear = () => context.clearRect(0, 0, width, height);

  function resize() {
    width = innerWidth; height = innerHeight;
    canvas.width = width; canvas.height = height;
    readColors();
  }

  function tick(now) {
    const elapsed = Math.min(50, now - lastFrame || 16);
    lastFrame = now;
    clear();
    context.textAlign = "center"; context.textBaseline = "middle";
    for (const note of notes) {
      note.age += elapsed;
      const life = note.age / LIFE_MS;
      if (life >= 1) continue;
      const fade = life < 0.15 ? life / 0.15 : 1 - (life - 0.15) / 0.85; // quick fade-in, slow fade-out
      const x = note.x + Math.sin(note.age / 260 + note.phase) * 6;
      const y = note.y - life * 34;                                       // drifts upward
      const c = colorA.map((v, k) => Math.round(v + (colorB[k] - v) * life));
      context.font = `${note.size}px ui-sans-serif, -apple-system, "Apple Symbols", sans-serif`;
      context.fillStyle = `rgba(${c.join(",")},${(fade * 0.8).toFixed(3)})`;
      context.fillText(note.glyph, x, y);
    }
    notes = notes.filter((note) => note.age < LIFE_MS);
    if (notes.length) frame = requestAnimationFrame(tick);
    else { frame = 0; clear(); }
  }

  addEventListener("pointermove", (event) => {
    if (event.pointerType === "touch" || !enabled()) return;
    if (previous) travelled += Math.hypot(event.clientX - previous.x, event.clientY - previous.y);
    previous = { x: event.clientX, y: event.clientY };
    if (travelled < SPACING) return;
    travelled = 0;
    notes.push({ x: event.clientX, y: event.clientY, age: 0, phase: Math.random() * 6.28, size: 19 + Math.random() * 6, glyph: GLYPHS[glyph++ % GLYPHS.length] });
    if (notes.length > MAX_NOTES) notes.shift();
    if (!frame) { lastFrame = performance.now(); frame = requestAnimationFrame(tick); }
  }, { passive: true });

  document.addEventListener("pointerleave", () => { previous = null; }, { passive: true });
  addEventListener("resize", resize);
  settings.onChange((state, before) => { if (before.visuals && !state.visuals) { notes = []; clear(); } });
  new MutationObserver(readColors).observe(root, { attributes: true, attributeFilter: ["data-theme"] });
  resize();
})();
