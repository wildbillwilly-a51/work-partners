"""Local consultation receipts. Native desktop tools do the sending/reading."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import uuid

PENDING=('prepared','dispatch_intent','awaiting_reply')
# No ordinary-ChatGPT native pre-delivery rejection contract is verified yet.
# Future entries require a versioned reference and an exact correlated matcher.
# This is a code-owned allowlist, never populated from CLI flags/native claims.
REJECTION_CONTRACTS={}
NO_NATIVE_RESULT=object()
digest=lambda text:hashlib.sha256(text.encode('utf-8')).hexdigest()

class ConsultationError(Exception): pass

def canonical(value):
    try:encoded=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
    except (TypeError,ValueError):raise ConsultationError('send_evidence_must_be_json') from None
    if len(encoded.encode('utf-8'))>1048576:raise ConsultationError('send_evidence_exceeds_1_mib')
    return encoded

def native_views(payload,depth=0):
    if not isinstance(payload,dict) or depth>4:return []
    views=[payload]
    views.extend(native_views(payload.get('structuredContent'),depth+1))
    for item in payload.get('content',[]) if isinstance(payload.get('content'),list) else []:
        if isinstance(item,dict) and item.get('type')=='text':
            try:parsed=json.loads(item.get('text',''))
            except (ValueError,TypeError):continue
            views.extend(native_views(parsed,depth+1))
    return views

def accepted_send_result(payload,thread_id,depth=0):
    """Recognize the observed acknowledgement shape; new schemas stay unknown."""
    if not isinstance(payload,dict) or depth>4:return False
    if 'isError' in payload and payload['isError'] is not False:return False
    if 'threadId' in payload:
        return payload['threadId']==thread_id and set(payload).issubset({'threadId','isError','_meta'})
    if not set(payload).issubset({'content','structuredContent','isError','_meta'}):return False
    children=[]
    if 'structuredContent' in payload:children.append(payload['structuredContent'])
    if 'content' in payload:
        if not isinstance(payload['content'],list):return False
        for item in payload['content']:
            if not isinstance(item,dict) or item.get('type')!='text':return False
            try:children.append(json.loads(item.get('text','')))
            except (ValueError,TypeError):return False
    return bool(children) and all(accepted_send_result(child,thread_id,depth+1) for child in children)

def native(payload):
    if payload.get('isError'): raise ConsultationError('native_tool_error_no_automatic_resend')
    if 'thread' in payload or 'threadId' in payload:return payload
    if isinstance(payload.get('structuredContent'),dict):return payload['structuredContent']
    for item in payload.get('content',[]):
        if item.get('type')=='text':
            try:
                parsed=json.loads(item['text'])
                if isinstance(parsed,dict) and ('thread' in parsed or 'threadId' in parsed):return parsed
            except (ValueError,KeyError):pass
    raise ConsultationError('native_read_shape_not_recognized')

def verified_thread(payload,expected=None):
    result=native(payload);thread=result.get('thread',{})
    if thread.get('kind')!='chatgpt':raise ConsultationError('ordinary_chatgpt_required_not_codex_or_work')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,120}',str(thread.get('id',''))):raise ConsultationError('invalid_thread_id')
    if expected and thread['id']!=expected:raise ConsultationError('wrong_thread_read')
    return result

def root_path(value):
    path=Path(value).expanduser()
    if not path.is_absolute() or not path.is_dir():raise ConsultationError('absolute_existing_project_required')
    return str(path.resolve())

class Receipts:
    def __init__(self,directory):
        self.directory=Path(directory).expanduser().resolve();self.directory.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.directory/'consultations.sqlite',timeout=5)
        self.db.row_factory=sqlite3.Row
        self.db.executescript("""PRAGMA journal_mode=WAL;
          CREATE TABLE IF NOT EXISTS bindings(scope TEXT PRIMARY KEY,thread_id TEXT NOT NULL,title TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS consultations(id TEXT PRIMARY KEY,project TEXT NOT NULL,thread_id TEXT NOT NULL,
            state TEXT NOT NULL,payload TEXT NOT NULL);
          CREATE UNIQUE INDEX IF NOT EXISTS one_pending_per_chat ON consultations(thread_id)
            WHERE state IN ('prepared','dispatch_intent','awaiting_reply');""")
    def close(self):self.db.close()
    def protect_project(self,project):
        if self.directory.is_relative_to(Path(project)):raise ConsultationError('receipts_must_be_outside_project')
    def binding(self,project):
        return self.db.execute('SELECT * FROM bindings WHERE scope=?',(project,)).fetchone() or self.db.execute("SELECT * FROM bindings WHERE scope='*'").fetchone()
    def bind(self,payload,project=None):
        result=verified_thread(payload);t=result['thread'];scope=root_path(project) if project else '*'
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute('INSERT OR REPLACE INTO bindings VALUES(?,?,?)',(scope,t['id'],str(t.get('title',''))));self.db.commit()
        except Exception:self.db.rollback();raise
        return {'bound':True,'scope':scope,'thread_id':t['id'],'title':t.get('title'),'chat_url':'https://chatgpt.com/c/'+t['id'],
                'message':'Destination selected. Existing consultations are preserved; setup sends nothing.',
                'status':self.project_status(project) if project else None}
    def project_status(self,project):
        project=root_path(project);binding=self.binding(project)
        rows=self.db.execute('SELECT payload FROM consultations WHERE project=? ORDER BY rowid DESC LIMIT 10',(project,)).fetchall()
        # Show all unresolved work even after changing the destination or many completed jobs.
        unresolved=self.db.execute("SELECT payload FROM consultations WHERE project=? AND state IN ('prepared','dispatch_intent','awaiting_reply') ORDER BY rowid DESC",(project,)).fetchall()
        result={'project':project,'state':'setup_required','destination':None,
                'unresolved':[self.summary(json.loads(row[0]),include_reply=False) for row in unresolved],
                'recent':[self.summary(json.loads(row[0]),include_reply=False) for row in rows],
                'message':'Select an existing ordinary ChatGPT chat with setup. No message has been sent by this command.'}
        if binding:
            pending=self.db.execute("SELECT payload FROM consultations WHERE thread_id=? AND state IN ('prepared','dispatch_intent','awaiting_reply')",(binding['thread_id'],)).fetchone()
            result.update(state='destination_in_use' if pending else 'ready',
                          destination={'thread_id':binding['thread_id'],'title':binding['title'],'scope':binding['scope'],'chat_url':'https://chatgpt.com/c/'+binding['thread_id']},
                          destination_pending=self.summary(json.loads(pending[0]),include_reply=False) if pending else None,
                          message='Recover the pending consultation or select another intended chat; do not resend.' if pending else 'Ready to prepare a consultation for this destination.')
        return result
    def prepare(self,project,question,context=''):
        project=root_path(project);self.protect_project(project)
        if not question.strip() or len(question)>20000 or len(context)>80000:raise ConsultationError('question_or_context_size_invalid')
        binding=self.binding(project)
        if not binding:raise ConsultationError('bind_an_existing_ordinary_chat_first')
        ident=str(uuid.uuid4());marker='Work Partners consultation '+ident;footer='WPEND '+ident
        prompt=f'{marker}\nProject: {project}\n\n{question.strip()}\n'
        if context.strip():prompt+='\nProject background supplied by the coding lead:\n'+context.strip()+'\n'
        prompt+=(
            '\nJoin the discussion as an independent thinking partner. Explore ideas, challenge assumptions, '
            'ask questions or follow a useful thread. Use available sources and tools within the user\'s '
            'authorization for this discussion. Use earlier chat history as background. '
            'Take the user\'s stated or adopted requirements as the brief '
            'and share your suggestions and concerns as advice.\n'
            'End your reply with this exact receipt line so Codex can recover it:\n'+footer
        )
        job={'request_id':ident,'project':project,'thread_id':binding['thread_id'],'chat_title':binding['title'],'state':'prepared','created_at':time.time(),'question':question,'prompt':prompt,'marker':marker,'footer':footer,'prompt_sha256':digest(prompt)}
        try:
            self.db.execute('INSERT INTO consultations VALUES(?,?,?,?,?)',(ident,project,binding['thread_id'],'prepared',json.dumps(job)));self.db.commit()
        except sqlite3.IntegrityError:
            self.db.rollback();raise ConsultationError('chat_has_pending_consultation_collect_it_before_another_send') from None
        return self.summary(job,include_prompt=True)
    def job(self,ident):
        row=self.db.execute('SELECT payload FROM consultations WHERE id=?',(ident,)).fetchone()
        if not row:raise ConsultationError('unknown_consultation')
        return json.loads(row['payload'])
    def summary(self,job,include_prompt=False,include_reply=True):
        keys=['request_id','project','thread_id','chat_title','state','native_turn_id','waiting_for','reply_sha256','native_reply_sha256','abandoned_at','abandon_reason']
        if include_reply:keys.append('reply')
        result={k:job[k] for k in keys if k in job};result['chat_url']='https://chatgpt.com/c/'+job['thread_id']
        events=job.get('send_outcomes',[])
        rejected=any(e['classification']=='rejected_before_delivery' for e in events)
        accepted=bool(job.get('send_accepted_at'))
        conflict=rejected and (accepted or bool(job.get('native_turn_id')))
        result['delivery_conflict']=conflict
        result['send_outcome']='conflicting_evidence' if conflict else ('rejected_before_delivery' if rejected else ('accepted' if accepted else ('unknown' if events else 'not_recorded')))
        result['send_audit']=[{k:e[k] for k in ('audit_id','recorded_at','classification','reason','correlation','native_sha256','arguments_sha256','rejection_contract') if k in e} for e in events]
        result['delivery']='observed_in_chat' if job.get('native_turn_id') else ('unknown' if conflict else ('rejected_before_delivery' if rejected else ('accepted_by_desktop' if accepted else ('unknown' if job.get('dispatch_intent_at') else 'not_dispatched'))))
        result['response']='complete' if job['state']=='complete' else ('incomplete' if job.get('native_turn_id') else 'not_observed')
        if conflict:action='Conflicting delivery evidence retained. Inspect the send audit and matching turn; never replay this request.'
        elif job['state']=='prepared':action='Read the destination and dispatch once, or discard this unsent request.'
        elif job['state']=='complete':action='Use the saved reply. No resend is needed.'
        elif job['state']=='rejected':action='Verified pre-delivery rejection retained. This request cannot be dispatched again; any later consultation is a new deliberate request.'
        elif job['state']=='discarded':action='Unsent local request discarded. A new consultation can be prepared.'
        elif job['state']=='abandoned':action='Local waiting ended. Nothing was cancelled in ChatGPT; never replay this request. A late reply can still be recovered.'
        else:action='Reread the same chat and recover this request. If ending local waiting, explicitly abandon it with a reason; delivery may still be uncertain. Do not resend.'
        result['next_action']=action
        if include_prompt:result['prompt']=job['prompt']
        return result
    def update(self,job):
        self.db.execute('UPDATE consultations SET state=?,payload=? WHERE id=?',(job['state'],json.dumps(job),job['request_id']))
    def dispatch(self,ident,payload):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            job=self.job(ident)
            if job['state']!='prepared':raise ConsultationError('dispatch_already_attempted_collect_without_resending')
            read=verified_thread(payload,job['thread_id'])
            if read['thread'].get('status',{}).get('type')!='idle':raise ConsultationError('chat_busy_wait_without_sending')
            job['state']='dispatch_intent';job['dispatch_intent_at']=time.time();job['dispatch_id']=str(uuid.uuid4())
            job['dispatch_arguments_sha256']=digest(canonical({'threadId':job['thread_id'],'prompt':job['prompt']}))
            self.update(job);self.db.commit()
        except Exception:self.db.rollback();raise
        return {'request_id':ident,'state':job['state'],'dispatch_id':job['dispatch_id'],'arguments_sha256':job['dispatch_arguments_sha256'],
                'native_tool':'send_message_to_thread','arguments':{'threadId':job['thread_id'],'prompt':job['prompt']},
                'instruction':'Save this dispatch record privately. Send its arguments once using the native desktop tool, then record send-outcome. This command has NOT sent anything.'}
    def send_outcome(self,ident,payload,dispatch=None,*,native_file=None,dispatch_file=None,no_result_reason=None):
        missing=payload is NO_NATIVE_RESULT
        if missing and (not isinstance(no_result_reason,str) or not no_result_reason.strip() or len(no_result_reason)>2000):
            raise ConsultationError('missing_native_result_requires_reason_max_2000_characters')
        if not missing and no_result_reason is not None:raise ConsultationError('native_result_or_missing_reason_not_both')
        encoded=canonical(None if missing else payload)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            job=self.job(ident)
            if not job.get('dispatch_intent_at'):raise ConsultationError('dispatch_intent_required_before_send_receipt')
            arguments={'threadId':job['thread_id'],'prompt':job['prompt']};arguments_sha=digest(canonical(arguments))
            correlated=dispatch is not None
            if correlated:
                if not isinstance(dispatch,dict) or dispatch.get('request_id')!=ident or dispatch.get('native_tool')!='send_message_to_thread' or dispatch.get('arguments')!=arguments:
                    raise ConsultationError('dispatch_record_does_not_match_request_and_arguments')
                if job.get('dispatch_id') and dispatch.get('dispatch_id')!=job['dispatch_id']:raise ConsultationError('wrong_dispatch_identity')
                if dispatch.get('arguments_sha256',arguments_sha)!=arguments_sha:raise ConsultationError('wrong_dispatch_arguments_hash')
            views=native_views(payload)
            for view in views:
                thread_id=view.get('threadId') or (view.get('thread',{}).get('id') if isinstance(view.get('thread'),dict) else None)
                if thread_id and thread_id!=job['thread_id']:raise ConsultationError('wrong_thread_send_receipt')
            classification='unknown';reason='dispatch_evidence_required' if not correlated else 'unrecognized_native_send_result';contract=None
            errors=any(v.get('isError') or v.get('error') or v.get('detail') or v.get('accepted') is False or v.get('success') is False or v.get('status') in ('failed','rejected','error') for v in views)
            if correlated:
                if missing:reason='no_native_result_delivery_unknown'
                elif errors:
                    reason='native_error_delivery_unknown'
                    for name,adapter in REJECTION_CONTRACTS.items():
                        if adapter['reference'] and adapter['matches'](payload,job):
                            classification='rejected_before_delivery';reason='verified_pre_delivery_contract'
                            contract={'id':name,'reference':adapter['reference']};break
                elif accepted_send_result(payload,job['thread_id']):
                    classification='accepted';reason='native_desktop_acceptance_needs_readback'
            dispatch_sha=digest(canonical(dispatch)) if correlated else None
            audit_id=digest(canonical([ident,dispatch_sha,digest(encoded),no_result_reason]))
            if any(e['audit_id']==audit_id for e in job.get('send_outcomes',[])):
                self.db.commit();return self.summary(job)
            event={'audit_id':audit_id,'recorded_at':time.time(),'request_id':ident,'thread_id':job['thread_id'],
                   'dispatch_id':job.get('dispatch_id'),'arguments_sha256':arguments_sha,
                   'dispatch_record_sha256':dispatch_sha,'correlation':('dispatch_record' if job.get('dispatch_id') else 'legacy_dispatch_record') if correlated else 'unverified',
                   'native_sha256':digest(encoded),'native_result_available':not missing,
                   'native_result':None if missing else payload,'classification':classification,'reason':reason}
            if no_result_reason:event['no_result_reason']=no_result_reason.strip()
            if native_file:event['native_file']=native_file
            if dispatch_file:event['dispatch_file']=dispatch_file
            if contract:event['rejection_contract']=contract
            job.setdefault('send_outcomes',[]).append(event)
            if classification=='accepted':job.setdefault('send_accepted_at',event['recorded_at'])
            if classification=='rejected_before_delivery' and not job.get('native_turn_id') and not job.get('send_accepted_at') and job['state'] in PENDING:
                job['state']='rejected';job.pop('waiting_for',None)
            self.update(job);self.db.commit();return self.summary(job)
        except Exception:self.db.rollback();raise
    def sent(self,ident,payload,dispatch=None):
        # Legacy spelling retained. Without the dispatch record it audits unknown,
        # rather than attributing another request's same-chat acknowledgement.
        return self.send_outcome(ident,payload,dispatch)
    def audit(self,ident):
        job=self.job(ident)
        return {'request_id':ident,'send_outcomes':job.get('send_outcomes',[])}
    def collect(self,ident,payload):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            job=self.job(ident)
            if job['state']=='complete':self.db.commit();return self.summary(job)
            if job['state']=='discarded':raise ConsultationError('consultation_was_discarded')
            read=verified_thread(payload,job['thread_id']);matches=[]
            for turn in read.get('turns',[]):
                for item in turn.get('items',[]):
                    if item.get('type')=='userMessage':
                        text=''.join(c.get('text','') for c in item.get('content',[]) if c.get('type')=='text')
                        if text.startswith(job['marker']+'\n'):matches.append(turn);break
            if len(matches)>1:raise ConsultationError('duplicate_native_turns_inspect_before_continuing')
            job['last_checked_at']=time.time()
            if not matches:job['waiting_for']='matching_native_turn'
            else:
                turn=matches[0];job['native_turn_id']=turn.get('id')
                if job['state'] not in ('abandoned','rejected'):job['state']='awaiting_reply'
                messages=[i for i in turn.get('items',[]) if i.get('type')=='agentMessage' and i.get('text','').strip()]
                last=messages[-1] if messages else {}
                text=last.get('text','');ending=re.search(r'(?:^|\n)[`* ]*'+re.escape(job['footer'])+r'[`* ]*\s*(?:```)?\s*$',text)
                if turn.get('error'):job['waiting_for']='native_turn_error_inspect_without_resending'
                elif turn.get('status')!='completed' or not messages:job['waiting_for']='completed_assistant_reply'
                elif any(i.get('truncated') for i in messages):job['waiting_for']='untruncated_native_read_request_more_output'
                elif not ending:job['waiting_for']='full_reply_footer_native_read_may_be_partial'
                elif not text[:ending.start()].strip():job['waiting_for']='substantive_assistant_reply'
                else:
                    reply=text[:ending.start()].strip()
                    job.update(state='complete',reply=reply,reply_sha256=digest(reply),native_reply_sha256=digest(text),completed_at=time.time(),
                               native_evidence={'thread':{k:read['thread'].get(k) for k in ['id','kind','title']},'turn':turn})
                    job.pop('waiting_for',None)
            self.update(job);self.db.commit();return self.summary(job)
        except Exception:self.db.rollback();raise
    def discard(self,ident):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            job=self.job(ident)
            if job['state']!='prepared':raise ConsultationError('delivery_possible_collect_before_discarding')
            job['state']='discarded';self.update(job);self.db.commit();return self.summary(job)
        except Exception:self.db.rollback();raise
    def abandon(self,ident,reason):
        if not reason.strip() or len(reason)>2000:raise ConsultationError('abandon_reason_required_max_2000_characters')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            job=self.job(ident)
            if job['state']=='complete':raise ConsultationError('completed_consultation_keep_saved_reply')
            if job['state'] not in ('abandoned','discarded','rejected'):
                job.update(state='discarded' if job['state']=='prepared' else 'abandoned',abandon_reason=reason.strip(),abandoned_at=time.time())
                self.update(job)
            self.db.commit();return self.summary(job)
        except Exception:self.db.rollback();raise

def read_json(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def read_evidence(path):
    data=path.read_bytes()
    if len(data)>1048576:raise ConsultationError('send_evidence_exceeds_1_mib')
    try:payload=json.loads(data.decode('utf-8-sig'))
    except (ValueError,UnicodeError):raise ConsultationError('send_evidence_file_must_be_utf8_json') from None
    return payload,{'path':str(path.resolve()),'sha256':hashlib.sha256(data).hexdigest()}
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,default=Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData/Local'))/'WorkPartnersChatGPT/consultations')
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ['setup','bind']:
        bind=sub.add_parser(name);bind.add_argument('--native-json',type=Path,required=True);bind.add_argument('--project')
    prepare=sub.add_parser('prepare');prepare.add_argument('--project',required=True);prepare.add_argument('--question-file',type=Path,required=True);prepare.add_argument('--context-file',type=Path)
    for name in ['dispatch','collect','recover']:
        command=sub.add_parser(name);command.add_argument('--request',required=True);command.add_argument('--native-json',type=Path,required=True)
    for name in ['send-outcome','sent']:
        command=sub.add_parser(name);command.add_argument('--request',required=True)
        command.add_argument('--dispatch-json',type=Path,required=name=='send-outcome')
        evidence=command.add_mutually_exclusive_group(required=True)
        evidence.add_argument('--native-json',type=Path);evidence.add_argument('--no-result-reason')
    command=sub.add_parser('audit');command.add_argument('--request',required=True)
    status=sub.add_parser('status');selection=status.add_mutually_exclusive_group(required=True);selection.add_argument('--request');selection.add_argument('--project')
    command=sub.add_parser('discard');command.add_argument('--request',required=True)
    command=sub.add_parser('abandon');command.add_argument('--request',required=True);command.add_argument('--reason',required=True)
    args=parser.parse_args();store=Receipts(args.data_dir)
    try:
        if args.command in ('bind','setup'):result=store.bind(read_json(args.native_json),args.project)
        elif args.command=='prepare':result=store.prepare(args.project,args.question_file.read_text(encoding='utf-8-sig'),args.context_file.read_text(encoding='utf-8-sig') if args.context_file else '')
        elif args.command=='dispatch':result=store.dispatch(args.request,read_json(args.native_json))
        elif args.command in ('collect','recover'):result=store.collect(args.request,read_json(args.native_json))
        elif args.command in ('send-outcome','sent'):
            payload,native_file=read_evidence(args.native_json) if args.native_json else (NO_NATIVE_RESULT,None)
            dispatch,dispatch_file=read_evidence(args.dispatch_json) if args.dispatch_json else (None,None)
            result=store.send_outcome(args.request,payload,dispatch,native_file=native_file,dispatch_file=dispatch_file,no_result_reason=args.no_result_reason)
        elif args.command=='audit':result=store.audit(args.request)
        elif args.command=='discard':result=store.discard(args.request)
        elif args.command=='abandon':result=store.abandon(args.request,args.reason)
        else:result=store.project_status(args.project) if args.project else store.summary(store.job(args.request))
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except ConsultationError as error:
        print(json.dumps({'error':str(error)}),file=sys.stderr);return 1
    finally:store.close()

if __name__=='__main__':raise SystemExit(main())
