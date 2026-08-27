# Workspace CLI

`scripts/workspace.py` is the dependency-free local contract shared by the
Skill and a future frontend. It manages project workspaces; exact cross-project
personal facts continue to use `scripts/store.py`.

## Storage location

Workspace roots resolve in this order:

1. `MWORK_WORKSPACES_DIR` when explicitly set as a compatibility override;
2. `${MEMORY_HOME}/workspaces` when `MEMORY_HOME` is set;
3. `~/.memory-home/workspaces` by default.

The CLI refuses writes under a temporary directory. Tests may explicitly set
`MWORK_ALLOW_TRANSIENT=1`; production workflows must not use that override.

Initialize Memory Home, then run `doctor` before the first Workspace write in a
new environment:

```bash
python3 <skill-dir>/scripts/home.py init
python3 <skill-dir>/scripts/workspace.py doctor
```

## Commands

```bash
# Initialize a durable project workspace.
python3 <skill-dir>/scripts/workspace.py init product-research \
  --name "Product Research"

# Discover and inspect workspaces.
python3 <skill-dir>/scripts/workspace.py list
python3 <skill-dir>/scripts/workspace.py inspect product-research

# Copy one manual file into immutable raw storage and create S-001.
python3 <skill-dir>/scripts/workspace.py source ingest \
  product-research /absolute/path/to/source.md --title "Source title"

# Validate manifest, required layout, raw hashes, Wiki links, and operations.
python3 <skill-dir>/scripts/workspace.py check product-research

# Stage, review, approve, and apply an Agent-authored Wiki page.
python3 <skill-dir>/scripts/workspace.py operation propose-file \
  product-research wiki/topics/example.md \
  --content-file /path/to/candidate.md \
  --input-ref wiki/sources/S-001.md
python3 <skill-dir>/scripts/workspace.py operation show \
  product-research <operation-id>
python3 <skill-dir>/scripts/workspace.py operation approve \
  product-research <operation-id>
python3 <skill-dir>/scripts/workspace.py operation apply \
  product-research <operation-id>

# Rebuild the disposable UI index and query local knowledge.
python3 <skill-dir>/scripts/workspace.py index rebuild product-research
python3 <skill-dir>/scripts/workspace.py query product-research "example"
```

Add `--json` after any leaf command for the stable machine-readable envelope:

```json
{
  "ok": true,
  "command": "source.ingest",
  "result": {}
}
```

Errors use the same envelope with `ok: false` and a human-readable `error`.
`check` exits non-zero when validation returns `FAILED`.

## Ingest guarantees

- Source bytes are copied once under `raw/inbox/`; an existing file is never
  replaced.
- SHA-256 is recorded in the Source Note and checked by `check`.
- Identical content is idempotent: the existing Source ID is returned and no
  new operation is created.
- A filename collision with different content receives a hash suffix.
- The initial Source Note records capture metadata only. It does not invent
  claims, citations, dates, or synthesis.
- Each explicit mutating CLI call records an approved, applied Operation. The
  CLI invocation is the owner's authorization; agent-authored synthesis should
  still follow the workspace's configured review mode.
- Files containing a recognizable private-key header are rejected. The
  workspace is not a secret vault.

## Scope of this MVP

This version implements initialization, inspection, structural/integrity
validation, manual source capture, reviewable Markdown changes, a disposable
read model, and local query. See `references/review-index.md` for the lifecycle
and UI contract. Connector refresh, automatic Claim synthesis, and the frontend
remain later layers built on the same manifests and operation records.
