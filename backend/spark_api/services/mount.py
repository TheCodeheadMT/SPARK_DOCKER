"""Timeouts and health checks for the Blackboard WebDAV mount.

A davfs2 mount whose server has gone away can block a stat() or listdir() for minutes.
Every poller call to the mount therefore runs on one dedicated thread with a timeout.
If a call hangs, later calls queue behind it and time out too, so at most one thread
is ever stuck; the mount is reported as not responding until that call returns.
"""
from __future__ import annotations

import concurrent.futures as cf
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TypeVar

from adapters.bb_ultra.adapter import ExportInProgress, is_mount_error

T = TypeVar("T")

REMOUNT_HINT = "On the server: sudo umount -l {mp} && sudo mount {mp}   (see deploy/webdav/README.md)"


class MountTimeout(TimeoutError):
    pass


class MountIO:
    def __init__(self, timeout_s: float):
        self.timeout_s = timeout_s
        self._pool = cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix="bb-mount")
        self._busy_since: float | None = None
        self._lock = threading.Lock()

    def call(self, fn: Callable[..., T], *args, timeout_s: float | None = None) -> T:
        def wrapped():
            with self._lock:
                self._busy_since = time.monotonic()
            try:
                return fn(*args)
            finally:
                with self._lock:
                    self._busy_since = None
        fut = self._pool.submit(wrapped)
        try:
            return fut.result(timeout=timeout_s or self.timeout_s)
        except cf.TimeoutError:
            raise MountTimeout(f"The Blackboard mount did not respond within {timeout_s or self.timeout_s:.0f} s")

    @property
    def stuck_for_s(self) -> float | None:
        with self._lock:
            return None if self._busy_since is None else time.monotonic() - self._busy_since


@dataclass
class MountStatus:
    configured: bool
    root: str | None
    mount_point: str | None = None
    is_mount: bool | None = None
    fstype: str | None = None
    is_webdav: bool | None = None
    server_ok: bool | None = None            # the WebDAV server itself answered (not just davfs2's cache)
    server_ms: float | None = None
    reachable: bool = False
    latency_ms: float | None = None
    courses: int | None = None
    error: str | None = None
    hint: str | None = None
    checked_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def dict(self) -> dict:
        return asdict(self)


def _mount_point(spark_dir: Path) -> Path:
    """Nearest mount point at or above the SPARK folder (the davfs2 mount)."""
    p = spark_dir.resolve()
    while not os.path.ismount(p) and p != p.parent:
        p = p.parent
    return p


def mount_info(mp: Path) -> tuple[str | None, str | None]:
    """(fstype, source) for a mount point from /proc/mounts; (None, None) elsewhere."""
    try:
        with open("/proc/mounts") as f:
            rows = [line.split() for line in f]
    except OSError:
        return None, None
    for src, target, fstype, *_ in reversed(rows):            # last entry wins (stacked mounts)
        if target.replace("\\040", " ") == str(mp):
            return fstype, src
    return None, None


def check_server(url: str, timeout_s: float = 5.0) -> tuple[bool, float | None, str | None]:
    """Any HTTP answer (even 401) means the server is up. Credentials are never sent."""
    import requests
    t0 = time.perf_counter()
    try:
        requests.request("OPTIONS", url, timeout=timeout_s, allow_redirects=False)
        return True, round((time.perf_counter() - t0) * 1000, 1), None
    except requests.RequestException as exc:
        return False, None, type(exc).__name__


def describe_error(exc: BaseException, mp: str | None) -> tuple[str, str | None]:
    if isinstance(exc, MountTimeout):
        return str(exc), REMOUNT_HINT.format(mp=mp or "/mnt/bb/SPARK")
    if is_mount_error(exc):
        return f"Mount error: {exc.strerror or exc}", REMOUNT_HINT.format(mp=mp or "/mnt/bb/SPARK")
    if isinstance(exc, ExportInProgress):
        return str(exc), None
    if isinstance(exc, PermissionError):
        return f"Permission denied: {exc.filename}", "Check uid/gid/file_mode on the mount and the service user."
    return f"{type(exc).__name__}: {exc}", None


def probe(io: MountIO, adapter, require_mount: bool = False) -> MountStatus:
    if adapter is None or adapter.root is None:
        return MountStatus(configured=False, root=None, error="BB_ROOT is not set")
    spark = adapter.root
    st = MountStatus(configured=True, root=str(spark))

    def _check():
        mp = _mount_point(spark) if spark.exists() else None
        t0 = time.perf_counter()
        if not spark.is_dir():
            raise FileNotFoundError(f"{spark} does not exist (is the WebDAV share mounted?)")
        n = len(adapter.list_courses())
        return mp, (time.perf_counter() - t0) * 1000, n

    try:
        mp, ms, n = io.call(_check)
        st.mount_point = str(mp) if mp else None
        st.is_mount = bool(mp and mp != Path(mp.anchor))     # "/" itself is the root fs, not our mount
        if st.is_mount:
            st.fstype, src = mount_info(mp)
            st.is_webdav = bool(src and src.lower().startswith(("http://", "https://")))
            if st.is_webdav:
                st.server_ok, st.server_ms, why = check_server(src)
                if not st.server_ok:
                    st.error = (f"The Blackboard WebDAV server is not answering ({why}). davfs2 is serving cached "
                                "files, so new exports will not appear until it is back.")
                    st.hint = "Check the server's network access to Blackboard; the mount recovers on its own."
        st.reachable, st.latency_ms, st.courses = True, round(ms, 1), n
        if not st.is_mount and require_mount:
            st.reachable, st.courses = False, None
            st.error = f"{spark} is not mounted (BB_REQUIRE_MOUNT is on). The WebDAV share is down or was unmounted."
            st.hint = "On the server: sudo mount " + str(spark) + "   or run /usr/local/sbin/spark-mount-check.sh"
        elif not st.is_mount:
            st.hint = "SPARK is a local folder, not a mount (fine for development)."
        elif st.is_webdav is False:
            st.hint = f"{mp} is a {st.fstype or 'non-WebDAV'} mount, not the davfs2 share. Check BB_ROOT."
    except Exception as exc:  # noqa: BLE001  surfaced to admins
        st.error, st.hint = describe_error(exc, st.mount_point)
    return st
