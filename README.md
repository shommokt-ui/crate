# Crate — SoundCloud playlist downloader

Paste a public playlist or song link, choose a format (MP3, AAC, Opus, FLAC, WAV or the original file), and download the available songs together in **one ZIP** (or a single song as one file). Songs keep their playlist order and get readable artist/title filenames. A failed track does not stop the rest of the playlist.

## Install (for friends)

You need a Mac with Apple Silicon (M1 or newer) on macOS 13 or later. You don't need to install Python or anything else.

1. Open the **[Releases](../../releases/latest)** page and download **Crate.zip**.
2. Double-click the zip to unzip it, then drag **Crate.app** into your **Applications** folder.
3. Open Crate. Because the app isn't from the App Store, macOS blocks it the first time:
   - Click **Done** (or **OK**) on the warning.
   - Open **System Settings → Privacy & Security**, scroll down, and click **Open Anyway** next to the Crate message. Confirm with your password.
   - **If that doesn't work:** open Terminal, paste this line, and press Return:
     ```bash
     xattr -dr com.apple.quarantine /Applications/Crate.app
     ```
     Then open Crate normally.
4. After that, open Crate like any other app. Paste a SoundCloud, Spotify or YouTube link (or type a song name), press **Download**, and save the ZIP.

**Updating:** download the newest Crate.zip from Releases, replace the old app in Applications, and repeat step 3.

## Open on this Mac

Double-click **Crate.app**. It opens in its own Mac window, with a Dock icon and a native ZIP save dialog. Python, the downloader, certificates, and the audio converter are included in the app. No Terminal, browser, setup, hosting, or online deployment is required.

After logging out or restarting your Mac, **just open Crate again**. It starts everything it needs automatically. Closing the app stops its helper processes. You can move the entire `Crate.app` to Applications or add it to the Dock; it does not depend on this project folder. An internet connection is needed to retrieve playlists and songs. The built app targets Apple Silicon Macs running macOS 13 or newer.

Save completed ZIPs before quitting. Unsaved jobs do not resume after closing the app or logging out. Files you save remain where you chose to put them.

## Run from source (developers only)

The bundled app needs no separately installed Python. Source development requires Python 3.11 or newer. **Start Crate.command** remains available as a browser-based development launcher (Control+C stops it).

Or launch from a terminal:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

Use `--port 8766` if the default port is occupied, or `--no-browser` to skip opening the browser.

## Links

You can also type a song name instead of pasting a link; Crate finds the best match on YouTube (skipping remixes, covers, slowed versions and long loops) and loads it like a pasted link. If a SoundCloud or Spotify song can't be downloaded from its own source, Crate looks for the same recording (same length) as another SoundCloud upload and then on YouTube, and notes which it used.

YouTube works too: paste a video link (watch, youtu.be, Shorts or YouTube Music) for a single song, or a playlist link for a ZIP. Mixes (auto-generated lists) can't be downloaded, private or age-restricted videos are skipped with the reason shown, and DRM-protected streams are never downloaded.

Paste a playlist or a single-song link from SoundCloud or Spotify; Crate tells them apart by the URL. A playlist downloads as one ZIP, a single song as the audio file itself. Spotify audio is encrypted and is never downloaded: Crate reads the public song list and finds each song on SoundCloud (matching title, artist and length, skipping remixes and sped-up versions). Matches can differ from the original, and songs that are missing or encrypted-only are skipped with the reason shown. Spotify shares only the first part of very large playlists.

## Choosing songs

Loading a playlist link only shows its songs, all ticked; nothing is downloaded or searched until you press **Download**. Untick any songs you don't want (or use **Select none / Select all**), and the button shows how many will be downloaded. Single songs have nothing to choose, so the "Download single songs right away" setting can start those immediately.

Search by title or artist to narrow a large playlist. **Select shown / Deselect shown** only changes visible matches; your choices outside the filter stay selected. Clear the search to see the whole list again.

## History

The clock button (top right) lists what you've downloaded, newest first, with a **Load** button to fetch it again (it only shows the songs, nothing downloads), an arrow on playlists to see their songs and whether each was downloaded, skipped or failed, and an x to remove one. Only the title, type, format, counts and link are saved (in `~/Library/Application Support/Crate/history.json`), never the audio. Turn it off with **Keep history** in settings, or use **Clear history** in the panel.

## Repeat songs

With **Untick songs I already have** on (it needs **Keep history**), Crate remembers every song you download. When a link contains songs that are already in your history, they start unticked and are marked "Downloaded before"; tick any you want again. A single song you already have also starts unticked, and Download stays off until you tick it. Songs are matched by link or by the first word of the artist plus the title, so a repeat is caught even from a different site; clearing or removing a history item forgets its songs.

## Settings

The sliders button (top right) opens settings, saved in `~/Library/Application Support/Crate/settings.json`: theme (Auto/Light/Dark), the cursor effect on/off, default format, quality (MP3/AAC/Opus), numbered filenames, song info and cover art in the files, auto-download after loading a link, and clearing the link when done. The sun/moon button switches the theme with a circular reveal.

The cursor effect is a glowing comet that follows the mouse. It only draws while the mouse is moving and stops completely once the tail fades, so it costs nothing when idle. Crate plays no sounds.

Quality: MP3 defaults to 320 kbps. SoundCloud's own best stream is about 160 kbps AAC, so a higher MP3 setting makes bigger files but cannot add detail; AAC and Original keep that 160 kbps stream untouched. If a SoundCloud song is locked by SoundCloud (it only sends an encrypted copy), Crate looks for another public upload of the *same length* (a re-upload of the full track) and uses it, labelled "Used another upload of this song"; a short clip or a remix is never substituted.

## Use it

1. Copy the playlist's full link, such as `https://soundcloud.com/artist/sets/playlist-name`.
2. Paste the link and load the playlist.
3. Choose a format. **MP3** plays everywhere; **AAC** (.m4a) and **Opus** give better quality at the same size; **FLAC** and **WAV** are lossless containers (large files, and they can't improve on the source quality); **Original** keeps the untouched source file with no conversion.
4. Start the download and wait for the ZIP button.
5. Save the ZIP, then unzip it to get the individual audio files.

Public playlists with up to 500 tracks are supported. Short `on.soundcloud.com` share links should be opened in a browser first; copy the full playlist URL from the address bar. Private playlists, login-only tracks, paid previews, and unavailable tracks are not supported. The app skips tracks that cannot be downloaded and reports failures. Audio quality is limited by the source; converting to MP3 does not improve it. Use tracks you have permission to download.

## Files and privacy

The desktop window uses a private helper bound only to `127.0.0.1` on a free port chosen at launch. Nothing is hosted online or exposed to your network. It downloads from SoundCloud directly to this computer. No account or API key is needed.

The desktop app stores temporary audio in `~/Library/Caches/Crate/downloads/`; the source launcher uses `.runtime/downloads/`. Each ZIP includes a playlist manifest and any failure report. Temporary downloads older than 24 hours are cleaned up on the next request or app launch. Preview and job state live in memory: reloading the window resumes the current job, but reopening the app requires loading the playlist again. Cancelled or failed jobs have their temporary files removed. One playlist downloads at a time.

## Maintenance and verification

SoundCloud can change its delivery API. To update dependencies and rebuild the desktop app:

```bash
.venv/bin/python -m pip install --upgrade -r requirements-build.txt
.venv/bin/python desktop/build_app.py
```

Run the automated checks:

```bash
.venv/bin/python -m unittest discover -s tests -v
node --check static/app.js
node tests/wave_check.js
```

Building requires Apple's Xcode Command Line Tools (`swiftc`) on an Apple Silicon Mac. The build stages a new bundle and preserves the previous app under `build/Crate-previous.app`. It is signed locally for use on this Mac; public distribution would require Developer ID signing and notarization.

The download engine uses [yt-dlp](https://github.com/yt-dlp/yt-dlp). See `PROJECT_CONTEXT.md` for architecture, implementation decisions, and handoff information.

## iPhone

Crate can be used from an iPhone on the same wifi, in Safari. The Mac does the downloading and the phone receives the files.
Turn it on in Settings → Phone, then scan the code with the phone's camera. Share → Add to Home Screen gives it an icon.

Keep Crate open on your Mac while using your phone. If you make a new pairing code, scan the new QR again; it replaces the old phone session. The phone loads its interface in the initial response and shows a reload message if startup fails. If a blank page persists, reopen the current QR link in Safari.
