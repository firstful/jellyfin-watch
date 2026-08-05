# jellyfin-watch

NFS inotify watcher for the Jellyfin media tree.

Jellyfin's realtime library monitor uses inotify, which NFS does not deliver
to client pods — so new files on the RAID store never trigger an index update
on their own. This watcher runs as a k3s Deployment pinned to sutliffserver
(the NFS server, where the tree is **local** and inotify works) and POSTs a
debounced `POST /Library/Refresh` to Jellyfin whenever the media tree changes.

- Covers every publisher: cs-downloader→media-ingest moves, yt-channel-sync,
  manual SMB/NFS drops, renames, deletes.
- Debounce 20s quiet period after the last event; 60s minimum between
  refreshes (anti-hammer); startup refresh covers downtime.
- Config via env: `WATCH_ROOT`, `JELLYFIN_URL`, `JELLYFIN_API_KEY`,
  `DEBOUNCE_SECONDS`, `MIN_INTERVAL_SECONDS`, `STARTUP_REFRESH`.

Deploy: see `apps/jellyfin-watch/` in `reeds/sutliff-cluster` (hostPath
`/mnt/raid_store/jellyfin` read-only, CP toleration, API key secret
`jellyfin-api-key` created out-of-band like yt-sync's).
