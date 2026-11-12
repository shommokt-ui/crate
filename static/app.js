"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const terminalStates = new Set(["completed", "failed", "cancelled"]);
  const storageKey = "crate.activeJob";
  const state = { preview: null, job: null, timer: null, pollFailures: 0, busy: false, starting: false, cancelling: false, health: null, requestVersion: 0, retry: null, trackSignature: "", needsMetadata: false, deselected: new Set() };

  const linkPatterns = {
    "soundcloud.com": /^\/[^/]+\/(sets\/)?[^/]+\/?$/,
    "open.spotify.com": /^(\/intl-[a-z-]+)?\/(embed\/)?(playlist|track)\/[A-Za-z0-9]{22}\/?$/,
    "youtube.com": /^\/(watch|playlist|(shorts|live|embed)\/[\w-]{11})\/?$/,
    "music.youtube.com": /^\/(watch|playlist)\/?$/,
    "youtu.be": /^\/[\w-]{11}\/?$/,
  };
  function isSupportedLink(parsed) {
    const host = parsed.hostname.toLowerCase().replace(/^(www|m)\./, "");
    return ["https:", "http:"].includes(parsed.protocol) && Boolean(linkPatterns[host]?.test(parsed.pathname));
  }
  const isSong = () => state.preview?.kind === "track" || state.job?.kind === "track";

  class ApiError extends Error {
    constructor(message, status) { super(message); this.status = status; }
  }

  async function api(path, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), path === "/api/preview" ? 120000 : 30000);
    try {
      const response = await fetch(path, { ...options, headers: { "Content-Type": "application/json", ...options.headers }, signal: controller.signal });
      let result;
      try { result = await response.json(); } catch { throw new ApiError("No response. Try again.", response.status); }
      if (!response.ok) throw new ApiError(result.error || "Something went wrong.", response.status);
      return result;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("Taking too long. Try again.");
      if (error instanceof TypeError) throw new Error("Couldn’t reach Crate.");
      throw error;
    } finally { clearTimeout(timeout); }
  }

  function notify(message = "", type = "", retry = null) {
    $("notice").hidden = !message;
    $("notice").className = `notice ${type}`;
    $("notice-text").textContent = message;
    $("retry-button").hidden = !retry;
    state.retry = retry;
  }

  function rememberJob(id) {
    try { if (id) localStorage.setItem(storageKey, id); else localStorage.removeItem(storageKey); } catch { /* Private browsing can disable storage. */ }
  }

  function savedJob() { try { return localStorage.getItem(storageKey); } catch { return null; } }
  function isActive() { return Boolean(state.job && !terminalStates.has(state.job.status)); }
  function setFormBusy(busy) {
    state.busy = busy;
    $("preview-button").disabled = busy || isActive() || state.starting;
    $("playlist-url").disabled = busy || isActive() || state.starting;
    $("preview-label").textContent = busy ? "Loading…" : "Load";
    $("preview-spinner").hidden = !busy;
    $("preview-arrow").hidden = busy;
    $("playlist-form").setAttribute("aria-busy", String(busy));
  }

  function duration(value) {
    const seconds = Math.max(0, Math.floor(Number(value) || 0));
    if (!seconds) return "—";
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  }

  function count(value) { return Array.isArray(value) ? value.length : Math.max(0, Number(value) || 0); }
  function plural(value, word) { return `${value} ${word}${value === 1 ? "" : "s"}`; }
  function svgIcon(name, className = "icon") {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.classList.add(className); svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", `#icon-${name}`); svg.append(use); return svg;
  }

  function filterWords(value) {
    return String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
  }

  function matchesFilter(track) {
    const words = filterWords($("track-search").value);
    const text = filterWords(`${track.title || ""} ${track.artist || track.uploader || ""}`).join(" ");
    return words.every((word) => text.includes(word));
  }

  function renderTracks(tracks, jobMode = false) {
    const picking = !jobMode && (tracks.length > 1 || tracks.some((track) => track.previously)); // waiting for the person to choose songs
    $("track-filter").hidden = jobMode || tracks.length < 2;
    const filtering = !jobMode && tracks.length > 1;
    const visible = tracks.map((track, index) => ({ track, index })).filter(({ track }) => !filtering || matchesFilter(track));
    $("track-filter-summary").textContent = filtering && $("track-search").value.trim() ? `${visible.length} of ${tracks.length} tracks shown. Selection includes hidden tracks.` : "";
    $("track-empty").hidden = !filtering || visible.length > 0;
    $("track-search-clear").hidden = !$("track-search").value;
    const signature = JSON.stringify([tracks, jobMode, picking, $("track-search").value, picking ? [...state.deselected] : []]);
    if (state.trackSignature === signature) return;
    state.trackSignature = signature;
    const list = $("track-list");
    const scrollPosition = list.scrollTop;
    const fragment = document.createDocumentFragment();
    visible.forEach(({ track, index }) => {
      const status = track.status || (track.available === false ? "unavailable" : jobMode ? "pending" : "ready");
      const labels = { ready: "", pending: "Waiting", queued: "Waiting", downloading: "Downloading", processing: "Converting", complete: "Done", completed: "Done", downloaded: "Done", success: "Done", failed: "Failed", error: "Failed", skipped: "Skipped", unavailable: state.preview?.source === "youtube" ? "Can’t download" : "Will look elsewhere", cancelled: "Cancelled" };
      const trackId = String(track.id ?? index);
      const row = document.createElement("li"); row.className = `track-row ${track.available === false ? "unavailable" : ""}`;
      let pick = null;
      if (picking) {
        row.classList.add("selectable");
        row.classList.toggle("off", state.deselected.has(trackId));
        const label = document.createElement("label"); label.className = "track-pick";
        pick = document.createElement("input"); pick.type = "checkbox"; pick.checked = !state.deselected.has(trackId); pick.setAttribute("aria-label", `Include ${track.title || "song"}`);
        const box = document.createElement("span"); box.className = "pick-box"; box.setAttribute("aria-hidden", "true");
        label.append(pick, box); row.append(label);
        const setPicked = (picked) => { if (picked) state.deselected.delete(trackId); else state.deselected.add(trackId); row.classList.toggle("off", !picked); state.trackSignature = ""; updateSelection(); };
        pick.addEventListener("change", () => setPicked(pick.checked));
        row.addEventListener("click", (event) => { if (!event.target.closest(".track-pick")) { pick.checked = !pick.checked; setPicked(pick.checked); } });
      }
      const indexEl = document.createElement("span"); indexEl.className = "track-index"; indexEl.textContent = String(index + 1).padStart(2, "0");
      const info = document.createElement("div"); info.className = "track-text";
      const title = document.createElement("p"); title.className = "track-title"; title.textContent = track.title || `Track ${index + 1}`;
      const artist = document.createElement("p"); artist.className = "track-artist"; artist.textContent = track.artist || track.uploader || "Unknown artist";
      info.append(title, artist);
      if (track.previously && !track.note && !track.error && !jobMode) { const seen = document.createElement("p"); seen.className = "track-note"; seen.textContent = tracks.length === 1 ? "Downloaded before. Tick it to download again." : "Downloaded before"; info.append(seen); }
      if (track.note && !track.error) { const note = document.createElement("p"); note.className = "track-note"; note.textContent = track.note; info.append(note); }
      if (track.error || (track.available === false && track.reason)) { const error = document.createElement("p"); error.className = "track-error"; error.textContent = track.error || track.reason; info.append(error); }
      const time = document.createElement("span"); time.className = "track-duration"; time.textContent = duration(track.duration);
      const statusEl = document.createElement("span"); statusEl.className = "track-status";
      if (["complete", "completed", "downloaded", "success"].includes(status)) { statusEl.classList.add("completed"); statusEl.append(svgIcon("check")); }
      if (["failed", "error"].includes(status)) statusEl.classList.add("failed");
      if (["downloading", "processing"].includes(status)) { statusEl.classList.add("downloading"); const spinner = document.createElement("span"); spinner.className = "spinner"; spinner.setAttribute("aria-hidden", "true"); statusEl.append(spinner); }
      const label = document.createElement("span"); label.textContent = status in labels ? labels[status] : "Waiting"; statusEl.append(label);
      row.append(indexEl, info, time, statusEl); fragment.append(row);
    });
    list.replaceChildren(fragment); list.scrollTop = scrollPosition;
    updateSelection();
  }

  // The count, the select-all toggle and the Download button all follow what is ticked.
  function updateSelection() {
    const tracks = state.preview?.tracks || [];
    const picking = (tracks.length > 1 || tracks.some((track) => track.previously)) && !state.job;
    $("select-toggle").hidden = !picking;
    if (!picking) return;
    const selectedTracks = tracks.filter((track, index) => !state.deselected.has(String(track.id ?? index)));
    const selected = selectedTracks.length;
    const seconds = selectedTracks.reduce((sum, track) => sum + Math.max(0, Number(track.duration) || 0), 0);
    const visible = tracks.map((track, index) => ({ track, index })).filter(({ track }) => matchesFilter(track));
    const allVisibleSelected = visible.every(({ track, index }) => !state.deselected.has(String(track.id ?? index)));
    const filtered = Boolean($("track-search").value.trim());
    $("track-list-summary").textContent = `${selected} of ${tracks.length} selected${seconds ? ` · ${Math.max(1, Math.round(seconds / 60))} min` : ""}`;
    $("select-toggle").textContent = filtered ? (allVisibleSelected ? "Deselect shown" : "Select shown") : (allVisibleSelected ? "Select none" : "Select all");
    $("select-toggle").disabled = visible.length === 0 || state.starting;
    $("download-button").disabled = selected === 0 || state.starting;
    $("download-button").querySelector("span").textContent = selected === tracks.length || selected === 0 ? "Download" : `Download ${selected}`;
  }

  function showPlaylist(preview, focus = false) {
    document.body.classList.add("has-playlist");
    $("playlist-panel").hidden = false;
    $("how-it-works").hidden = true;
    $("playlist-title").textContent = preview.title || "Playlist";
    const tracks = Array.isArray(preview.tracks) ? preview.tracks : [];
    const total = Number(preview.track_count) || tracks.length;
    $("playlist-count").textContent = preview.kind === "track" ? "Song" : plural(total, "track");
    $("track-list-heading").hidden = preview.kind === "track" && !tracks.some((track) => track.available === false || track.previously);
    const totalSeconds = tracks.reduce((sum, track) => sum + (Number(track.duration) || 0), 0);
    const creator = typeof preview.creator === "object" ? preview.creator?.username || preview.creator?.name : preview.creator;
    $("playlist-byline").textContent = [creator, preview.kind === "track" ? "" : plural(total, "track"), totalSeconds ? `${Math.max(1, Math.round(totalSeconds / 60))} min` : ""].filter(Boolean).join(" · ");
    $("playlist-artwork").hidden = true;
    $("cover-fallback").hidden = false;
    if (preview.artwork && /^https?:\/\//i.test(preview.artwork)) {
      const artwork = $("playlist-artwork");
      artwork.onload = () => { artwork.hidden = false; $("cover-fallback").hidden = true; };
      artwork.onerror = () => { artwork.hidden = true; $("cover-fallback").hidden = false; };
      artwork.src = preview.artwork;
    } else { $("playlist-artwork").removeAttribute("src"); }
    const unavailable = tracks.filter((track) => track.available === false).length;
    $("track-list-summary").textContent = unavailable ? `${unavailable} unavailable` : "";
    $("download-button").disabled = total === 0;
    $("download-button").querySelector("span").textContent = "Download";
    renderTracks(tracks);
    if (focus) $("playlist-title").focus({ preventScroll: true });
  }

  async function previewPlaylist() {
    if (state.busy || isActive() || state.starting) return;
    const input = $("playlist-url");
    const url = input.value.trim();
    if (!url) { notify("Paste a link or type a song.", "error"); input.setAttribute("aria-invalid", "true"); input.focus(); return; }
    let parsed = null;
    try { parsed = new URL(url); } catch { /* Not a link: treated as a song search below. */ }
    const searching = !parsed && url.length >= 3 && !/^[\w.-]+\.[a-z]{2,}(\/|$)/i.test(url);
    if (!parsed && !searching) { notify("That isn’t a link.", "error"); input.setAttribute("aria-invalid", "true"); input.focus(); return; }
    if (parsed && !isSupportedLink(parsed)) { notify("That link isn’t supported.", "error"); input.setAttribute("aria-invalid", "true"); input.focus(); return; }
    stopPolling(); state.job = null; rememberJob(null);
    const version = ++state.requestVersion;
    setFormBusy(true); notify();
    $("download-options").hidden = true;
    $("job-panel").hidden = true;
    try {
      const preview = await api("/api/preview", { method: "POST", body: JSON.stringify({ url }) });
      if (version !== state.requestVersion) return;
      $("track-search").value = "";
      state.preview = preview; state.needsMetadata = false; state.deselected = new Set((preview.tracks || []).map((track, index) => track.previously ? String(track.id ?? index) : null).filter(Boolean)); state.trackSignature = ""; showPlaylist(preview, true); $("download-options").hidden = false;
      if (window.CrateSettings?.get().autostart && !state.fromHistory && preview.kind === "track" && preview.tracks?.some((track) => track.available !== false && !track.previously)) startJob();
      state.fromHistory = false;
    } catch (error) {
      if (version !== state.requestVersion) return;
      notify(error.message, "error", previewPlaylist);
      if (state.preview) $("download-options").hidden = false;
    } finally { state.fromHistory = false; if (version === state.requestVersion) setFormBusy(false); }
  }

  function stopPolling() { if (state.timer) clearTimeout(state.timer); state.timer = null; }
  function schedulePoll(id, delay = 1000) { stopPolling(); state.timer = setTimeout(() => pollJob(id), delay); }

  function mergedJobTracks(job) {
    const previewTracks = state.preview?.tracks || [];
    const jobTracks = Array.isArray(job.tracks) ? job.tracks : [];
    if (!jobTracks.length) return previewTracks;
    return jobTracks.map((track, index) => ({ ...((track.id != null && previewTracks.find((candidate) => String(candidate.id) === String(track.id))) || previewTracks[index] || {}), ...track }));
  }

  function playlistFromJob(job) {
    return job.playlist || job.preview || { source: job.source, kind: job.kind, id: job.preview_id, title: job.playlist_title || job.title || "Your playlist", creator: job.creator, url: job.url, artwork: job.artwork, track_count: job.total_tracks, tracks: job.tracks || [] };
  }

  function renderJob(job) {
    const wasActive = isActive();
    state.job = job;
    if (state.needsMetadata && (job.title || job.playlist || job.preview)) {
      state.preview = playlistFromJob(job);
      if (state.preview.url) $("playlist-url").value = state.preview.url;
      showPlaylist(state.preview);
      state.needsMetadata = false;
    }
    const done = terminalStates.has(job.status);
    const completed = count(job.completed_tracks);
    const failed = count(job.failed_tracks);
    const total = count(job.total_tracks) || state.preview?.track_count || job.tracks?.length || 0;
    const percentage = Math.min(100, Math.max(0, Number(job.progress) || 0));
    $("download-options").hidden = true;
    $("job-panel").hidden = false;
    $("job-panel").className = `job-panel ${done ? job.status : ""}`;
    window.CrateWave.draw(percentage, job.status === "completed" ? "done" : ["failed", "cancelled"].includes(job.status) ? "failed" : "active");
    $("progress-bar").setAttribute("aria-valuenow", String(Math.round(percentage)));
    $("job-percentage").replaceChildren(document.createTextNode(String(Math.round(percentage))));
    const percent = document.createElement("span"); percent.textContent = "%"; $("job-percentage").append(percent);
    const titles = { queued: "Starting", downloading: "Downloading", packing: "Zipping", completed: "Ready", failed: "Failed", cancelled: "Cancelled" };
    $("job-title").textContent = titles[job.status] || "Downloading";
    $("job-eyebrow").textContent = "";
    const defaultMessage = job.status === "completed" ? `${completed} downloaded${failed ? ` · ${failed} skipped` : ""}` : job.status === "cancelled" ? "" : "Starting…";
    $("job-message").textContent = job.message || defaultMessage;
    $("cancel-button").hidden = done;
    $("cancel-button").disabled = state.cancelling;
    $("cancel-button").textContent = state.cancelling ? "Cancelling…" : "Cancel";
    $("job-actions").hidden = !done;
    $("zip-download").hidden = job.status !== "completed" || Boolean(job.delivered);
    $("zip-download").querySelector("span").textContent = isSong() ? "Save song" : "Save ZIP";
    if (job.status === "completed") $("zip-download").href = `/api/jobs/${encodeURIComponent(job.id)}/download`;
    $("retry-job-button").hidden = !state.preview?.id || !["failed", "cancelled"].includes(job.status);
    $("track-list-summary").textContent = done ? `${completed} of ${total}${failed ? ` · ${failed} skipped` : ""}` : `${completed} of ${total}`;
    renderTracks(mergedJobTracks(job), true);
    setFormBusy(false);
    if (done) { stopPolling(); rememberJob(job.status === "completed" ? job.id : null); }
    if (job.status === "completed" && wasActive) {
      const settings = window.CrateSettings?.get() || {};
      if (settings.clear) $("playlist-url").value = "";
    }
  }

  async function startJob() {
    if (!state.preview?.id || isActive() || state.starting) return;
    state.starting = true;
    $("download-button").disabled = true;
    $("retry-job-button").disabled = true;
    document.querySelectorAll('input[name="format"]').forEach((input) => { input.disabled = true; });
    setFormBusy(false); notify();
    try {
      const format = document.querySelector('input[name="format"]:checked')?.value || "original";
      const allTracks = state.preview.tracks || [];
      const idOf = (track, index) => String(track.id ?? index);
      const chosen = allTracks.filter((track, index) => !state.deselected.has(idOf(track, index)));
      let skipIds = allTracks.filter((track, index) => state.deselected.has(idOf(track, index))).map((track) => track.id);
      if (!chosen.length) return;
      const job = await api("/api/jobs", { method: "POST", body: JSON.stringify({ preview_id: state.preview.id, format, skip_ids: skipIds }) });
      state.pollFailures = 0;
      rememberJob(job.id);
      renderJob(job);
      if (!terminalStates.has(job.status)) schedulePoll(job.id);
    } catch (error) {
      const expired = error.status === 404 || error.status === 410 || /preview.*expired|load the playlist again/i.test(error.message);
      notify(error.message, "error", expired ? previewPlaylist : startJob);
    } finally {
      state.starting = false;
      $("download-button").disabled = false;
      $("retry-job-button").disabled = false;
      applyCapabilities(); setFormBusy(false); updateSelection();
    }
  }

  async function pollJob(id) {
    if (state.job && state.job.id !== id) return;
    try {
      const job = await api(`/api/jobs/${encodeURIComponent(id)}`);
      if (!state.job || state.job.id !== id) return;
      if (terminalStates.has(state.job.status)) return;
      if (state.pollFailures) notify();
      state.pollFailures = 0;
      renderJob(job);
      if (!terminalStates.has(job.status)) schedulePoll(id);
    } catch (error) {
      if (!state.job || state.job.id !== id) return;
      if (error.status === 404 || error.status === 410) {
        renderJob({ ...state.job, status: "failed", message: "This download is no longer available. Load the link again." });
        state.preview = null;
        $("retry-job-button").hidden = true;
        notify("That download expired. Paste the link again.", "warning");
        return;
      }
      state.pollFailures++;
      notify("Connection lost. Retrying…", "warning", () => { stopPolling(); return pollJob(id); });
      schedulePoll(id, Math.min(10000, 1500 * state.pollFailures));
    }
  }

  async function cancelJob() {
    if (!isActive() || state.cancelling) return;
    const id = state.job.id;
    state.cancelling = true;
    $("cancel-button").disabled = true;
    $("cancel-button").textContent = "Cancelling…";
    try {
      const job = await api(`/api/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST", body: "{}" });
      if (state.job?.id !== id) return;
      renderJob(job);
      if (!terminalStates.has(job.status)) schedulePoll(id);
    } catch (error) { notify(error.message, "error", cancelJob); }
    finally { state.cancelling = false; $("cancel-button").disabled = false; $("cancel-button").textContent = "Cancel"; }
  }

  function reset(force = false) {
    if (!force && (isActive() || state.starting)) return;
    stopPolling(); ++state.requestVersion;
    state.preview = null; state.job = null; state.trackSignature = ""; state.needsMetadata = false; rememberJob(null);
    document.body.classList.remove("has-playlist");
    $("playlist-panel").hidden = true;
    $("job-panel").hidden = true;
    $("how-it-works").hidden = false;
    $("playlist-url").value = "";
    $("track-search").value = "";
    $("track-list").replaceChildren(); notify(); setFormBusy(false); $("playlist-url").focus();
  }

  function applyCapabilities() {
    const radios = [...document.querySelectorAll('input[name="format"]')];
    const converting = state.health?.capabilities?.mp3_available !== false;
    radios.forEach((radio) => { radio.disabled = state.starting || (radio.value !== "original" && !converting); });
    if (!converting && !document.querySelector('input[name="format"][value="original"]').checked) document.querySelector('input[name="format"][value="original"]').checked = true;
    $("format-help").hidden = converting;
    $("format-help").textContent = "Only Original is available. Audio conversion isn’t set up on this device.";
    showFormatNote();
  }

  async function loadHealth() {
    try { state.health = await api("/api/health"); applyCapabilities(); } catch { /* The first playlist request shows connection errors with a retry. */ }
  }

  async function restoreJob() {
    const id = savedJob();
    if (!id) return;
    state.job = { id, status: "queued", progress: 0, tracks: [] };
    setFormBusy(false);
    notify("Resuming…");
    try {
      const job = await api(`/api/jobs/${encodeURIComponent(id)}`);
      state.preview = playlistFromJob(job);
      if (state.preview.url) $("playlist-url").value = state.preview.url;
      showPlaylist(state.preview); notify(); renderJob(job);
      if (!terminalStates.has(job.status)) schedulePoll(id);
    } catch (error) {
      if (error.status === 404 || error.status === 410) {
        state.job = null; rememberJob(null); setFormBusy(false);
        notify("That download expired. Paste the link again.", "warning");
      } else {
        state.needsMetadata = true;
        showPlaylist({ title: "Playlist", tracks: [] });
        renderJob({ ...state.job, message: "Reconnecting…" });
        state.pollFailures = 1;
        notify("Reconnecting…", "warning", () => pollJob(id));
        schedulePoll(id, 2000);
      }
    }
  }

  $("playlist-form").addEventListener("submit", (event) => { event.preventDefault(); previewPlaylist(); });
  $("playlist-url").addEventListener("input", () => $("playlist-url").removeAttribute("aria-invalid"));
  $("select-toggle").addEventListener("click", () => {
    if (state.job || state.starting) return;
    const tracks = state.preview?.tracks || [];
    const visible = tracks.map((track, index) => ({ track, index })).filter(({ track }) => matchesFilter(track));
    const everyone = visible.every(({ track, index }) => !state.deselected.has(String(track.id ?? index)));
    visible.forEach(({ track, index }) => {
      const id = String(track.id ?? index);
      if (everyone) state.deselected.add(id); else state.deselected.delete(id);
    });
    state.trackSignature = ""; renderTracks(tracks);
  });
  $("track-search").addEventListener("input", () => { if (!state.job) { $("track-list").scrollTop = 0; renderTracks(state.preview?.tracks || []); } });
  $("track-search-clear").addEventListener("click", () => { $("track-search").value = ""; renderTracks(state.preview?.tracks || []); $("track-search").focus(); });
  $("download-button").addEventListener("click", startJob);
  $("retry-job-button").addEventListener("click", startJob);
  $("cancel-button").addEventListener("click", cancelJob);
  $("start-another").addEventListener("click", () => reset());
  $("retry-button").addEventListener("click", () => { const retry = state.retry; if (retry) { notify(); retry(); } });
  window.addEventListener("pagehide", stopPolling);
  window.addEventListener("pageshow", (event) => { if (event.persisted && isActive()) schedulePoll(state.job.id, 0); });
  function showFormatNote() {
    const chosen = document.querySelector('input[name="format"]:checked')?.value;
    $("format-note").textContent = window.FORMAT_NOTES?.[chosen] || "";
  }
  function syncFormat() {
    const wanted = window.CrateSettings?.get().format;
    const radio = document.querySelector(`input[name="format"][value="${wanted}"]`);
    if (radio && !radio.disabled && !isActive() && !state.starting) radio.checked = true;
    showFormatNote();
  }
  // "Save to": songs always go to one folder, changed only through the Finder panel.
  const canPick = Boolean(window.webkit?.messageHandlers?.crate); // only the Mac app picks folders; a phone just receives files
  const DEFAULT_FOLDER = "~/Music/Crate";
  function showDestination() {
    const folder = window.CrateSettings.get().folder || DEFAULT_FOLDER;
    $("folder-name").textContent = folder.split("/").filter(Boolean).pop() || "Crate";
    $("folder-button").title = folder;
    $("destination").hidden = !canPick; // only the Mac app can pick a folder
    $("destination-note").textContent = `Songs are saved to ${folder.replace(/^\/Users\/[^/]+/, "~")}. Playlists get their own subfolder.`;
  }
  window.CrateFolderPicked = (path) => { if (path) window.CrateSettings.set({ folder: path }); showDestination(); };
  $("folder-button").addEventListener("click", () => window.webkit.messageHandlers.crate.postMessage({ type: "chooseFolder", current: window.CrateSettings.get().folder }));
  window.CrateSettings.onChange(showDestination);
  showDestination();

  document.querySelectorAll('input[name="format"]').forEach((input) => input.addEventListener("change", () => { window.CrateSettings?.set({ format: input.value }); showFormatNote(); }));
  window.CrateSettings?.onChange((now, before) => { if (now.format !== before.format) syncFormat(); });
  document.addEventListener("crate:load", (event) => {
    if (isActive() || state.starting || state.busy) return;
    $("playlist-url").value = event.detail;
    state.fromHistory = true; // the person chose this from History: show it and wait, never start on its own
    previewPlaylist();
  });
  loadHealth().then(syncFormat); syncFormat(); restoreJob();
  document.dispatchEvent(new Event("crate:ready"));
})();
