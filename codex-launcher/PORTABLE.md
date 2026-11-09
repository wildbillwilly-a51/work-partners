# Start Codex chats from regular ChatGPT

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
