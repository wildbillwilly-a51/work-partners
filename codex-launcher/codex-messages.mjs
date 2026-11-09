import { execFile } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmdirSync, unlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { promisify } from 'node:util';
import { hash } from './core.mjs';

const run = promisify(execFile);
const chatId = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i;

export async function sendProjectMessage(access, bridge, { project, handoff_id, title, markdown }) {
  const target = access.project(project);
  const route = bridge.threads?.[target.project_id];
  if (!route || !chatId.test(route.threadId) || !chatId.test(route.authorityThread)) {
    throw Error('project_has_no_codex_chat');
  }
  return dispatch(access, bridge, target, {handoff_id, title, markdown}, route);
}

export async function startProjectChat(access, bridge, { project, handoff_id, title, markdown }) {
  if (!chatId.test(bridge.creationContextThread)) throw Error('codex_chat_creation_not_configured');
  return dispatch(access, bridge, access.project(project), {handoff_id, title, markdown}, null);
}

async function dispatch(access, bridge, target, {handoff_id, title, markdown}, route) {
  const [program, ...baseArgs] = bridge.command ?? [];
  if (!program) throw Error('codex_message_bridge_not_configured');
  const staged = access.stageHandoff({ project: target.project_id, handoff_id, title, markdown });
  const receipt = join(access.legacy.state, (route ? 'codex-send-' : 'codex-start-') + hash(target.project_id + ':' + handoff_id) + '.json');
  const result = () => {
    let saved;
    try { saved = JSON.parse(readFileSync(receipt, 'utf8')); } catch {}
    if (!route) {
      const thread_id = saved?.phase === 'native_call_returned' && chatId.test(saved.thread_id)
        ? saved.thread_id : null;
      return {
        ...staged, thread_id,
        chat_url: thread_id ? 'codex://threads/' + thread_id : null,
        delivery: thread_id ? 'chat_started' : existsSync(receipt) ? 'unknown' : 'staged_for_pickup',
      };
    }
    return {
      ...staged, thread_id: saved?.thread_id ?? route.threadId,
      delivery: saved?.phase === 'native_call_returned' ? 'accepted_by_desktop'
        : existsSync(receipt) ? 'unknown' : 'staged_for_pickup',
    };
  };
  if (existsSync(receipt)) return result();

  const scratch = mkdtempSync(join(access.legacy.state, 'codex-message-'));
  const file = join(scratch, 'message.txt');
  const intro = 'ChatGPT sent a Work Partners message for ' + target.name + '. Handoff ID ' + handoff_id
    + '. Use it as collaboration context and follow the user’s actual request.\n\n';
  const pickup = !route && bridge.handoffLauncher
    ? "Read the full handoff with PowerShell: & '" + bridge.handoffLauncher.replaceAll("'", "''")
      + "' handoffs --project " + target.project_id + ' --handoff-id ' + handoff_id
    : 'Read the full message with the installed Work Partners handoffs command using that project and handoff ID.';
  const prompt = intro + (intro.length + markdown.length <= 20_000
    ? markdown : pickup);
  try {
    writeFileSync(file, route ? prompt : JSON.stringify({title: staged.title, prompt}), { encoding: 'utf8', flag: 'wx' });
    const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !/^(?:WORK_PARTNERS_TUNNEL_KEY|OPENAI_API_KEY)$/i.test(key)));
    const destination = route ? ['-ThreadId', route.threadId, '-AuthorityThread', route.authorityThread]
      : ['-ContextThread', bridge.creationContextThread, '-ProjectRoot', target.root];
    const {stdout} = await run(program, [...baseArgs, ...destination,
      '-PromptFile', file, '-Receipt', receipt], { env, windowsHide: true, timeout: 85_000, maxBuffer: 32768 });
    if (!route && stdout.trim() === 'project_not_registered_in_codex') throw Error('project_not_registered_in_codex');
  } catch (error) {
    if (error.message === 'project_not_registered_in_codex') throw error;
    // The native helper reserves a durable receipt before sending. An
    // ambiguous native outcome stays tied to that receipt on later calls.
  } finally {
    try { unlinkSync(file); } catch (error) { if (error.code !== 'ENOENT') throw error; }
    rmdirSync(scratch);
  }
  return result();
}
