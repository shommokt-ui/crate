"""Build the native, self-contained Apple Silicon Crate.app on macOS."""

from __future__ import annotations

import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
STAGING = BUILD / "Crate.app"


def run(*args):
    print("Building:", args[0], " ".join(map(str, args[1:3])), flush=True)
    environment = {**os.environ, "PYINSTALLER_CONFIG_DIR": str(BUILD / "pyinstaller-cache")}
    subprocess.run(list(map(str, args)), cwd=ROOT, env=environment, check=True)


def main():
    if sys.platform != "darwin":
        raise SystemExit("Build Crate.app on macOS.")
    BUILD.mkdir(exist_ok=True)
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--target-arch", "arm64", "--name", "crate-server",
        "--distpath", DIST, "--workpath", BUILD / "pyinstaller", "--specpath", BUILD,
        "--add-data", f"{ROOT / 'static'}:static", "--collect-submodules", "yt_dlp",
        "--collect-all", "imageio_ffmpeg", "--collect-data", "certifi",
        "--exclude-module", "tkinter", ROOT / "desktop_server.py")

    # Build in staging; leave the previous application intact on build failures.
    if STAGING.exists():
        shutil.rmtree(STAGING)
    contents = STAGING / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"
    macos.mkdir(parents=True)
    resources.mkdir()
    shutil.copytree(DIST / "crate-server", resources / "backend", symlinks=True)
    run("xcrun", "swiftc", "-O", "-target", "arm64-apple-macosx13.0",
        "-module-cache-path", BUILD / "swift-cache", ROOT / "desktop" / "Crate.swift",
        "-o", macos / "Crate", "-framework", "Cocoa", "-framework", "WebKit")
    run("xcrun", "swiftc", "-module-cache-path", BUILD / "swift-cache",
        ROOT / "desktop" / "Icon.swift", "-o", BUILD / "make-icon", "-framework", "AppKit")
    run(BUILD / "make-icon", BUILD / "Crate.iconset")
    run("iconutil", "-c", "icns", BUILD / "Crate.iconset", "-o", resources / "Crate.icns")
    plist = {
        "CFBundleName": "Crate", "CFBundleDisplayName": "Crate",
        "CFBundleIdentifier": "local.crate.playlistdownloader",
        "CFBundleExecutable": "Crate", "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "1.0.0", "CFBundleVersion": "2",
        "CFBundleIconFile": "Crate", "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True, "NSPrincipalClass": "NSApplication",
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    }
    with (contents / "Info.plist").open("wb") as file:
        plistlib.dump(plist, file)
    (contents / "PkgInfo").write_bytes(b"APPL????")
    # Local ad-hoc signing supports normal launch on this Mac. Developer ID
    # signing/notarization is a separate distribution step, not needed here.
    run("codesign", "--force", "--deep", "--sign", "-", STAGING)
    run("codesign", "--verify", "--deep", "--strict", STAGING)
    destination = ROOT / "Crate.app"
    previous = BUILD / "Crate-previous.app"
    if previous.exists():
        shutil.rmtree(previous)
    if destination.exists():
        destination.rename(previous)
    STAGING.rename(destination)
    print(f"\nReady: {destination}\nDouble-click Crate.app to open. No Python or Terminal required.")


if __name__ == "__main__":
    main()
