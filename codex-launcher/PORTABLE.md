# Start Codex chats from regular ChatGPT

In Codex or Claude Code, say **“Set up Work Partners so regular ChatGPT can
send work to Codex.”** The agent performs the local installation and tests the
connection. The user chooses a local Codex project, completes any required
OpenAI sign-in or approval, and pastes a restricted key into the launcher's
masked window. The launcher starts in the background at Windows sign-in.
Codex desktop must be running and signed in to receive a handoff.

**Preview.** Guided setup is new and has not yet been tested on a freshly set
up Windows account. If a step fails, the agent reports the failing step and its
output, and can continue with the advanced portable setup below.

## Agent workflow

Run these commands yourself from the launcher directory. It is
`codex-launcher` in the distribution and `experiments/chatgpt-context` in
the development checkout. Use the
same per-user launcher home throughout. The default is
`%USERPROFILE%/.a51/WorkPartnersChatGPT`, outside packaged app redirection.
The launcher keeps credentials and receipts outside Git.

1. Run `python -B setup.py status` and
   `python -B setup.py preflight`. Resolve any missing trusted
   prerequisites before changing account settings. If an existing portable
   installation is detected, preserve it and verify or repair it separately.
   Never run two clients for the same tunnel.
2. Resolve a local Codex routing chat and the project the user selected. Use
   native Codex tools when available. From Claude Code, if needed, guide the
   user to open Codex desktop and copy a local chat link. The entire
   `codex://threads/…` link is accepted. Run
   `setup.py projects --context LINK` if needed to validate and list the
   registered local projects. Use the selected project's exact path.
3. Open the OpenAI Platform **Tunnels** page returned by status. Guide the user
   to sign in and select or create a Secure MCP Tunnel in the intended ChatGPT
   workspace. Reuse a suitable existing tunnel. The tunnel ID is configuration,
   not the secret. Run `setup.py configure --context LINK --project PATH
   --tunnel ID`. This downloads and checks the pinned official tunnel client
   and installs the launcher with only the selected project in scope.
4. Open the Platform **API keys** page returned by status and guide the user
   to create a restricted key with only **Tunnels Read and Use**. Let the user
   complete key creation. Run `setup.py prompt-key` to open the local window.
   The on-demand Windows task keeps this window independent of the agent's
   shell. Ask the user to paste the key there and select **Verify and save**. Keep the
   key out of chat, terminal arguments, logs, and browser or clipboard reads.
   Poll `setup.py status` for the verified result and resume automatically.
   The prompt lets the user retry without losing a previous saved key. To
   replace an existing key, use the same prompt, then activate and verify the
   new connection before retiring the old key.
5. Run `setup.py activate`. It registers and starts a limited per-user Windows
   sign-in task without a console window. Wait for `next: verify_chatgpt`
   on a new install or key. An already verified key can return `next: ready`.
   If startup reports `retrying`, poll status while the launcher retries.
   In ChatGPT, guide or use the custom MCP connector with **Tunnel**, selecting
   the same ID. Refresh its tools if the launch action is missing, with any
   required user approval.
6. When verification is needed or the user asks for a fresh test, run
   `setup.py prepare-test`. Send its synthetic prompt in an ordinary
   ChatGPT conversation with the connector selected. Use the signed-in browser
   when available. If the host cannot operate regular ChatGPT, open the chat
   and ask the user to submit only that prepared ChatGPT prompt. Use any
   in-product confirmation required for the handoff. Never dispatch this
   setup test directly through the local launcher.
7. Run `setup.py verify-test --codex-link LINK` with the link returned by
   regular ChatGPT. This matches the local handoff receipt to the actual Codex
   thread and checks its project, synthetic request and exact reply. Poll
   while the reply is pending. Keep the same handoff ID on an uncertain
   delivery. Report the link and completion only when `verified: true`.

An agent without access to the user's regular ChatGPT browser can still
verify the handoff locally after the user submits the prepared ChatGPT prompt
and provides the returned Codex link. A local diagnostic or tunnel readiness
check alone does not verify the ChatGPT route.

## Advanced portable setup

The optional Windows launcher lets ChatGPT start a new chat in a registered
local Codex project and give it the user's request and handoff. It needs a
running, signed-in Codex desktop app, Python 3.12+, Node.js 24+ with npm, Git,
and ChatGPT access to custom MCP servers through a Secure MCP Tunnel.

1. Open a local chat in Codex desktop and use **Copy chat deep link**. Copy the
   chat ID from the end of the link. The launcher uses that chat to route the
   desktop request. It starts a separate chat in the project you name.
2. Set up your own [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
   and download the official Windows tunnel client. The installer recognizes
   the tested v0.0.19 client by checksum. For a different client, supply its
   independently verified checksum with `--tunnel-sha256 HASH`.
3. From the `codex-launcher` directory of this repository, run:

   ```powershell
   python -B install.py install --workspace "C:/path/to/workspace" --tunnel-id tunnel_YOURS --tunnel-binary "C:/path/to/tunnel-client.exe" --codex-context-thread YOUR_CHAT_ID
   cd "$env:LOCALAPPDATA/WorkPartnersChatGPT"
   .\work-partners.cmd doctor --local-only
   .\work-partners.cmd start
   ```

   Set `--workspace` to a directory containing the Git projects you want to
   expose, one level down. Repeat the option for more workspace directories.
   The tunnel key is read from `WORK_PARTNERS_TUNNEL_KEY` or a hidden prompt
   at startup. Never put it in a command line, Git, or a chat.
4. In ChatGPT, add a custom MCP server with **Tunnel** and select the same ID.
   For an existing connection, use **Manage → Refresh tools**. In regular Chat,
   ask: “Start a new Codex chat in MyProject with this plan.”

Only a registered local Codex project whose folder matches a configured project
can be launched. The opening prompt uses your Codex app's normal settings and
approvals. The tool returns the chat URL and stores the full handoff locally
under the install's private state directory. For a long handoff, Codex receives
the receipt ID and can read the full text with:

```powershell
.\work-partners.cmd handoffs --project MyProject --handoff-id HANDOFF_ID
```

Reuse the same handoff ID for retries. An uncertain result means the desktop
may already have opened the chat. Check the Codex sidebar before launching
another. Use `work-partners.cmd status` and `work-partners.cmd stop` in a second
terminal to inspect or stop the tunnel. Keep the computer online while using it.
