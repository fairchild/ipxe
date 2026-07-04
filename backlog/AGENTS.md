# backlog/

`CLAUDE.md` here is a symlink to this file — read one, not both.

Deferred work, one markdown file per task. Location = status:

- `todo/`  — available
- `doing/` — claimed, in flight
- `done/`  — completed (and cancelled — discriminated by the `cancelled` log line)

Use the `backlog` skill (add / advance / progress / cancel / fail / rescue / retry / maintain / status) to interact. Schema: the `backlog` skill's `references/agents-schema.md`.

## Backend

`maildir-git` — everything in this directory is committed to git; claim is `git mv`. See the `backlog` skill's `references/backends/maildir-git.md`.

## Defaults

- `priority: 999` (declare to drive auto-pick ordering)
- `timeout: 7d`
- `dependencies: {}`

## Pipeline

`todo → doing → done`

## ROADMAP

Strategic counterpart at `backlog/ROADMAP.md`. See the `backlog` skill's `references/roadmap.md`.
