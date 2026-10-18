"""Install matching personal skills and one shared runner; no services or sign-ins."""
import sys
if sys.version_info < (3, 12):
    raise SystemExit("work-partners requires Python 3.12 or newer; use a compatible Python executable")

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def checked_root(path):
    path = path.expanduser().absolute()
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise RuntimeError("Installation roots must not traverse symlinks or junctions")
    return path.resolve()


def install(home, codex_root=None, claude_root=None):
    home = checked_root(home)
    runtime = checked_root(home / ".a51/work-partners")
    agents = {"codex": checked_root(codex_root or home / ".codex"),
              "claude": checked_root(claude_root or home / ".claude")}
    destinations = [runtime, *(checked_root(root / "skills/work-partners") for root in agents.values())]
    for index, destination in enumerate(destinations):
        if destination.is_relative_to(ROOT):
            raise RuntimeError("Keep installation destinations outside the source checkout")
        for other in destinations[:index]:
            if destination.is_relative_to(other) or other.is_relative_to(destination):
                raise RuntimeError("Installation destinations must not overlap")
    manifest = runtime / "installation.json"
    if manifest.is_symlink() or manifest.is_junction():
        raise RuntimeError("Installation manifest must not be a symlink or junction")
    previous = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {"files": {}}
    source = ROOT / "skills/work-partners"
    planned = {runtime / "partner.py": source / "scripts/partner.py"}
    for agent, agent_root in agents.items():
        destination = agent_root / "skills/work-partners"
        planned[destination / "SKILL.md"] = source / "SKILL.md"
        if agent == "codex":
            planned[destination / "agents/openai.yaml"] = source / "agents/openai.yaml"
    hashes = {}
    for path, original in planned.items():
        checked_root(path.parent)
        if path.is_symlink() or path.is_junction():
            raise RuntimeError("Installation target is a symlink or junction")
        if path.exists():
            old = hashlib.sha256(path.read_bytes()).hexdigest()
            expected = previous["files"].get(str(path))
            if old != expected and path.read_bytes() != original.read_bytes():
                raise RuntimeError(f"Preserving an existing or locally modified file: {path}")
        hashes[str(path)] = hashlib.sha256(original.read_bytes()).hexdigest()
    # All preservation checks precede any installation writes. The manifest is last.
    for path, original in planned.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".installing")
        shutil.copyfile(original, temporary)
        os.replace(temporary, path)
    result = {"version": 1, "source": str(ROOT), "files": hashes}
    temporary = manifest.with_name("installation.json.installing")
    temporary.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, manifest)
    if any(hashlib.sha256(Path(p).read_bytes()).hexdigest() != h for p, h in hashes.items()):
        raise RuntimeError("Installed bytes do not match their source")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, help="Isolated personal home; ignores agent-root environment variables")
    parser.add_argument("--codex-root", type=Path, help="Explicit Codex configuration root")
    parser.add_argument("--claude-root", type=Path, help="Explicit Claude Code configuration root")
    try:
        args = parser.parse_args()
        codex_root = args.codex_root or (Path(os.environ["CODEX_HOME"]) if args.home is None and os.environ.get("CODEX_HOME") else None)
        claude_root = args.claude_root or (Path(os.environ["CLAUDE_CONFIG_DIR"]) if args.home is None and os.environ.get("CLAUDE_CONFIG_DIR") else None)
        print(json.dumps(install(args.home or Path.home(), codex_root, claude_root), indent=2))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
