# Memory Home

Status: Implemented layout and migration foundation v0.1
Updated: 2026-08-27

Memory Home is the single durable root for a user's local memory system. Its
default path is `~/.memory-home/`. Personal Memory and Workspaces are sibling
knowledge scopes; runtime queues and indexes live under a separate system layer.

## Layout

```text
~/.memory-home/
├── memory-home.json
├── personal/
│   ├── index.md
│   ├── profile/exact.json
│   ├── work/
│   │   ├── overview.md
│   │   ├── portfolio.md
│   │   ├── people/
│   │   ├── themes/
│   │   └── timeline/
│   ├── preferences/
│   ├── learning/{query-habits.md,policies/,policy-activations/}
│   └── captures/
├── workspaces/
│   └── <workspace-id>/
└── system/
    ├── capture/
    ├── operations/
    ├── index/
    ├── connectors/
    └── migrations/
```

- `personal/` answers user-centered questions across Workspaces.
- `workspaces/` keeps source-backed, project or domain-centered knowledge.
- `system/` is control state, not long-term knowledge.
- Exact profile fields remain JSON under `personal/profile/`; they are one
  Personal Memory subtype rather than a separate memory product.

## Commands

```bash
python3 scripts/home.py doctor --json
python3 scripts/home.py status --json
python3 scripts/home.py init --json
```

`status` is read-only. `init` creates missing standard directories and starter
Markdown pages without replacing existing files. `MEMORY_HOME` may override the
root; component-specific `PMEM_*` and `MWORK_*` variables remain compatibility
overrides but can produce a split layout.

## Legacy migration

Earlier releases used both `~/.personal-memory/` and `~/.memory-workspace/`.
Migration is always two-step and copy-only:

```bash
python3 scripts/home.py migration-plan --json
python3 scripts/home.py migrate --json
```

The plan maps:

| Legacy source | Memory Home target |
|---|---|
| `~/.personal-memory/store.json` | `personal/profile/exact.json` |
| `~/.personal-memory/workspaces/` | `workspaces/` |
| `~/.memory-workspace/capture/` | `system/capture/` |

Within the legacy Capture root, `learning/`, `policies/`, and
`policy-activations/` are routed to `personal/learning/`; the remaining queue
and audit files are routed to `system/capture/`.

Migration rejects symbolic links and any different-content target conflict
before copying. Identical files are skipped. Every copied file is hash-verified,
and a receipt is written under `system/migrations/`. Source files are never
deleted; cleanup, if ever desired, is a separate explicit user action.

## Ownership rules

- Workspace evidence remains canonical for project facts.
- Personal Work pages are cross-Workspace projections and link to Workspace
  evidence or owner captures instead of copying source dumps.
- Exact Profile accepts explicit personal facts and preserves exact readback.
- One Candidate has one write target. The same evidence may create separate
  Personal and Workspace Candidates so each change can be reviewed independently.

Candidate schema v3 represents `scope=personal|workspace`. The current Writer
still executes legacy exact-profile and Workspace candidates; reviewed Personal
Work page application is the next implementation layer and must not be claimed
as complete yet.
