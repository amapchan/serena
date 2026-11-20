"""Tests for serena.util.client_workspace"""

from __future__ import annotations

from pathlib import Path

import pytest

from serena.util.client_workspace import extract_workspace_path_from_commandline


@pytest.fixture
def workspace_dir(tmp_path: Path) -> Path:
    """An existing empty directory simulating a client workspace."""
    ws = tmp_path / "my-repo"
    ws.mkdir()
    return ws


class TestExtractWorkspaceFromPositionalArgument:
    def test_absolute_existing_dir(self, workspace_dir: Path) -> None:
        cmdline = ["Trae.exe", str(workspace_dir)]
        assert extract_workspace_path_from_commandline(cmdline) == str(workspace_dir)

    def test_no_arguments(self) -> None:
        cmdline = ["Trae.exe"]
        assert extract_workspace_path_from_commandline(cmdline) is None

    def test_flag_only_arguments(self) -> None:
        cmdline = ["Trae.exe", "--some-flag", "--other=value"]
        assert extract_workspace_path_from_commandline(cmdline) is None

    def test_non_existing_dir(self, tmp_path: Path) -> None:
        cmdline = ["Trae.exe", str(tmp_path / "does-not-exist")]
        assert extract_workspace_path_from_commandline(cmdline) is None

    def test_file_argument_is_ignored(self, tmp_path: Path) -> None:
        file_path = tmp_path / "notes.txt"
        file_path.write_text("x")
        cmdline = ["Trae.exe", str(file_path)]
        assert extract_workspace_path_from_commandline(cmdline) is None

    def test_relative_path_is_ignored(self, workspace_dir: Path) -> None:
        # the client's cwd is unknown to us, so relative paths must not be resolved
        cmdline = ["Trae.exe", workspace_dir.name]
        assert extract_workspace_path_from_commandline(cmdline) is None

    def test_first_existing_dir_wins(self, tmp_path: Path, workspace_dir: Path) -> None:
        other = tmp_path / "other-repo"
        other.mkdir()
        cmdline = ["Trae.exe", "--foo", str(workspace_dir), str(other)]
        assert extract_workspace_path_from_commandline(cmdline) == str(workspace_dir)


class TestExtractWorkspaceFromOpenUrl:
    def test_trae_file_url(self, workspace_dir: Path) -> None:
        url = "trae://file/" + workspace_dir.as_posix()
        cmdline = ["Trae.exe", "--open-url", "--", url]
        assert extract_workspace_path_from_commandline(cmdline) == str(workspace_dir)

    def test_url_encoded_path(self, workspace_dir: Path) -> None:
        # colons must be percent-encoded within the URL path ("h:/my-repo" -> "h%3A/my-repo")
        url = "trae://file/" + workspace_dir.as_posix().replace(":", "%3A")
        cmdline = ["Trae.exe", "--open-url", "--", url]
        assert extract_workspace_path_from_commandline(cmdline) == str(workspace_dir)

    def test_no_url_after_flag(self) -> None:
        cmdline = ["Trae.exe", "--open-url", "--"]
        assert extract_workspace_path_from_commandline(cmdline) is None

    def test_unknown_scheme_is_rejected(self, workspace_dir: Path) -> None:
        url = "malware://file/" + workspace_dir.as_posix()
        cmdline = ["Trae.exe", "--open-url", "--", url]
        assert extract_workspace_path_from_commandline(cmdline) is None

    def test_url_to_non_existing_dir(self, tmp_path: Path) -> None:
        url = "vscode://file/" + (tmp_path / "missing").as_posix()
        cmdline = ["Trae.exe", "--open-url", "--", url]
        assert extract_workspace_path_from_commandline(cmdline) is None
