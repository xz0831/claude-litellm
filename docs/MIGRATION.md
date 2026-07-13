# Migration

The old installations used `~/.local/share/ai-litellm-fabric` or
`~/.local/share/ai-litellm`. The new package uses
`~/.local/share/claude-litellm`.

Migration preserves durable Claude state:

- project transcripts and auto-memory under `projects/`
- `history.jsonl`
- isolated `.claude.json` metadata
- the package `state/ai-litellm/env` file (including existing provider/master
  keys), preserved with mode `0600` when it does not conflict

It does not migrate generated settings overlays, caches, session environments,
OAuth token state, runtime virtual environments or Codex state. Old Codex state
is included in the cutover backup but native `~/.codex` is never changed.

The installer first stops only a verified prefix-owned proxy, validates and
publishes the package, isolated runtime, and public shim on one rollback
boundary, then invokes the migrator. The migrator fully backs up each recognized
legacy package, builds a strict merge plan, rejects differing destination
collisions, backs up selected destination state, copies and byte-verifies the
merged durable state, and only then removes the legacy package. Master-key setup
runs after migration so a migrated key wins. Migration, key setup, and proxy
restart affect durable/external state after package commit; a failure is reported
as an explicit post-install warning rather than falsely reporting that the
already-committed package install rolled back. Keychain entries are preserved
unless the user explicitly requests credential deletion. Post-install smoke
tests are an operator/CI step, not part of the mutating installer transaction.

When the installer removes a recognized legacy package, it first copies and
byte-verifies the complete package under
`~/.local/share/claude-litellm-backups/<UTC timestamp>-<pid>/`. This rollback
copy includes Codex state, old virtual environments, overlays, and logs even
though those items are intentionally excluded from the new Claude-only state.
