import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { z } from 'zod';
import { fromConfig } from './core.mjs';
import { ProjectAccess } from './project-access.mjs';
import { startProjectChat } from './codex-messages.mjs';

const configPath = process.env.WORK_PARTNERS_CONTEXT_CONFIG;
const config = JSON.parse(readFileSync(configPath, 'utf8').replace(/^\uFEFF/, ''));
const store = fromConfig(configPath);
const access = new ProjectAccess(config, store, configPath);
const server = new McpServer({name: 'Work Partners Codex Launcher', version: '0.3.0'});
const read = {readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false};
const project = z.string().min(1).max(200);
const handoff_id = z.string().regex(/^[a-zA-Z0-9_-]{1,120}$/);

function tool(name, description, inputSchema, handler, annotations = read) {
  server.registerTool(name, {description, inputSchema, annotations}, async args => {
    try {
      const data = await handler(args);
      return {content: [{type: 'text', text: JSON.stringify(data)}], structuredContent: data};
    } catch (error) {
      const message = /^[a-z_]+$/.test(error.message) ? error.message : 'launcher_error_check_local_runtime';
      return {isError: true, content: [{type: 'text', text: message}]};
    }
  });
}

tool('list_projects', 'List locally configured projects by name and ID. Use the ID when names conflict.',
  {}, () => ({projects: access.projects().map(({name, project_id}) => ({name, project_id}))}));
tool('list_project_handoffs', 'Read saved handoff receipts. Supply the handoff ID to read its full text.',
  {project, handoff_id: handoff_id.optional()}, args => access.handoffs(args));

if (config.codexMessaging?.enabled && config.codexMessaging.creationContextThread) {
  const bridge = {...config.codexMessaging, handoffLauncher: join(dirname(configPath), 'work-partners.cmd')};
  tool('start_project_chat_in_codex',
    'When the user asks to start a new Codex chat in a named local project, give it the user’s request and complete handoff text. Use list_projects to resolve the project. Use one handoff ID per requested launch and reuse it on retries. On chat_started, show the exact chat_url as code-formatted text. On unknown, the desktop might have created the chat, so never resend under a new ID. staged_for_pickup confirms only that the handoff was saved.',
    {project, handoff_id, title: z.string().min(1).max(200), markdown: z.string().min(1).max(96000)},
    args => startProjectChat(access, bridge, args), {...read, readOnlyHint: false});
}

await server.connect(new StdioServerTransport());
process.on('SIGTERM', () => {store.close(); process.exit(0);});
