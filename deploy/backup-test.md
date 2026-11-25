# Isolated backup rehearsal

These examples are not installed or enabled. They use `environment: test`.
Use fixture data first. Production requires the explicit safeguards described below;
never relabel a fixture configuration as a production configuration.

## Explicit sources and keys

The data root must contain `site_accounts.sqlite3`, `resources.sqlite3`, and
`test_features.sqlite3`. `newapi_sqlite` must identify the separate, actual SQLite
database used by NewAPI. Other SQLite files detected under configured sources also
receive SQLite snapshots. WAL/journal companions are not copied as ordinary files.
The entire data root, including attachments, is otherwise included.

List every application recovery key explicitly under `recovery_keys`; key discovery
is deliberately not automatic. These keys are encrypted inside the backup because
encrypted account/resource/OAuth records need the matching keys during recovery.
`extra_paths` maps explicit files or directories outside the data root, for example
Codex tips, images, external authorization files and required runtime configuration.
Mapping names are relative archive paths. No symbolic links, overlapping sources,
or `..` mappings are accepted. Configuration files must not contain the backup key.

The independent backup encryption key must be exactly 32 random bytes in a private
0600 file, outside every source, backup and restore directory. Provision and retain
it separately; losing it makes these backups unrecoverable. This command never
prints, uploads or automatically includes that key. Backup/restore roots must be
private 0700 directories. Do not use live application keys as the backup key.

## Rehearsal commands

Copy and adapt `backup-test.example.json` using fixture paths. With the local uv
environment, run the following from the project directory:

```sh
uv run --python .venv/bin/python python scripts/manage_backups.py --config /private/tmp/backup-test.json create
uv run --python .venv/bin/python python scripts/manage_backups.py --config /private/tmp/backup-test.json verify
uv run --python .venv/bin/python python scripts/manage_backups.py --config /private/tmp/backup-test.json status
uv run --python .venv/bin/python python scripts/manage_backups.py --config /private/tmp/backup-test.json restore --destination /private/tmp/isolated-restore/manual-check
uv run --python .venv/bin/python python scripts/manage_backups.py --config /private/tmp/backup-test.json run-due
```

Restore only creates a new child directory of `restore_root`. It does not overwrite
data, change paths in databases, start an application or switch any running service.
The restore/verify commands do not require the original source directories or
application recovery keys to still exist; they need the registered archives and
the separate backup decryption key. Backup creation still requires every source.
Weekly verification restores into a fresh `integrity-*` directory, verifies every
file and SQLite integrity/foreign keys, then removes only that temporary directory.
A manual recovery requires mapping `data/`, `newapi/`, `recovery/` and `extras/` back
to a separate runtime and checking absolute references/application decryption before
any eventual production recovery. Integrity verification is not a full login test.

## Consistency, scheduling and retention

Snapshots use SQLite's backup API and compare `data_version` on the same open
connections before/after copying. Entire ordinary-file inventories and hashes are
compared before/after, and copied hashes must match. A changed source retries at
most three times; ongoing writes can cause a safe failure. Application-level
multi-file transactions not yet completed before a snapshot cannot be inferred.

Each archive is AES-256-GCM encrypted in chunks and authenticated before extraction.
An archive is published only after a full isolated decrypt/restore check. Temporary
plaintext is inside private directories and cleaned on ordinary success/failure;
an abrupt process/host crash may leave a private temporary directory for manual
inspection. Allow space for source snapshots and isolated verification copies.

`run-due` is a standalone scheduler tick: daily 03:00 and Sunday 03:30 in
`Asia/Shanghai`. It catches up one latest missed period and does not repeat an
attempt in that period, including failed attempts. A manual `create` or `verify`
can retry after a failure. The two systemd files are examples only; do not install
or enable them for production in this change.

The application adapter `operational_backup_status.configure(app)` optionally runs
the same scheduler tick in a worker thread every 60 seconds. An explicit
`MAILAPI_MODE` and matching `MAILAPI_BACKUP_CONFIG` path are required. Production
also requires `MAILAPI_BACKUP_ENABLED=1`, configuration `environment: production`,
`production_enabled: true` and `fixture_only: false`. Unconfigured applications
have no backup task. On shutdown, an in-flight snapshot finishes its
cleanup before the task exits. Use either this adapter or the external timer; the
job lock also prevents simultaneous archive writes. For this round, configure only
the separate fixture directory. For production, use a separately reviewed source
inventory with the actual NewAPI database, runtime, client configuration and keys.
Create and verify an encrypted pre-deployment backup before enabling the schedule.

The adapter installs safe `backup_status_provider` and `backup_notice_provider`
callbacks for the administrator UI. Status corruption/configuration failures use a
separate stable condition key; a later successful backup/drill clears its failure
condition. Neither callback exposes configuration paths or key contents.
Set `fixture_only: true` for isolated sample configurations so the UI labels these
timestamps as sample rehearsals, not backups of live business data. The flag defaults
to false and accepts only JSON `true`. Unreadable status returns `available: false`
instead of presenting the configured plan as healthy.

After a successful backup, archives older than 30 days are removed only when their
names and hashes match this module's own success catalog. Unknown, changed and
unregistered files are never removed. The backup directory has an owner namespace;
it cannot be silently reused for another environment. No remote/offsite replication
is configured here; copy encrypted archives separately for host-loss protection.

`read_status(backup_root)` supplies safe administrator-only notification data:
`events[].{id,time,action,success,summary}`, `last_success` and `last_attempt`. Event IDs remain
stable after writes; the newest 100 events are retained. Failure summaries contain
no source paths, invoice values, usernames or key material. The application must
enforce administrator permissions when displaying these notifications.

## Explicit production activation

Production uses a separate private configuration with `environment: production`,
`production_enabled: true` and `fixture_only: false`; the application must also set
`MAILAPI_BACKUP_ENABLED=1`. A test process rejects production configuration and a
production process rejects fixture/test configuration. Missing or invalid
configuration reports an administrator-visible failure without blocking startup.
Keep the production backup key separate from application recovery keys. Use the
actual independent NewAPI SQLite path, never a test gateway database. Register the
runtime, matching application key, website configuration and gateway service
configuration. Preserve a matching source-code archive for recovery. First create
and verify a backup, then configure the application scheduler; do not enable the
example external timer at the same time. Recovery checks always use a new isolated
child directory and never switch running services or overwrite source data.
