# Runtime capability negotiation

Memory Workspace is portable across agent runtimes by capability, not by a
closed list of product names. A runtime is compatible only to the extent that
it can expose the required execution and storage capabilities.

## Capability contract

The local implementation needs all of the following for full operation:

- load this Skill and resolve resources relative to `SKILL.md`;
- execute a local command with Python 3.10 or newer;
- read files explicitly placed in scope by the user;
- write to an explicit durable directory that survives later sessions;
- surface permission failures instead of silently changing the destination.

The probe also reports `capture_learning` storage and a separate
`history_access` capability. History access is optional for ordinary
Workspace/Personal use. When configured, it is only a handoff contract for
normalized JSONL that the current Agent is already authorized to see; it is
not permission to discover product databases or other accounts.

Run the read-only probe with an available Python launcher:

```bash
<python> <skill-dir>/scripts/bootstrap.py --json
```

`<python>` means the exact executable that passed the minimum-version check.
Agents should preserve `capabilities.python.executable` in later launch commands
instead of falling back to a different `python3` found on `PATH`.

The probe does not create a test file, install software, change permissions, or
contact a remote service. Host-level sandbox approval may still be required
when the first real command writes data.

## Status handling

| Status | Meaning | Required agent behavior |
|---|---|---|
| `READY` | Local runtime and durable paths appear usable | Continue, then run the operation-specific `doctor` before a write |
| `NEEDS_RUNTIME` | Python is older than 3.10 | Report the missing runtime; install only with explicit authorization |
| `NEEDS_PERSISTENT_PATH` | A selected path is transient | Select or request a mounted durable path and rerun |
| `NEEDS_PERMISSION` | The nearest existing parent is not writable | Request access to the exact path or select another durable path |
| `UNSUPPORTED` | Required Skill resources are missing | Reinstall/restore the Skill; do not reconstruct commands from memory |

Exit codes are `0` for `READY`, `1` when setup is required, and `2` for an
unsupported installation. The JSON report is the contract; do not infer
success from an empty terminal response.

The quickstart receipt also describes proactive-memory behavior. A value of
`memory_behavior.scheduling_status=host_integration_required` means the data
protocol and nightly command are available, not that an OS timer or durable
background Worker is already running. The Agent host should register its own
evening trigger, or use the declared next-startup/idle fallback.

## Runtime modes

`local-full` means the current package can run through its local CLI. A setup
status uses `local-setup-required` until the exact issue is resolved.
`instruction-only` means the agent may explain the workflow but must not claim
that any data was persisted.

If an environment cannot execute the probe at all, classify it from observed
capabilities:

1. Local command and durable filesystem available: locate an appropriate
   Python launcher and run the probe.
2. Filesystem available but commands unavailable: remain read-only unless a
   separately installed tool adapter exposes the same operations.
3. Remote or chat-only environment: use only an explicitly configured API/tool
   adapter. If that adapter cannot provide local durable storage, do not claim
   that onboarding state or `query-habits.md` was saved.
4. Unknown environment: report the missing capability rather than guessing the
   product or claiming local persistence.

## Local UI boundary

The review UI is an optional same-device capability, not proof of core setup.
Machine-readable quickstart initializes storage and returns
`ui.status=not_started`; it does not create a background server. An Agent may
offer a link only after all of the following are observed:

1. the browser and command runtime share the same device;
2. a long-lived UI process successfully binds a loopback port;
3. `GET /api/health` returns `status=ready`.

If any condition is unknown, keep review in CLI or conversation and do not show
a `127.0.0.1` URL.

## Adding another agent runtime

A new runtime should not require a new branch in `SKILL.md`. Its installer or
adapter only needs to make the Skill discoverable, expose the capabilities
above, and preserve the same CLI/JSON contracts. For first-use learning, it may
also hand off the minimum normalized user Query fields through
`MWORK_HISTORY_FILE` and name its capability with `MWORK_HISTORY_ADAPTER`.
Runtime-specific packaging remains outside the core Skill workflow.
