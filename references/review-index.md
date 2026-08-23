# Review workflow and derived index

Read this reference when proposing Agent-authored Wiki changes, reviewing an
Operation, or building a UI/query consumer.

## Review lifecycle

Explicit `init` and manual `source ingest` commands remain direct, approved
actions. Agent-authored changes to maintained Wiki pages use this lifecycle:

```text
candidate Markdown
      ↓
proposed ─────→ rejected
      ↓
approved
      ↓
applied

approved ── stale hash / invalid artifact ──→ remains approved, no write
approved ── apply or workspace check error ─→ failed, canonical files rolled back
```

### Create or update a page

```bash
python3 <skill-dir>/scripts/workspace.py operation propose-file \
  product-research wiki/topics/example.md \
  --content-file /path/to/candidate.md \
  --input-ref wiki/sources/S-001.md \
  --json
```

The target must be a Markdown file under `wiki/projects/`, `wiki/topics/`,
`wiki/entities/`, or `wiki/syntheses/`. `wiki/index.md` and `wiki/log.md` may be
updated but not deleted. `raw/` and `wiki/sources/` are not mutable through a
Proposal.

### Delete a maintained page

```bash
python3 <skill-dir>/scripts/workspace.py operation propose-file \
  product-research wiki/topics/obsolete.md --delete --json
```

The original bytes remain in the Operation artifact directory for review and
rollback.

### Review and decide

```bash
python3 <skill-dir>/scripts/workspace.py operation list \
  product-research --status proposed --json
python3 <skill-dir>/scripts/workspace.py operation show \
  product-research <operation-id> --json
python3 <skill-dir>/scripts/workspace.py operation approve \
  product-research <operation-id> --json
python3 <skill-dir>/scripts/workspace.py operation reject \
  product-research <operation-id> --json
python3 <skill-dir>/scripts/workspace.py operation apply \
  product-research <operation-id> --json
```

`show` returns the validated Operation plus unified diffs. `approve` records the
deciding actor and time but does not write canonical pages. `apply` verifies
every `before_hash` and staged `after_hash`, writes the approved changes, runs a
full Workspace check, and rebuilds the derived index. A stale hash blocks all
writes. A post-write validation failure triggers rollback and marks the
Operation failed.

Operation artifacts use this convention:

```text
.llm-wiki/operations/
├── op_<id>.json
└── op_<id>/
    ├── 000.before
    ├── 000.after
    └── 000.diff
```

## Derived read model

The UI/search index is a disposable projection, not canonical knowledge:

```text
llm-wiki.json + wiki/ + Operation manifests
                    ↓ index rebuild
.llm-wiki/index/workspace-index.json
                    ↓
            CLI query / future UI
```

Rebuild and query it with:

```bash
python3 <skill-dir>/scripts/workspace.py index rebuild product-research --json
python3 <skill-dir>/scripts/workspace.py query \
  product-research "review workflow" --limit 20 --json
```

`query` rebuilds by default so results reflect canonical files and current
Operation statuses. `--no-rebuild` reads the existing projection. Results are
ranked local matches across projects, Source metadata, Wiki content, and
Operation metadata. A consumer should use result paths to read only the minimum
canonical pages needed for the task.

The index may contain private Wiki text and therefore remains local. It may be
deleted and rebuilt at any time; the frontend must never write canonical data
by editing the index.
