# Project context for the next agent

## User's request

Build a SoundCloud playlist downloader that avoids downloading songs manually. The implementation interprets the request as **all available songs in one ZIP**, with each song a separate audio file inside. The user also explicitly requested this context file so another agent can pick up the work.

Latest request (November 10, 2026): review the existing work and make it a real Mac GUI app, with no browser or deployment. After logout/restart, the user should only need to open the app; no manual server startup. This does not request a login item or automatic launch at login. Preserve the Spotify functionality added to the workspace between turns.

## Product and implementation

- App name: **Crate**. Self-contained native macOS application: Swift AppKit window and WKWebView, with a private bundled Python helper. The helper uses a random loopback port and is owned by the GUI process. No browser, Terminal, installed Python, hosting, or deployment is required to use the built `.app`.
- Original checkout: `/Users/shommoktalukder/PlaylistDownloader`. The configured workspace `/Users/shommoktalukder/Documents/ChatGPT/PlaylistDownloader` was missing and has been restored as a source copy for the November 12 improvements.
- Stack: Python 3.11+, Flask, yt-dlp, imageio-ffmpeg, certifi, vanilla HTML/CSS/JavaScript. No Node build step.
- Full public SoundCloud playlist URLs only; up to 500 tracks. No cookies, account login, private-playlist support, DRM bypass, or paid preview downloads.
- Default output: MP3; optional original/source format. FFmpeg ships through the Python dependency instead of requiring Homebrew.
- Preview first, then one background download job, per-track progress/failures, cancellation, and an explicit final ZIP save button.
- One ZIP with ordered artist/title audio filenames, a manifest, and a report for failed tracks. Continue after individual failures; fail the job if no songs succeed.

## Files

- `app.py`: Flask API, single-worker job manager, job/preview retention, localhost restrictions, ZIP delivery, CLI.
- `downloader.py`: URL validation, fast playlist metadata preview, yt-dlp audio downloads, format filtering, MP3 conversion, cancellation, ZIP assembly.
- `static/index.html`, `static/style.css`, `static/app.js`: responsive interface and job recovery on browser reload.
- `Start Crate.command`: double-click macOS launcher; creates `.venv` and installs dependencies on first launch.
- `requirements.txt`: runtime dependencies.
- `tests/`: deterministic Python unit/integration tests with mocked upstream downloads.
- `README.md`: user setup, limitations, maintenance, and usage.
- `.runtime/`: ignored temporary runtime data; do not commit or include song downloads in source control.
- `desktop/Crate.swift`: native window/menu, automatic helper launch, native ZIP Save dialog, helper cleanup on quit.
- `desktop_server.py`: bundled helper entry point; random port, readiness JSON, parent-process monitoring, process-group cleanup.
- `desktop/build_app.py`: PyInstaller + Swift compilation + icon generation + local signing. Builds root `Crate.app`, preserving previous app in `build/Crate-previous.app`.
- `desktop/Icon.swift`: deterministic native icon drawing, used only at build time.
- `requirements-build.txt`: runtime dependencies plus PyInstaller.
- `Crate.app`, `build/`, `dist/`: generated, ignored build artifacts. The entire `.app` is self-contained and movable.

## Spotify source

`spotify.py` reads a public playlist's track list from Spotify's embed page (no API key; no audio), then `find_on_soundcloud` searches SoundCloud (`scsearch`) and ranks candidates by title/artist/duration, rejecting remix/slowed/sped-up variants. `download_playlist` tries up to 3 matches per song. `POST /api/preview` takes just `{url}` (playlist or song); previews/jobs carry `source`. The source is chosen from the URL host (no tabs); `kind: "track"` previews are single songs delivered as the bare audio file (the native Save panel keeps its extension). The UI is a glass/dark-or-light theme (`static/theme.js` sets it pre-paint; circular View Transition reveal with a CSS fallback). SoundCloud tracks whose only streams are encrypted (`ctr-`/`cbc-encrypted-hls`) or preview-only are skipped with the specific reason (see `restriction_reason`; shown to people as "Locked by SoundCloud", "Only a 30-second preview is available", etc.); Crate makes no judgement about copyright, it only reads SoundCloud's stream list.

## Settings, theme and cursor effect

`settings.py` validates/persists settings (`GET/POST /api/settings`); the file lives in `~/Library/Application Support/Crate/settings.json` (passed by `desktop_server.py`; the page origin changes port every launch, so localStorage cannot be used). `index()` injects the settings into a `<meta name="crate-settings">` tag so `static/theme.js` applies theme (orange only) before first paint. `static/settings.js` owns state and the dialog; `static/cursor.js` is a canvas comet that follows the pointer and stops its rAF loop when the tail fades (verified with a Node harness: zero frames when idle). No audio anywhere by design. Download options (quality, numbering, tags, cover art; cover art is MP3-only because other containers need ffprobe) are read from settings when a job starts. The app icon is drawn by `desktop/Icon.swift` and matches `static/logo.svg` (orange).

## Progress waveform

`static/wave.js` (`window.CrateWave.draw`) paints a DJ-style canvas waveform (thin aligned bars mirrored on one centre line, coloured by bass/mid/high mix, beat grid, playhead). The shape is synthetic (seeded), not the audio; it repaints only on progress, theme change or resize, and while a download is active the bars shake (about 30 fps; the loop stops when the download ends, the window is hidden, or reduced motion is on). A stray CSS selector once broke an earlier DOM-bar version in dark mode; `StylesheetTests` guards key rules.

## YouTube

`downloader.classify_youtube_url` / `preview_youtube` handle YouTube video and playlist links (host allowlist, 11-char IDs, mixes refused). Downloads reuse `download_playlist` with `source: "youtube"` (extractors limited to `youtube`/`youtube:tab`, audio-only formats, DRM skipped). Titles are split `Artist - Song` when present. Image hosts `*.ytimg.com` / `*.ggpht.com` are allowed in the CSP.

## Search and fallbacks

`looks_like_search` / `preview_search` (typed names, YouTube `ytsearch10`, ranked by query-word coverage, no variants, 60-900 s). In `download_playlist`, a failed or unmatched song goes through one fallback chain: SoundCloud re-upload (`find_on_soundcloud(strict_only=True)`), then `spotify.find_on_youtube` (same-length match); the row gets a note (`Used another upload of this song` / `Used a YouTube copy`).

## Repeat-song check

History entries carry `songs` (`[key, url]`, key = first artist word + cleaned title via `history.song_key`). `/api/preview` flags tracks with `previously` (when `history` and `skip_downloaded` are on); the UI starts those songs unticked (no prompt) and Download sends the unticked ids as `skip_ids`, which `JobManager.start` removes from the job (400 if nothing is left).

## Choosing songs and history song lists

The UI keeps `state.deselected` (ids); Download sends them as `skip_ids` (plus repeat-skips), `JobManager.start` filters them and keeps the whole list as `_full_list`, so history entries store `tracks` (`{title, artist, status: done|failed|skipped}`) in addition to `songs` (matching keys). `GET /api/history` is a light list (`track_count`); `GET /api/history/<id>` returns one entry's songs. Playlists never auto-start; the `autostart` setting applies to single songs only.

## Formats

`downloader.CONVERSIONS` maps mp3/aac(.m4a)/opus/flac/wav to yt-dlp codecs; `original` skips conversion. Cover art embeds via `mutagen` (now in requirements; WAV cannot carry art) and a failed embed is retried without cover so the song is never lost. `POST /api/jobs` accepts any of these in `format`.

## API contract

- `GET /api/health` → `{ok, capabilities: {mp3_available, ffmpeg_available}}`.
- `POST /api/preview` with `{url}` → playlist `{id, url, title, creator, artwork, track_count, tracks}`.
- `POST /api/jobs` with `{preview_id, format: "mp3" | "original"}` → job snapshot (HTTP 202).
- `GET /api/jobs/<id>` → job snapshot.
- `POST /api/jobs/<id>/cancel` with `{}` → cancellation request.
- `GET /api/jobs/<id>/download` → ZIP attachment after completion.
- Error shape: `{error: "Readable explanation"}` with an appropriate HTTP status.
- Job states: `queued`, `downloading`, `packing`, `completed`, `failed`, `cancelled`. Track completion state: `complete` (UI also accepts `completed`).
- POST requests require JSON and a matching Origin if Origin is supplied. All requests require a loopback Host; the server listens only on `127.0.0.1`.

## Commands

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py --no-browser
.venv/bin/python -m unittest discover -s tests -v
node --check static/app.js
.venv/bin/python -m pip install -r requirements-build.txt
.venv/bin/python desktop/build_app.py
```

The source/browser development server uses `http://127.0.0.1:8765`. CLI supports `--port` and `--no-browser`. The native app allocates its own free port on every launch; the user never needs to see a URL.

## State and lifecycle

Job and preview state are in memory. The webview remembers the most recent job ID in localStorage and can resume polling after a window refresh. Quitting/reopening loses unsaved job state; saved ZIP files remain at the user's chosen location. Preview TTL is one hour, maximum 20 cached previews; completed job retention is 24 hours. Cleanup runs at startup and per request, only removes UUID-named job directories, and never removes active jobs. Failed/cancelled jobs clean their own directory.

Native temporary downloads are in `~/Library/Caches/Crate/downloads/`, outside the signed bundle. Readiness JSON and session logs use a unique temporary directory. `desktop_server.py` starts a process group; the native app terminates that group on quit, including FFmpeg. Parent monitoring also exits if the GUI is force-quit or the user logs out. Every app launch starts a fresh backend automatically. There are no LaunchAgents or login items.

## Validation and handoff status

Desktop packaging verification is in progress. Installed runtime during development: Python 3.14.3, Flask 3.1.3, yt-dlp 2026.10.26, imageio-ffmpeg 0.8.1, certifi, PyInstaller 6.22.3. Native build targets arm64 macOS 13+.

Verified so far: 36 Python tests pass (including preserved Spotify tests), JavaScript syntax passes. Packaged engine launched twice from a temporary working directory with minimal PATH and no virtualenv, served correct health/static assets, reported bundled MP3 capability, and exited cleanly both times. Previous live testing successfully downloaded all three tracks in `https://soundcloud.com/cross_couloir/sets/creative-commons-free-tracks`, converted them to MP3, served a 6,089,652-byte ZIP, and passed ZIP integrity checks. DRM-only Royal Concept test track correctly failed rather than generating a false-success archive. Finish native window/Save/reopen verification and update this section.

## Known boundaries

- SoundCloud may change its API or block/rate-limit requests. Keep yt-dlp current; surface upstream failures rather than presenting false success.
- Only canonical playlist URLs work; users must resolve short share links themselves.
- ZIP is packaging, not an audio format: it contains individual MP3/source audio files.
- No deployment, repository remote, publishing, or messages to other people were requested.
- Future agents should read this file and the actual implementation, verify current state with `git status`, and avoid assuming that downloaded files or running servers persist.
- The app is locally ad-hoc signed, not Developer ID notarized. It is built for this Apple Silicon Mac. Public distribution/Intel builds were not requested.

## iPhone access (added)
- `phone.py`: pairing token (`~/Library/Application Support/Crate/phone-token`), LAN IP (en0/en1 first, a VPN address is unreachable), QR (segno), private-network host check (incl. 100.64/10).
- `desktop_server.py` also listens on 0.0.0.0:8765 (random port if busy). `app.py` `before_request`: non-loopback requests need setting `phone` on, a local-network Host, and the code (cookie `crate_phone` or `?t=`). Icon/manifest are public. Phone jobs (`_remote`) skip folder delivery and offer the ZIP/song as a download; phones cannot change `phone`/`folder`.
- `/api/phone` and `/api/phone/reset` are Mac-only; Settings → Phone shows the QR.

## Phone = the website (clarified November 12, 2026)
The user's "phone version" means the **website opened on the phone by scanning the QR in Settings → Phone**, not a native app. The Swift app that used to live in `ios/` was removed at the user's request (Nov 12); don't recreate it. Verified Nov 12: page renders in mobile Safari (simulator) through the LAN address + code; a remote preview → job → ZIP download works and writes nothing to the Mac's folder. A refused phone (access off / stale code) now gets a small HTML page (`refuse_phone` in `app.py`) instead of JSON; API calls still get JSON. The token stays in the URL on purpose so an "Add to Home Screen" icon keeps working (home-screen web apps don't reliably share Safari's cookies). The app has now been rebuilt to include `refuse_phone` and the phone-startup improvements below.

## Phone startup and reliability improvements (November 12, 2026)

The user reported that scanning the QR opened a blank page in Safari. The running app log showed a successful phone GET of the page but no follow-up asset requests. `phone_page.py` now inlines startup styles and scripts for authenticated phone requests, preserving dependency order and using a fresh CSP script nonce. `static/phone-startup.js` shows a readable reload message if `crate:ready` is not emitted. Desktop assets remain external. The cause on the physical phone has not been reproduced locally; verify by rescanning after the app rebuild.

A valid QR now supersedes stale cookies. Public icons cannot issue a pairing cookie without a valid code. Invalid typed settings fall back to defaults. Settings saves are serialized with retained pending changes and visible retry. Playlist filtering preserves selection outside the visible matches. Tests include `test_phone_page.py`, `test_settings.py`, phone authentication regressions, and the Node selection/settings harnesses.

Validation: 95 Python tests pass; JavaScript syntax checks and selection, settings persistence, and waveform harnesses pass. Built with `desktop/build_app.py` and verified the ad-hoc signature. Both `/Applications/Crate.app` and `/Users/shommoktalukder/PlaylistDownloader/Crate.app` were updated, with previous apps and edited source files preserved under the original checkout’s `build/before-phone-fix-20261112-114916/`. `/Applications/Crate.app` was reopened. The installed QR page renders in the iPhone 17 Pro simulator Safari; the mobile Chromium check works with every `/static/` request blocked, no JS errors or horizontal overflow, and functioning Settings. Still awaiting confirmation on the user’s physical phone.

## Phone page stalls on big shared wifi (November 12, 2026)
Symptom on the real iPhone: dark blank Safari page that keeps loading. The Mac's `engine.log` showed the phone's `GET /?t=…` answered 200 but no `/api/health` afterwards, while the same build rendered in the simulator; the network is a /15 campus-style wifi where no neighbour answers ping. Working theory (not confirmed on the phone): large Mac→phone transfers stall. Mitigations: `desktop_server.py` sets `TCP_MAXSEG` 1200 on each accepted LAN connection (it cannot be set on the listener on macOS), and `index()` gzips the inlined phone page (~101 KB → ~28 KB). If it still stalls, test on the iPhone's Personal Hotspot or home wifi to confirm it is the network. The installed app is `/Applications/Crate.app`; `build_app.py` builds `./Crate.app`, which must be moved there.
