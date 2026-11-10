"use strict";

// History panel: what was downloaded, grouped by day, with one click to load a link again.
(() => {
  const dialog = document.getElementById("history-dialog");
  const body = document.getElementById("history-body");
  const empty = document.getElementById("history-empty");
  const count = document.getElementById("history-count");
  const clearButton = document.getElementById("history-clear");
  const relative = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  const FORMAT_LABELS = { mp3: "MP3", aac: "AAC", opus: "Opus", flac: "FLAC", wav: "WAV", original: "Original" };

  async function call(path, payload) {
    const response = await fetch(path, payload === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (!response.ok) throw new Error("History unavailable");
    return response.json();
  }

  function ago(iso) {
    const seconds = Math.round((new Date(iso).getTime() - Date.now()) / 1000);
    if (!Number.isFinite(seconds)) return "";
    for (const [unit, size] of [["day", 86400], ["hour", 3600], ["minute", 60]]) if (Math.abs(seconds) >= size) return relative.format(Math.round(seconds / size), unit);
    return "just now";
  }

  function dayGroup(iso) {
    const then = new Date(iso), today = new Date();
    const days = Math.round((new Date(today.getFullYear(), today.getMonth(), today.getDate()) - new Date(then.getFullYear(), then.getMonth(), then.getDate())) / 86400000);
    return days <= 0 ? "Today" : days === 1 ? "Yesterday" : days < 7 ? "This week" : "Earlier";
  }

  const el = (tag, className, text) => { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; };
  const icon = (name) => { const wrap = document.createElement("span"); wrap.innerHTML = `<svg class="icon" aria-hidden="true"><use href="#icon-${name}"/></svg>`; return wrap.firstChild; };

  async function showSongs(li, item, panel) {
    const open = li.classList.toggle("open");
    panel.hidden = !open;
    if (!open || panel.dataset.loaded) return;
    panel.replaceChildren(el("p", "history-note", "Loading…"));
    try {
      const detail = await call(`/api/history/${encodeURIComponent(item.id)}`);
      const list = el("ol", "history-songs");
      detail.tracks.forEach((song, index) => {
        const entry = el("li", "history-song");
        const name = el("span", "history-song-name", song.title); if (song.artist) name.append(el("small", "", song.artist));
        const state = { done: "Downloaded", skipped: "Not downloaded", failed: "Failed" }[song.status] || "";
        entry.append(el("span", "history-song-no", String(index + 1)), name, el("span", `song-state ${song.status}`, state));
        list.append(entry);
      });
      panel.replaceChildren(list);
      panel.dataset.loaded = "1";
    } catch { panel.replaceChildren(el("p", "history-note", "Couldn't load the songs.")); }
  }

  function card(item) {
    const song = item.kind === "track";
    const li = el("li", "history-item");
    const row = el("div", "history-row");
    const cover = el("div", "history-cover");
    if (/^https:\/\/[^/]+\.(sndcdn\.com|scdn\.co|spotifycdn\.com|ytimg\.com|ggpht\.com)\//.test(item.artwork)) { const img = el("img"); img.alt = ""; img.src = item.artwork; cover.append(img); }
    else cover.append(icon(song ? "music" : "box"));
    const text = el("div", "history-text");
    const chips = el("div", "history-chips");
    chips.append(el("span", "chip chip-kind", song ? "Song" : "Playlist"), el("span", "chip", FORMAT_LABELS[item.format] || item.format));
    if (!song) chips.append(el("span", "chip", item.failed ? `${item.completed}/${item.total} songs` : `${item.completed} songs`));
    text.append(el("p", "history-title", item.title), el("p", "history-by", [item.creator, ago(item.at)].filter(Boolean).join(" · ")), chips);
    const actions = el("div", "history-actions");
    const panel = el("div", "history-tracks"); panel.hidden = true;
    if (!song && item.track_count > 0) { // older entries didn't save their songs
      const toggle = el("button", "history-songs-toggle"); toggle.type = "button"; toggle.title = "Show songs"; toggle.setAttribute("aria-label", "Show songs"); toggle.append(icon("chevron"));
      toggle.addEventListener("click", () => showSongs(li, item, panel));
      actions.append(toggle);
    }
    const load = el("button", "history-load"); load.type = "button"; load.title = "Load again"; load.setAttribute("aria-label", "Load again"); load.append(icon("arrow"));
    load.addEventListener("click", () => { dialog.close(); document.dispatchEvent(new CustomEvent("crate:load", { detail: item.url })); });
    const remove = el("button", "history-remove"); remove.type = "button"; remove.title = "Remove"; remove.setAttribute("aria-label", "Remove"); remove.append(icon("close"));
    remove.addEventListener("click", async () => { try { render(await call("/api/history/delete", { id: item.id })); } catch { /* Leave the list as is. */ } });
    actions.append(load, remove);
    row.append(cover, text, actions);
    li.append(row, panel);
    return li;
  }

  function render(items) {
    body.replaceChildren();
    const groups = new Map();
    for (const item of items) { const name = dayGroup(item.at); if (!groups.has(name)) groups.set(name, []); groups.get(name).push(item); }
    for (const [name, group] of groups) {
      const section = el("section", "history-group");
      const list = el("ol", "history-list");
      list.append(...group.map(card));
      section.append(el("h3", "history-day", name), list);
      body.append(section);
    }
    count.textContent = items.length ? `${items.length} download${items.length === 1 ? "" : "s"}` : "";
    empty.hidden = items.length > 0;
    body.hidden = items.length === 0;
    clearButton.hidden = items.length === 0;
  }

  document.getElementById("history-button").addEventListener("click", async () => {
    dialog.showModal();
    try { render(await call("/api/history")); } catch { render([]); }
  });
  dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
  clearButton.addEventListener("click", async () => { try { render(await call("/api/history/clear", {})); } catch { /* Leave the list as is. */ } });
})();
