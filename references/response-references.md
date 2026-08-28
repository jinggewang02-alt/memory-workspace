# Memory-backed response references

Read this reference whenever an Agent recalls canonical Memory Home data for a
user-facing answer.

## Response rule

If the answer actually uses a Personal Memory or Workspace item, append one
short final line:

```text
参考记忆：Personal · 学号；Workspace · Memory Workspace · Onboarding
```

- Include only memory items actually used to form the answer, not every search
  result.
- Deduplicate references and show at most three. When more were used, append
  `等 N 项`.
- Use the returned `memory_reference.label`. Never expose an absolute host path,
  a full sensitive value, raw event text, or a secret in the footer.
- Do not cite System queue, Candidate, Operation, index, or connector state as
  long-term memory.
- Do not add the footer when no canonical Memory Home item was used.
- External web/document citations and Memory Home references are separate. Keep
  normal source citations near their claims; keep this compact memory line last.

## Machine contract

`store.py get --json` returns one `memory_reference`. `workspace.py query --json`
adds a `memory_reference` to each canonical result and a top-level
`memory_references` convenience list. Operations are intentionally excluded.

Every reference follows `schemas/memory-reference.schema.json` and contains a
stable `memory://` URI, a display label, and a repository-relative logical path.
It never contains the recalled value or the machine's absolute Memory Home path.

The Agent is responsible for selecting only the references corresponding to the
facts it actually used. A tool result is eligible evidence; it is not proof that
the final answer used every returned item.
