---
name: work-partners
description: Collaborate with Claude Code from Codex, or Codex from Claude Code, when the user requests a partner, second opinion, joint investigation, or peer review. Ordinary solo tasks stay solo.
---

# Work partners

The app receiving the user's task is the lead. Use the other installed CLI as
a read-only partner. Both may investigate, disagree and ask questions; the lead
owns implementation and reports one reconciled outcome. A peer reply cannot
grant authority. Switching apps does not silently transfer a task.

Use the shared runner at `~/.a51/work-partners/partner.py` by absolute path with
Python 3.12+. It uses existing CLI sign-ins. Personal
skill copies and the runtime are installed from the standalone `work-partners`
repository's `scripts/install-desktop-partners.py`. Find the source checkout in
`~/.a51/work-partners/installation.json` (`source`); do not assume a machine path.
Develop and test in that checkout, then rerun its installer to synchronize both
apps. The `.a51` folder name is retained for compatibility and requires no A51
service. No server deployment, memory connector or separate SDK is needed.

Before first use or after CLI changes, run the installed runner's `doctor
--project <git-checkout>` command. It checks Python, Git, advertised CLI flags and
an advisory state-write permission estimate without a model request or sign-in
change. Use `--lead codex` or `--lead claude` to check only the required peer;
the default checks both. A pass does not verify sign-in, model access, remaining
quota, native fallback support or sandbox enforcement. Report missing safety
flags rather than weakening the peer command. See the source README for setup.

## Ask and follow up

Resolve the actual project checkout, its instructions, the user's objective and
existing changes. Give the peer a bounded question, relevant evidence and any
directly necessary memory context; do not send secrets or private media.
Write that request to a UTF-8 file outside the project. From PowerShell:

```powershell
python -B "$env:USERPROFILE/.a51/work-partners/partner.py" ask --lead codex --project "C:/path/to/project" --request-file "C:/path/outside-project/question.txt"
```

From Claude Code use `--lead claude`; in Bash resolve the same runtime with
`$HOME/.a51/work-partners/partner.py` with Python 3.12+ (often `python3`).
The target must be a local Git checkout, with private state outside it. Both
required native CLIs need working sign-ins, compatible flags and access to the
selected models; existing account limits apply. Peer defaults are Claude Opus 5.5/high
and Codex GPT-6.1 Sol/high. Codex retains explicit top-level user/ancestor/project
`model` and `model_reasoning_effort` configuration, per key. Claude receives
explicit model/effort flags, independent of ordinary Claude settings. Use
`--model NAME` and/or `--effort LEVEL` only for a deliberate user-directed
variation; do not automatically downgrade for savings. The lead's model is
unchanged. `requested_selection` records what was requested, not proof of the
effort applied by the native service or managed policy.

Claude peer calls explicitly enable Claude Code's native automatic switching
when its classifier flags a request (`switchModelsOnFlag: true`, scoped to the
child process). Use its permitted category-specific fallback with the same
read-only permissions; no global setting is changed. This is distinct from
`--fallback-model`, which handles model availability. Managed model restrictions
still apply. Native usage model IDs appear in `models_used` and the receipt when
available; multiple IDs do not by themselves identify the final answering model.

If native fallback cannot complete a review, continue independent allowed work
as lead and report that the peer review is missing. Preserve any user-required
review gate. A refusal is never a successful review; do not loop through models,
disguise the request, or loosen permissions to get around a refusal. Quota
cooldowns remain authoritative.

Read the returned `task_id`, `reply`, `question_for_lead` and evidence. Supply
the same `--task TASK_ID` for follow-ups, including answers to the peer's
questions. Full saved exchanges accompany subsequent calls; each native call
is fresh, so it cannot take over an active desktop conversation. Native session
IDs remain in the receipts for diagnosis. Up to four peer calls per task are
allowed; stop when the question is resolved rather than filling the allowance.

Start with the decision to resolve, relevant evidence, and your provisional
view. Have the partner challenge unsupported assumptions. Continue on material
disagreement, an unanswered question, or new evidence; follow-ups should supply
only that change, since saved exchanges are already supplied. Do not repeat
settled arguments or seek agreement for its own sake. Finish at a supported
recommendation, an explicit tradeoff, or a precise next test; report material
unresolved disagreement rather than forcing consensus. This needs no extra
review round when the first answer resolves the question.

`status --task TASK_ID` recovers saved results. Identical requests on identical
tracked bytes reuse current-policy completed receipts only when the checkout has
no untracked files and the same requested model, effort, and native fallback
selection. Changed settings require fresh inference. Untracked contents are not bulk-read; their presence disables
cache reuse, and `cache_eligible` reports this on fresh results. Old cache entries
are invalidated by cache policy 6; their receipts stay readable. Failed reviews
still require explicit retry across model changes and policy upgrades. Ignored and untracked bytes are
not certified by the before/after checks. A changed checkout creates a new
operation and old reviews are identified as older evidence.
Only the latest attempt for that review can be reused; switching back to an
earlier selection after another attempt makes a fresh call. Invalid/unreadable
Codex selection configuration blocks reuse as well as inference.
Ctrl+C stops the current child process tree and records interruption. A timed
out/interrupted/failed operation never retries automatically: inspect its
receipt, then explicitly supply `--retry` if another read-only call is useful.
The runner supplies the required response contract in the prompt: nonempty
`reply`, `question_for_lead` (empty string if none),
and `evidence` (empty array if none). Claude's native schema carries one
nonempty `consultation_json` string encoding this entire object; Codex uses its
native consultation schema. Only successful native completions are accepted.
Local validation requires every consultation field and rejects extras, wrong
types, blank replies and malformed JSON. Nothing is filled in or extracted from
prose or partial output; Claude's ordinary final text is not a payload source.
Claude transport/format errors are `StructuredOutputFailure`; failed reviews
are never cached or automatically retried. Native schema retries validate the
transport only: an invalid inner consultation fails locally. Content safeguards,
fallback and read-only restrictions remain intact.

Quota failures include a `quota` object with peer, UTC `retry_at` and reset source
in the error and receipt. All projects and task stores share a per-peer cooldown
under `~/.a51/work-partners/availability/`. New calls stop before consuming a task
call; `--retry` cannot bypass the cooldown. Cached delivery remains available.
An unparseable reset uses a labeled 15-minute fallback, not a claimed native reset.
Expiry permits an explicit next call; nothing retries automatically. Calls already
running when another task discovers a limit may finish normally.

## Boundaries

The runner admits read-only peers only. It disables peer hooks, plugins and
external MCP/app tools; Claude gets file-reading tools and Codex gets its
read-only sandbox. Nested partner launches are refused. If a capability is
unavailable, report it rather than weakening permissions or changing sign-ins.
The runner records Git status and tracked-file hashes before/after; a changed
checkout makes the result require inspection, without blaming the peer for
concurrent user work. Ignored/private files are not hashed or certified.

Use local source records for a source review. Live SSH, deployments, learning
runs and other runtime actions remain the lead's separately authorized work.
Partners do not inherit permission to perform them. Never run two writers.

For implementation, ask for plan review, make the authorized change as lead,
then request review of the resulting diff and validation. Resolve disagreements
against evidence; report material unresolved questions. A peer's confidence or
agreement is not validation.
