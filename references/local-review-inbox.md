# Local Review Inbox

Status: MVP v0.1

The Review Inbox is a local, derived view over asynchronous capture candidates.
It is not a second memory store and it does not write directly to Workspace
Markdown or Profile Memory JSON.

## Start

Run the capability probe first in a new Agent environment, then launch:

```bash
python3 <skill-dir>/scripts/ui.py
```

The command opens `http://127.0.0.1:8741/`. Use `--no-open` when the current
environment cannot open a browser, and `--port <port>` if the default port is
occupied. The host is intentionally limited to `localhost` or `127.0.0.1`.

## UI contract

The first release exposes one workflow:

1. list proposed, approved, rejected, or applied Candidates;
2. inspect the Candidate, its resolution reason, Policy metadata, and redacted
   evidence previews;
3. approve an exact target or reject it with optional similar-item suppression;
4. leave canonical application to the existing single Writer.

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
- Candidate approval and rejection call `capture.decide_candidate`; the UI does
  not bypass validation or edit queue files.

This is a local convenience boundary, not protection from other software
already running with the owner's filesystem or process permissions.
