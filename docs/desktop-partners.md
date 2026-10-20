# Using work partners

The app receiving the task is the lead. It owns edits and final decisions. The
peer stays read-only and cannot grant authority. Switching apps does not silently
transfer ownership. Never use two writers on the same files.

## Ask and discuss

The installed skill documents the workflow. For implementation, complete plan
review before editing; challenge the design and its highest-impact failure paths.
For manual use, write a bounded request to a UTF-8 file outside the reviewed Git
checkout, then run:

```powershell
python -B "$env:USERPROFILE/.a51/work-partners/partner.py" ask --lead codex --project "C:/path/to/project" --request-file "C:/path/outside-project/question.txt"
```

From Claude Code use `--lead claude`. On POSIX use Python 3.12+ (often `python3`)
and `$HOME/.a51/work-partners/partner.py`. Run `--help` for all options.

Supply the decision, relevant evidence and provisional view. Quote relevant owner
criteria, constraints and rejected approaches so the peer can flag conflicts
with those goals and challenge unsupported assumptions. Read its reply, question and evidence. Use
the same `--task <id>` to answer a question, introduce material new evidence or
resolve disagreement. Saved completed exchanges accompany each fresh native
session; this does not take over an existing desktop conversation.

When related defects recur across fixes, reassess shared state, ownership or
invariants before another local correction. When necessary evidence is
inaccessible or stale, identify what is missing and qualify the conclusion;
distinguish a suspected risk from a demonstrated defect.

Stop at a supported recommendation, explicit tradeoff or precise next test.
Do not force consensus or fill a review-round quota. Each task permits at most
four peer calls, and peers cannot launch nested partners. `status --task <id>`
recovers private saved results. Large requests or accumulated discussion can hit
the bounded context/time limits; keep questions focused.

For change reviews, supply relevant diff hunks, base/head identifiers, necessary
snippets and test commands/results;
Claude's file-reading tools cannot run Git or tests. Keep the checkout stable
during review and send only new relevant hunks on follow-ups, since saved
requests are replayed in full. If code changes during review, inspect and review
the relevant delta as needed. Distinguish peer-reviewed changes from later
corrections verified only by the lead. Agreement alone is not validation.

## Models and boundaries

Defaults are Claude Opus 5.5/high and Codex GPT-6.1 Sol/high. Codex top-level
configuration overrides defaults per field. Explicit `--model` / `--effort`
overrides allow deliberate variations; the lead's model is unchanged. Requests
record selection separately from native model usage. Neither requested effort
nor model-usage IDs prove applied reasoning or the final answering model.

Claude peers enable native category-based fallback per child process, keeping
managed policy and content safeguards intact. This differs from availability
fallback. The runner does not disguise requests, loosen permissions or implement
its own alternate-model chain. If native fallback cannot complete, the review
fails. Continue independent allowed work and report any missing required review.

Claude gets Read/Glob/Grep in restricted safe mode without custom MCP, hooks or
plugins. Codex uses its native read-only sandbox with approvals disabled and
app/plugin/multi-agent/MCP integrations disabled. Doctor checks advertised flags,
not actual enforcement. Native CLI builds and account policies must support
these settings. No sign-in or global model-setting changes are made.

## Delivery, caching and failures

Only explicit successful native completions are accepted. Claude's native schema
carries one nonempty consultation_json string; strict local validation requires
reply, question_for_lead and evidence, correct types and a nonblank reply.
Optional information uses an empty question string or evidence array, never a
missing field. Ordinary final prose and partial attempts are not salvaged.
Native schema retries validate the outer transport only; malformed inner content
fails locally. Codex retains its native consultation schema.

Completed receipts are saved before delivery. Identical requests can reuse the
latest completed matching receipt only for unchanged tracked bytes, identical
selection, current cache policy and no untracked files. Ignored/untracked bytes
are not hashed or certified. Before/after checks detect tracked changes but do
not certify that every file was untouched or that a concurrent change was the
peer's fault.

Failed, interrupted or timed-out calls never retry automatically. Inspect the
private receipt before an explicit `--retry`. A shared per-peer quota cooldown
under `~/.a51/work-partners/availability/` covers all projects and task stores;
`--retry` cannot bypass it. Cached results remain available. Unknown reset times
use a labeled 15-minute fallback. Expiry does not start a new call, and running
calls are not cancelled when another task discovers a limit.

## Privacy

Requests, raw outputs, session IDs and tracked snapshots stay under
`~/.a51/work-partners/state/`, outside the reviewed checkout. Native CLIs may keep
additional local transcripts. Do not share these or sign-ins. Peers can read
untracked and ignored files: read-only permission is not redaction. Provider
processing follows your native account terms. See [NOTICE](../NOTICE).
