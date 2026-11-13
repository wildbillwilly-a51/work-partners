"""User-facing portable commands. No services or global installation."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from runtime import HERE,SetupError,check_client,child_environment,credential,profile,request_stop,run,status,tunnel_readiness,validate

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=HERE.parent/'config.json')
    parser.add_argument('command',choices=['doctor','start','status','stop','projects','notes','handoffs'])
    parser.add_argument('--project',help='Project name or ID for notes or handoffs; use projects to resolve duplicate names')
    parser.add_argument('--handoff-id',help='Read the complete staged handoff with this receipt ID')
    parser.add_argument('--local-only',action='store_true',help='Check local installation and actual MCP tools without credentials or remote polling')
    args=parser.parse_args(); path=args.config.resolve()
    try:
        if sys.version_info<(3,12): raise SetupError('python_312_or_newer_required')
        if args.command=='start': return run(path)
        if args.command in ['projects','notes','handoffs']:
            if args.command!='projects' and not args.project: raise SetupError(f'project_required_for_{args.command}')
            config,state,binary,node=validate(path)
            argument=None
            try:
                command=[node,str(HERE/'cli.mjs'),'projects' if args.command=='projects' else f'project-{args.command}',str(path)]
                if args.command!='projects':
                    query={'project':args.project,'limit':20}
                    if args.command=='handoffs' and args.handoff_id is not None:
                        query['handoff_id']=args.handoff_id
                    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',suffix='.json',prefix=f'{args.command}-',dir=state,delete=False) as output:
                        json.dump(query,output);argument=Path(output.name)
                    command.append(str(argument))
                response=subprocess.run(command,cwd=HERE,env=child_environment(path,'',config=config),capture_output=True,encoding='utf-8',timeout=60,creationflags=subprocess.CREATE_NO_WINDOW)
                if response.returncode: raise SetupError('local_readback_failed_check_project_name_or_id')
                result=json.loads(response.stdout)
            finally:
                if argument: argument.unlink(missing_ok=True)
        elif args.command=='status': result=status(path)
        elif args.command=='stop': result=request_stop(path)
        else:
            config,state,binary,node=validate(path)
            env=child_environment(path,'',config=config)
            probe=subprocess.run([node,str(HERE/'probe.mjs'),str(path)],cwd=HERE,env=env,capture_output=True,text=True,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW)
            if probe.returncode: raise SetupError('local_mcp_probe_failed_check_dependencies_and_projects')
            runtime_status=status(path)
            result={'local':json.loads(probe.stdout),'runtime':runtime_status,'remote':'not_checked'}
            if not args.local_only:
                result['tunnel_readiness']=tunnel_readiness(state,runtime_status)
                env=child_environment(path,credential(config),config=config)
                with tempfile.TemporaryDirectory(prefix='doctor-',dir=state) as scratch:
                    check_client(binary,profile(config,Path(scratch),node),env)
                result['remote']='tunnel_doctor_passed_not_a_chatgpt_tool_test'
        print(json.dumps(result,indent=2)); return 0
    except SetupError as error:
        print(json.dumps({'error':str(error)}),file=sys.stderr);return 1
    except Exception as error:
        print(json.dumps({'error':'setup_failed','type':type(error).__name__}),file=sys.stderr);return 1

if __name__=='__main__': raise SystemExit(main())
