"""Owner-authorized live acceptance: installed skill, actual Claude lead -> Codex peer.

Codex -> Claude is exercised directly by the initiating Codex desktop task.
This test uses only local source; private evidence lives outside the target repo.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import uuid


def load_runtime(path):
    spec = importlib.util.spec_from_file_location("partner_runtime", path)
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    return runtime


def create_fixture(output):
    """Retain an isolated, credential-free Git fixture with a reviewable defect."""
    project = Path(tempfile.mkdtemp(prefix="fixture-", dir=output))
    files = {
        "AGENTS.md": "This is a read-only review fixture. Do not edit files or use the network.\n",
        "README.md": "total(values) must sum every supplied number, including the last one.\n",
        "calculator.py": "def total(values):\n    return sum(values[:-1])\n",
    }
    for name, content in files.items():
        (project / name).write_text(content, encoding="utf-8")
    for args in (["init", "-q"], ["config", "user.name", "Work partners fixture"],
                 ["config", "user.email", "fixture@local"], ["add", "."],
                 ["commit", "-qm", "Create read-only review fixture"]):
        subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True)
    return project


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, help="Existing Git checkout; omit for a disposable fixture")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    runtime = Path.home() / ".a51/work-partners/partner.py"
    skill = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / "skills/work-partners/SKILL.md"
    partner = load_runtime(runtime)
    output = args.output.resolve()
    if output.is_relative_to(Path(__file__).resolve().parents[1]):
        raise RuntimeError("Live-test evidence must be outside the work-partners source repository")
    project = partner.project_root(args.project) if args.project else None
    if project and output.is_relative_to(project):
        raise RuntimeError("Live-test evidence must be outside the reviewed project")
    output.mkdir(parents=True, exist_ok=True)
    if project is None:
        project = create_fixture(output)
    request = output / "claude-to-codex.txt"
    request.write_text(
        "Read-only review of this source checkout: read AGENTS.md and README.md if present, then "
        "the directly relevant source. Identify concrete defects or unmet requirements with file/line "
        "evidence. Distinguish observations from assumptions; do not invent a quota of findings. "
        "No network, hosts, private media, secrets or changes. Ask the lead one focused follow-up question.",
        encoding="utf-8")
    task = str(uuid.uuid4())
    python = Path(sys.executable).as_posix()
    command = " ".join(shlex.quote(x) for x in [python, "-B", runtime.as_posix(), "ask", "--lead", "claude",
        "--project", project.as_posix(), "--request-file", request.as_posix(), "--task", task, "--timeout", "240"])
    prefix = " ".join(shlex.quote(x) for x in [python, "-B", runtime.as_posix()])
    prompt = ("You are the Claude desktop lead in an owner-authorized, read-only partnership acceptance test. "
              f"First Read the installed skill at {skill.as_posix()}. Then use Bash exactly once to run the "
              "following shared-runner command, in the foreground with a 300000 ms tool timeout. This asks "
              "Codex, your read-only partner, to review the actual project. Do not run any other shell command, "
              "edit anything, access hosts/network directly, or delegate any additional work. Report the "
              "partner's findings, question and task ID. A successful skill invocation requires actually running "
              "the command; explaining it is insufficient.\n" + command)
    argv = [partner.trusted_executable("claude", project), "-p", "--restricted", "--safe-mode", "--strict-mcp-config",
            "--permission-mode", "dontAsk", "--permission-prompts", "none", "--tools", "Read,Bash",
            "--allowedTools", "Read", "Bash(" + prefix + " *)", "--add-dir", str(skill.parent),
            "--output-format", "stream-json", "--verbose", "--no-session-persistence"]
    before = partner.snapshot(project)
    partner.save(output / "before.json", before)
    (output / "lead-request.txt").write_text(prompt, encoding="utf-8")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    for k in (partner.GUARD, "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CODEX_THREAD_ID"):
        env.pop(k, None)
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    process = subprocess.Popen(argv, cwd=project, env=env, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
    try:
        stdout, stderr = process.communicate(prompt.encode(), timeout=360)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        partner.stop(process)
        stdout, stderr = process.communicate()
        (output / "lead-stdout.jsonl").write_bytes(stdout)
        (output / "lead-stderr.log").write_bytes(stderr)
        raise
    (output / "lead-stdout.jsonl").write_bytes(stdout)
    (output / "lead-stderr.log").write_bytes(stderr)
    if process.returncode:
        raise RuntimeError("Claude lead did not complete; inspect private lead logs")
    tools = []
    result = None
    for line in stdout.decode("utf-8-sig").splitlines():
        value = json.loads(line)
        if value.get("type") == "assistant":
            tools += [part for part in value.get("message", {}).get("content", []) if part.get("type") == "tool_use"]
        if value.get("type") == "result":
            result = value
    bash = [t for t in tools if t["name"] == "Bash"]
    reads = [t for t in tools if t["name"] == "Read"]
    if (not result or result.get("is_error") or len(bash) != 1
            or bash[0]["input"]["command"].strip() != command
            or not any(Path(t["input"]["file_path"]).resolve() == skill.resolve() for t in reads)
            or any(t["name"] not in {"Bash", "Read"} for t in tools)):
        raise RuntimeError("Claude did not read the installed skill and invoke only the specified runner command")
    task_path = Path.home() / ".a51/work-partners/state" / task / "task.json"
    record = json.loads(task_path.read_text(encoding="utf-8"))
    op = record["operations"][-1]
    if record["lead"] != "claude" or record["peer"] != "codex" or op["status"] != "completed":
        raise RuntimeError("No completed Codex peer receipt from the Claude lead")
    # Re-delivery must return the saved result, not create another native call.
    cached = subprocess.run([sys.executable, "-B", str(runtime), "ask", "--lead", "claude", "--project", str(project),
        "--request-file", str(request), "--task", task], capture_output=True, text=True, encoding="utf-8", timeout=30)
    if cached.returncode or not json.loads(cached.stdout)["cached"]:
        raise RuntimeError("Completed-response re-delivery was not cached")
    after = partner.snapshot(project)
    partner.save(output / "after.json", after)
    if before != after:
        raise RuntimeError("Reviewed checkout changed; inspect private before/after evidence")
    proof = dict(passed=True, direction="claude-lead-to-codex-peer", task_id=task, receipt=str(task_path),
        installed_skill_sha256=hashlib.sha256(skill.read_bytes()).hexdigest(),
        actual_lead_tools=[t["name"] for t in tools], native_peer_session_id=op["native_session_id"],
        project_head=after["head"], project_unchanged=True, completed_redelivery_cached=True,
        peer_result=op["result"], lead_result=result.get("result"), limits=["CLI harness; desktop UI not clicked", "Local source review; no live host health certification"])
    partner.save(output / "proof.json", proof)
    print(json.dumps(proof, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
