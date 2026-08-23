# LLM Wiki Data Model

Status: Draft v0.2
Updated: 2026-08-22

This document defines the data contract shared by an LLM Wiki workspace, its
agent Skill, and its user interface. It extracts the reusable model from the
current project-centered workspace and the `personal-memory` exact-recall
model. It is intentionally independent of a particular LLM, UI framework, or
external connector.

## Contents

1. [Goals](#1-goals)
2. [Mental model](#2-mental-model)
3. [Canonical and derived data](#3-canonical-and-derived-data)
4. [Recommended workspace layout](#4-recommended-workspace-layout)
5. [Shared conventions](#5-shared-conventions)
6. [Core resources](#6-core-resources)
7. [Relationships](#7-relationships)
8. [Operation semantics](#8-operation-semantics)
9. [Privacy classes and export](#9-privacy-classes-and-export)
10. [Validation invariants](#10-validation-invariants)
11. [Skill and UI contract boundary](#11-skill-and-ui-contract-boundary)
12. [Compatibility with the current workspace](#12-compatibility-with-the-current-workspace)

## 1. Goals

The model MUST support:

- immutable source material and connector snapshots;
- project-centered organization without treating a chat or document as the
  project itself;
- persistent Markdown knowledge maintained by an agent;
- claim-level separation of fact, inference, owner view, and open question;
- exact, lossless personal facts that can be recalled across workspaces;
- explicit source scope, capture time, coverage, and uncertainty;
- reviewable agent changes for a frontend UI;
- local-first storage and safe public export.

The model MUST NOT treat the Wiki as a password manager or secret vault.
Passwords, one-time codes, private keys, and seed phrases are prohibited data.

## 2. Mental model

There are two storage scopes:

```text
User
├── Profile Memory Store             cross-workspace, exact personal facts
└── Workspaces[]                     one private knowledge environment each
    ├── Projects[]
    ├── Sources[] → Snapshots[]
    ├── Wiki Pages[] → Claims[]
    ├── Personal Authoring[]
    ├── Source Mappings[]
    └── Operations[]
```

The Profile Memory Store and Workspace Personal Authoring are deliberately
different:

| Store | Purpose | Example | Canonical form |
|---|---|---|---|
| Profile Memory Store | Exact facts reused across workspaces | an email address or a structured experience entry | JSON |
| Personal Authoring | Thoughts tied to a workspace or project | a concern, hypothesis, reflection, or preference | Markdown |

A workspace MAY read selected Profile Memory fields for an authorized task. It
MUST NOT copy the entire profile into the workspace or silently write newly
observed values back to Profile Memory.

## 3. Canonical and derived data

Each datum has exactly one canonical representation. Other representations are
views and MUST be rebuildable.

| Data | Canonical representation | Mutability |
|---|---|---|
| Manually added source | Original file under `raw/` | Immutable |
| Connector result | Timestamped snapshot under `connected/` | Immutable |
| Source description | Markdown source note under `wiki/sources/` | Maintained |
| Project knowledge | Markdown under `wiki/projects/` | Maintained |
| Topic, entity, synthesis | Markdown under `wiki/` | Maintained |
| Owner capture or reflection | Markdown under `personal/` | Append or explicitly edit |
| Exact personal fact | JSON in the external Profile Memory Store | Explicit mutation only |
| Source authorization | YAML under `config/` | Explicit mutation only |
| Search/UI index | SQLite, JSON, or another generated cache | Derived and disposable |
| Agent proposal | Operation manifest plus file diff | Immutable after proposal |

The frontend MUST NOT become a second source of truth. It reads canonical files
and generated indexes, and writes through reviewed operations.

## 4. Recommended workspace layout

```text
workspace/
├── llm-wiki.json
├── raw/
│   └── inbox/
├── connected/
│   └── <connector>/
│       ├── snapshots/
│       └── manifests/
├── config/
│   └── projects/
├── personal/
│   ├── captures/
│   ├── reflections/
│   └── preferences/
├── wiki/
│   ├── index.md
│   ├── log.md
│   ├── sources/
│   ├── projects/
│   ├── topics/
│   ├── entities/
│   └── syntheses/
└── .llm-wiki/
    ├── index/                       generated, disposable
    └── operations/                  review and audit manifests
```

The Profile Memory Store lives outside any workspace so that it is durable and
reusable across projects. Its path MUST be resolvable and diagnosable. A Skill
MUST reject accidental writes to a temporary directory unless the operation is
an explicit test.

## 5. Shared conventions

### 5.1 Identifiers

Every independently referenced resource MUST have a stable ID. IDs are opaque;
display names are allowed to change.

| Resource | Recommended form | Current compatible form |
|---|---|---|
| Workspace | stable lowercase slug | repository directory name |
| Project | stable lowercase slug | `project-slug` |
| Source | workspace-unique ID | `S-001` |
| Snapshot | connector ID plus capture timestamp | timestamped path |
| Page | stable ID or canonical relative path | Wiki relative path |
| Claim | page-local or workspace-unique stable ID | optional in current pages |
| Memory item | stable ID plus exact human label | current exact key |
| Operation | sortable unique ID | timestamp plus operation type |

External object IDs MUST NOT be used as internal primary keys because providers
can change and public exports must be able to omit them.

### 5.2 Time

- Machine-readable timestamps MUST use ISO 8601 with timezone.
- A date-only field MUST use `YYYY-MM-DD`.
- `captured_at` describes when content was retrieved or stated.
- `coverage` describes the time or version represented by the content.
- `updated_at` describes the local record change, not source freshness.

### 5.3 Status and deletion

Records SHOULD be superseded or archived instead of silently overwritten when
history matters. Destructive deletion requires an explicit target and user
confirmation. Immutable source files and connector snapshots MUST never be
rewritten in place.

## 6. Core resources

### 6.1 Workspace manifest

`llm-wiki.json` identifies the workspace and supported schema version. JSON is
the reference serialization so the local Skill remains dependency-free; other
implementations MAY expose an equivalent YAML projection.

```yaml
schema_version: 1
workspace:
  id: example-workspace
  name: "Example Workspace"
  created_at: 2026-08-22T10:00:00+08:00
defaults:
  language: zh-CN
  timezone: Asia/Shanghai
  review_mode: proposed_changes
profile_memory:
  enabled: true
  provider: personal-memory
```

The manifest MUST NOT contain credentials or connector access tokens.

### 6.2 Project

A Project is the primary unit of work. Chats, documents, tickets, and personal
notes are context or evidence for a Project; none is the Project itself.

Required logical fields:

| Field | Type | Meaning |
|---|---|---|
| `id` | string | Stable project slug |
| `name` | string | Human-readable name |
| `status` | enum | `initializing`, `active`, `paused`, `complete`, `archived` |
| `objective` | text | Intended outcome |
| `scope` | text | Included and excluded work |
| `created_at` | timestamp | Creation time |
| `updated_at` | timestamp | Last local update |
| `source_refs` | list | Authorized source relationships |

The recommended Markdown projection is a directory containing `overview.md`,
`context.md`, `decisions.md`, `execution.md`, `owner-notes.md`, and `sources.md`.
Empty pages SHOULD NOT be created merely to satisfy the projection.

### 6.3 Source mapping

A Source Mapping authorizes which external resources may contribute to a
Project. Discovery, membership, or keyword similarity does not create
authorization.

```yaml
project_id: example-project
connector: example-provider
resource_type: chat
external_ref: opaque-provider-reference
relationship: direct_execution
scope: "Delivery decisions and progress for this project"
sync:
  mode: manual
  default_window_days: 14
```

`relationship` SHOULD use one of:

- `direct_execution`: task, decision, delivery, and progress evidence;
- `macro_context`: strategy, policy, resourcing, or cross-project dependency;
- `formal_constraint`: requirements, reviews, policies, or approved plans;
- `delivery_state`: current milestone, issue, owner, due date, or status;
- `other`: explicitly described custom relationship.

Mappings MUST contain the minimum useful scope and MUST NOT include secrets.

### 6.4 Source

A Source represents an evidence object. Its bytes live in an immutable file or
snapshot; its description lives in a source note.

Required logical fields:

```yaml
schema_version: 1
id: S-001
kind: manual_file
title: "Example source"
content_path: raw/inbox/example.md
external_ref: null
captured_at: 2026-08-22T10:00:00+08:00
coverage:
  type: document_version
  value: "version available at capture time"
content_hash: "sha256:..."
status: processed
limitations: []
```

`kind` is extensible. Initial values are `manual_file`, `chat`, `document`,
`meeting`, `task_system`, `web`, `owner_statement`, and `other`.

`content_hash` is RECOMMENDED for integrity and deduplication. `limitations`
MUST record partial results, missing permissions, truncation, or uncertain
coverage. The absence of an item from a retrieved scope is not proof that it
never existed.

### 6.5 Snapshot manifest

Every connector retrieval creates a new immutable snapshot and manifest.

```yaml
schema_version: 1
snapshot_id: example-provider-20260822T100000+0800
source_id: S-002
connector: example-provider
resource_type: chat
external_ref: opaque-provider-reference
captured_at: 2026-08-22T10:00:00+08:00
coverage:
  from: 2026-08-08T00:00:00+08:00
  to: 2026-08-22T10:00:00+08:00
result_count: 42
snapshot_path: connected/example-provider/snapshots/example.jsonl
content_hash: "sha256:..."
limitations: []
```

A refresh MUST create a new snapshot rather than replacing an older one.

### 6.6 Wiki page

Wiki pages are Markdown documents with short YAML frontmatter.

Common logical fields:

```yaml
schema_version: 1
id: topics/example-topic
kind: topic
title: "Example topic"
created_at: 2026-08-22T10:00:00+08:00
updated_at: 2026-08-22T10:00:00+08:00
status: current
project_refs: [example-project]
source_refs: [S-001]
privacy: workspace_private
```

Initial `kind` values are `source`, `project_overview`, `project_context`,
`project_decisions`, `project_execution`, `project_owner_notes`, `topic`,
`entity`, and `synthesis`.

The Markdown body remains flexible. Material statements are normalized into
Claims by the Skill or generated index.

### 6.7 Claim

A Claim is the smallest evidence-bearing statement shown by the UI.

| Field | Type | Meaning |
|---|---|---|
| `id` | string | Stable within its page or workspace |
| `page_ref` | string | Owning Wiki page |
| `type` | enum | `fact`, `inference`, `owner_view`, `open_question` |
| `text` | string | Statement shown without silent rewriting |
| `source_refs` | list | Supporting or conflicting sources |
| `owner_capture_ref` | string or null | Required source for an owner view when available |
| `status` | enum | `current`, `contested`, `superseded`, `resolved` |
| `confidence` | enum or null | Optional explicit assessment, never fabricated |
| `updated_at` | timestamp | Last local change |

Rules:

- `fact` MUST reference at least one Source.
- `inference` MUST be labelled as inference and reference its supporting
  Sources.
- `owner_view` MUST be labelled as the owner's judgment, hypothesis, concern,
  or preference. It MUST NOT be presented as an external fact.
- `open_question` MUST state what evidence is missing or what would resolve it.
- Conflicting claims MUST coexist until explicitly resolved or superseded.

In v0.1, Claim objects MAY be generated from Markdown rather than stored as
separate files. Generated claims are an index projection, not a second source
of truth.

### 6.8 Personal authoring record

Personal Authoring preserves the owner's wording and its relationship to a
workspace or project.

```yaml
schema_version: 1
id: capture-20260822-001
kind: capture
created_at: 2026-08-22T10:00:00+08:00
project_refs: [example-project]
status: captured
```

Initial `kind` values are:

- `capture`: minimally edited original wording;
- `reflection`: evolving analysis separating observation, hypothesis, and
  conclusion;
- `preference`: `tentative` or explicitly `confirmed`.

A repeated pattern MUST NOT become a confirmed preference without explicit
owner confirmation.

### 6.9 Profile Memory item

Profile Memory stores exact personal facts outside the workspace. JSON is the
canonical form because it supports exact strings and structured one-to-many
records. Markdown export is a view.

```json
{
  "schema_version": 2,
  "updated_at": "2026-08-22T10:00:00+08:00",
  "items": {
    "contact-email": {
      "id": "mem-contact-email",
      "label": "联系邮箱",
      "type": "single",
      "value": "person@example.com",
      "scope": {},
      "provenance": {
        "kind": "owner_statement",
        "captured_at": "2026-08-22T10:00:00+08:00"
      },
      "verified_at": "2026-08-22T10:00:00+08:00"
    },
    "experience": {
      "id": "mem-experience",
      "label": "经历",
      "type": "entries",
      "value": [
        {
          "organization": "Example Organization",
          "role": "Example Role",
          "period": "2025.01-2025.06"
        }
      ],
      "scope": {},
      "provenance": {
        "kind": "owner_statement",
        "captured_at": "2026-08-22T10:00:00+08:00"
      },
      "verified_at": "2026-08-22T10:00:00+08:00"
    }
  }
}
```

Profile Memory rules:

- Values MUST round-trip exactly; no spelling, punctuation, capitalization, or
  numeric normalization is allowed without owner confirmation.
- `single` stores one exact string. `entries` stores an ordered array of
  structured records.
- Values that differ by edition, account, organization, or context MUST use an
  explicit `scope` or an unambiguous label. They MUST NOT overwrite each other.
- Reads MUST request only the fields needed for the current task.
- Recall is read-only. A task that uses a fact MUST NOT update it implicitly.
- Writes MUST be read back and compared after persistence.
- Overwrites SHOULD preserve a recoverable previous version.
- Large imports SHOULD use a review checklist before persistence.
- Unknown or conflicting values MUST be marked for confirmation rather than
  guessed.

Schema version 1 of the existing `personal-memory` store remains compatible as
an input adapter:

```json
{
  "schema_version": 1,
  "updated_at": 1721600000,
  "items": {
    "联系邮箱": {
      "type": "single",
      "value": "person@example.com"
    }
  }
}
```

The v1 key maps to v2 `label`; an adapter may generate `id`, empty `scope`, and
missing provenance metadata. Missing provenance MUST remain unknown rather than
being invented.

### 6.10 Operation and ChangeSet

Every agent action that can change canonical workspace data is represented as
an Operation. This is the contract used by the Skill and review UI.

```json
{
  "schema_version": 1,
  "operation_id": "op_20260822_100000_example",
  "type": "ingest",
  "status": "proposed",
  "requested_at": "2026-08-22T10:00:00+08:00",
  "actor": "owner_via_agent",
  "input_refs": ["raw/inbox/example.md"],
  "changes": [
    {
      "path": "wiki/sources/S-001.md",
      "action": "create",
      "before_hash": null,
      "after_hash": "sha256:...",
      "diff_path": ".llm-wiki/operations/op_20260822_100000_example/001.diff"
    }
  ],
  "validation": {
    "status": "passed",
    "checks": ["schema", "links", "source-citations"]
  },
  "approval": null
}
```

Initial operation types are `init`, `ingest`, `refresh`, `capture`, `query`,
`synthesis`, `lint`, `memory_read`, and `memory_write`.

State transitions:

```text
draft → proposed → approved → applied
                 ↘ rejected
approved/applied → failed only with an error record
```

An applied Operation manifest is immutable. A correction creates another
Operation. Read-only queries MAY omit a ChangeSet when they create no durable
artifact.

### 6.11 Activity log

`wiki/log.md` is the human-readable chronological view of meaningful
operations. Operation manifests are the machine-readable audit record. The log
MUST NOT contain credentials, full sensitive personal values, or copied source
dumps.

## 7. Relationships

```text
Project ──authorizes──> Source Mapping ──resolves──> External Resource
                                                └──captures──> Snapshot
Source Note ──describes──> Source or Snapshot
Wiki Page ──contains──> Claim ──supported by/challenged by──> Source Note
Project ──has──> Personal Authoring ──supports──> Owner View
Task ──selectively reads──> Profile Memory Item
Operation ──proposes/applies──> Canonical files
UI Index ──derives from──> Canonical files and Operation manifests
```

## 8. Operation semantics

### Ingest

1. Resolve the exact immutable source path.
2. Create one Source record and source note.
3. Extract claims without inventing dates, citations, or locations.
4. Update only materially affected pages.
5. Generate a ChangeSet, validate it, and record the result.

### Connector refresh

1. Resolve a Project and its authorized Source Mappings.
2. Retrieve only the minimum useful fields and coverage window.
3. Save new immutable snapshots and manifests.
4. Create or update Source notes.
5. Update materially affected Project pages and record limitations.

### Query and synthesis

1. Read the workspace index and the smallest relevant set of pages and Sources.
2. Lead with the answer while preserving fact, inference, owner view, and open
   question labels.
3. Persist only answers worth retaining, through a reviewed Operation.

### Personal capture

1. Preserve the owner's original wording.
2. Link it to Projects when relevant.
3. Promote a preference to confirmed only after explicit confirmation.

### Profile Memory write

1. Diagnose the actual persistent store path.
2. Classify the value as `single` or `entries`.
3. For a small unambiguous update, write directly; for a large import, preview a
   checklist.
4. Read the saved value back and compare it exactly.
5. Never fall back to temporary or workspace storage silently.

## 9. Privacy classes and export

Every canonical record SHOULD have an effective privacy class, inherited from
its storage layer when not explicit:

| Class | Meaning | Public export |
|---|---|---|
| `public_template` | Empty schemas, templates, fictional examples | Allowed |
| `workspace_private` | Real project knowledge and mappings | Excluded by default |
| `personal_sensitive` | Profile Memory and personal authoring | Always excluded by default |
| `prohibited_secret` | Passwords, OTPs, private keys, seed phrases | Reject storage |

A public export MUST include only framework files, templates, schemas, scripts,
and explicitly fictional examples. It MUST exclude:

- raw sources and connector snapshots;
- real Source notes and Wiki conclusions;
- external object IDs and real project mappings;
- Personal Authoring and Profile Memory;
- operation diffs that contain private content;
- caches, credentials, cookies, and tokens.

## 10. Validation invariants

An implementation conforming to this specification MUST validate at least:

1. Required files and schema versions are present.
2. Stable internal references resolve.
3. A fact has a Source reference.
4. An inference is labelled and cites supporting Sources.
5. An owner view is not presented as an external fact.
6. Source coverage and capture time are distinguishable.
7. Immutable files are not overwritten by an Operation.
8. Exact Profile Memory values survive write-read round trips.
9. Ambiguous scoped identifiers do not overwrite one another.
10. Generated UI/search indexes can be deleted and rebuilt.
11. Public export contains no private storage layers or real external IDs.
12. Prohibited secrets are rejected.

## 11. Skill and UI contract boundary

The Skill owns operation semantics, validation, and canonical file changes. The
UI owns presentation, navigation, review, and user intent capture.

| Capability | Skill | UI |
|---|---|---|
| Parse sources and extract claims | Owns | Starts and displays |
| Resolve evidence and uncertainty | Owns | Visualizes |
| Generate and validate ChangeSets | Owns | Reviews and approves |
| Write canonical files | Owns after authorization | Never writes directly |
| Build disposable index | Provides or triggers | Consumes |
| Browse projects and graph | Supplies data | Owns experience |
| Profile Memory exact recall | Selective adapter read | Shows only requested fields |

The first implementation SHOULD stabilize this contract before adding
connector-specific UI behavior.

## 12. Compatibility with the current workspace

| Current element | v0.1 model |
|---|---|
| `raw/` | Immutable manual Sources |
| `connected/lark/` | One connector-specific Snapshot implementation |
| `config/projects/*.yaml` | Source Mappings |
| `personal/captures/` and `reflections/` | Personal Authoring |
| `wiki/sources/S-*.md` | Source Notes |
| `wiki/projects/<slug>/` | Project Markdown projection |
| `wiki/topics/`, `entities/`, `syntheses/` | Maintained Wiki Pages |
| `wiki/index.md` | Human-readable catalog |
| `wiki/log.md` | Human-readable Activity Log |
| `scripts/wiki_check.py` | Initial validator |
| `~/.personal-memory/store.json` | External Profile Memory Store v1 |

The executable reference schemas live in `schemas/`. The dependency-free
`scripts/schema_check.py` validates the bundled examples and the subset of JSON
Schema keywords used by those files.

The first executable Workspace adapter lives in `scripts/workspace.py`. It
initializes the recommended layout, captures manual sources without overwriting
their bytes, emits Operation manifests, and validates source hashes and Wiki
links. Its command and storage contract are documented in
`references/workspace-cli.md`.

The v0.2 adapter adds a real proposal lifecycle for maintained Markdown pages
and a disposable JSON read model. Proposal artifacts hold before/after bytes and
unified diffs until an approved Operation is applied. The derived index is
rebuilt from canonical files and Operation manifests; it is never a second
write target. See `references/review-index.md`.

This draft adds a Workspace manifest, stable Page and Claim identities,
Snapshot hashes, Operation manifests, privacy classes, and a formal Skill/UI
boundary. These are forward-compatible targets; existing workspace pages do
not need to be migrated until the Skill and UI contracts are implemented.
