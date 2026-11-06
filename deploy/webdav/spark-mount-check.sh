#!/usr/bin/env bash
# Remount the Blackboard WebDAV share if it has stopped answering.
# Run from spark-mount-check.timer (every 5 minutes) as root.
#
# A hung davfs2 mount can't be fixed with a plain "umount": that calls umount.davfs,
# which waits for the stuck mount.davfs process forever. So: detach lazily without the
# helper (-i), stop the mount.davfs process for this mount point, then mount again.
# Every step has a time limit so this script itself can never hang.
set -u
MP="${SPARK_MOUNT:-/mnt/bb/SPARK}"
TIMEOUT="${SPARK_MOUNT_TIMEOUT:-20}"
PIDFILE="/var/run/mount.davfs/$(echo "${MP#/}" | tr '/' '-').pid"
log() { logger -t spark-mount-check "$*"; echo "$*"; }

# Healthy = it is a mount AND it answers. (An empty folder left behind by a dropped mount
# also answers "ls", so the mountpoint check matters.) The ls also triggers the automount.
if timeout "$TIMEOUT" ls "$MP" >/dev/null 2>&1 && timeout 5 mountpoint -q "$MP"; then
  exit 0
fi

log "$MP not mounted or not responding within ${TIMEOUT}s; remounting"
PID=$(cat "$PIDFILE" 2>/dev/null || pgrep -f "mount.davfs .* $MP( |$)" | head -1)
timeout 10 umount -l -i "$MP" 2>/dev/null || true # lazy detach, bypassing umount.davfs
if [ -n "${PID:-}" ] && kill -0 "$PID" 2>/dev/null; then
  kill -TERM "$PID" 2>/dev/null; kill -CONT "$PID" 2>/dev/null
  for _ in 1 2 3 4 5; do kill -0 "$PID" 2>/dev/null || break; sleep 1; done
  kill -0 "$PID" 2>/dev/null && kill -KILL "$PID" 2>/dev/null
fi
rm -f "$PIDFILE"

if timeout 60 mount "$MP" && timeout 5 mountpoint -q "$MP" && timeout "$TIMEOUT" ls "$MP" >/dev/null 2>&1; then
  log "$MP remounted"
  exit 0
fi
log "$MP remount FAILED (check network, credentials in /etc/davfs2/secrets, and: journalctl -t mount.davfs)"
exit 1
