# SPDX-License-Identifier: GPL-3.0-or-later

import json
from abc import ABC, abstractmethod
from pathlib import Path

import click

from serena.util.shell import execute_shell_command


class ClientSetupHandler(ABC):
    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    def is_applicable(self) -> bool:
        """
        :return: whether the client setup can applied (respective client is available)
        """

    @abstractmethod
    def get_mcp_server_options(self) -> list[str]:
        pass

    def get_mcp_server_command(self) -> str:
        return f"serena start-mcp-server {' '.join(self.get_mcp_server_options())}"

    def _run_shell_command(self, cmd: str) -> bool:
        """
        Runs the given shell command.
        If the command fails (i.e., with non-zero exit code), prints the stdout and stderr of the command for debugging.

        :param cmd: the shell command to execute
        :return: whether the command executed successfully (i.e., with exit code 0)
        """
        click.echo("Running command:")
        click.echo(cmd)
        result = execute_shell_command(cmd)
        is_success = result.return_code == 0
        if not is_success:
            if result.stdout:
                click.echo(result.stdout)
            if result.stderr:
                click.echo(result.stderr)
        return is_success

    @abstractmethod
    def apply(self) -> bool:
        """
        Applies the client setup
        """


class ClientSetupHandlerClaudeCode(ClientSetupHandler):
    def __init__(self) -> None:
        super().__init__("claude-code")

    def is_applicable(self) -> bool:
        result = execute_shell_command("claude --version", capture_stderr=True)
        return result.return_code == 0 and "Claude" in result.stdout

    def get_mcp_server_options(self) -> list[str]:
        return ["--context=claude-code", "--project-from-cwd"]

    def apply(self) -> bool:
        cmd = f"claude mcp add --scope user serena -- {self.get_mcp_server_command()}"
        is_success = self._run_shell_command(cmd)
        if is_success:
            click.echo("\nIMPORTANT: We additionally recommend to set up hooks for Claude Code to ensure the best experience.")
            click.echo("   Please read the instructions here:")
            click.echo("   https://oraios.github.io/serena/02-usage/030_clients.html#claude-code")
        return is_success


class ClientSetupHandlerCodex(ClientSetupHandler):
    """
    Setup for Codex CLI and Codex App (shared config)
    """

    def __init__(self) -> None:
        super().__init__("codex")

    def is_applicable(self) -> bool:
        result = execute_shell_command("codex --version", capture_stderr=True)
        return result.return_code == 0 and "codex-cli" in result.stdout

    def get_mcp_server_options(self) -> list[str]:
        return ["--context=codex", "--project-from-cwd"]

    def apply(self) -> bool:
        return self._run_shell_command(f"codex mcp add serena -- {self.get_mcp_server_command()}")


class ClientSetupHandlerCodeBuddy(ClientSetupHandler):
    def __init__(self) -> None:
        super().__init__("codebuddy")

    def is_applicable(self) -> bool:
        result = execute_shell_command("codebuddy --version", capture_stderr=True)
        return result.return_code == 0

    def get_mcp_server_options(self) -> list[str]:
        return ["--context=codebuddy", "--project-from-cwd"]

    def apply(self) -> bool:
        cmd = f"codebuddy mcp add --scope user serena -- {self.get_mcp_server_command()}"
        is_success = self._run_shell_command(cmd)
        if is_success:
            click.echo("\nIMPORTANT: We additionally recommend to set up hooks for CodeBuddy to ensure the best experience.")
            click.echo("   Please read the instructions here:")
            click.echo("   https://oraios.github.io/serena/02-usage/030_clients.html#codebuddy")
        return is_success


class ClientSetupHandlerGrok(ClientSetupHandler):
    """
    Setup for xAI Grok Build.
    """

    def __init__(self) -> None:
        super().__init__("grok")

    def is_applicable(self) -> bool:
        version_result = execute_shell_command("grok --version", capture_stderr=True)
        if version_result.return_code != 0 or not version_result.stdout.lower().startswith("grok "):
            return False

        mcp_result = execute_shell_command("grok mcp add --help", capture_stderr=True)
        return mcp_result.return_code == 0 and "Add or update an MCP server" in mcp_result.stdout

    def get_mcp_server_options(self) -> list[str]:
        return ["--context=grok", "--project-from-cwd"]

    def apply(self) -> bool:
        cmd = f"grok mcp add --scope user serena -- {self.get_mcp_server_command()}"
        is_success = self._run_shell_command(cmd)
        if is_success:
            click.echo("\nIMPORTANT: We additionally recommend to set up hooks for Grok to ensure the best experience.")
            click.echo("   Please read the instructions here:")
            click.echo("   https://oraios.github.io/serena/02-usage/030_clients.html#grok")
        return is_success


class ClientSetupHandlerTrae(ClientSetupHandler):
    """
    Setup for Trae IDE (international) and Trae CN.

    Trae has no CLI command to register an MCP server; it reads project-level MCP
    configuration from ``<project>/.trae/mcp.json`` (after enabling "Project-level MCP"
    in Settings > MCP). Both the Trae IDE and the Trae CLI consume this file, so we
    write/merge the Serena entry there.
    """

    def __init__(self) -> None:
        super().__init__("trae")

    def is_applicable(self) -> bool:
        """
        :return: always ``True`` — the setup only writes project-level configuration files (creating
            them as needed) and does not depend on any Trae executable being available
        """
        return True

    def get_mcp_server_options(self) -> list[str]:
        # Trae resolves ${workspaceFolder} to the project root inside mcp.json args.
        return ["--context=trae", "--project=${workspaceFolder}"]

    def apply(self) -> bool:
        trae_dir = Path(".trae")
        try:
            trae_dir.mkdir(exist_ok=True)
        except OSError as e:
            click.echo(f"Error: could not create {trae_dir.resolve()}: {e}")
            return False

        mcp_json_path = trae_dir / "mcp.json"

        config: dict = {}
        if mcp_json_path.exists():
            try:
                config = json.loads(mcp_json_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                click.echo(f"Error: could not parse existing {mcp_json_path.resolve()}: {e}")
                return False
            if not isinstance(config, dict):
                click.echo(f"Error: {mcp_json_path.resolve()} does not contain a JSON object.")
                return False

        mcp_servers = config.get("mcpServers")
        if mcp_servers is None:
            mcp_servers = {}
        if not isinstance(mcp_servers, dict):
            mcp_servers = {}

        mcp_servers["serena"] = {
            "command": "serena",
            "args": ["start-mcp-server", *self.get_mcp_server_options()],
        }
        config["mcpServers"] = mcp_servers

        try:
            mcp_json_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except OSError as e:
            click.echo(f"Error: could not write {mcp_json_path.resolve()}: {e}")
            return False

        click.echo(f"Updated {mcp_json_path.resolve()} with the Serena MCP server configuration.")
        click.echo("\nIMPORTANT: In Trae, enable 'Project-level MCP' in Settings > MCP for this configuration to take effect.")
        click.echo("   Note: on Windows the `command` field cannot contain spaces, so `serena` must be on PATH.")
        click.echo("   We additionally recommend setting up hooks for Trae. Please read:")
        click.echo("   https://oraios.github.io/serena/02-usage/030_clients.html#trae")
        return True


client_setup_handlers = [
    ClientSetupHandlerClaudeCode(),
    ClientSetupHandlerCodeBuddy(),
    ClientSetupHandlerCodex(),
    ClientSetupHandlerGrok(),
    ClientSetupHandlerTrae(),
]
