# Local Review Inbox

Status: MVP v0.3

The Review Inbox is a local, derived view over asynchronous capture candidates.
It is not a second memory store and it does not write directly to Workspace
Markdown or Exact Profile JSON.

## Start

Run the capability probe first in a new Agent environment, then launch:

```bash
python3 <skill-dir>/scripts/ui.py
```

The command opens `http://127.0.0.1:8741/`. Use `--no-open` when the current
environment cannot open a browser, and `--port <port>` if the default port is
occupied. The host is intentionally limited to `localhost` or `127.0.0.1`.

## UI contract

The UI exposes two connected workflows. First-use learning:

1. read onboarding status without mutating history;
2. when a host has configured an Agent-visible history handoff, start one
   idempotent rolling 30-day analysis automatically;
3. otherwise accept one exact local JSONL path selected by the owner;
4. show conversation/Query coverage and tentative Query habits;
5. only after an explicit owner action, confirm the Markdown report and
   activate its matching Policy draft.

Candidate review then continues through the existing workflow:

1. list proposed, approved, rejected, or applied Candidates;
2. inspect the Candidate, its resolution reason, Policy metadata, and redacted
   evidence previews;
3. approve an exact target or reject it with optional similar-item suppression;
4. after a separate explicit action, call the existing single Writer and show
   its verified application receipt.

Sensitive Candidate content is omitted from the normal detail response. The
owner must explicitly reveal it through a session-token-protected local
request.

## Security boundary

- The HTTP server refuses non-loopback bind addresses and non-loopback Host
  headers.
- Mutation and sensitive reveal requests require an unguessable in-memory
  session token.
- Responses disable caching, framing, cross-origin scripts, and MIME sniffing.
- The API accepts JSON bodies up to 64 KiB and never enables CORS.
- Candidate approval and rejection call `capture.decide_candidate`. Apply calls
  `writer.apply_candidate`, which uses a reviewed Workspace Operation or exact
  Profile write/readback and then records a schema v2 application receipt.
- Browser code does not bypass validation or directly edit queue or canonical
  files. A failed Writer leaves the Candidate `approved` for safe retry.
- The browser never discovers account history itself. `/api/onboarding/run`
  delegates to the same bounded importer and only reads an explicit path or the
  host-configured `MWORK_HISTORY_FILE` handoff. Rows outside 30 days are not
  turned into Events.
- `query-habits.md` contains abstract observations, counts, confidence, and
  Agent guidance; it does not copy raw Query text. Tentative habits and the
  Policy remain inactive until `/api/onboarding/confirm`.

This is a local convenience boundary, not protection from other software
already running with the owner's filesystem or process permissions.
