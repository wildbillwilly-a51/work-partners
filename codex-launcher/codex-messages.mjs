import { execFile, execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { existsSync, mkdtempSync, readFileSync, realpathSync, rmdirSync, unlinkSync, writeFileSync } from 'node:fs';
import { isAbsolute, join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { promisify } from 'node:util';
import { setTimeout as delay } from 'node:timers/promises';
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

export async function startProjectChat(access, bridge, { project, handoff_id, title, markdown, request_marker }) {
  if (!chatId.test(bridge.creationContextThread)) throw Error('codex_chat_creation_not_configured');
  return dispatch(access, bridge, access.project(project), {handoff_id, title, markdown, request_marker}, null);
}

async function dispatch(access, bridge, target, {handoff_id, title, markdown, request_marker}, route) {
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
    + '. Use it as collaboration context and follow the user’s actual request.\n'
    + (request_marker ? request_marker + '\n' : '') + '\n';
  const pickup = !route && bridge.handoffLauncher
    ? "Read the full handoff with PowerShell: & '" + bridge.handoffLauncher.replaceAll("'", "''")
      + "' handoffs --project " + target.project_id + ' --handoff-id ' + handoff_id
    : 'Read the full message with the installed Work Partners handoffs command using that project and handoff ID.';
  const prompt = intro + (intro.length + markdown.length <= 20_000
    ? markdown : pickup);
  try {
    writeFileSync(file, route ? prompt : JSON.stringify({title: staged.title, prompt}), { encoding: 'utf8', flag: 'wx' });
    const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !/^(?:WORK_PARTNERS_TUNNEL_KEY|OPENAI_API_KEY)$/i.test(key)));
    let destination;
    if (!route && bridge.nativeStart) {
      destination = [bridge.creationContextThread, target.root, file, receipt];
    } else {
      const routing = route ? ['-ThreadId', route.threadId, '-AuthorityThread', route.authorityThread]
        : ['-ContextThread', bridge.creationContextThread, '-ProjectRoot', target.root];
      destination = [...routing, '-PromptFile', file, '-Receipt', receipt];
    }
    const {stdout} = await run(program, [...baseArgs, ...destination],
      { env, windowsHide: true, timeout: 85_000, maxBuffer: 32768 });
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

const nativeEnvironment = () => Object.fromEntries(Object.entries(process.env)
  .filter(([key]) => !/^(?:WORK_PARTNERS_TUNNEL_KEY|OPENAI_API_KEY)$/i.test(key)));

function requestTexts(item) {
  if (item.type === 'userMessage')
    return item.content?.filter(part => part.type === 'text').map(part => part.text) ?? [];
  if (item.type === 'functionCallOutput' && item.namespace === 'codex_app'
      && ['send_message_to_thread', 'create_thread'].includes(item.name) && !item.output?.truncated) {
    const match = /^<codex_delegation>\s*<source_thread_id>[a-f0-9-]+<\/source_thread_id>\s*<input>([\s\S]*)<\/input>\s*<\/codex_delegation>$/.exec(item.output?.text ?? '');
    if (match) return [match[1]];
  }
  return [];
}

function hasUserMessage(turn, text) {
  return turn.items?.some(item => requestTexts(item).some(value => value.split(/\r?\n/).includes(text)));
}

function sameProjectWorkspace(root, cwd) {
  if (typeof cwd !== 'string' || !isAbsolute(cwd)) return false;
  const canonical = path => {
    const real = realpathSync.native(path);
    return process.platform === 'win32' ? real.toLowerCase() : real;
  };
  const commonGitDir = path => canonical(execFileSync('git', ['--no-optional-locks', '-C', path,
    'rev-parse', '--path-format=absolute', '--git-common-dir'], {encoding:'utf8', timeout:5000, stdio:['ignore','pipe','ignore']}).trim());
  try {
    return canonical(root) === canonical(cwd) || commonGitDir(root) === commonGitDir(cwd);
  } catch { return false; }
}

function matchingChatGPTReply(turn, job) {
  const item = turn?.items?.filter(message => message.type === 'agentMessage' && message.text?.trim()).at(-1);
  if (item?.truncated) return {truncated:true};
  const lines = item?.text?.trimEnd().split(/\r?\n/) ?? [];
  if (lines.at(-1)?.trim() === '```') lines.pop();
  if (lines.pop()?.replace(/^[`* ]+|[`* ]+$/g, '') !== `WPEND ${job.request_id}`) return null;
  return {text:lines.join('\n').trim(), nativeHash:hash(item.text)};
}

const historyLimit = error => ['chatgpt_history_paused', 'chatgpt_history_rate_limited'].includes(error?.message);

export class CodexConversations {
  // availability: the broker's account pause, when the broker runs in the same
  // server, so these ChatGPT history reads honour and report the same limit.
  constructor(access, bridge, {waitMs = 12000, pollMs = 3000, returnGraceMs = 15000, companionPath, availability = null} = {}) {
    this.access = access;
    this.bridge = bridge;
    this.db = access.legacy.db;
    this.waitMs = waitMs;
    this.pollMs = pollMs;
    this.returnGraceMs = returnGraceMs;
    this.companionPath = companionPath;
    this.running = new Map();
    this.historyPausedUntil = 0;
    this.historyBackoffMs = 0;
    this.availability = availability;
    this.historyQueue = Promise.resolve();
    this.db.exec(`CREATE TABLE IF NOT EXISTS codex_conversations (
      id TEXT PRIMARY KEY, project TEXT NOT NULL, thread TEXT NOT NULL,
      status TEXT NOT NULL, delivery TEXT NOT NULL, data TEXT NOT NULL
    ); CREATE UNIQUE INDEX IF NOT EXISTS codex_conversations_active_thread
      ON codex_conversations(thread) WHERE status = 'working';`);
  }

  // With the broker on, only companion consultations (which return into Codex
  // chats) are watched, so the broker is the only sender into ChatGPT.
  watch({reverseOnly = false} = {}) {
    return setInterval(() => {
      try { this.readCompanionRequests(); } catch { /* The companion store can be unavailable during startup. */ }
      for (const row of this.db.prepare(`SELECT id FROM codex_conversations
        WHERE delivery IN ('pending', 'returning') ${reverseOnly ? `AND json_extract(data, '$.direction') = 'reverse'` : ''}
        ORDER BY rowid LIMIT 8`).all()) {
        void this.refresh(row.id).catch(() => {});
      }
    }, 15000).unref();
  }

  readCompanionRequests() {
    if (!this.companionPath || !existsSync(this.companionPath)) return;
    const db = new DatabaseSync(this.companionPath, {readOnly:true});
    try {
      const requests = db.prepare(`SELECT payload FROM consultations
        WHERE state IN ('dispatch_intent', 'awaiting_reply') AND json_extract(payload, '$.origin_thread') IS NOT NULL`).all();
      for (const row of requests) {
        const source = JSON.parse(row.payload);
        if (![source.origin_thread, source.thread_id, source.request_id].every(value => chatId.test(value ?? ''))) continue;
        const id = hash(`reverse:${source.request_id}`);
        const job = {id, direction:'reverse', project:source.project, thread:source.thread_id,
          origin:source.origin_thread, request_id:source.request_id, nonce:source.request_id, requestHash:source.prompt_sha256,
          status:'working', delivery:'pending', started:source.dispatch_intent_at * 1000};
        this.db.prepare('INSERT OR IGNORE INTO codex_conversations VALUES (?, ?, ?, ?, ?, ?)')
          .run(id, job.project, job.thread, job.status, job.delivery, JSON.stringify(job));
      }
    } finally { db.close(); }
  }

  companionStillWaiting(job) {
    if (!this.companionPath || !existsSync(this.companionPath)) return false;
    const db = new DatabaseSync(this.companionPath, {readOnly:true});
    try {
      const row = db.prepare('SELECT state FROM consultations WHERE id = ? AND thread_id = ?').get(job.request_id, job.thread);
      return ['dispatch_intent', 'awaiting_reply'].includes(row?.state);
    } finally { db.close(); }
  }

  recordCompanionReturn(job) {
    const db = new DatabaseSync(this.companionPath);
    try {
      db.exec('PRAGMA busy_timeout = 5000');
      const row = db.prepare('SELECT state, payload FROM consultations WHERE id = ? AND thread_id = ?').get(job.request_id, job.thread);
      if (!['dispatch_intent', 'awaiting_reply'].includes(row?.state)) return;
      const source = JSON.parse(row.payload);
      if (source.origin_thread !== job.origin || source.prompt_sha256 !== job.requestHash) return;
      const state = job.reply ? 'complete' : 'returned_without_reply';
      const update = {...source, state, native_turn_id:job.turnId, completed_at:Date.now() / 1000};
      delete update.waiting_for;
      if (job.reply) {
        update.reply = job.reply;
        update.reply_sha256 = hash(job.reply);
        update.native_reply_sha256 = job.nativeHash;
      } else update.return_detail = job.detail;
      const result = db.prepare('UPDATE consultations SET state = ?, payload = ? WHERE id = ? AND payload = ?')
        .run(state, JSON.stringify(update), job.request_id, row.payload);
      if (!result.changes) throw Error('companion_request_changed');
    } finally { db.close(); }
  }

  get(id) {
    const row = this.db.prepare('SELECT data FROM codex_conversations WHERE id = ?').get(id);
    return row ? JSON.parse(row.data) : null;
  }

  save(job) {
    const revision = job.revision ?? 0;
    const update = {...job, revision:revision + 1};
    let changed;
    try {
      // The thread column backs the one-working-request-per-chat index.
      changed = this.db.prepare(`UPDATE codex_conversations SET thread = ?, status = ?, delivery = ?, data = ?
        WHERE id = ? AND COALESCE(json_extract(data, '$.revision'), 0) = ?`)
        .run(job.thread, job.status, job.delivery, JSON.stringify(update), job.id, revision);
    } catch (error) {
      if (/UNIQUE constraint failed/.test(error?.message ?? '')) return false;
      throw error;
    }
    if (changed.changes) job.revision = update.revision;
    return Boolean(changed.changes);
  }

  async native(job, operation, args = {}, thread = job.thread) {
    const [program, ...base] = this.bridge.command ?? [];
    if (!program) throw Error('codex_message_bridge_not_configured');
    const scratch = mkdtempSync(join(this.access.legacy.state, 'codex-conversation-'));
    const file = join(scratch, 'arguments.txt');
    try {
      writeFileSync(file, operation === 'send' ? args.prompt : JSON.stringify(args), {encoding:'utf8', flag:'wx'});
      const flags = operation === 'send'
        ? ['-ThreadId', job.thread, '-AuthorityThread', job.authority, '-Receipt', join(this.access.legacy.state, `codex-conversation-${job.id}.json`)]
        : ['-ConversationOperation', operation, '-ContextThread', job.direction === 'reverse' ? job.origin
          : job.context ?? (chatId.test(job.thread) ? job.thread : this.bridge.creationContextThread),
          '-ThreadId', chatId.test(thread) ? thread : this.bridge.creationContextThread];
      const {stdout} = await run(program, [...base, ...flags, '-PromptFile', file], {
        env:nativeEnvironment(), windowsHide:true, timeout:85000, maxBuffer:2000000,
      });
      return JSON.parse(stdout);
    } finally {
      if (existsSync(file)) unlinkSync(file);
      rmdirSync(scratch);
    }
  }

  async talk({project, request_id, message, handoff_id}, origin) {
    const target = this.access.project(project);
    const id = hash(`${target.project_id}:${request_id}`);
    const previous = this.get(id);
    if (previous) {
      if (previous.requestHash !== hash(message)) throw Error('payload_conflict');
      if (previous.origin && chatId.test(origin ?? '') && previous.origin !== origin) throw Error('chatgpt_chat_identity_mismatch');
      this.claimOrigin(previous, origin);
      return this.consume(await this.refresh(id, false));
    }
    const job = {id, project:target.project_id, thread:`routing:${id}`, message, handoff_id,
      request_id, requestHash:hash(message), nonce:randomUUID(), status:'routing', delivery:'pending', started:Date.now()};
    await this.captureOrigin(job, origin);
    this.insert(job);
    if (!job.origin) return this.result(job);
    const deadline = Date.now() + this.waitMs;
    let current = await this.refresh(id, false);
    while (['working', 'routing'].includes(current.status) && Date.now() < deadline) {
      await delay(this.pollMs);
      current = await this.refresh(id, false);
    }
    return this.consume(current);
  }

  insert(job) {
    this.db.prepare('INSERT INTO codex_conversations VALUES (?, ?, ?, ?, ?, ?)')
      .run(job.id, job.project, job.thread, job.status, job.delivery, JSON.stringify(job));
  }

  // ChatGPT conversation reads share one account rate limit. Polling through
  // it keeps the limit in force, so pause all such reads and back off.
  historyPaused() {
    return Date.now() < this.historyPausedUntil || Date.now() < (this.availability?.pausedUntil() ?? 0);
  }

  historyLimited() {
    this.historyBackoffMs = Math.min(Math.max(this.historyBackoffMs * 2, 60000), 900000);
    this.historyPausedUntil = Date.now() + this.historyBackoffMs;
    this.availability?.pause(this.historyBackoffMs, 'companion_history_rate_limited');
  }

  // One ChatGPT history operation runs at a time, so concurrent refreshes
  // cannot all pass the pause check before one of them meets the limit.
  history(job, operation, args = {}, thread = job.thread) {
    const turn = this.historyQueue.then(() => this.historyCall(job, operation, args, thread));
    this.historyQueue = turn.catch(() => {});
    return turn;
  }

  async historyCall(job, operation, args, thread) {
    // Paused means the operation never started; rate_limited may follow partial work.
    if (this.historyPaused()) throw Error('chatgpt_history_paused');
    let result;
    try { result = await this.native(job, operation, args, thread); }
    catch (error) {
      if (!`${error?.stdout ?? ''}${error?.stderr ?? ''}`.includes('native_rate_limited')) throw error;
      this.historyLimited();
      throw Error('chatgpt_history_rate_limited');
    }
    // A scan cut short by the limit is incomplete: its matches cannot prove uniqueness.
    if (result?.rate_limited) {
      this.historyLimited();
      throw Error('chatgpt_history_rate_limited');
    }
    // A pre-send return refusal is a result, so the caller can release its reservation.
    if (result?.status === 'rate_limited') this.historyLimited();
    // Cached reads can succeed during a limit, so recover gradually.
    else this.historyBackoffMs = this.historyBackoffMs > 60000 ? this.historyBackoffMs / 2 : 0;
    return result;
  }

  // A retry of a request saved before its caller was known supplies the caller to verify.
  claimOrigin(job, origin) {
    if (job.origin || job.claimedOrigin || !chatId.test(origin ?? '')) return;
    job.claimedOrigin = origin;
    if (!this.save(job)) delete job.claimedOrigin;
  }

  async captureOrigin(job, origin) {
    if (!chatId.test(origin ?? '')) return;
    try {
      const source = await this.history(job, 'read', {limit:1}, origin);
      if (source.thread.id === origin && source.thread.kind === 'chatgpt') job.origin = origin;
    } catch {
      // Verify the caller's own conversation later instead of scanning others.
      job.claimedOrigin = origin;
    }
  }

  linkedRoute(project, origin) {
    const previous = this.db.prepare(`SELECT data FROM codex_conversations
      WHERE project = ? AND json_extract(data, '$.origin') = ?
        AND json_extract(data, '$.direction') IS NULL
        AND COALESCE(json_extract(data, '$.route_disabled'), 0) = 0
      ORDER BY json_extract(data, '$.started') DESC`).all(project, origin)
      .map(row => JSON.parse(row.data)).find(job => chatId.test(job.thread));
    if (previous) return {threadId:previous.thread, authorityThread:previous.authority ?? previous.thread};
  }

  async sendRouted(job) {
    if (!job.origin) {
      if (this.historyPaused()) return false;
      if (job.claimedOrigin) {
        let source;
        try { source = await this.history(job, 'read', {limit:1}, job.claimedOrigin); }
        catch (error) {
          if (historyLimit(error)) return false;
          source = null;  // Unreadable: fall back to the visible marker.
        }
        if (source?.thread?.id === job.claimedOrigin && source.thread.kind === 'chatgpt') job.origin = job.claimedOrigin;
        else {
          delete job.claimedOrigin;
          if (!this.save(job)) return false;
        }
      }
      if (!job.origin) {
        let found;
        try { found = await this.history(job, 'origin', {marker:`Work Partners reply ${job.nonce}`, since:job.started}); }
        catch { return false; }
        if (found.matches.length !== 1 || !chatId.test(found.matches[0])) return false;
        job.origin = found.matches[0];
      }
      if (!this.save(job)) return false;
    }
    const linked = this.linkedRoute(job.project, job.origin);
    // Codex sets a created chat's permissions. Owners can direct a caller's
    // first task to the project's designated chat, which keeps its own.
    const designated = this.bridge.launchIntoProjectChat === true && Boolean(this.bridge.threads?.[job.project]);
    if (job.launch && (job.newChat || (!linked && !designated))) {
      job.status = 'starting';
      job.started = Date.now();
      if (!this.save(job)) return false;
      await startProjectChat(this.access, this.bridge, {
        project:job.project, handoff_id:job.request_id, title:job.title, markdown:job.message,
        request_marker:`Work Partners request ${job.nonce}`,
      });
      return true;
    }
    const route = linked ?? this.bridge.threads?.[job.project];
    if (!route || !chatId.test(route.threadId) || !chatId.test(route.authorityThread)) throw Error('project_has_no_codex_chat');
    const destination = {...job, thread:route.threadId, authority:route.authorityThread};
    const chat = await this.native(destination, 'read', {resolve_project:true});
    const target = this.access.project(job.project);
    if (chat.thread.id !== destination.thread || chat.thread.kind !== 'codex') throw Error('codex_chat_identity_mismatch');
    if (!sameProjectWorkspace(target.root, chat.thread.cwd)
        && !sameProjectWorkspace(target.root, chat.thread.assignedProjectRoot)) throw Error('codex_chat_workspace_mismatch');
    // Older rows kept a routing placeholder in the thread column; check their data too.
    if (chat.thread.status.type !== 'idle' || this.db.prepare(`SELECT id FROM codex_conversations
      WHERE status = 'working' AND id <> ? AND (thread = ? OR json_extract(data, '$.thread') = ?)`)
      .get(job.id, destination.thread, destination.thread)) return false;
    const reserved = {...job, ...destination, status:'working', launch:false, started:Date.now()};
    if (!this.save(reserved)) return false;
    Object.assign(job, reserved);
    // Reserve before dispatch. A transport error may follow delivery; never resend.
    const intro = `Message from ChatGPT via Work Partners\nWork Partners request ${job.nonce}\nWork Partners will return your final answer to ChatGPT.\n\n`;
    const message = job.message.length + intro.length <= 20_000 ? job.message
      : `Read the complete message with the installed Work Partners handoffs command for project ${job.project} and handoff ID ${job.handoff_id ?? job.request_id}.`;
    await this.native(job, 'send', {prompt:intro + message}).catch(() => {});
    return true;
  }

  async handoff(args, origin, start = false) {
    const target = this.access.project(args.project);
    this.access.stageHandoff({...args, project:target.project_id});
    const id = hash(`${target.project_id}:handoff:${args.handoff_id}`);
    let job = this.get(id);
    if (job) {
      if (job.requestHash !== hash(args.markdown)) throw Error('payload_conflict');
      if (job.origin && chatId.test(origin ?? '') && job.origin !== origin) throw Error('chatgpt_chat_identity_mismatch');
      this.claimOrigin(job, origin);
      return this.handoffResult(await this.refresh(id, false));
    }
    const receipt = join(this.access.legacy.state, `${start ? 'codex-start-' : 'codex-send-'}${hash(target.project_id + ':' + args.handoff_id)}.json`);
    if (!start && !existsSync(receipt)) {
      const result = await this.talk({project:target.project_id, request_id:`handoff-${args.handoff_id}`,
        message:args.markdown, handoff_id:args.handoff_id}, origin);
      return {...result, handoff_id:args.handoff_id};
    }
    const adopt = existsSync(receipt);
    job = {id, project:target.project_id, thread:`routing:${id}`, request_id:args.handoff_id,
      requestHash:hash(args.markdown), nonce:randomUUID(), status:adopt ? 'starting' : 'routing',
      delivery:'pending', started:Date.now(), receipt, launch:start, newChat:args.new_chat === true,
      title:args.title, message:args.markdown};
    await this.captureOrigin(job, origin);
    // Older launch receipts have no nonce. Their exact handoff line identifies the original turn.
    if (adopt) job.requestLine = `ChatGPT sent a Work Partners message for ${target.name}. Handoff ID ${args.handoff_id}. Use it as collaboration context and follow the user’s actual request.`;
    this.insert(job);
    if (!job.origin && !adopt) return this.handoffResult(job);
    return this.handoffResult(await this.refresh(id, false));
  }

  handoffResult(job) {
    return {...this.result(job), handoff_id:job.request_id,
      ...(job.launch && chatId.test(job.thread) ? {delivery:'chat_started', reply_delivery:job.delivery} : {}),
      thread_id:chatId.test(job.thread) ? job.thread : null,
      chat_url:chatId.test(job.thread) ? `codex://threads/${job.thread}` : null,
      return_instructions:!job.origin ? 'Include return_marker exactly in your visible response so the reply returns to this conversation.' : undefined};
  }

  async status({project, request_id}) {
    const target = this.access.project(project);
    const id = [hash(`${target.project_id}:${request_id}`), hash(`${target.project_id}:handoff:${request_id}`)]
      .find(candidate => this.get(candidate));
    if (!id) throw Error('codex_request_not_found');
    return this.consume(await this.refresh(id, false));
  }

  consume(job) {
    if (!['working', 'routing', 'starting'].includes(job.status) && job.delivery === 'pending') {
      job.delivery = 'inline';
      if (!this.save(job)) job = this.get(job.id);
    }
    return this.result(job);
  }

  result(job) {
    const result = {request_id:job.request_id, status:job.status, delivery:job.delivery};
    if (job.reply) result.reply = job.reply;
    if (job.detail) result.detail = job.detail;
    if (!job.origin && job.delivery === 'pending') result.return_marker = `Work Partners reply ${job.nonce}`;
    if (job.status === 'routing') result.detail = job.origin
      ? 'Waiting for the project Codex chat to finish its current work before sending this message.'
      : this.historyPaused()
        ? 'ChatGPT is temporarily rate-limiting conversation reads. Work Partners will identify this conversation and route the message automatically; do not resend it.'
        : 'Waiting to identify this ChatGPT conversation before routing its message. Include return_marker exactly in your visible response.';
    return result;
  }

  refresh(id, allowReturn = true) {
    if (!this.running.has(id)) this.running.set(id, this.update(this.get(id), allowReturn)
      .finally(() => this.running.delete(id)));
    return this.running.get(id);
  }

  async update(job, allowReturn) {
    if (job.status === 'routing' && !await this.sendRouted(job)) return job;
    if (job.status === 'starting') {
      let receipt;
      try { receipt = JSON.parse(readFileSync(job.receipt, 'utf8')); } catch { return job; }
      if (receipt.phase !== 'native_call_returned' || !chatId.test(receipt.thread_id)) return job;
      Object.assign(job, {thread:receipt.thread_id, authority:receipt.thread_id, status:'working'});
      if (!this.save(job)) return this.get(job.id);
    }
    const reverse = job.direction === 'reverse';
    if (reverse && job.delivery === 'pending' && !this.companionStillWaiting(job)) {
      job.status = 'closed';
      job.delivery = 'inline';
      this.save(job);
      return job;
    }
    const recovering = job.status === 'unconfirmed';
    if (job.status === 'working' || recovering) {
      let chat;
      try { chat = reverse ? await this.history(job, 'read') : await this.native(job, 'read'); }
      catch { return job; }
      if (chat.thread.id !== job.thread || chat.thread.kind !== (reverse ? 'chatgpt' : 'codex')) return job;
      const turn = reverse
        ? chat.turns?.find(turn => turn.items?.some(item =>
          requestTexts(item).length > 0 && hash(requestTexts(item).join('')) === job.requestHash))
        : chat.turns?.find(turn => hasUserMessage(turn, job.requestLine ?? `Work Partners request ${job.nonce}`));
      const answer = reverse ? matchingChatGPTReply(turn, job)
        : turn?.items?.filter(item => item.type === 'agentMessage' && item.phase === 'final_answer').at(-1);
      const speaker = reverse ? 'ChatGPT' : 'Codex';
      if (turn?.id) job.turnId = turn.id;
      if (turn?.status === 'completed' && answer?.text && !answer.truncated) {
        job.status = 'completed';
        job.reply = answer.text;
        delete job.detail;
        if (recovering) {
          job.delivery = 'pending';
          job.returnMarker = `Work Partners returned ${job.nonce} ${hash(job.reply).slice(0, 16)}`;
          delete job.returnStarted;
        }
        if (reverse) job.nativeHash = answer.nativeHash;
      } else if (['failed', 'interrupted', 'cancelled'].includes(turn?.status) ||
          (turn?.status === 'completed' && (turn.error || answer?.truncated))) {
        job.status = 'failed';
        job.detail = answer?.truncated ? `${speaker} finished, but the reply exceeded the desktop read limit.` : `${speaker} ended the turn without a complete reply.`;
      } else if (chat.thread.status.type === 'idle' && Date.now() - job.started > 180000) {
        job.status = 'unconfirmed';
        job.detail = turn ? `${speaker} is idle and a complete answer was not visible.` : `${speaker} is idle and this request was not visible.`;
      }
      if (job.status !== 'working') job.finished = Date.now();
      if (!this.save(job)) return this.get(job.id);
    }
    if (allowReturn && ['pending', 'returning'].includes(job.delivery) && Date.now() - job.started >= this.waitMs + this.returnGraceMs)
      await this.returnResult(job);
    return job;
  }

  async returnResult(job) {
    const marker = `Work Partners reply ${job.nonce}`;
    const returned = job.returnMarker ?? `Work Partners returned ${job.nonce}`;
    // A reverse job returns to a Codex chat; all others read and write ChatGPT history.
    const caller = job.direction === 'reverse' ? this.native.bind(this) : this.history.bind(this);
    if (job.direction !== 'reverse' && this.historyPaused()) return;
    let dispatching = false;
    try {
      if (job.delivery === 'returning') {
        const chat = await caller(job, 'read', {}, job.origin);
        if (chat.thread.id === job.origin && chat.turns?.some(turn => hasUserMessage(turn, returned))) {
          if (job.direction === 'reverse') this.recordCompanionReturn(job);
          job.delivery = 'delivered';
          this.save(job);
        } else if (Date.now() - job.returnStarted > 180000) {
          job.delivery = 'return_unconfirmed';
          this.save(job);
        }
        return;
      }
      if (!job.origin) {
        const {matches} = await caller(job, 'origin', {marker, since:job.started});
        if (matches.length !== 1 || !chatId.test(matches[0])) {
          if (job.finished && Date.now() - job.finished > 600000) {
            job.delivery = 'reply_available';
            this.save(job);
          }
          return;
        }
        job.origin = matches[0];
        if (!this.save(job)) return;
      }
      if (job.status === 'working') return;
      const reverse = job.direction === 'reverse';
      if (reverse && !this.companionStillWaiting(job)) {
        job.delivery = 'inline';
        this.save(job);
        return;
      }
      job.delivery = 'returning';
      job.returnStarted = Date.now();
      if (!this.save(job)) return;
      const speaker = reverse ? 'ChatGPT' : 'Codex';
      const message = `From ${speaker} via Work Partners. Share this reply with the user. Send a follow-up only if the user asked for a discussion or a follow-up.\n${returned}\n\n${job.reply ?? job.detail}`;
      dispatching = true;
      const result = await caller(job, 'return', {message, kind:reverse ? 'codex' : 'chatgpt'}, job.origin);
      if (result.status === 'rate_limited') {
        // The bridge reports this before sending, so the return can be retried.
        job.delivery = 'pending';
        delete job.returnStarted;
        this.save(job);
      }
      if (result.status === 'busy') {
        job.delivery = 'pending';
        this.save(job);
      }
      if (result.status === 'origin_unconfirmed') {
        job.delivery = 'reply_available';
        this.save(job);
      }
    } catch (error) {
      // The pause gate stopped this return before the bridge ran, so nothing was sent.
      if (dispatching && error?.message === 'chatgpt_history_paused') {
        job.delivery = 'pending';
        delete job.returnStarted;
        this.save(job);
        return;
      }
      // Once a return may have been sent, subsequent checks only look for the same message.
      if (job.delivery === 'returning' && !this.historyPaused() && Date.now() - job.returnStarted > 180000) {
        job.delivery = 'return_unconfirmed';
        this.save(job);
      }
    }
  }
}
