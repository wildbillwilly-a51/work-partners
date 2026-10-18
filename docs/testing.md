# Testing

## Offline checks

From this checkout, with Python 3.12+ and Git:

```powershell
python -B -m unittest discover -s tests -p test_desktop_partners.py
```

Use `python3` on systems where that is the compatible interpreter. Tests create
temporary Git fixtures and mock model responses. They cover both directions,
discussion context, strict delivery, caches/upgrades, shared cooldowns, quota
parsing, retry gates, locks, tracked changes, timeout termination, call bounds,
CLI restrictions, prerequisite checks and installation preservation/custom roots.
They make no model request or sign-in change. They do not prove vendor behavior,
account access or complete sandbox enforcement.

## Installation and local checks

```powershell
python -B skills/work-partners/scripts/partner.py doctor --project .
python -B scripts/install-desktop-partners.py --home <temporary-directory>
```

An explicit temporary home isolates installation from the real user and ignores
agent-root environment variables unless explicit root options are also supplied.
Keep it outside this checkout. Check the four installed files and ownership
manifest. Modified installed files must be preserved on refusal. Never copy
private state into the repository.

For a real installation, omit `--home`. The installed runner's `doctor` verifies
local advertised options with no model request or task-state write. Missing flags
fail without safety downgrades. Sign-ins, model access, quota, native settings and
sandbox efficacy remain unverified; the permission estimate is advisory.

## Explicit live acceptance

Live calls consume existing account limits. Run them only when authorized and
no shared cooldown is active. Never automatically retry a known quota failure.

For a Claude lead consulting Codex, the optional harness creates a separate Git
fixture by default and retains evidence outside the checkout:

```powershell
python -B tests/manual_desktop_partners.py --output "$env:USERPROFILE/.a51/work-partners/validation/acceptance"
```

It asks Claude to read the installed skill and invoke the specified runner once,
then checks the completed Codex receipt, cached redelivery and tracked bytes.
The arithmetic fixture intentionally omits the last value; inspect the result
to confirm the peer finds that defect. `--project <git-root>` selects an existing
authorized target instead. On POSIX, use the matching Python executable and
`$HOME` path. CLI support is required; Windows is the acceptance-tested platform.

For a Codex lead consulting Claude, invoke the installed runner with `--lead
codex`, pointing to a small Git fixture and request outside it. Use the returned
task ID for a focused follow-up. Check field types, evidence, an empty question
when resolved, unchanged tracked bytes and cached delivery. Keep within four
calls. Native model usage alone does not prove the final answering model.

Native discovery and CLI function are distinct. Restricted Claude peers hide
personal skills; they receive their read-only instructions directly. Normal new
sessions discover personal skills; an existing session can read SKILL.md directly.
These tests do not certify desktop UI operation or live service health. macOS/Linux
live acceptance is unverified. See [usage](desktop-partners.md) and [NOTICE](../NOTICE).
