# LLM Wiki Data Model

Status: Draft v0.5
Updated: 2026-08-27

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
- non-blocking capture events and reviewable background candidates;
- local-first storage and safe public export.

The model MUST NOT treat the Wiki as a password manager or secret vault.
Passwords, one-time codes, private keys, and seed phrases are prohibited data.

## 2. Mental model

Memory Home has two sibling knowledge scopes and one control plane:

```text
Memory Home
├── Personal Memory                  user-centered, cross-Workspace
│   ├── Exact Profile                lossless personal facts
│   ├── Personal Work                responsibilities, portfolio, people, themes, timeline
│   ├── Preferences                  confirmed and tentative kept separate
│   └── Learning                     Query habits and reviewable Policy
├── Workspaces[]                     project/domain-centered evidence and Wiki
│   ├── Sources[] → Snapshots[]
│   ├── Wiki Pages[] → Claims[]
│   ├── Owner Notes[]
│   └── Source Mappings[]
└── System                           control state, not long-term knowledge
    ├── Capture Queue → Candidates
    ├── Operations and receipts
    ├── Disposable indexes
    └── Connector Configs and Checkpoints
```

Personal Memory and Workspaces are parallel but linked:

| Store | Purpose | Example | Canonical form |
|---|---|---|---|
| Exact Profile | Exact facts reused across Workspaces | an email address or a structured experience entry | JSON |
| Personal Work | User-centered synthesis across Workspaces | current responsibilities, collaborators, themes | Markdown |
| Workspace | Evidence and maintained knowledge about one project/domain | decisions, status, artifacts, source notes | Files + Markdown |
| Owner Notes | User viewpoint tied to one Workspace | a project concern, hypothesis, or judgment | Markdown |

A Workspace MAY read selected Exact Profile fields for an authorized task.
Personal Work pages MAY link to Workspace claims or owner captures but MUST NOT
copy source dumps. A Workspace MUST NOT silently write observed values into
Exact Profile.

## 3. Canonical and derived data

Each datum has exactly one canonical representation. Other representations are
views and MUST be rebuildable.

| Data | Canonical representation | Mutability |
|---|---|---|
| Manually added source | Original file under `raw/` | Immutable |
| Connector result | Timestamped snapshot under `connected/` | Immutable |
| Connector activation | JSON under `config/connectors/` | Explicit mutation only |
| External observation | JSON referencing an immutable connector snapshot | Immutable staging |
| Connector checkpoint | JSON under `.llm-wiki/connectors/` | Advance after successful snapshot only |
| Source description | Markdown source note under `wiki/sources/` | Maintained |
| Project knowledge | Markdown under `wiki/projects/` | Maintained |
| Topic, entity, synthesis | Markdown under `wiki/` | Maintained |
| Owner capture or reflection | Markdown under `personal/` | Append or explicitly edit |
| Exact personal fact | JSON under `personal/profile/exact.json` | Explicit mutation only |
| Personal Work synthesis | Markdown under `personal/work/` | Reviewed maintained projection |
| Personal preference | Markdown under `personal/preferences/` | Confirmed and tentative separate |
| Query habit and Policy | Files under `personal/learning/` | Draft then explicit activation |
| Source authorization | YAML under `config/` | Explicit mutation only |
| Search/UI index | SQLite, JSON, or another generated cache | Derived and disposable |
| Agent proposal | Operation manifest plus file diff | Immutable after proposal |
| Async capture event | Immutable local JSON event | Expiring staging |
| Async resolution and review | Immutable JSON resolution/receipt | Append-only audit |

The frontend MUST NOT become a second source of truth. It reads canonical files
and generated indexes, and writes through reviewed operations.

## 4. Recommended workspace layout

```text
~/.memory-home/
├── memory-home.json
├── personal/
│   ├── index.md
│   ├── profile/exact.json
│   ├── work/{overview.md,portfolio.md,people/,themes/,timeline/}
│   ├── preferences/{confirmed.md,tentative.md}
│   ├── learning/{query-habits.md,policies/,policy-activations/}
│   └── captures/
├── workspaces/
│   └── <workspace-id>/
│       ├── llm-wiki.json
│       ├── raw/inbox/
│       ├── connected/<connector>/{snapshots/,manifests/}
│       ├── config/{projects/,connectors/}
│       ├── wiki/{sources/,projects/,topics/,entities/,syntheses/}
│       └── .llm-wiki/{index/,operations/,connectors/}
└── system/
    ├── capture/
    ├── operations/
    ├── index/
    ├── connectors/
    └── migrations/
```

Connector directories are optional. A generic Workspace without a connector
MUST remain fully valid and MUST NOT probe an external CLI. An external read is
allowed only when the matching Connector Config records an explicit activation
and `enabled=true`.

All three layers share one durable root. `MEMORY_HOME` may override it. Legacy
component variables remain compatibility overrides, but new installations
SHOULD avoid split roots. A Skill MUST reject accidental writes to a temporary
directory unless the operation is an explicit test.

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
schema_version: 2
scope: workspace
workspace:
  id: example-workspace
  name: "Example Workspace"
  created_at: 2026-08-22T10:00:00+08:00
defaults:
  language: zh-CN
  timezone: Asia/Shanghai
  review_mode: proposed_changes
```

The manifest MUST NOT contain credentials or connector access tokens. A v2
Workspace does not own Personal Memory and therefore has no nested
`profile_memory` configuration. Schema v1 remains readable for compatibility.

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
Workspace or project. It is canonically stored under Memory Home `personal/`,
not inside an individual Workspace.

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

### 6.9 Exact Profile item

Exact Profile stores exact personal facts at
`personal/profile/exact.json`. It is a Personal Memory sublayer parallel to
Workspaces, rather than a separate product or a directory owned by one
Workspace. JSON is canonical because it supports exact strings and structured
one-to-many records. Markdown export is a view.

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

Exact Profile rules:

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
an input adapter and can be copied into Memory Home through the migration
workflow:

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

### 6.12 Async Capture Event and Candidate

An Async Capture Event removes semantic memory classification from the user's
critical path. Enqueue performs only a local write; it does not mean the content
should be persisted.

```json
{
  "schema_version": 1,
  "event_id": "evt_20260824T120000Z_example",
  "created_at": "2026-08-24T12:00:00Z",
  "expires_at": "2026-08-31T12:00:00Z",
  "source": {
    "agent": "example-agent",
    "conversation_id": "opaque-conversation-id",
    "message_id": null,
    "workspace_id": "example-project"
  },
  "payload": {
    "user_message": "Use a platform-neutral compatibility layer.",
    "assistant_summary": null
  },
  "privacy": "local_plaintext_staging"
}
```

A Worker resolves legacy events exactly once as `ignore`, `session`, `project`,
or `profile`. Only `project` and `profile` create a Candidate. In Candidate v3,
their canonical scopes are named `workspace` and `personal`; Personal is then
routed to Exact Profile, Personal Work, Preferences, or Learning. A Candidate
is not a ChangeSet and MUST NOT modify canonical data. It becomes writable only
after a separate owner decision identifies the target. A single Writer then
uses a canonical Workspace Operation or Personal write workflow and records a
verified application receipt. Receipt schema v2 currently identifies
`profile_single` or `workspace_operation`, carries the Operation id when
applicable, and lists the checks that passed. Presentation clients may invoke
this Writer after a separate explicit apply action, but must never edit
canonical files themselves.

The reference Writer currently applies the legacy Exact Profile and Workspace
targets. Candidate v3 defines the broader routing contract, but automatic
materialization of Personal Work Markdown is not implemented yet.

The queue MUST remain optional, local, expiring, and independent of a particular
Agent's Subagent API. Environments without durable background execution process
pending events on a later startup, idle cycle, UI visit, or explicit sync.

### 6.13 Connector Config, External Observation, and Candidate v2/v3

A Connector Config records a provider-specific opt-in. It is not an access
token and MUST contain no credential. Absence or `enabled=false` means that the
provider is outside the current read scope.

An External Observation is immutable staging derived from a connector snapshot.
It keeps the provider object ID, actors, minimal content, snapshot reference,
coverage and project-routing state together. Discovery alone SHOULD set
`candidate_eligible=false`; a Resolver may change eligibility only with an
explainable project match and material new evidence.

Candidate schema v2 adds project-context fields while keeping schema v1 valid:

| Field | Purpose |
|---|---|
| `project_id` | Resolved project, or `null` while unresolved |
| `claim_type` | Context, relationship, decision, action, artifact, status, or profile fact |
| `subject_refs` | Stable projects, entities, artifacts, or tasks affected |
| `evidence_refs` | Capture Event, External Observation, or Source Note references |
| `temporal_scope` | Time window to which the proposed claim applies |
| `novelty_score` | Evidence-level change signal, not a truth probability |
| `reason_codes` | Reviewable reasons for creating the Candidate |
| `proposed_patch` | Intended target and operation; still not authorization to write |

Connector evidence always has `profile_write_allowed=false`. A Candidate may
propose a `profile_fact` only through an independent, explicit owner request,
not because an external source happened to contain personal data.

Candidate schema v3 replaces the legacy `project/profile` scope names with
`workspace/personal` and adds an explicit Personal destination hint:

| Field | Purpose |
|---|---|
| `scope` | `workspace` or `personal` |
| `target_hint.workspace_id` | Required routing hint for Workspace knowledge |
| `target_hint.personal_section` | `exact_profile`, `work`, `relationships`, `preferences`, or `learning` |
| `target_hint.exact_key` | Exact key when the destination is Exact Profile |

An external observation may support a Personal Work proposal when it reveals
user responsibilities, collaborators, or cross-Workspace themes, but it MUST
retain evidence references and go through review. It MUST NOT authorize an
Exact Profile write.

## 7. Relationships

```text
Project ──authorizes──> Source Mapping ──resolves──> External Resource
                                                └──captures──> Snapshot
Source Note ──describes──> Source or Snapshot
Wiki Page ──contains──> Claim ──supported by/challenged by──> Source Note
Personal Memory ──contains──> Personal Authoring ──supports──> Owner View
Task ──selectively reads──> Exact Profile Item
Personal Work ──links to──> Workspace Claim and Owner Capture
Operation ──proposes/applies──> Canonical files
UI Index ──derives from──> Canonical files and Operation manifests
Capture Event ──resolves to──> ignore / session / Candidate
Enabled Connector ──captures──> Snapshot ──normalizes──> External Observation
External Observation ──resolves to──> project / ignore / Candidate
Approved Candidate ──routes through──> single Writer and Workspace/Personal workflow
```

## 8. Operation semantics

### Ingest

1. Resolve the exact immutable source path.
2. Create one Source record and source note.
3. Extract claims without inventing dates, citations, or locations.
4. Update only materially affected pages.
5. Generate a ChangeSet, validate it, and record the result.

### Connector refresh

1. Confirm that the provider's Connector Config is explicitly activated and enabled.
2. Generate a bounded baseline or incremental plan from the latest successful checkpoint.
3. Resolve a Project and its authorized Source Mappings.
4. Retrieve only the minimum useful fields and coverage window.
5. Save new immutable snapshots and manifests, then normalize Observations.
6. Advance the checkpoint only after the snapshot exists.
7. Create Candidates or Source notes; update Project pages only through reviewed writes.

### Query and synthesis

1. Read the workspace index and the smallest relevant set of pages and Sources.
2. Lead with the answer while preserving fact, inference, owner view, and open
   question labels.
3. Persist only answers worth retaining, through a reviewed Operation.

### Personal capture

1. Preserve the owner's original wording.
2. Link it to Projects when relevant.
3. Promote a preference to confirmed only after explicit confirmation.

### Exact Profile write

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
| `personal_sensitive` | Personal Memory, including Exact Profile and personal authoring | Always excluded by default |
| `prohibited_secret` | Passwords, OTPs, private keys, seed phrases | Reject storage |

A public export MUST include only framework files, templates, schemas, scripts,
and explicitly fictional examples. It MUST exclude:

- raw sources and connector snapshots;
- real Source notes and Wiki conclusions;
- external object IDs and real project mappings;
- Personal Memory, including Personal Authoring and Exact Profile;
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
8. Exact Profile values survive write-read round trips.
9. Ambiguous scoped identifiers do not overwrite one another.
10. Generated UI/search indexes can be deleted and rebuilt.
11. Public export contains no private storage layers or real external IDs.
12. Prohibited secrets are rejected.
13. Event enqueue does not write canonical Workspace or Personal data.
14. A Candidate cannot be marked applied without an owner decision and a
    canonical verification receipt.
15. Missing or disabled Connector Config yields no external read commands.
16. A connector checkpoint advances only after its immutable snapshot exists.
17. Connector observations cannot authorize Exact Profile writes.

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
| Exact Profile recall | Selective adapter read | Shows only requested fields |
| Queue potential captures | Enqueues and resolves | Shows pending/review states |
| Approve/reject Candidate | Enforces transition | Captures explicit decision |
| Apply approved Candidate | Single Writer through canonical workflow | Never writes directly |
| Activate or disable Connector | Validates explicit config and boundary | Captures explicit intent |
| Plan external read | Produces bounded plan only when enabled | Displays scope and due state |

The first implementation SHOULD stabilize this contract before adding
connector-specific UI behavior.

## 12. Compatibility with the current workspace

| Current element | v0.1 model |
|---|---|
| `raw/` | Immutable manual Sources |
| `connected/lark/` | One connector-specific Snapshot implementation |
| `config/projects/*.yaml` | Source Mappings |
| `~/.memory-home/personal/` | Exact Profile, Personal Work, preferences, learning, and owner captures |
| `~/.memory-home/workspaces/<id>/` | Project-centered evidence and maintained Wiki |
| `~/.memory-home/system/` | Capture, operations, indexes, connector state, and migrations |
| `wiki/sources/S-*.md` | Source Notes |
| `wiki/projects/<slug>/` | Project Markdown projection |
| `wiki/topics/`, `entities/`, `syntheses/` | Maintained Wiki Pages |
| `wiki/index.md` | Human-readable catalog |
| `wiki/log.md` | Human-readable Activity Log |
| `scripts/wiki_check.py` | Initial validator |
| `~/.personal-memory/store.json` | Legacy Exact Profile input; copy to `personal/profile/exact.json` |
| `~/.personal-memory/workspaces/` | Legacy Workspace root; copy to `workspaces/` |
| `~/.memory-workspace/capture/` | Legacy Capture root; copy to `system/capture/` |

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

The v0.3 adapter adds an optional durable capture queue and Candidate inbox. It
keeps model classification outside the current Query, supports background or
deferred Workers without requiring a named Agent platform, and preserves the
existing reviewed Workspace/Personal write boundaries. See
`references/async-capture.md`.

The v0.6 adaptive layer adds versioned live/history events, stable conversation
Episodes, reviewable personal Policy drafts, separate Candidate evidence,
append-only feedback, and time-split replay. Historical Query frequency alone
is not a positive label: the learner only treats Episodes with a later explicit
canonical save route as positive evidence. See `references/adaptive-policy.md`.

The v0.8 single Writer closes the reviewed Candidate lifecycle. It maps an
approved target to the canonical Exact Profile or Workspace protocol, rejects
unsafe Exact Profile overwrites, verifies readback/checks, and emits a machine-readable
application receipt consumed by the local Review Inbox.

The v0.9 connector protocol adds optional `Connector Config`, `External
Observation`, and `Sync Checkpoint` resources. The first Lark planner requires
explicit activation, uses a bounded 30-day baseline and checkpointed daily
increments, and returns a plan without invoking `lark-cli`. See
`references/lark-connector.md`.

The v0.10 Memory Home foundation unifies defaults under `~/.memory-home/`,
makes Personal Memory and Workspaces sibling scopes, emits Workspace manifests
at schema v2, and adds a conflict-safe, copy-only migration from the former
split roots. Candidate v3 defines Personal/Workspace routing while preserving
the existing Writer as a compatibility layer. See `references/memory-home.md`.

The v0.11 project loop separates a provider-neutral Memory Core from optional
Provider Adapters. An Adapter reads only `direct_execution` mappings and emits a
standard Sync Bundle; the Core alone persists immutable snapshots, generic sync
manifests, Source Notes, External Observations, checkpoints, and the disposable
`project-memory.json` UI view. The bundled Lark Adapter supplies `lark-cli`
commands and Lark field normalization without being imported by the Core. See
`references/adapter-contract.md` and `references/lark-connector.md`.

This draft adds a Workspace manifest, stable Page and Claim identities,
Snapshot hashes, Operation manifests, privacy classes, and a formal Skill/UI
boundary. These are forward-compatible targets; existing workspace pages do
not need to be migrated until the Skill and UI contracts are implemented.
