"use strict";

// DJ-style waveform: one hairline column per two device pixels, all mirrored on a single centre line.
// Dark pink (bass), dark blue (vocals/mids) and dark green (highs) are woven column by column.
// Columns never overlap: the lighter core of a loud column is its own segment, not an overlay.
(() => {
  const wave = { canvas: document.getElementById("wave-canvas"), progress: 0, mode: "active", cache: null, time: 0, loop: 0, lastFrame: 0 };
  const PALETTE = {
    dark: { bass: [222, 30, 112], mid: [40, 80, 240], high: [20, 168, 88] },
    light: { bass: [196, 20, 100], mid: [30, 64, 216], high: [8, 142, 74] },
  };
  const calm = matchMedia("(prefers-reduced-motion: reduce)");
  const smooth = (x) => x * x * (3 - 2 * x);

  // A made-up track: sections of different loudness and character (not the real audio).
  const SECTIONS = [
    [0.00, 0.07, 0.34, [0.12, 0.80, 0.08]], [0.07, 0.24, 0.88, [0.86, 0.10, 0.04]], [0.24, 0.36, 0.38, [0.10, 0.80, 0.10]],
    [0.36, 0.64, 0.96, [0.84, 0.12, 0.04]], [0.64, 0.76, 0.46, [0.08, 0.22, 0.70]], [0.76, 0.90, 0.92, [0.86, 0.10, 0.04]],
    [0.90, 1.00, 0.52, [0.12, 0.78, 0.10]],
  ];
  function sectionAt(t) {
    const index = Math.max(0, SECTIONS.findIndex(([from, to]) => t >= from && t < to));
    const [from, to, level, mix] = SECTIONS[index];
    const next = SECTIONS[Math.min(index + 1, SECTIONS.length - 1)];
    const blend = smooth(Math.min(1, Math.max(0, (t - (to - 0.03)) / 0.03))); // soften each boundary
    return { level: level + (next[2] - level) * blend, mix: mix.map((value, k) => value + (next[3][k] - value) * blend) };
  }

  function waveShape(count) {
    if (wave.cache?.count === count) return wave.cache.bars;
    let seed = 11;
    const random = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
    const period = Math.max(8, Math.round(count / 48)); // one beat
    const bars = [], weights = [];
    for (let i = 0; i < count; i++) {
      const section = sectionAt(i / count);
      const phase = (i % period) / period;
      const kick = 0.5 + 0.5 * Math.exp(-phase * 4.5);                    // transient at the start of each beat
      const texture = 0.4 + 0.6 * random() ** 0.8;                         // fine column-to-column jaggedness
      const teeth = i % 2 ? 1 : 0.8;                                       // comb pattern seen in real waveforms
      const swell = i % 233 < 36 && section.level < 0.8 ? 1 : 0;          // occasional hat/cymbal wash
      const mix = swell ? [section.mix[0] * 0.35, section.mix[1] * 0.5, 0.85] : section.mix;
      weights.push(mix);
      bars.push({ amp: Math.min(1, Math.max(0.04, section.level * kick * texture * teeth)), phase: random() * 6.28, speed: 7 + 16 * mix[2] + 5 * random() });
    }
    // Colour is chosen column by column from the section's mix, so pink, blue and green are woven together
    // rather than sitting in long blocks. A column avoids repeating its neighbour's colour when it can.
    let previous = -1;
    for (let i = 0; i < count; i++) {
      const mix = weights[i];
      const pick = () => { let r = random() * (mix[0] + mix[1] + mix[2]); return r < mix[0] ? 0 : r < mix[0] + mix[1] ? 1 : 2; };
      let band = pick();
      if (band === previous) band = pick(); // one re-roll: neighbours differ more often than not
      previous = band;
      const own = 0.82 + 0.1 * random(); // mostly the chosen colour, with a trace of the others
      const rest = (1 - own) / 2;
      [bars[i].bass, bars[i].mid, bars[i].high] = [0, 1, 2].map((k) => (k === band ? own : rest));
    }
    wave.cache = { count, bars, themes: {} };
    return bars;
  }

  function barColors(bar, theme) {
    const store = (wave.cache.themes[theme] ||= new Map());
    let colors = store.get(bar);
    if (!colors) {
      const p = PALETTE[theme];
      const rgb = [0, 1, 2].map((c) => Math.round(p.bass[c] * bar.bass + p.mid[c] * bar.mid + p.high[c] * bar.high));
      const tint = rgb.map((c) => Math.round(c + (255 - c) * (theme === "dark" ? 0.42 : 0.3)));
      colors = { lit: `rgb(${rgb})`, core: `rgb(${tint})`, dim: `rgba(${rgb},${theme === "dark" ? 0.5 : 0.4})` };
      store.set(bar, colors);
    }
    return colors;
  }

  function paintWave() {
    const canvas = wave.canvas;
    const box = canvas.getBoundingClientRect();
    if (!box.width) return false; // hidden panel; repainted when it is shown
    const ratio = Math.min(devicePixelRatio || 1, 2);
    const W = Math.round(box.width * ratio), H = Math.round(box.height * ratio);
    if (canvas.width !== W || canvas.height !== H) { canvas.width = W; canvas.height = H; }
    const context = canvas.getContext("2d");
    context.setTransform(1, 0, 0, 1, 0, 0); // draw in device pixels so every column is a crisp hairline
    context.clearRect(0, 0, W, H);
    const theme = document.documentElement.dataset.theme === "light" ? "light" : "dark";
    const dark = theme === "dark";
    const STEP = 2;                                   // 1 device pixel of bar + 1 of gap
    const count = Math.floor(W / STEP);
    const bars = waveShape(count);
    const offset = Math.floor((W - count * STEP) / 2);
    const middle = H / 2;
    const finished = wave.mode === "done", dead = wave.mode === "failed";
    const live = wave.mode === "active" && !calm.matches;
    const played = Math.round((wave.progress / 100) * count);

    // Beat grid: evenly spaced hairlines behind the columns.
    context.fillStyle = dark ? "rgba(255,255,255,0.1)" : "rgba(20,24,50,0.12)";
    for (let k = 1; k < 16; k++) context.fillRect(Math.round((W * k) / 16), 0, Math.max(1, Math.round(ratio / 2)), H);

    for (let i = 0; i < count; i++) {
      const bar = bars[i];
      const lit = !dead && i < played;
      // Columns just behind the playhead shake hardest, calming further back; unplayed ones barely move.
      const energy = !live ? 0 : lit ? 0.3 + 0.7 * Math.max(0, 1 - (played - i) / 90) : 0.06;
      const shake = 1 + energy * 0.3 * Math.sin(wave.time * bar.speed + bar.phase);
      const half = Math.max(1, Math.min(H / 2, (bar.amp * shake * H * 0.94) / 2));
      const x = offset + i * STEP;
      const colors = barColors(bar, theme);
      if (!lit) {
        context.fillStyle = dead ? (dark ? "rgba(255,255,255,0.16)" : "rgba(20,24,50,0.18)") : colors.dim;
        context.fillRect(x, middle - half, 1, half * 2);
        continue;
      }
      if (bar.amp > 0.55) { // loud column: lighter core between two coloured arms, no overlap
        const core = half * 0.42;
        context.fillStyle = colors.lit;
        context.fillRect(x, middle - half, 1, half - core);
        context.fillRect(x, middle + core, 1, half - core);
        context.fillStyle = colors.core;
        context.fillRect(x, middle - core, 1, core * 2);
      } else {
        context.fillStyle = colors.lit;
        context.fillRect(x, middle - half, 1, half * 2);
      }
    }
    if (!finished && !dead && played > 0 && played < count) { // playhead
      context.save();
      context.shadowColor = "rgba(255,140,40,0.95)"; context.shadowBlur = 8 * ratio;
      context.fillStyle = "#ffa040"; context.fillRect(offset + played * STEP - 1, 0, Math.max(2, Math.round(ratio)), H);
      context.restore();
    }
    return true;
  }

  // The shake loop runs only while a download is active and visible, at about 30 frames a second.
  function shakeLoop(now) {
    wave.loop = 0;
    if (wave.mode !== "active" || calm.matches || document.hidden) return;
    if (now - wave.lastFrame >= 33) {
      wave.lastFrame = now; wave.time = now / 1000;
      if (!paintWave()) return; // panel hidden: stop until it is shown again
    }
    wave.loop = requestAnimationFrame(shakeLoop);
  }
  function draw(progress, mode) {
    wave.progress = progress; wave.mode = mode;
    paintWave();
    if (mode === "active" && !wave.loop && !calm.matches) wave.loop = requestAnimationFrame(shakeLoop);
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden && wave.mode === "active" && !wave.loop && !calm.matches) wave.loop = requestAnimationFrame(shakeLoop); });

  new ResizeObserver(paintWave).observe(document.getElementById("progress-bar"));
  new MutationObserver(paintWave).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  window.CrateWave = { draw, state: wave };
})();
