# Local Memory Home UI

Status: MVP v0.6

The UI is a local, derived view over the whole Memory Home. Personal Memory,
Workspaces, first-use learning, and asynchronous capture candidates are sibling
sections in one interface. It is not a second memory store.

## Start

The preferred same-device experience is the Electron host:

```bash
npm install
npm run desktop
```

Electron launches `quickstart.py` itself, selects a supported Python 3.10+
runtime, initializes the durable Memory Home idempotently, starts Core on a
random loopback port, waits for `GET /api/health`, and only then reveals the
window. Closing the application stops the child Core process. The restricted
preload exposes runtime metadata only; renderer code has no Node or filesystem
access.

The first-run shell offers three routes: direct start, one explicitly selected
30-day history handoff, or optional Provider setup. Completing this shell writes
only `system/ui/onboarding.json` through `POST /api/ui-state/complete`; it does
not change canonical Personal or Workspace data.

The browser entry remains a compatibility path.

Run the capability probe first in a new Agent environment, then launch:

```bash
python3 <skill-dir>/scripts/ui.py
```

The command opens `http://127.0.0.1:8741/`. Use `--no-open` when the current
environment cannot open a browser, and `--port <port>` if the default port is
occupied. The host is intentionally limited to `localhost` or `127.0.0.1`.

Loopback is reachable only from a browser on the same device while the server
process remains alive. A remote Agent MUST NOT present its loopback URL to the
user. After launch, verify `GET /api/health`; only a response containing
`status=ready` proves that the link is currently live. `quickstart --json` never
starts this process and returns `ui.status=not_started` with a null URL.

## Home read-model contract

`GET /api/home` returns a schema-validated `memory-home-view` document:

- Home identity and readiness;
- masked Exact Profile field metadata, never the stored values;
- read-only Personal Work and preference Markdown, capped at 32,000 characters
  per document;
- counts for people, themes, timeline entries, and captures;
- Workspace identity, health, source count, and operation count without absolute
  Workspace paths, plus mapped-source and last-sync status;
- current candidate-review counts.

The protocol is defined by `schemas/memory-home-view.schema.json`. An exact
Profile value is fetched only after an explicit owner action through
`POST /api/personal/profile/reveal`, which requires the in-memory session token.

## UI workflows

The UI exposes four connected workflows.

Home and Personal Memory:

1. show Personal Memory and Workspaces as sibling scopes under one Home;
2. list Exact Profile keys with values masked;
3. reveal a selected field only after an explicit token-protected action;
4. allow explicit create/update of a single-value Profile field, then read the
   exact value back before reporting success;
5. keep structured Profile entries and Personal Work/preference Markdown
   read-only in the current UI.

Workspace overview:

1. list the Workspaces already registered under this Home;
2. show each Workspace's health, source count, mapped external-source count, and last sync;
3. load `GET /api/workspaces/<id>` to display the rebuildable Project Memory view;
4. show recent evidence excerpts, explicit decision/action markers, observed people,
   artifacts, and Source Note references;
5. keep project file editing in the existing Agent/Operation workflow.

First-use learning:

1. read onboarding status without mutating history;
2. when a host has configured an Agent-visible history handoff, start one
   idempotent rolling 30-day analysis automatically;
3. otherwise accept one exact local JSONL path selected by the owner;
4. show conversation/Query coverage and tentative Query habits;
5. only after an explicit owner action, confirm the Markdown report and
   activate its matching Policy draft.

Candidate review continues through the existing workflow:

1. list proposed, approved, rejected, or applied Candidates;
2. inspect the Candidate, its resolution reason, Policy metadata, and redacted
   evidence previews;
3. approve an exact target or reject it with optional similar-item suppression;
4. after a separate explicit action, call the existing single Writer and show
   its verified application receipt.

Sensitive Candidate content and Exact Profile values are omitted from normal
listing responses. The owner must explicitly reveal either through a
session-token-protected local request.

## Security boundary

- The HTTP server refuses non-loopback bind addresses and non-loopback Host
  headers.
- Mutation and sensitive reveal requests require an unguessable in-memory
  session token.
- Responses disable caching, framing, cross-origin scripts, and MIME sniffing.
- The API accepts JSON bodies up to 64 KiB and never enables CORS.
- Exact Profile single-value create/update calls the existing Profile writer,
  rejects common secret shapes, and immediately performs an exact readback.
  Existing structured entries cannot be overwritten through this endpoint.
- Candidate approval and rejection call `capture.decide_candidate`. Apply calls
  `writer.apply_candidate`, which uses a reviewed Workspace Operation or exact
  Profile write/readback and then records a schema v2 application receipt.
- Browser code cannot directly edit Personal Markdown, Workspace files, queue
  files, or structured Profile entries. A failed Writer leaves the Candidate
  `approved` for safe retry.
- The browser never discovers account history itself. `/api/onboarding/run`
  delegates to the same bounded importer and only reads an explicit path or the
  host-configured `MWORK_HISTORY_FILE` handoff. Rows outside 30 days are not
  turned into Events.
- The Electron main process owns the Python child lifecycle, uses an ephemeral
  loopback port, blocks cross-origin navigation and popups, and denies renderer
  permission requests. External Providers remain disabled until configured
  through their existing Core contract.
- `query-habits.md` contains abstract observations, counts, confidence, and
  Agent guidance; it does not copy raw Query text. Tentative habits and the
  Policy remain inactive until `/api/onboarding/confirm`.

This is a local convenience boundary, not protection from other software
already running with the owner's filesystem or process permissions.
