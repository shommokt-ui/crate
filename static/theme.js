"use strict";

// Loaded in <head>: applies the saved theme before first paint.
(() => {
  const root = document.documentElement;
  const dark = matchMedia("(prefers-color-scheme: dark)");
  let saved = {};
  try { saved = JSON.parse(document.querySelector('meta[name="crate-settings"]')?.content || "{}"); } catch { /* Defaults apply. */ }
  window.__crateSettings = saved;

  const resolve = (theme) => (theme === "light" || theme === "dark" ? theme : dark.matches ? "dark" : "light");
  const apply = (theme) => { root.dataset.theme = resolve(theme); };
  apply(saved.theme);

  // Circular reveal where supported; cross-fade through CSS transitions elsewhere.
  function transition(change, origin) {
    const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (origin) {
      const box = origin.getBoundingClientRect();
      root.style.setProperty("--reveal-x", `${box.left + box.width / 2}px`);
      root.style.setProperty("--reveal-y", `${box.top + box.height / 2}px`);
    }
    if (document.startViewTransition && !reduced) { document.startViewTransition(change); return; }
    root.classList.add("theme-fading");
    change();
    setTimeout(() => root.classList.remove("theme-fading"), 700);
  }

  window.CrateTheme = { resolve, apply, transition, current: () => root.dataset.theme };

  document.addEventListener("DOMContentLoaded", () => {
    const button = document.getElementById("theme-toggle");
    button?.addEventListener("click", () => {
      const next = root.dataset.theme === "dark" ? "light" : "dark";
      transition(() => { root.dataset.theme = next; }, button);
      window.CrateSettings?.set({ theme: next }, { silentTheme: true });
    });
  });
})();
