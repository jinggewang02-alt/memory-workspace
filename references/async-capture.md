# Asynchronous capture queue

Read this reference when enabling background capture, dispatching a Worker or
Subagent, or building the candidate-inbox UI.

## Boundary

The queue removes model classification from the user's critical path. It does
not make memory writes automatic.

```text
user query
    ↓
main Agent completes the task
    ↓ local event enqueue only
user receives the answer
    ↓ optional background / next idle run
Worker resolves event
    ↓
ignore / session / Workspace Candidate / Personal Candidate
    ↓ user review
single Writer updates Workspace or Personal Memory
```

`scripts/capture.py` manages staging and review. It never writes canonical Wiki
pages or `store.json`.

## Storage layout

The default root is `~/.memory-home/system/capture`; override the unified root
with `MEMORY_HOME`, or use the legacy component override `MWORK_CAPTURE_DIR`
only when the destination is a durable local directory.
In JSON output, top-level `ok` means the command executed successfully;
`doctor.result.status` separately reports `OK`, `WARNING`, or an unusable path.

```text
capture/
├── events/                 # immutable raw events
├── resolutions/            # one immutable resolution per event
├── candidates/             # one immutable semantic proposal per Episode
├── candidate-decisions/    # one immutable owner decision per Candidate
├── applications/           # verified single-Writer receipts
└── feedback/               # append-only review and recall outcomes
```

Query habits and policies are durable Personal Memory, not queue state:

```text
~/.memory-home/personal/learning/
├── query-habits.json
├── query-habits.md
├── policies/
└── policy-activations/
```

When an explicit `root` is supplied by a legacy test or embedded adapter, the
old root-relative paths remain supported.

One-file-per-event uses exclusive creation, so concurrent Workers cannot both
resolve the same event. A losing Worker must stop when `event resolve` reports
that the event was already handled.

Raw events contain local plaintext. The queue rejects common password, Token,
Cookie, API-key and private-key shapes before writing. Events have an expiry;
run cleanup to remove expired raw payloads. Resolutions and review receipts are
kept as the audit trail.

## Main-Agent path

Enqueue one event without model work:

```bash
python3 <skill-dir>/scripts/capture.py event enqueue \
  --conversation-id <opaque-id> \
  --workspace-id <workspace-id> \
  --source-agent <agent-name> \
  --message-file /path/to/local-message.txt \
  --json
```

Do not wait for classification before answering. Do not claim that anything was
remembered merely because the event is pending.

If the environment guarantees a background task or Subagent survives the
current response, dispatch at most one Worker for the queue. Let that Worker
batch pending events by conversation after a short debounce; do not create one
Subagent per event. Otherwise leave the event pending. A durable pending event
is the portability fallback.

## Worker path

Plan and inspect pending Episodes:

```bash
python3 <skill-dir>/scripts/capture.py worker plan --json
python3 <skill-dir>/scripts/capture.py episode show <episode-id> --json
```

Use one of four decisions:

| Decision | Meaning | Persistent Candidate |
|---|---|---|
| `ignore` | Ordinary question, one-off instruction, transient feedback, duplicate, or noise | No |
| `session` | Useful only to the current task or current conversation | No |
| `project` | Stable, attributable project decision or constraint that may matter later | Yes |
| `profile` | Cross-project personal fact or stable personal preference | Yes |

Conservative rule: when uncertain between `session` and a persistent scope, use
`session`. Do not promote guesses, brainstorming, temporary metrics, or active
task instructions into Candidates.

When `worker plan` returns `feedback_only`, use its `resolution_decision`
(`session`). `episode resolve` then closes the pending events and records the
explicit-save or recall feedback automatically; it must not create a Candidate.

Resolve a non-persistent Episode:

```bash
python3 <skill-dir>/scripts/capture.py episode resolve <episode-id> \
  --decision session \
  --reason "Only constrains the current answer" \
  --json
```

Propose a project Candidate:

```bash
python3 <skill-dir>/scripts/capture.py episode resolve <episode-id> \
  --decision project \
  --kind learning \
  --content "Agent compatibility must not hard-code platform names." \
  --confidence 0.86 \
  --workspace-id memory-workspace \
  --reason "Confirmed cross-platform project constraint" \
  --json
```

Propose a legacy Profile Candidate only for durable cross-project exact facts.
It maps to the Personal `exact_profile` destination. Mark exact identifiers,
addresses, contact data, and similar values sensitive:

```bash
python3 <skill-dir>/scripts/capture.py episode resolve <episode-id> \
  --decision profile \
  --kind fact \
  --content "<exact owner-provided value>" \
  --profile-key "<unambiguous key>" \
  --confidence 0.98 \
  --sensitivity sensitive \
  --reason "Owner-provided exact fact with future reuse" \
  --json
```

A Worker must not run `workspace.py operation approve/apply` or `store.py set/add`.

## Review and single Writer

```bash
python3 <skill-dir>/scripts/capture.py candidate list --status proposed --json
python3 <skill-dir>/scripts/capture.py candidate show <candidate-id> --json
```

Approval requires an explicit destination:

```bash
python3 <skill-dir>/scripts/capture.py candidate approve <candidate-id> \
  --target-ref "workspace:memory-workspace/wiki/projects/example/decisions.md" \
  --actor owner_via_cli \
  --json
```

For a project Candidate, the referenced Workspace must already exist, the
relative path must not escape it, and the Workspace id must match the Candidate
hint. A legacy Profile target uses `profile:<key>` and must match its key hint.

After approval, invoke the single Writer:

```bash
python3 <skill-dir>/scripts/capture.py candidate apply <candidate-id> \
  --actor owner_via_agent \
  --json
```

The command uses the existing canonical path:

- project Candidate: create and apply a reviewed Workspace Operation;
- profile Candidate: run `store.py doctor`, write with `set` / `add`, and read
  back with `get`.

It records a schema v2 application receipt only after canonical verification.
The receipt includes Writer kind, optional Workspace Operation id, and named
checks so a UI can display the result without parsing prose.

Candidate v3 renames persistent scopes to `workspace` and `personal`, then uses
`target_hint.personal_section` to distinguish Exact Profile, Personal Work,
relationships, preferences, and learning. The current reference Writer applies
only legacy Workspace and Exact Profile targets; Personal Work Markdown remains
a reviewable protocol target, not an implemented automatic write path.

`mark-applied` remains a low-level compatibility command for an external Writer
that has already performed and verified the canonical write. Do not use it to
skip the Writer:

```bash
python3 <skill-dir>/scripts/capture.py candidate mark-applied <candidate-id> \
  --verification "Workspace operation op_x applied; workspace check OK" \
  --actor owner_via_agent \
  --json
```

Profile single-value conflicts stop without overwrite and leave the Candidate
`approved`. String Candidates do not auto-apply to structured Profile entries.

Reject a Candidate without touching canonical data:

```bash
python3 <skill-dir>/scripts/capture.py candidate reject <candidate-id> \
  --feedback-reason "not durable" \
  --suppress-similar \
  --actor owner_via_cli \
  --json
```

## Codex adapter and portable fallback

The Codex hook keeps async capture off by default. Enable one of:

```bash
export MWORK_ASYNC_CAPTURE=signals  # only broad potential-value signals
export MWORK_ASYNC_CAPTURE=all      # all prompts without a direct Workspace/Exact Profile route
export MWORK_ASYNC_CAPTURE=adaptive # all prompts as observations; decide later by Episode + Policy
```

Optional settings:

```bash
export MWORK_CAPTURE_RETENTION_DAYS=7
export MWORK_ASYNC_CAPTURE_HINT=1
export MWORK_WORKSPACE_ID=<workspace-id>
```

`MWORK_ASYNC_CAPTURE_HINT=1` injects a small instruction telling a capable Agent
to dispatch a background Worker without waiting. Set it to `0` when an external
daemon or UI consumes the queue.

In `adaptive` mode, explicit Workspace/Exact Profile compatibility routes are stored as
observation-only feedback and cannot create another Candidate. Other Agent
environments should implement the same event/resolution/candidate
contract rather than copying Codex-specific hook names. If they cannot run
background work, process pending events on next startup, during idle time, when
the UI opens the inbox, or through an explicit sync command.

## Cleanup

Preview before deleting expired raw event payloads:

```bash
python3 <skill-dir>/scripts/capture.py cleanup expired --json
python3 <skill-dir>/scripts/capture.py cleanup expired --apply --json
```

Cleanup does not delete Candidate decisions or application receipts.

History import, draft activation, learned stage patterns, feedback, and
time-split replay are specified in [adaptive-policy.md](adaptive-policy.md).
