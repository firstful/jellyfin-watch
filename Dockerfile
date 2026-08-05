FROM python:3.11-slim

RUN pip install --no-cache-dir watchdog==6.0.0

WORKDIR /app
COPY watcher.py .

ENV WATCH_ROOT=/mnt/media \
    DEBOUNCE_SECONDS=20 \
    MIN_INTERVAL_SECONDS=60 \
    STARTUP_REFRESH=1

# uid 911 = Jellyfin uid; the media tree is world/ACL readable to it.
USER 911:911

CMD ["python", "-u", "watcher.py"]
