# Adaptive history policy

Read this reference when importing local Query history, learning a personal
capture policy, running the Episode Worker, reviewing why a Candidate appeared,
or evaluating trigger volume.

## Mental model

The system learns **when to review a possible memory**, not what to write
automatically. It uses later explicit saves as weak positive labels:

```text
bounded local history                 live Query
        │                                │
        └──────── immutable Event v2 ────┘  critical path ends here
                         │
                  conversation Episode
                         │
        explicit save later? ── yes ──> Policy evidence
                         │ no
                         └─────────────> unlabeled, not a negative label

active Policy + Episode stage ──> ignore / session / Candidate review
                                           │
                            approve / edit / reject / suppress
                                           │
                              append-only Policy feedback
```

A frequent phrase such as “为什么” or “总结一下” is not sufficient evidence.
For example, `learning_synthesis` is enabled only when history contains an
Episode that moved through clarification and synthesis and was later explicitly
saved to a Workspace. A new similar Episode may then be proposed for review at
the synthesis stage. It is never written automatically.

## Import contract

The importer accepts bounded, normalized JSONL. It is deliberately
platform-neutral; an Agent adapter should convert only the minimum local fields
it is authorized to read.

```json
{"role":"user","conversation_id":"opaque-conversation","message_id":"opaque-message","occurred_at":"2026-08-20T09:10:00+08:00","workspace_id":"example-project","content":"总结一下核心能学到什么"}
```

Required fields are `conversation_id`, timezone-aware `occurred_at`, and
`content` (or `user_message`). Optional fields are `message_id`, `workspace_id`,
`assistant_summary`, `source_agent`, and `direct_route`. Non-user rows are
ignored. Import is idempotent by adapter, conversation/message identity, time,
and content. Common credential shapes are rejected before writing.

```bash
python3 <skill-dir>/scripts/capture.py history import \
  --file /absolute/path/to/normalized-history.jsonl \
  --adapter <local-adapter-name> \
  --limit 1000 \
  --retention-days 30 \
  --json
```

The command never uploads history and never edits canonical memory. Start with
a small time range. If the current Agent cannot access history under the user's
permissions, stop at that boundary; do not broaden account or file access.

## First-use learning orchestration

The public trigger is first Skill activation, not a product-specific install
hook. A host adapter may hand off only the normalized history it is already
authorized to see through `MWORK_HISTORY_FILE` / `MWORK_HISTORY_ADAPTER`, or the
Agent may pass an exact file and capability name:

```bash
python3 <skill-dir>/scripts/capture.py onboarding status --json
python3 <skill-dir>/scripts/capture.py onboarding run \
  --file /absolute/path/to/agent-visible-history.jsonl \
  --adapter <capability-name> \
  --days 30 \
  --json
```

`onboarding run` is idempotent unless `--force` is explicit. It discards rows
outside the rolling window before Event creation, rejects common secrets, and
produces two separate review drafts:

- `~/.memory-home/personal/learning/query-habits.md` plus its validated JSON source describe recurring
  interaction patterns for humans and Agents;
- `~/.memory-home/personal/learning/policies/policy_*.json` controls Candidate
  timing and remains inactive.

The local baseline requires a pattern to appear in at least two conversations.
It defines “distinctive” relative to the same user's repeated behavior, not a
cross-user comparison. A semantic Agent may refine the draft without changing
its coverage or citing events outside the imported history:

```bash
python3 <skill-dir>/scripts/capture.py onboarding refine \
  --file /absolute/path/to/semantic-query-habits.json \
  --json
```

The semantic report uses `query-habits.schema.json`, sets
`method=agent_semantic_review` and `state=tentative`, and references only opaque
Event IDs. Raw Query text is not copied into Markdown. The owner completes the
gate through the UI or CLI:

```bash
python3 <skill-dir>/scripts/capture.py onboarding confirm --json
```

Confirmation marks the Markdown report confirmed and activates the matching
Policy. It does not write the imported history into Workspace or Exact Profile
Memory. If no adapter is available, status remains `needs_history_source`; the
system must not claim that the user has no history.

## Draft and activate a Policy

Build creates an immutable draft. It stores counts, learned rule parameters,
and opaque evidence Episode IDs, not copied raw queries:

```bash
python3 <skill-dir>/scripts/capture.py policy build --json
python3 <skill-dir>/scripts/capture.py policy show <policy-id> --json
python3 <skill-dir>/scripts/capture.py policy activate <policy-id> \
  --actor owner_via_cli --json
```

Activation is explicit and append-only. The latest activation is the active
Policy; earlier drafts remain auditable. A new draft includes accumulated
feedback. When rejection and suppression outweigh approval, learned rules
require more positive historical support.

The immutable Policy object always retains `state: draft`. Use the computed
`effective_state` returned by `policy show` / `policy list` to determine whether
it is currently active; activation does not rewrite an audited draft.

`source_summary.positive_episode_count` counts all Episodes with an explicit
canonical save route. Each rule's `evidence_episode_ids` is the narrower subset
whose stages and destination qualify for that rule, so the two counts need not
match.

## Episode Worker

An Episode groups events with the same conversation ID until the configured
idle gap. Cheap stage features (`explore`, `clarify`, `revise`, `synthesize`,
`commit`) help schedule semantic review; they are not persistence decisions.

```bash
python3 <skill-dir>/scripts/capture.py worker plan --json
python3 <skill-dir>/scripts/capture.py episode show <episode-id> --json
python3 <skill-dir>/scripts/capture.py episode resolve <episode-id> \
  --decision project \
  --kind learning \
  --content "<semantic summary grounded in the displayed evidence>" \
  --confidence 0.86 \
  --policy-version <policy-id> \
  --policy-rule learning_synthesis \
  --reason "<why this Episode crossed the review threshold>" \
  --json
```

The deterministic CLI plans and validates; the background Agent supplies the
semantic scope, content, confidence, and reason. One Episode normally produces
one Candidate with multiple `evidence_event_ids`. Companion events resolve as
session context. An Episode with an explicit direct write is `feedback_only`
and is forbidden from producing another Candidate.

Close a `feedback_only` Episode with the plan's `resolution_decision`
(`session`):

```bash
python3 <skill-dir>/scripts/capture.py episode resolve <episode-id> \
  --decision session \
  --reason "Explicit save or recall already handled the canonical action" \
  --json
```

This resolves all pending events and appends `explicit_save_followup` or
`recalled` feedback. It does not create a Candidate. `ignore` is rejected for a
direct-route Episode so a positive outcome is not mislabeled.

## Review feedback

Candidate review remains the write gate:

```bash
python3 <skill-dir>/scripts/capture.py candidate approve <candidate-id> \
  --target-ref "workspace:example/wiki/projects/example/decisions.md" \
  --edited-content "<owner-corrected content>" \
  --feedback-reason "corrected scope" \
  --json

python3 <skill-dir>/scripts/capture.py candidate reject <candidate-id> \
  --feedback-reason "not durable" \
  --suppress-similar \
  --json
```

Supported feedback includes approval, rejection, edit, suppression of similar
Candidates, explicit-save follow-up, successful recall, and complaints that
something was forgotten. Feedback is append-only. Candidate fingerprints avoid
repeated proposals with the same normalized scope, kind, target hint, and
content. The single Writer and canonical Workspace/Personal verification remain
unchanged; the current executable Personal target is Exact Profile.

## Offline replay

Use a chronological split so the Policy is learned only from earlier events:

```bash
python3 <skill-dir>/scripts/capture.py eval replay \
  --split-time 2026-08-15T00:00:00+08:00 \
  --json
```

Replay reports train/test counts, Episode plans, Candidate review hints, and
hints per 100 Queries. Without owner labels for the test period, it **does not**
report precision or recall. UI and experiments should display that boundary
rather than treating trigger volume as quality.

## UI-facing protocol

The UI can remain a replaceable local client. It should read these immutable
records and show:

- Candidate content, scope, kind, confidence, target hint, and status;
- why it appeared: Policy version, matched rule, trigger stage, and reason;
- evidence: Episode and event IDs, with raw text revealed only on demand;
- actions: approve, edit-then-approve, reject, or suppress similar;
- evaluation: trigger volume and labeled quality as separate metrics.

The UI must route approval through `candidate approve` and canonical writes
through the single Writer. Only that Writer (or a verified external compatibility
Writer) may produce `mark-applied`. The UI also must not reinterpret an unlabeled
Episode as a negative example merely because the user did not explicitly save it.
