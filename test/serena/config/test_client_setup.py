import json

import pytest

from serena.config.client_setup import (
    ClientSetupHandlerGrok,
    ClientSetupHandlerTrae,
    client_setup_handlers,
)
from serena.config.context_mode import SerenaAgentContext
from serena.util.shell import ShellCommandResult


def _result(command: str, return_code: int = 0, stdout: str = "") -> ShellCommandResult:
    return ShellCommandResult(stdout=stdout, stderr="", return_code=return_code, cwd=".")


def test_grok_setup_handler_is_applicable_for_grok_build(monkeypatch):
    commands: list[str] = []

    def fake_execute_shell_command(command: str, capture_stderr: bool = False) -> ShellCommandResult:
        commands.append(command)
        if command == "grok --version":
            return _result(command, stdout="grok 0.2.82 (6d0b07d2de) [stable]\n")
        if command == "grok mcp add --help":
            return _result(command, stdout="Add or update an MCP server\n")
        return _result(command, return_code=1)

    monkeypatch.setattr("serena.config.client_setup.execute_shell_command", fake_execute_shell_command)

    assert ClientSetupHandlerGrok().is_applicable() is True
    assert commands == ["grok --version", "grok mcp add --help"]


def test_grok_setup_handler_rejects_binary_without_mcp_add(monkeypatch):
    def fake_execute_shell_command(command: str, capture_stderr: bool = False) -> ShellCommandResult:
        if command == "grok --version":
            return _result(command, stdout="grok 0.2.82 (6d0b07d2de) [stable]\n")
        return _result(command, return_code=1, stdout="unknown command\n")

    monkeypatch.setattr("serena.config.client_setup.execute_shell_command", fake_execute_shell_command)

    assert ClientSetupHandlerGrok().is_applicable() is False


@pytest.mark.parametrize(
    ("return_code", "stdout"),
    [
        (1, ""),
        (0, "0.0.34\n"),
        (0, "my-grok wrapper 1.0\n"),
        (0, "grok\n"),
        (0, "  grok 0.2.82"),
    ],
)
def test_grok_setup_handler_short_circuits_when_version_probe_does_not_match(monkeypatch, return_code: int, stdout: str):
    commands: list[str] = []

    def fake_execute_shell_command(command: str, capture_stderr: bool = False) -> ShellCommandResult:
        commands.append(command)
        if command == "grok --version":
            return _result(command, return_code=return_code, stdout=stdout)
        return _result(command, stdout="Add or update an MCP server\n")

    monkeypatch.setattr("serena.config.client_setup.execute_shell_command", fake_execute_shell_command)

    assert ClientSetupHandlerGrok().is_applicable() is False
    assert commands == ["grok --version"]


def test_grok_setup_handler_accepts_case_insensitive_grok_version(monkeypatch):
    commands: list[str] = []

    def fake_execute_shell_command(command: str, capture_stderr: bool = False) -> ShellCommandResult:
        commands.append(command)
        if command == "grok --version":
            return _result(command, stdout="Grok 0.3.0 [stable]")
        if command == "grok mcp add --help":
            return _result(command, stdout="Add or update an MCP server\n")
        return _result(command, return_code=1)

    monkeypatch.setattr("serena.config.client_setup.execute_shell_command", fake_execute_shell_command)

    assert ClientSetupHandlerGrok().is_applicable() is True
    assert commands == ["grok --version", "grok mcp add --help"]


@pytest.mark.parametrize("help_stdout", ["Usage: grok mcp add\n", "add or update an mcp server\n"])
def test_grok_setup_handler_rejects_mcp_add_help_without_expected_text(monkeypatch, help_stdout: str):
    commands: list[str] = []

    def fake_execute_shell_command(command: str, capture_stderr: bool = False) -> ShellCommandResult:
        commands.append(command)
        if command == "grok --version":
            return _result(command, stdout="grok 0.2.82 (6d0b07d2de) [stable]\n")
        if command == "grok mcp add --help":
            return _result(command, stdout=help_stdout)
        return _result(command, return_code=1)

    monkeypatch.setattr("serena.config.client_setup.execute_shell_command", fake_execute_shell_command)

    assert ClientSetupHandlerGrok().is_applicable() is False
    assert commands == ["grok --version", "grok mcp add --help"]


def test_grok_setup_handler_apply_uses_grok_mcp_add(monkeypatch):
    commands: list[str] = []

    def fake_run_shell_command(self: ClientSetupHandlerGrok, command: str) -> bool:
        commands.append(command)
        return True

    monkeypatch.setattr(ClientSetupHandlerGrok, "_run_shell_command", fake_run_shell_command)

    assert ClientSetupHandlerGrok().apply() is True
    assert commands == ["grok mcp add --scope user serena -- serena start-mcp-server --context=grok --project-from-cwd"]


def test_grok_setup_handler_apply_failure_skips_hook_recommendation(monkeypatch, capsys):
    def fake_run_shell_command(self: ClientSetupHandlerGrok, command: str) -> bool:
        return False

    monkeypatch.setattr(ClientSetupHandlerGrok, "_run_shell_command", fake_run_shell_command)

    assert ClientSetupHandlerGrok().apply() is False
    assert "recommend" not in capsys.readouterr().out.lower()


def test_client_setup_handlers_use_resolvable_contexts():
    handler_names = [handler.name for handler in client_setup_handlers]
    assert "grok" in handler_names
    assert "trae" in handler_names

    for handler in client_setup_handlers:
        context_options = [option for option in handler.get_mcp_server_options() if option.startswith("--context=")]
        assert len(context_options) == 1
        context_name = context_options[0].removeprefix("--context=")
        assert SerenaAgentContext.from_name(context_name).name == context_name


def test_trae_setup_handler_is_applicable_when_trae_dir_exists(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".trae").mkdir()

    def fake_execute_shell_command(command: str, capture_stderr: bool = False) -> ShellCommandResult:
        return _result(command, return_code=1)

    monkeypatch.setattr("serena.config.client_setup.execute_shell_command", fake_execute_shell_command)

    assert ClientSetupHandlerTrae().is_applicable() is True


def test_trae_setup_handler_is_applicable_via_traecli(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    def fake_execute_shell_command(command: str, capture_stderr: bool = False) -> ShellCommandResult:
        if command == "traecli --version":
            return _result(command, stdout="traecli 0.120.42\n")
        return _result(command, return_code=1)

    monkeypatch.setattr("serena.config.client_setup.execute_shell_command", fake_execute_shell_command)

    assert ClientSetupHandlerTrae().is_applicable() is True


def test_trae_setup_handler_not_applicable(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    def fake_execute_shell_command(command: str, capture_stderr: bool = False) -> ShellCommandResult:
        return _result(command, return_code=1)

    monkeypatch.setattr("serena.config.client_setup.execute_shell_command", fake_execute_shell_command)

    assert ClientSetupHandlerTrae().is_applicable() is False


def test_trae_setup_handler_apply_writes_mcp_json(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert ClientSetupHandlerTrae().apply() is True

    mcp_json = tmp_path / ".trae" / "mcp.json"
    assert mcp_json.exists()
    config = json.loads(mcp_json.read_text(encoding="utf-8"))
    assert config["mcpServers"]["serena"] == {
        "command": "serena",
        "args": ["start-mcp-server", "--context=trae", "--project=${workspaceFolder}"],
    }


def test_trae_setup_handler_apply_merges_existing_mcp_json(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    trae_dir = tmp_path / ".trae"
    trae_dir.mkdir()
    (trae_dir / "mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "other"}}}), encoding="utf-8")

    assert ClientSetupHandlerTrae().apply() is True

    config = json.loads((trae_dir / "mcp.json").read_text(encoding="utf-8"))
    # existing entry preserved
    assert config["mcpServers"]["other"] == {"command": "other"}
    # serena entry added
    assert config["mcpServers"]["serena"]["command"] == "serena"


def test_trae_setup_handler_apply_fails_on_invalid_existing_json(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    trae_dir = tmp_path / ".trae"
    trae_dir.mkdir()
    (trae_dir / "mcp.json").write_text("{not valid json", encoding="utf-8")

    assert ClientSetupHandlerTrae().apply() is False
    # existing file left untouched
    assert (trae_dir / "mcp.json").read_text(encoding="utf-8") == "{not valid json"
    assert "could not parse" in capsys.readouterr().out.lower()
