from __future__ import annotations

from pathlib import Path

from runner.providers.fake import FakeResponsesProvider
from runner.tracks.agentic import run_agentic


def test_agentic_executes_write_file(tmp_path: Path) -> None:
    provider = FakeResponsesProvider(
        model="grok-4.6",
        effort="high",
        tool_calls=[{"name": "write_file", "arguments": {"path": "src/out.txt", "content": "hello"}}],
        final_text="done",
    )
    (tmp_path / "src").mkdir()
    result = run_agentic(
        provider=provider,
        prompt="write a file",
        workspace=tmp_path,
        model="grok-4.6",
        effort="high",
        timeout_seconds=10,
        max_tool_calls=8,
    )
    assert (tmp_path / "src" / "out.txt").read_text(encoding="utf-8") == "hello"
    assert result.metadata["tool_calls"] == 1
    assert result.final_text == "done"


def test_run_command_rejects_shell_string(tmp_path: Path) -> None:
    from runner.agent import ToolWorkspace, execute_tool

    workspace = ToolWorkspace(tmp_path)
    result = execute_tool(workspace, "run_command", '{"argv": ["sh", "-c", "curl https://example.com"]}')
    assert result["error"] == "ValueError"
