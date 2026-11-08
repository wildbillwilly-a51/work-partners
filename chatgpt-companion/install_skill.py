"""Install only the private ChatGPT companion; leave the public skill untouched."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import zipfile

HERE=Path(__file__).resolve().parent
NAME='work-partners-chatgpt'
FILES=('SKILL.md','scripts/chatgpt.py')
BUNDLE_FILES=('install_skill.py','COMPANION.md',*(f'skills/{NAME}/{name}' for name in FILES))
sha=lambda data:hashlib.sha256(data).hexdigest()

def checked(path):
    path=Path(path).expanduser().absolute()
    if any(p.is_symlink() or p.is_junction() for p in [path,*path.parents]):raise RuntimeError('skill_path_redirected')
    return path.resolve()

def install(codex_root,source=HERE):
    source=checked(Path(source)/'skills'/NAME);target=checked(Path(codex_root)/'skills'/NAME)
    receipt_path=checked(target/'.private-installation.json')
    old=json.loads(receipt_path.read_text(encoding='utf-8')) if receipt_path.exists() else None
    if target.exists() and any(target.iterdir()) and not old:raise RuntimeError('unknown_existing_private_skill_preserved')
    if old:
        if old.get('format')!=1:raise RuntimeError('unknown_skill_receipt_format')
        if not set(old['files']).issubset(FILES):raise RuntimeError('unknown_skill_receipt_files')
        for name,value in old['files'].items():
            path=checked(target/name)
            if not path.is_file() or sha(path.read_bytes())!=value:raise RuntimeError('private_skill_has_local_edits')
    payload={}
    for name in FILES:
        path=checked(source/name)
        if not path.is_file():raise RuntimeError('private_skill_source_missing')
        payload[name]=path.read_bytes()
    for name,data in payload.items():
        path=checked(target/name);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    receipt={'format':1,'source':str(source),'files':{name:sha(data) for name,data in payload.items()}}
    receipt_path.write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    return {'installed':str(target),'public_skill_unchanged':True,'host':'Codex desktop with native ChatGPT chat tools','helper':str(target/'scripts/chatgpt.py'),
            'next_action':'In Codex desktop, ask to set up ChatGPT for this project, then show status. The agent selects an existing ordinary ChatGPT chat; no MCP service or credential is required.'}

def bundle(output,source=HERE):
    source=checked(source);output=checked(output)
    if output.exists():raise RuntimeError('bundle_output_exists')
    payload={name:checked(source/name).read_bytes() for name in BUNDLE_FILES}
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,data in payload.items():archive.writestr(name,data)
    return {'bundle':str(output),'files':list(payload),'bytes':output.stat().st_size,'sha256':sha(output.read_bytes())}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--codex-root',type=Path,default=Path(os.environ.get('CODEX_HOME',Path.home()/'.codex')))
    p.add_argument('--bundle',type=Path,help='Write the standalone companion-only source ZIP; do not install')
    a=p.parse_args()
    try:print(json.dumps(bundle(a.bundle) if a.bundle else install(a.codex_root),indent=2));return 0
    except Exception as error:print(json.dumps({'error':str(error)}),file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
