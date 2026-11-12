"""Private backend owned by the native Crate application process.

The app launches this bundled executable on each open. No login item, fixed
port, Python installation, browser, or manually started server is required.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import signal
import socket
import threading

from werkzeug.serving import make_server

from app import create_app


def main():
    parser = argparse.ArgumentParser(description="Crate desktop backend")
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    args = parser.parse_args()
    if args.parent_pid <= 1:
        parser.error("A valid owning application process is required")

    # Give the backend and any audio converters their own process group so
    # quitting Crate can terminate exactly its own processes.
    try:
        os.setsid()
    except OSError:
        pass
    # Foundation.Process may already launch us as a group leader, in which
    # case setsid fails with EPERM even though this is our own safe group.
    owns_group = os.getpgrp() == os.getpid()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    # Settings live outside the cache folder so macOS never purges them.
    application = create_app(args.data_dir, Path.home() / "Library" / "Application Support" / "Crate" / "settings.json")
    server = make_server("127.0.0.1", 0, application, threaded=True)
    # Phones on the same wifi reach Crate here. Requests are refused unless phone access is on and the pairing code matches.
    try:
        lan_server = make_server("0.0.0.0", 8765, application, threaded=True)
    except OSError:
        lan_server = make_server("0.0.0.0", 0, application, threaded=True)
    application.extensions["lan_port"] = lan_server.server_port
    # Big shared wifi (campus, office) often drops full-size packets sent between two of its own devices, so a
    # phone gets the first bytes of the page and then waits forever. Smaller packets get through.
    accept = lan_server.get_request

    def accept_with_small_packets():
        connection, address = accept()
        try:
            connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_MAXSEG, 1200)
        except OSError:
            pass
        return connection, address

    lan_server.get_request = accept_with_small_packets
    threading.Thread(target=lan_server.serve_forever, daemon=True, name="crate-lan").start()
    server.timeout = 0.4
    args.ready_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_ready = args.ready_file.with_suffix(".tmp")
    temporary_ready.write_text(json.dumps({
        "url": f"http://127.0.0.1:{server.server_port}",
        "pid": os.getpid(),
    }))
    temporary_ready.chmod(0o600)
    temporary_ready.replace(args.ready_file)

    try:
        while not stop.is_set():
            # Also clean up if the GUI was force-quit or the user logged out.
            if os.getppid() != args.parent_pid:
                break
            server.handle_request()
    finally:
        manager = application.extensions["jobs"]
        with manager.lock:
            for event in manager.events.values():
                event.set()
        server.server_close()
        lan_server.shutdown()
        lan_server.server_close()
        args.ready_file.unlink(missing_ok=True)
        if owns_group:
            # Our signal handler makes this safe for the current process while
            # its default-action children (e.g. FFmpeg) terminate immediately.
            os.killpg(os.getpgrp(), signal.SIGTERM)


if __name__ == "__main__":
    main()
