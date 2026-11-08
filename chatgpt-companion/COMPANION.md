# ChatGPT companion for Codex desktop

This optional skill uses your existing ordinary ChatGPT conversation as a
thinking partner. It requires Python 3.12+ and Codex desktop with native
`read_thread` / `send_message_to_thread` tools. Consultations use your ChatGPT
account and its available limits. Your question and selected project context are
visible in the destination conversation. Receipts stay on your computer.

Codex and ChatGPT can explore ideas, disagree and ask each other questions.
The user's requirements guide the discussion and any handoff. ChatGPT can
suggest methods or checks, and Codex chooses what the task and repository
require.

## Install and select a destination

Run from the `chatgpt-companion` directory or the extracted standalone ZIP.

```powershell
python -B install_skill.py
```

For an isolated or nonstandard Codex configuration root:

```powershell
python -B install_skill.py --codex-root "C:/path/to/codex"
```

The installer copies `work-partners-chatgpt` into your Codex skills directory and
preserves local edits. The Python helper stores receipts on your computer.
Codex desktop provides the native tools for sending and reading messages.

In Codex desktop say **“Set up ChatGPT for this project and show its status.”**
The agent checks tool availability and identifies an intended existing ordinary
ChatGPT chat. If needed, open a new ordinary ChatGPT chat yourself once and give
Codex its link. Codex's native new-task tool does not create ordinary ChatGPT chats.
The agent verifies the chat kind/title/link and binds it for the current project.
Then say **“Involve ChatGPT in discussing …”**. No manual message copying is needed.

## Status and recovery

Ask **“Show this project's ChatGPT status”** or **“Recover the pending ChatGPT
consultation.”** The agent uses the installed helper and native reads. Status
shows the destination, pending/recent request IDs and next action. It distinguishes
local intent, desktop acceptance, an observed matching turn and a complete reply.
An accepted send is not proof that the reply is available; a completed native turn
can still contain partial text. Recovery rereads; it never resends the question.

The send-outcome audit keeps actual native results (including errors), timestamps,
file hashes/location and dispatch identity privately. Generic errors, rate limits,
timeouts and unrecognized results mean **unknown delivery**. Confirmed rejection
before delivery requires a verified native contract; no production rejection
adapter is currently registered. Its terminal path is tested with a synthetic
contract only. A native payload cannot declare itself authoritative. Contradictory
later evidence is flagged and retained without taking a newer request's slot.
The audit records operator-supplied evidence; it is not a signed server attestation.

If continuing to wait is no longer useful, ask to **abandon that consultation**.
The agent records the reason and ends local waiting without deleting history,
cancelling ChatGPT or claiming the message was never sent. It can recover a late
reply by request ID. A new consultation is a new request, not a retry of the old
one. A different intended chat can be selected while old receipts remain visible.
Do not silently repeat an uncertain question or end another project's request.

Commands below are for the agent; use the absolute installed helper path:

```text
python chatgpt.py status --project ABSOLUTE_PROJECT
python chatgpt.py setup --project ABSOLUTE_PROJECT --native-json READ_JSON
python chatgpt.py prepare --project ABSOLUTE_PROJECT --question-file QUESTION
python chatgpt.py dispatch --request ID --native-json CURRENT_READ_JSON
# Save the entire dispatch output as DISPATCH_JSON, then send its arguments ONCE.
python chatgpt.py send-outcome --request ID --dispatch-json DISPATCH_JSON --native-json SEND_RESULT_JSON
# If no tool result returned, use --no-result-reason REASON instead of --native-json.
python chatgpt.py audit --request ID
python chatgpt.py recover --request ID --native-json LATEST_READ_JSON
python chatgpt.py status --request ID
python chatgpt.py abandon --request ID --reason REASON
```

`bind`/`collect` remain compatibility aliases for setup/recover. `discard` ends an
unsent prepared request. `sent` remains an audit alias; without `--dispatch-json`
it retains the result as unknown rather than claiming same-chat acceptance.
The dispatch record must match the exact request, destination, prompt and new
dispatch identity. Saved legacy dispatch records also work; older receipts remain
readable without invented historical audit evidence. Duplicate audit recording is
idempotent. No outcome-recording command sends or authorizes replay.
Default private storage is
`%LOCALAPPDATA%/WorkPartnersChatGPT/consultations`. For an isolated test, place
`--data-dir ABSOLUTE_PRIVATE_FOLDER` before the command or use a child process's
isolated `LOCALAPPDATA`; do not change global environment variables. Native JSON
and question/context files belong outside project Git. No personal receipts are
included in the package. The helper's state is local to each installation/store.

Replies currently need the final correlation footer and fit within the host's
native read limit (tested at 20,000 characters per item). Incomplete output stays
incomplete. Images, automatic chat creation and other client transports are not
provided. Read-only consultation instructions do not reconfigure connected apps
or create a separate ChatGPT tool sandbox.

To make a standalone four-file ZIP, run the installer with `--bundle`.

```powershell
python -B install_skill.py --bundle "C:/path/to/chatgpt-companion.zip"
```
