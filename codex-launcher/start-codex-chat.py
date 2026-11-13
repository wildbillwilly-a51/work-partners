"""Start one local project chat through the running Codex desktop app."""
import json
import os
from pathlib import Path
import sys
import uuid
from codex_desktop import AppTools, desktop_server, native_data


def start_chat(client, root, prompt_file, receipt):
    projects = native_data(client.call("list_projects", {}))["projects"]
    root = os.path.normcase(os.path.realpath(root))
    matches = [
        p["projectId"] for p in projects
        if p.get("hostId") == "local" and p.get("path")
        and os.path.normcase(os.path.realpath(p["path"])) == root
    ]
    if len(matches) != 1:
        print("project_not_registered_in_codex")
        return

    message = json.loads(Path(prompt_file).read_text(encoding="utf-8"))
    receipt = Path(receipt)
    with receipt.open("x", encoding="utf-8") as handle:
        json.dump({"phase": "dispatching"}, handle)
        handle.flush()
        os.fsync(handle.fileno())

    data = native_data(client.call("create_thread", {
        "target": {"type": "project", "projectId": matches[0], "environment": {"type": "local"}},
        "title": message["title"],
        "prompt": message["prompt"],
    }))
    thread_id = str(uuid.UUID(data["threadId"]))
    complete = receipt.with_suffix(".complete")
    with complete.open("x", encoding="utf-8") as handle:
        json.dump({"phase": "native_call_returned", "thread_id": thread_id}, handle)
        handle.flush()
        os.fsync(handle.fileno())
    complete.replace(receipt)


if __name__ == "__main__":
    try:
        if len(sys.argv) == 6:
            node, context_thread, root, prompt_file, receipt = sys.argv[1:]
            server = desktop_server()
        else:
            node, server, context_thread, root, prompt_file, receipt = sys.argv[1:]
        client = AppTools(node, server, context_thread)
        try:
            start_chat(client, root, prompt_file, receipt)
        finally:
            client.close()
    except Exception as error:
        # A reserved receipt stays pending even if the native reply is lost.
        print(type(error).__name__, file=sys.stderr)
        sys.exit(1)
