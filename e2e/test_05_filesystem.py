"""Filesystem access as an agent sees it: the proxy has no FS, so the model reaches files only through
tool calls. Each test runs a full tool loop against an in-memory VirtualFS and checks the end state."""
import secrets

import pytest

import e2e_helpers as h

pytestmark = pytest.mark.live


def token(prefix: str) -> str:
    return f"{prefix}-{secrets.randbelow(9000) + 1000}-{secrets.token_hex(2).upper()}"


@pytest.mark.core
def test_agent_reads_a_file(client):
    secret = token("ZEBRA")
    fs = h.VirtualFS({"/project/notes.txt": f"Meeting notes.\nThe vault codeword is {secret}.\n"})
    answer, _ = h.run_agent(client, fs, "Read /project/notes.txt and tell me the vault codeword.")
    assert secret in answer
    assert ("read_file", {"path": "/project/notes.txt"}) in fs.trace


def test_agent_explores_directories_to_find_a_file(client):
    secret = token("OTTER")
    fs = h.VirtualFS({
        "/repo/README.md": "Nothing interesting here.",
        "/repo/src/main.py": "print('hello')",
        "/repo/config/secrets.env": f"DEPLOY_TOKEN={secret}\nREGION=eu\n",
    })
    answer, _ = h.run_agent(client, fs, "Somewhere under /repo there is a deploy token. Find it and report its value.",
                            max_steps=10)
    assert secret in answer
    assert any(name == "list_directory" for name, _ in fs.trace)


def test_agent_writes_a_file(client):
    fs = h.VirtualFS({"/work/.keep": ""})
    answer, _ = h.run_agent(client, fs, "Create /work/hello.txt containing exactly the text: Hello from Argon")
    assert "/work/hello.txt" in fs.files
    assert fs.files["/work/hello.txt"].strip() == "Hello from Argon"
    assert answer


def test_agent_read_modify_write(client):
    fs = h.VirtualFS({"/work/config.ini": "[server]\nport=8080\nhost=localhost\n"})
    h.run_agent(client, fs, "In /work/config.ini change port from 8080 to 9090 and keep everything else the same. "
                            "Write the result back to the same file.")
    new = fs.files["/work/config.ini"]
    assert "port=9090" in new and "host=localhost" in new and "8080" not in new


def test_agent_reports_missing_file_instead_of_inventing(client):
    fs = h.VirtualFS({"/data/real.txt": "exists"})
    answer, _ = h.run_agent(client, fs, "Read /data/ghost.txt and quote its first line.")
    assert any(args.get("path") == "/data/ghost.txt" for _, args in fs.trace)
    low = answer.lower()
    assert any(w in low for w in ("not exist", "no such", "doesn't exist", "does not exist", "missing", "not found", "error"))


def test_agent_streaming_tool_loop(client):
    secret = token("HERON")
    fs = h.VirtualFS({"/a/b.txt": f"value={secret}"})
    messages = [{"role": "user", "content": "Read /a/b.txt and tell me the value."}]
    import json
    for _ in range(6):
        res = h.chat_stream(client, messages, tools=h.FS_TOOLS)
        if not res.tool_calls:
            assert secret in res.text
            return
        calls = [{"id": f"call_{i}", "type": "function", "function": c} for i, c in enumerate(res.tool_calls)]
        messages.append({"role": "assistant", "content": None, "tool_calls": calls})
        for c in calls:
            out = fs.run(c["function"]["name"], json.loads(c["function"]["arguments"]))
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": out})
    raise AssertionError(f"no final answer; trace={fs.trace}")
