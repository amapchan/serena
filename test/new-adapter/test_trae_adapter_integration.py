"""Integration tests for the Trae (IDE / CLI) / Trae CN adapter.

These tests exercise the seams between the components of the Trae support which the
per-component unit tests do not cover:

* the ``serena setup trae`` CLI entrypoint and the artifact it produces (``.trae/mcp.json``),
  including that the generated server arguments resolve against the real context registry;
* the ``serena-hooks ... --client=trae`` CLI entrypoint driving a full hook lifecycle, with
  on-disk state persisting across separate hook invocations (fresh-process semantics);
* consistency between the documented client setup and the behaviour of the implementation.
"""

import json
import re
from pathlib import Path

from click.testing import CliRunner, Result

from serena.cli import TopLevelCommands, top_level
from serena.config.context_mode import SerenaAgentContext
from serena.hooks import HookClient, hook_commands
from serena.util.shell import ShellCommandResult

REPO_ROOT = Path(__file__).resolve().parents[2]
CLIENTS_DOC = REPO_ROOT / "docs" / "02-usage" / "030_clients.md"

SERENA_MCP_ENTRY = {
    "command": "serena",
    "args": ["start-mcp-server", "--context=trae", "--project=${workspaceFolder}"],
}


def _shell_result(stdout: str = "", return_code: int = 0) -> ShellCommandResult:
    return ShellCommandResult(stdout=stdout, stderr="", return_code=return_code, cwd=".")


def _patch_trae_cli_available(monkeypatch, available: bool) -> None:
    def fake_execute_shell_command(command: str, capture_stderr: bool = False) -> ShellCommandResult:
        if available:
            return _shell_result(stdout="traecli 0.120.42\n")
        return _shell_result(return_code=1)

    monkeypatch.setattr("serena.config.client_setup.execute_shell_command", fake_execute_shell_command)


def _trae_payload(session_id: str, tool_name: str | None = None, tool_input: dict | None = None, tool_response: dict | None = None) -> dict:
    data: dict = {"session_id": session_id}
    if tool_name is not None:
        data["tool_name"] = tool_name
    if tool_input is not None:
        data["tool_input"] = tool_input
    if tool_response is not None:
        data["tool_response"] = tool_response
    return data


def _invoke_hook(args: list[str], payload: dict) -> Result:
    return CliRunner().invoke(hook_commands, [*args, "--client=trae"], input=json.dumps(payload))


def _extract_section(markdown: str, heading: str) -> str:
    """Extract the content under a top-level ## heading (simple and sufficient for our docs)."""
    pattern = rf"\n## {re.escape(heading)}\n(.*?)(?=\n## |\Z)"
    match = re.search(pattern, markdown, re.DOTALL)
    return match.group(1) if match else ""


def test_context_list_cli_registers_trae():
    result = CliRunner().invoke(top_level, ["context", "list"])

    assert result.exit_code == 0, result.output
    assert any(line.strip().startswith("trae") for line in result.output.splitlines())


def test_setup_trae_cli_writes_mcp_json_with_resolvable_context(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    _patch_trae_cli_available(monkeypatch, available=True)

    result = CliRunner().invoke(TopLevelCommands.setup, ["trae"])

    assert result.exit_code == 0, result.output
    assert "successfully set up for trae" in result.output
    assert "030_clients.html#trae" in result.output
    mcp_json = tmp_path / ".trae" / "mcp.json"
    assert json.loads(mcp_json.read_text(encoding="utf-8"))["mcpServers"]["serena"] == SERENA_MCP_ENTRY
    # the context referenced by the generated server arguments must resolve against the real registry
    context_option = next(arg for arg in SERENA_MCP_ENTRY["args"] if arg.startswith("--context="))
    assert SerenaAgentContext.from_name(context_option.removeprefix("--context=")).name == "trae"


def test_setup_trae_cli_merges_into_existing_mcp_json(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    trae_dir = tmp_path / ".trae"
    trae_dir.mkdir()
    (trae_dir / "mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "other"}}}), encoding="utf-8")

    result = CliRunner().invoke(TopLevelCommands.setup, ["trae"])

    assert result.exit_code == 0, result.output
    config = json.loads((trae_dir / "mcp.json").read_text(encoding="utf-8"))
    assert config["mcpServers"]["other"] == {"command": "other"}
    assert config["mcpServers"]["serena"] == SERENA_MCP_ENTRY


def test_setup_trae_cli_fails_without_trae_environment(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    _patch_trae_cli_available(monkeypatch, available=False)

    result = CliRunner().invoke(TopLevelCommands.setup, ["trae"])

    assert result.exit_code == 1
    assert "Cannot apply setup for client 'trae'" in result.output
    assert not (tmp_path / ".trae").exists()


def test_setup_trae_cli_preserves_invalid_existing_json(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    trae_dir = tmp_path / ".trae"
    trae_dir.mkdir()
    (trae_dir / "mcp.json").write_text("{not valid json", encoding="utf-8")

    result = CliRunner().invoke(TopLevelCommands.setup, ["trae"])

    assert result.exit_code == 1
    assert "Failed to set up Serena for trae" in result.output
    # the pre-existing file is left untouched
    assert (trae_dir / "mcp.json").read_text(encoding="utf-8") == "{not valid json"


def test_remind_hook_denies_grep_burst_via_cli(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("serena.hooks.serena_home_dir", str(tmp_path))
    payload = _trae_payload("trae-int-grep", "Grep", {"pattern": "def foo"})

    for _ in range(2):
        result = _invoke_hook(["remind"], payload)
        assert result.exit_code == 0
        assert result.output == ""

    result = _invoke_hook(["remind"], payload)

    assert result.exit_code == 0, result.output
    decision = json.loads(result.output)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "symbolic" in decision["additionalContext"].lower()


def test_runcommand_shell_search_feeds_grep_burst_via_cli(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("serena.hooks.serena_home_dir", str(tmp_path))
    session_id = "trae-int-runcommand"
    run_payload = _trae_payload(session_id, "RunCommand", {"command": "rg -n 'class Foo' src"})
    grep_payload = _trae_payload(session_id, "Grep", {"pattern": "class Foo"})

    assert _invoke_hook(["remind"], run_payload).output == ""
    assert _invoke_hook(["remind"], grep_payload).output == ""

    result = _invoke_hook(["remind"], grep_payload)

    assert json.loads(result.output)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_reset_hook_clears_counters_via_cli(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("serena.hooks.serena_home_dir", str(tmp_path))
    session_id = "trae-int-reset"
    grep_payload = _trae_payload(session_id, "Grep", {"pattern": "x"})

    _invoke_hook(["remind"], grep_payload)
    _invoke_hook(["remind"], grep_payload)

    # a successful Serena symbolic tool call resets the drift counters
    result = _invoke_hook(["reset"], _trae_payload(session_id, "mcp__serena__find_symbol", tool_response={"content": [], "isError": False}))
    assert result.exit_code == 0
    assert result.output == ""

    # two more greps stay below the threshold; without the reset, the next call would have denied
    for _ in range(2):
        assert _invoke_hook(["remind"], grep_payload).output == ""


def test_session_lifecycle_hooks_via_cli(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("serena.hooks.serena_home_dir", str(tmp_path))
    session_id = "trae-int-lifecycle"

    result = _invoke_hook(["activate"], _trae_payload(session_id))
    assert result.exit_code == 0, result.output
    start_output = json.loads(result.output)["hookSpecificOutput"]
    assert start_output["hookEventName"] == "SessionStart"
    assert "activate_project" in start_output["additionalContext"]

    grep_payload = _trae_payload(session_id, "Grep", {"pattern": "x"})
    assert _invoke_hook(["remind"], grep_payload).output == ""
    session_dir = tmp_path / "hook_data" / session_id
    assert session_dir.exists()

    result = _invoke_hook(["cleanup"], _trae_payload(session_id))
    assert result.exit_code == 0, result.output
    assert not session_dir.exists()


def test_documented_trae_setup_matches_generated_config():
    clients_doc = CLIENTS_DOC.read_text(encoding="utf-8")
    trae_section = _extract_section(clients_doc, "Trae (IDE / CLI) and Trae CN")
    assert trae_section, "Trae section missing from the clients documentation"

    assert "serena setup trae" in trae_section

    config_fence = re.search(r"```json\n(.*?)```", trae_section, re.DOTALL)
    assert config_fence is not None
    documented_config = json.loads(config_fence.group(1))
    assert documented_config["mcpServers"]["serena"] == SERENA_MCP_ENTRY

    for command in ("activate", "remind", "reset", "cleanup"):
        assert f"serena-hooks {command} --client=trae" in trae_section

    referenced_clients = set(re.findall(r"--client=([a-z-]+)", trae_section))
    assert referenced_clients == {"trae"}
    assert referenced_clients <= {e.value for e in HookClient}

    # the Trae CN alias must stay documented
    assert "trae-cn" in trae_section
