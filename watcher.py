#!/usr/bin/env python3
"""jellyfin-watch — close the NFS realtime-monitor gap.

Jellyfin's realtime library monitor uses inotify, which NFS does not deliver
to client pods, so new files in /mnt/raid_store/jellyfin never trigger an
index update on their own. This watcher runs on sutliffserver (the NFS
server, where the media tree is LOCAL so inotify works) and POSTs a debounced
/Library/Refresh to Jellyfin whenever the tree changes.

Config (env):
    WATCH_ROOT           default /mnt/media     (hostPath of the media tree)
    JELLYFIN_URL         required               e.g. http://jellyfin.jellyfin.svc.cluster.local:8096
    JELLYFIN_API_KEY     required               X-Emby-Token header value
    DEBOUNCE_SECONDS     default 20             quiet time after last event before refresh
    MIN_INTERVAL_SECONDS default 60             floor between refreshes (anti-hammer)
    STARTUP_REFRESH      default 1              fire one refresh at boot (covers downtime)
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

WATCH_ROOT = os.environ.get("WATCH_ROOT", "/mnt/media")
JELLYFIN_URL = os.environ.get("JELLYFIN_URL", "").rstrip("/")
JELLYFIN_API_KEY = os.environ.get("JELLYFIN_API_KEY", "")
DEBOUNCE = float(os.environ.get("DEBOUNCE_SECONDS", "20"))
MIN_INTERVAL = float(os.environ.get("MIN_INTERVAL_SECONDS", "60"))
STARTUP_REFRESH = os.environ.get("STARTUP_REFRESH", "1") == "1"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("jellyfin-watch")


class _Handler(FileSystemEventHandler):
    def __init__(self) -> None:
        self.last_event: float = 0.0

    def on_any_event(self, event: FileSystemEvent) -> None:
        # Ignore hidden paths (temp/work/stash dirs like .yt-sync-work,
        # .dupes_kids) — real publishes always land or rename into a
        # visible path, which fires its own event.
        if "/." in event.src_path:
            return
        self.last_event = time.time()
        log.info("change: %s %s", event.event_type, event.src_path)


def scan_is_running() -> bool:
    """True when Jellyfin's Scan Media Library task is Running/Cancelling.

    Every POST /Library/Refresh cancels the in-flight scan, so a watcher that
    fires blind keeps scans at ~0% during busy writes (scan cancel-loop).
    Defer instead: report busy and let main() retry after MIN_INTERVAL.
    """
    if not (JELLYFIN_URL and JELLYFIN_API_KEY):
        return False
    try:
        req = urllib.request.Request(
            f"{JELLYFIN_URL}/ScheduledTasks",
            headers={"X-Emby-Token": JELLYFIN_API_KEY},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            tasks = json.load(resp)
        for t in tasks or []:
            if t.get("Name") == "Scan Media Library":
                return t.get("State") in ("Running", "Cancelling")
    except Exception as exc:  # noqa: BLE001
        log.error("task query failed: %s: %s", type(exc).__name__, exc)
    return False  # optimistic if we can't query — MIN_INTERVAL still coalesces


def refresh() -> bool:
    if not (JELLYFIN_URL and JELLYFIN_API_KEY):
        log.error("JELLYFIN_URL / JELLYFIN_API_KEY not set")
        return False
    req = urllib.request.Request(
        f"{JELLYFIN_URL}/Library/Refresh",
        headers={"X-Emby-Token": JELLYFIN_API_KEY},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            log.info("Library/Refresh -> HTTP %s", resp.status)
            return resp.status in (200, 204)
    except Exception as exc:  # noqa: BLE001
        log.error("refresh failed: %s: %s", type(exc).__name__, exc)
        return False


def main() -> None:
    root = Path(WATCH_ROOT)
    if not root.is_dir():
        log.error("watch root %s is not a directory", root)
        raise SystemExit(1)

    handler = _Handler()
    observer = Observer()
    observer.schedule(handler, str(root), recursive=True)
    observer.daemon = True
    observer.start()
    log.info("watching %s (debounce=%ss, min_interval=%ss)", root, DEBOUNCE, MIN_INTERVAL)

    last_refresh = 0.0
    if STARTUP_REFRESH:
        log.info("startup refresh")
        if refresh():
            last_refresh = time.time()

    while True:
        now = time.time()
        if handler.last_event:
            quiet = now - handler.last_event
            since = now - last_refresh
            if quiet >= DEBOUNCE and since >= MIN_INTERVAL:
                if scan_is_running():
                    # Never cancel an in-flight scan. Leave last_event set and
                    # reset the interval clock so we retry in MIN_INTERVAL —
                    # coalesced backoff instead of the scan cancel-loop.
                    log.info("scan in progress, deferring refresh")
                    last_refresh = now
                    continue
                if refresh():
                    handler.last_event = 0.0
                    last_refresh = time.time()
                # on failure leave last_event set -> retry after MIN_INTERVAL
        time.sleep(5)


if __name__ == "__main__":
    main()
