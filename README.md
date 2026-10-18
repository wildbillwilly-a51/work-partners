# Work partners

Let Codex and Claude Code discuss a project using their existing native CLIs.
The app receiving your task leads and owns edits. The other agent provides
bounded, read-only evidence, questions and challenges until there is a useful
conclusion, tradeoff or next test.

Source-available, free to install and use on your own computers. Redistribution,
commercial distribution and publication of modified versions require written
permission. This is not an open-source license. See [NOTICE](NOTICE).

## Requirements

- Python 3.12+ and Git on the local computer.
- Compatible Codex and Claude Code CLIs, working sign-ins, access to the selected
  models and available account quota. The runner needs the other agent's CLI for
  one direction; exercising both directions needs both CLIs.
- A local Git checkout to review, and permission to save private task state
  outside it, in your home directory.

No server, SDK, private infrastructure or memory connector is required. Windows
is the acceptance-tested platform. macOS/Linux live operation is unverified.
Linked installation roots are refused, which can affect POSIX temporary paths.

## Install

Clone or download this repository, then run from its directory on Windows:

```powershell
python -B skills/work-partners/scripts/partner.py doctor --project .
python -B scripts/install-desktop-partners.py
python -B "$env:USERPROFILE/.a51/work-partners/partner.py" doctor --project "C:/path/to/project"
```

On macOS/Linux, use a Python 3.12+ executable, usually `python3`:

```bash
python3 -B skills/work-partners/scripts/partner.py doctor --project .
python3 -B scripts/install-desktop-partners.py
python3 -B "$HOME/.a51/work-partners/partner.py" doctor --project /path/to/project
```

The installer places matching personal skills in both agents' configuration
directories and one shared runner at `~/.a51/work-partners/partner.py`. It records
source ownership and hashes in `~/.a51/work-partners/installation.json`. The `.a51`
folder and `A51_PARTNER_*` environment names are compatibility names with no
private-service dependency. The installer changes no sign-ins or model settings.
It refuses locally modified files, linked/overlapping destinations and installation
inside the source checkout.

Custom config roots follow CODEX_HOME and CLAUDE_CONFIG_DIR. Explicit installer
`--codex-root <dir>` / `--claude-root <dir>` options take precedence; these must be
the roots used by the corresponding CLIs. Changing a root leaves the old copies
untouched. For an isolated test, `--home <temporary-dir>` ignores those environment
variables unless explicit root options are supplied.

## Check compatibility

`doctor` uses CLI version/help and Git metadata probes with bounded timeouts. It
makes no model request, sign-in change or task-state write. Vendor startup activity
is outside the check's control. Use `--lead codex` to check the Claude peer,
`--lead claude` for Codex, or the default to check both.

A pass verifies advertised flags only. Sign-in, model entitlement, remaining
quota, native settings/fallback support, sandbox enforcement and live delivery
remain unverified. The state-write estimate is advisory, especially for Windows
ACLs. Missing or hidden required flags fail the check; do not remove safety flags
to make an incompatible CLI pass. There is no minimum CLI-version guarantee.

CLIs are found on PATH; Windows can use an existing bundled Codex desktop engine.
`A51_PARTNER_CODEX_CLI` / `A51_PARTNER_CLAUDE_CLI` select explicit trusted files.
Git and peer executable paths are resolved to absolute paths, and executables
inside the reviewed checkout are refused.

## Use

Ask either agent to use work-partners for a second opinion or a discussion of
your current project. New chats discover the skill; an existing chat can reread
the installed SKILL.md. [Usage](docs/desktop-partners.md) covers manual commands,
follow-ups and safeguards. [Testing](docs/testing.md) covers offline and explicit
live checks.

The default peers are Claude Opus 5.5/high and Codex GPT-6.1 Sol/high. Codex
top-level model/effort configuration and intentional `--model` / `--effort`
overrides are supported. Access depends on your account; no automatic capability
downgrade is made. Native classifier fallback remains subject to native policy.

Read-only access is not secret redaction. Peers can read untracked/ignored files
in the authorized project. Context and source read by the peer go through your
native CLI account; choose approved projects and context. Runner receipts stay
under `~/.a51/work-partners/state/`; native CLIs can also keep local transcripts.
Keep these, sign-ins and private state out of Git. Provider processing follows
the provider's account terms. See [NOTICE](NOTICE) for usage rights and data flow.
