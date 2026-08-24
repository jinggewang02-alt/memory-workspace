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
ignore / session / project Candidate / profile Candidate
    ↓ user review
single Writer updates Workspace or Profile Memory
```

`scripts/capture.py` manages staging and review. It never writes canonical Wiki
pages or `store.json`.

## Storage layout

The default root is `~/.memory-workspace/capture`; override it with
`MWORK_CAPTURE_DIR` only when the destination is a durable local directory.

```text
capture/
├── events/                 # immutable raw events
├── resolutions/            # one immutable resolution per event
├── candidate-decisions/    # one immutable owner decision per Candidate
└── applications/           # verified single-Writer receipts
```

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

List and inspect pending events:

```bash
python3 <skill-dir>/scripts/capture.py event list --status pending --json
python3 <skill-dir>/scripts/capture.py event show <event-id> --json
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

Resolve a non-persistent event:

```bash
python3 <skill-dir>/scripts/capture.py event resolve <event-id> \
  --decision session \
  --reason "Only constrains the current answer" \
  --json
```

Propose a project Candidate:

```bash
python3 <skill-dir>/scripts/capture.py event resolve <event-id> \
  --decision project \
  --content "Agent compatibility must not hard-code platform names." \
  --confidence 0.86 \
  --workspace-id memory-workspace \
  --reason "Confirmed cross-platform project constraint" \
  --json
```

Propose a Profile Candidate only for durable cross-project information. Mark
exact identifiers, addresses, contact data, and similar values sensitive:

```bash
python3 <skill-dir>/scripts/capture.py event resolve <event-id> \
  --decision profile \
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
hint. A Profile target uses `profile:<key>` and must match its key hint.

After approval, the single Writer uses the existing canonical path:

- project Candidate: create and apply a reviewed Workspace Operation;
- profile Candidate: run `store.py doctor`, write with `set` / `add`, and read
  back with `get`.

Only after canonical verification record the receipt:

```bash
python3 <skill-dir>/scripts/capture.py candidate mark-applied <candidate-id> \
  --verification "Workspace operation op_x applied; workspace check OK" \
  --actor owner_via_agent \
  --json
```

Reject a Candidate without touching canonical data:

```bash
python3 <skill-dir>/scripts/capture.py candidate reject <candidate-id> \
  --actor owner_via_cli \
  --json
```

## Codex adapter and portable fallback

The Codex hook keeps async capture off by default. Enable one of:

```bash
export MWORK_ASYNC_CAPTURE=signals  # only broad potential-value signals
export MWORK_ASYNC_CAPTURE=all      # all prompts without a direct Workspace/Profile route
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

Other Agent environments should implement the same event/resolution/candidate
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
