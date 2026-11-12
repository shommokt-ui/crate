"use strict";

// Owns the settings state, persists it through the local API, and drives the dialog.
(() => {
  const DEFAULTS = {
    theme: "system", visuals: true,
    format: "mp3", quality: 320, numbering: true, tags: true, cover: true, history: true, skip_downloaded: true, autostart: false, clear: true, phone: false, folder: "",
  };
  let state = { ...DEFAULTS, ...(window.__crateSettings || {}) };
  const listeners = new Set();
  const savedListeners = new Set();
  let saveTimer = null;
  let pending = {};
  let saving = false;
  let failures = 0;
  let saveNotice = null;
  let saveMessage = "";

  function showSaveError(message = "") {
    saveMessage = message;
    if (!saveNotice) return;
    saveNotice.hidden = !message;
    saveNotice.firstChild.textContent = message;
  }

  function save(delay = 350) {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(flush, delay);
  }

  async function flush() {
    if (saving || !Object.keys(pending).length) return;
    saving = true;
    const changes = pending;
    pending = {};
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    let succeeded = false;
    try {
      const response = await fetch("/api/settings", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(changes), signal: controller.signal,
      });
      if (!response.ok) throw new Error("Settings were not saved.");
      succeeded = true;
      failures = 0;
      showSaveError();
    } catch {
      // Newer choices always win when an older request has to be retried.
      pending = { ...changes, ...pending };
      failures += 1;
      showSaveError("Couldn’t save settings. Your changes are waiting to be saved. ");
    } finally {
      clearTimeout(timeout);
      saving = false;
    }
    if (succeeded) savedListeners.forEach((listener) => listener(changes));
    if (Object.keys(pending).length && (succeeded || failures <= 3)) save(succeeded ? 0 : 1000 * 2 ** (failures - 1));
  }

  function set(patch, { silentTheme = false } = {}) {
    const before = state;
    state = { ...state, ...patch };
    Object.assign(pending, patch);
    failures = 0;
    if (!silentTheme && "theme" in patch) {
      const change = () => window.CrateTheme.apply(state.theme);
      if ("theme" in patch && patch.theme !== before.theme && window.CrateTheme.resolve(patch.theme) !== window.CrateTheme.current()) window.CrateTheme.transition(change, document.getElementById("theme-toggle"));
      else change();
    }
    listeners.forEach((listener) => listener(state, before));
    save();
  }

  const FORMAT_NOTES = {
    mp3: "Plays on any device.",
    aac: "Better quality than MP3 at the same size. Great on Apple devices.",
    opus: "Very efficient with great quality. Needs a modern player.",
    flac: "Lossless, but large. Can’t improve on the source quality.",
    wav: "Uncompressed and very large. No cover art.",
    original: "Untouched source file, usually .m4a. Best quality; some older players can’t open it.",
  };
  window.FORMAT_NOTES = FORMAT_NOTES;
  window.CrateSettings = { get: () => state, set, defaults: DEFAULTS, onChange: (listener) => listeners.add(listener) };

  // Follow the system theme while set to Auto.
  matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", () => {
    if (state.theme === "system") window.CrateTheme.apply(state.theme);
  });

  document.addEventListener("DOMContentLoaded", () => {
    const dialog = document.getElementById("settings-dialog");
    const form = document.getElementById("settings-form");
    const field = (name) => (name === "format" ? "dl-format" : name);
    const inputs = (key) => form.querySelectorAll(`[name="${field(key)}"]`);
    saveNotice = document.createElement("p");
    saveNotice.className = "setting-note";
    saveNotice.setAttribute("role", "status");
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "text-button";
    retry.textContent = "Retry saving";
    retry.addEventListener("click", () => { failures = 0; save(0); });
    saveNotice.append(document.createTextNode(""), retry);
    form.append(saveNotice);
    showSaveError(saveMessage);

    function render() {
      for (const key of Object.keys(DEFAULTS)) {
        inputs(key).forEach((input) => {
          if (input.type === "radio") input.checked = String(state[key]) === input.value;
          else if (input.type === "checkbox") input.checked = Boolean(state[key]);
          else input.value = String(state[key]);
        });
      }
      document.getElementById("settings-format-note").textContent = FORMAT_NOTES[state.format];
      form.elements.quality.disabled = !["mp3", "aac", "opus"].includes(state.format);
      form.elements.cover.disabled = state.format === "wav";
      form.elements.skip_downloaded.disabled = !state.history; // it works from the history, so it needs one
    }

    // Phone pairing: shown only where the Mac-only /api/phone answers.
    const phoneGroup = document.getElementById("phone-group");
    async function renderPhone() {
      try {
        const response = await fetch("/api/phone", { cache: "no-store" });
        if (!response.ok) throw new Error("not here");
        const info = await response.json();
        phoneGroup.hidden = false;
        document.getElementById("phone-panel").hidden = !info.enabled;
        const qr = document.getElementById("phone-qr");
        const note = document.getElementById("phone-note");
        qr.replaceChildren();
        if (info.svg) qr.append(document.importNode(new DOMParser().parseFromString(info.svg, "image/svg+xml").documentElement, true));
        note.textContent = info.enabled && !info.url ? (info.reason || "Couldn’t find your wifi address.") : "Your phone and Mac need the same wifi. Keep this code private.";
      } catch { phoneGroup.hidden = true; }
    }
    document.getElementById("phone-reset").addEventListener("click", async () => {
      const button = document.getElementById("phone-reset");
      button.disabled = true;
      try {
        const response = await fetch("/api/phone/reset", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
        if (!response.ok) throw new Error("Reset failed");
        await renderPhone();
      } catch {
        document.getElementById("phone-note").textContent = "Couldn’t make a new code. Try again.";
      } finally { button.disabled = false; }
    });
    savedListeners.add((changes) => { if ("phone" in changes) renderPhone(); });

    form.addEventListener("input", (event) => {
      const input = event.target;
      const key = input.name === "dl-format" ? "format" : input.name;
      if (!(key in DEFAULTS)) return;
      let value;
      if (input.type === "checkbox") value = input.checked;
      else if (key === "quality") value = Number(input.value);
      else value = input.value;
      set({ [key]: value });
      render();
    });

    document.getElementById("settings-button").addEventListener("click", () => { render(); renderPhone(); dialog.showModal(); });
    dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
    document.getElementById("settings-reset").addEventListener("click", () => { set({ ...DEFAULTS }); render(); });
  });
})();
