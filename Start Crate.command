#!/bin/bash
set -e
cd -- "$(dirname -- "$0")"
trap 'printf "\nCrate stopped with an error. Read the message above.\n"; read -r -p "Press Return to close…"' ERR
if ! command -v python3 >/dev/null 2>&1; then
  printf "Python 3.11 or newer is required. Install Python from https://www.python.org/downloads/ and try again.\n"
  exit 1
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "Python 3.11 or newer is required. Please update Python.")'
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
if ! .venv/bin/python -c 'import flask, yt_dlp, imageio_ffmpeg, certifi' >/dev/null 2>&1; then
  printf "Installing Crate dependencies. This only happens on first launch…\n"
  .venv/bin/python -m pip install --no-cache-dir -r requirements.txt
fi
.venv/bin/python app.py
