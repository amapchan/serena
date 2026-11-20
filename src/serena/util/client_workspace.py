"""
Resolution of the workspace opened by the GUI client that launched this Serena instance.

Motivation: GUI clients like Trae spawn the Serena MCP server as a subprocess whose working
directory is unrelated to the opened workspace (typically the user's home directory).
However, when the client is launched with a workspace, the workspace path appears in the
command line of the client's **main process** (e.g. ``Trae.exe h:\\MyRepo``, a shortcut
argument, drag-and-drop onto the executable, or ``trae h:\\MyRepo`` via the installed CLI).
This module walks up the process tree, resolves the client's main process and extracts the
workspace path from its command line, such that Serena can auto-activate the corresponding
project (see the ``--project-from-client`` flag of ``serena start-mcp-server``).

Process tree assumptions (verified for VSCode-based clients like Trae): helper processes
(extension host, GPU, crashpad, ...) are spawned by the main process and their command lines
contain a ``--type=`` marker, whereas the main process command line does not. Serena itself is
typically spawned via a chain of startup wrappers (e.g. the ``serena.exe`` trampoline installed
by uv, intermediate interpreter processes, and ``multiprocessing.spawn`` children), none of
which carry a ``--type=`` marker. Therefore, both serena's own startup wrapper layers and the
client's helper processes are skipped during traversal, so that the first remaining ancestor
process is the client's main process.

If the workspace cannot be resolved (e.g. the client was started without a workspace argument),
``None`` is returned and the caller is expected to degrade gracefully (no project activated).

Note that resolving the workspace from the main-process command line only works when the client
was launched with the workspace as an argument (CLI, shortcut, protocol URL). When the user opens
a folder from within the GUI of an already-running client, the command line carries no workspace.
For such cases (VSCode-like clients), this module first attempts to resolve the workspace through
the per-workspace identifier which the extension host injects into the spawned process environment
(the ``VSCODE_ENV_*`` variables contain a ``workspaces/<hash-prefix>/`` path segment): the prefix
identifies the client's workspace storage directory
(``<user-data-dir>/User/workspaceStorage/<hash>/workspace.json``), whose ``folder`` entry holds the
workspace URI.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

import psutil
from sensai.util import logging

log = logging.getLogger(__name__)

_OPEN_URL_FLAG = "--open-url"
# VSCode-like clients register a URL protocol of the form "<scheme>://file/<path>"
_URL_SCHEMES = ("trae", "trae-cn", "vscode", "cursor", "windsurf", "code")
# markers identifying ancestor processes that belong to serena's own startup chain and must
# therefore be skipped when searching for the client's main process. Note that no marker
# contains the bare string "serena", since user paths may legitimately contain it.
_SERENA_STARTUP_MARKERS = ("multiprocessing.spawn", "start-mcp-server", "serena.exe", "serena-agent")
# environment variables via which VSCode-like clients inject per-process environment adjustments
# into spawned processes; these may carry a per-workspace path segment (see _WORKSPACE_HASH_PATTERN)
_CLIENT_ENV_VARIABLES = ("VSCODE_ENV_PREPEND", "VSCODE_ENV_APPEND", "VSCODE_ENV_REPLACE")
# a per-workspace identifier appearing in a path segment ``workspaces\<hash>\``; the hash is a
# (case-insensitive) prefix of the corresponding workspace storage directory name. Note that the
# client may escape backslashes, i.e. the path separators can appear doubled in the env value.
_WORKSPACE_HASH_PATTERN = re.compile(r"workspaces[\\/]+([0-9a-fA-F]{8,32})[\\/]+")
# maximum depth for process tree traversals
_MAX_PROCESS_TREE_DEPTH = 10
# fallback pattern for extracting the user data directory from the joined command line; only
# matches quoted values, since unquoted values containing spaces are merged into a single token
# by the process command line tokenization (handled by the token-based extraction below)
_USER_DATA_DIR_QUOTED_PATTERN = re.compile(r'--user-data-dir="([^"]+)"')


def _is_serena_startup_commandline(joined_cmdline: str) -> bool:
    """Whether the given (joined, lower-cased) command line belongs to serena's own startup chain."""
    return any(marker in joined_cmdline for marker in _SERENA_STARTUP_MARKERS)


def extract_workspace_path_from_commandline(cmdline: list[str]) -> str | None:
    """
    Extracts the workspace path from the command line of a GUI client's main process.

    Supported forms:
      * positional argument pointing to an existing absolute directory, e.g.
        ``["Trae.exe", "h:\\MyRepo"]`` (CLI launch, shortcut argument, drag-and-drop);
      * ``--open-url`` protocol form, e.g.
        ``["Trae.exe", "--open-url", "--", "trae://file/h%3A/MyRepo"]``.

    :param cmdline: the process command line as returned by :meth:`psutil.Process.cmdline`
    :return: absolute workspace directory path, or None if no suitable path is found
    """
    args = cmdline[1:]
    if not args:
        return None

    # protocol form: everything after --open-url (skipping any "--" separator) is expected to
    # contain a client URL whose path denotes the workspace
    if _OPEN_URL_FLAG in args:
        idx = args.index(_OPEN_URL_FLAG)
        for arg in args[idx + 1 :]:
            if arg == "--":
                continue
            if "://" in arg:
                return _workspace_from_url(arg)
        return None

    # positional form: the first existing absolute directory argument wins
    for arg in args:
        if arg.startswith("-"):
            continue
        candidate = Path(arg)
        if candidate.is_absolute() and candidate.is_dir():
            return str(candidate)
    return None


def _workspace_from_url(url: str) -> str | None:
    """
    Extracts the workspace directory from a client URL of the form ``<scheme>://file/<path>``.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() not in _URL_SCHEMES:
        return None
    if parsed.netloc.lower() != "file":
        return None
    # url2pathname performs the platform-specific conversion (e.g. "/c:/repo" -> "c:\repo" on Windows)
    path = urllib.request.url2pathname(urllib.parse.unquote(parsed.path))
    candidate = Path(path)
    return str(candidate) if candidate.is_absolute() and candidate.is_dir() else None


def _user_data_dir_from_ancestors() -> str | None:
    """
    Extracts the client's user data directory from the ``--user-data-dir`` argument of the
    command line of one of the ancestor processes (the client's extension host carries this
    argument on VSCode-like clients).

    :return: the user data directory path, or None if no ancestor provides it
    """
    try:
        proc: psutil.Process | None = psutil.Process()
        for _ in range(_MAX_PROCESS_TREE_DEPTH):
            if proc is None:
                break
            parent = proc.parent()
            if parent is None:
                break
            proc = parent
            try:
                cmdline = parent.cmdline()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            if not cmdline:
                continue
            # token-based extraction: command line tokenization merges quoted argument values
            # (e.g. --user-data-dir="...") into single tokens, so the value may contain spaces
            for i, arg in enumerate(cmdline):
                if arg == "--user-data-dir" and i + 1 < len(cmdline):
                    return cmdline[i + 1].strip('"')
                if arg.startswith("--user-data-dir="):
                    return arg.split("=", 1)[1].strip('"')
            # joined fallback for tokenizations that split quoted values at spaces
            m = _USER_DATA_DIR_QUOTED_PATTERN.search(" ".join(cmdline))
            if m:
                return m.group(1)
        log.debug("No ancestor process provides --user-data-dir")
        return None
    except Exception:
        log.debug("Failed to determine user data dir from the process tree", exc_info=True)
        return None


def _workspace_from_workspace_storage(user_data_dir: str, hash_prefix: str) -> str | None:
    """
    Resolves the workspace directory via the client's workspace storage: finds the storage
    directory whose name starts with the given hash prefix and reads the workspace URI from its
    ``workspace.json`` file.

    :param user_data_dir: the client's user data directory
    :param hash_prefix: the (case-insensitive) hash prefix identifying the workspace storage directory
    :return: absolute workspace directory path, or None if it cannot be resolved
    """
    storage_root = Path(user_data_dir) / "User" / "workspaceStorage"
    if not storage_root.is_dir():
        log.debug("Workspace storage root not found: %s", storage_root)
        return None
    prefix_lower = hash_prefix.lower()
    matching_dirs = [d for d in storage_root.iterdir() if d.name.lower().startswith(prefix_lower)]
    for storage_dir in matching_dirs:
        workspace_file = storage_dir / "workspace.json"
        if not workspace_file.is_file():
            continue
        try:
            data = json.loads(workspace_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.debug("Failed to read %s", workspace_file, exc_info=True)
            continue
        uri = data.get("folder") if isinstance(data.get("folder"), str) else data.get("folderUri")
        if not isinstance(uri, str) or "://" not in uri:
            continue
        parsed = urllib.parse.urlparse(uri)
        # url2pathname performs the platform-specific conversion (e.g. "/h:/repo" -> "h:\repo" on Windows)
        path = urllib.request.url2pathname(urllib.parse.unquote(parsed.path))
        candidate = Path(path)
        if candidate.is_absolute() and candidate.is_dir():
            log.debug("Resolved workspace %s from workspace storage %s", candidate, storage_dir.name)
            return str(candidate)
    return None


def _find_client_workspace_from_env() -> str | None:
    """
    Resolves the workspace of a VSCode-like client that opened the folder from within its GUI
    (in which case no process command line carries the workspace): the client's extension host
    injects per-workspace environment adjustments (``VSCODE_ENV_*`` variables) into spawned
    processes, which contain a ``workspaces/<hash>/`` path segment identifying the client's
    workspace storage directory (see module docstring).

    :return: absolute workspace directory path, or None if the environment carries no
             resolvable workspace identifier
    """
    env = os.environ
    values = [env.get(var, "") for var in _CLIENT_ENV_VARIABLES] + list(env.values())
    hash_prefixes: list[str] = []
    for value in values:
        if value:
            hash_prefixes.extend(_WORKSPACE_HASH_PATTERN.findall(value))
    if not hash_prefixes:
        return None
    user_data_dir = _user_data_dir_from_ancestors()
    if user_data_dir is None:
        return None
    log.debug("Found workspace hash(es) %s in client environment; user data dir: %s", hash_prefixes, user_data_dir)
    for hash_prefix in hash_prefixes:
        workspace = _workspace_from_workspace_storage(user_data_dir, hash_prefix)
        if workspace is not None:
            return workspace
    return None


def find_client_workspace_path(max_depth: int = 10) -> str | None:
    """
    Resolves the workspace of the GUI client that launched the current process.

    The workspace is resolved by first inspecting the environment for a per-workspace identifier
    injected by VSCode-like clients (see :meth:`_find_client_workspace_from_env`; required when the
    client opened the folder from within its GUI). If that fails, the process tree is walked up to
    the client's main process and the workspace path is extracted from its command line
    (see module docstring), which covers clients launched with the workspace as an argument.

    :param max_depth: maximum number of parent processes to inspect
    :return: absolute workspace directory path, or None if no client main process with a
             workspace argument was found
    """
    workspace = _find_client_workspace_from_env()
    if workspace is not None:
        return workspace
    try:
        proc: psutil.Process | None = psutil.Process()
        for _ in range(max_depth):
            if proc is None:
                break
            parent = proc.parent()
            if parent is None:
                break
            try:
                cmdline = parent.cmdline()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                log.debug("Cannot read command line of process %s; stopping process tree traversal", parent.pid)
                break
            if not cmdline:
                break
            joined_cmdline = " ".join(cmdline).lower()
            # skip serena's own startup wrapper chain (trampoline, interpreter layers, multiprocessing spawn)
            if _is_serena_startup_commandline(joined_cmdline):
                proc = parent
                continue
            # helper processes spawned by a VSCode-like client carry a "--type=" marker;
            # the first remaining ancestor without this marker is the main process
            if "--type=" not in joined_cmdline:
                log.debug("Client main process: pid=%s, name=%s", parent.pid, parent.name())
                return extract_workspace_path_from_commandline(cmdline)
            proc = parent
        log.debug("No client main process found within %d levels of the process tree", max_depth)
        return None
    except Exception:
        log.warning("Failed to resolve client workspace from the process tree", exc_info=True)
        return None
