# Mounting the Blackboard Content Collection (WebDAV) on the SPARK server

SPARK reads Blackboard exports from a read-only WebDAV mount of one Content
Collection folder, `SPARK`, and ingests new exports automatically.

```
Blackboard Content Collection            SPARK server (Linux VM)
  .../SPARK/                    davfs2     /mnt/bb/SPARK/            (read-only, user "spark")
    <COURSE_ID>/                ------->     <COURSE_ID>/gradebook/...
      gradebook/                                         grade_history/...
      grade_history/            poller     DATA_DIR/snapshots/...    (local copies, hashed)
      manifest/assignments.csv  ------->   DATA_DIR/courses/*.duckdb
```

Tested on Ubuntu 24.04 with davfs2 1.7.0 against a WebDAV test server. Tested: mounting,
new exports detected and ingested, a server outage, a frozen davfs2 process, a dropped
mount, and permissions. **Not yet tested against your Blackboard server.**

## 1. What you need from your Blackboard admin

| Item | Notes |
|---|---|
| WebDAV URL of the `SPARK` folder | Usually starts with `https://<your Blackboard host>/bbcswebdav/`. Mount only this folder, not the whole collection. |
| A service account | WebDAV signs in with a username and password (HTTP Basic). An account that can only sign in through SSO will not work; it needs a Blackboard password. **Read access** to the `SPARK` folder is enough. |
| Instructors' access | Each instructor needs write access to their own course folder under `SPARK` so the gradebook "Download" panel can save there. |

## 2. Install and mount

```bash
sudo apt install davfs2                      # answer "No" to letting unprivileged users mount
sudo useradd -r -s /usr/sbin/nologin spark   # if the SPARK service user doesn't exist yet
sudo mkdir -p /mnt/bb/SPARK

# Options for this mount only (includes the davfs2 1.7.0/1.7.1 listing-bug workaround):
sudo sh -c 'cat davfs2.conf.spark >> /etc/davfs2/davfs2.conf'

# Credentials (root only):
sudo sh -c 'echo "/mnt/bb/SPARK  svc-spark  \"THE-PASSWORD\"" >> /etc/davfs2/secrets'
sudo chmod 600 /etc/davfs2/secrets

# fstab: copy the line from fstab.example and put in your URL, then:
sudo systemctl daemon-reload
sudo systemctl restart mnt-bb-SPARK.automount
ls /mnt/bb/SPARK                             # first access mounts it
sudo -u spark ls /mnt/bb/SPARK/*/gradebook   # the service user can read the exports
```

`ro` in fstab means SPARK can never change anything in Blackboard. `uid=spark` plus
`file_mode=0440,dir_mode=0550` means only the `spark` user can read the student data.

## 3. Self-healing

```bash
sudo cp spark-mount-check.sh /usr/local/sbin/ && sudo chmod 755 /usr/local/sbin/spark-mount-check.sh
sudo cp spark-mount-check.service spark-mount-check.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now spark-mount-check.timer
```

Every 5 minutes the check makes sure the share is mounted and answering. If it isn't, the
script detaches it, stops the stuck davfs2 process and mounts it again. Every step has a
time limit so the check itself can't hang. A plain `umount` does hang in this situation,
because it waits for the frozen davfs2 process. Log: `journalctl -t spark-mount-check`.

## 4. SPARK settings (`.env`)

```
BB_ROOT=/mnt/bb
BB_REQUIRE_MOUNT=true      # an unmounted /mnt/bb/SPARK is an error, not "no courses"
BB_POLL_INTERVAL_S=300     # check for new exports every 5 minutes (0 = off)
BB_SETTLE_S=120            # ingest an export once it has been unchanged for 2 minutes
BB_IO_TIMEOUT_S=20         # give up on a mount call after 20 s
```

The poller runs inside the API by default. With several gunicorn workers, exactly one of
them polls (file lock in `DATA_DIR`). To run it as its own service instead, install
`spark-poller.service` and set `BB_POLL_INTERVAL_S=0` for the API.

For a single check from the command line: `python -m spark_api.poller --once` (from `backend/`).

**Docker:** mount on the host as above, then pass it into the container read-only with
propagation, so a remount on the host is visible inside the container:
`- type: bind, source: /mnt/bb, target: /mnt/bb, read_only: true, bind: {propagation: rslave}`

## 5. How SPARK reads the mount

- **Polling** lists folders and reads file size and modified time. It never downloads anything.
- **New export:** once it has been unchanged for `BB_SETTLE_S`, it is copied **once** into
  `DATA_DIR/snapshots`, hashed, and ingested from the local copy. If the file changes during
  the copy, the copy is thrown away and retried on the next poll.
- **Same content:** a re-saved export with identical content is recognised and not rebuilt.
- **Broken export:** if parsing fails, the failure is recorded once and not retried until a
  new file arrives. Admin > Blackboard mount > "Ingest now" forces a retry.
- **Blackboard down:** davfs2 keeps showing its cached files. SPARK also checks the server
  directly and shows "not answering" on the Admin page instead of pretending all is well.
- **New course folders** are picked up on the next poll. `lost+found`, which davfs2 creates,
  is ignored.

## 6. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `ls: reading directory ... Invalid argument` | davfs2 1.7.0/1.7.1 bug on newer kernels. `buf_size 64` in the mount section fixes it (included in `davfs2.conf.spark`). Remount after changing it. |
| `mount` asks for a password | `/etc/davfs2/secrets` is missing the line, or the first column doesn't match the mount point exactly. |
| `401 Unauthorized` | Wrong password, or the account is SSO-only (see section 1). |
| Admin page: "did not respond within 20 s" | davfs2 is hung. Wait for the timer, or run `sudo /usr/local/sbin/spark-mount-check.sh`. |
| Admin page: "is not mounted" | The share dropped. Same fix as the row above. |
| A new export doesn't appear | Folder listings are cached for up to `dir_refresh` (60 s), then the export must settle for `BB_SETTLE_S`. "Check for new exports now" on the Admin page polls immediately. |
| `Permission denied` for SPARK | SPARK must run as the `uid=` user from fstab (`spark`). |
