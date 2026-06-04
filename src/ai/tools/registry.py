"""Tool registry — the single source of truth for all agent tools.

Responsibilities:
  1. ``TOOL_DEFINITIONS`` — list of JSON-schema dicts ready to be passed
     straight into the ``tools`` parameter of the OpenAI-compatible API.
  2. ``TOOL_FUNCTIONS`` — a name → callable mapping so the backend can
     dispatch a ``tool_calls`` response without reflection hacks.
  3. ``execute_tool`` — a thin helper that looks up the function, calls it
     with the parsed arguments, and returns the serialised result.

Adding a new tool:
  1. Implement the function in a dedicated ``*_tools.py`` module.
  2. Add its JSON schema to ``TOOL_DEFINITIONS``.
  3. Register the callable in ``TOOL_FUNCTIONS``.
"""

import json
import logging
from typing import Any, Callable

from .file_tools import search_files_on_pc, read_file, list_directory
from .git_tools import git_manager
from .postgres_tools import backup_postgresql
from .web_tools import web_search
from .terminal_tools import run_terminal_command

logger = logging.getLogger(__name__)


# ── JSON schemas (OpenAI function-calling format) ───────────────────

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    # ── File search ──────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "search_files_on_pc",
            "description": (
                "Search for files on the host PC by a filename pattern. "
                "Uses shell-style wildcards (*, ?, [seq]). "
                "Returns up to 20 absolute file paths matching the pattern."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename_pattern": {
                        "type": "string",
                        "description": (
                            "Shell-style glob pattern to match against file names. "
                            'Examples: "*.txt", "backup*", "report.pdf".'
                        ),
                    },
                    "search_path": {
                        "type": "string",
                        "description": (
                            "Absolute or relative path to the root directory "
                            "where the search starts. Defaults to the current "
                            'working directory (".").'
                        ),
                        "default": ".",
                    },
                },
                "required": ["filename_pattern"],
                "additionalProperties": False,
            },
        },
    },

    # ── Web search ───────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the internet using DuckDuckGo and return a list of "
                "results. Each result contains a title, a short text snippet, "
                "and the URL. Returns 3–5 results."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "The search query string. "
                            'Example: "best practices for PostgreSQL backups".'
                        ),
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },

    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Absolute or relative path to the file to read.",
                    },
                },
                "required": ["filepath"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List the contents of a directory (files and folders).",
            "parameters": {
                "type": "object",
                "properties": {
                    "dir_path": {
                        "type": "string",
                        "description": "Absolute or relative path to the directory to list. Defaults to current directory.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_terminal_command",
            "description": "Execute a shell/terminal command on the host OS. Useful for running scripts, installing dependencies, or performing system administration tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute.",
                    },
                },
                "required": ["command"],
            },
        },
    },

    # ── Git manager ──────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "git_manager",
            "description": (
                "Manage a local Git repository. Supports four actions: "
                '"status" (show changed/staged/untracked files), '
                '"commit" (stage all changes and commit with a message), '
                '"pull" (fetch and merge from remote), and '
                '"push" (push local commits to the remote).'
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["status", "commit", "pull", "push"],
                        "description": "The Git operation to perform.",
                    },
                    "commit_message": {
                        "type": "string",
                        "description": (
                            "Commit message text. Required when action is "
                            '"commit", ignored otherwise.'
                        ),
                    },
                    "repo_path": {
                        "type": "string",
                        "description": (
                            "Path to the Git repository. Defaults to the "
                            "project root directory."
                        ),
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        },
    },

    # ── PostgreSQL backup ────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "backup_postgresql",
            "description": (
                "Create a full SQL backup of a PostgreSQL database using "
                "pg_dump. The backup file is saved to the agent data "
                "directory with a timestamped filename. Returns the file "
                "path and size on success, or an error description."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "db_uri": {
                        "type": "string",
                        "description": (
                            "PostgreSQL connection URI, e.g. "
                            '"postgresql://user:pass@host:5432/dbname". '
                            "If omitted, the POSTGRES_DATABASE_URL environment "
                            "variable is used."
                        ),
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
]


# ── Name → callable mapping ─────────────────────────────────────────

TOOL_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "search_files_on_pc": search_files_on_pc,
    "read_file": read_file,
    "list_directory": list_directory,
    "run_terminal_command": run_terminal_command,
    "web_search": web_search,
    "git_manager": git_manager,
    "backup_postgresql": backup_postgresql,
}


# ── Dispatcher ──────────────────────────────────────────────────────

def execute_tool(name: str, arguments: dict[str, Any]) -> str:
    """Look up *name* in the registry, call it with *arguments*, return JSON.

    Returns a JSON string so the result can be passed straight back to the
    model as a ``tool`` message content.

    If the tool is unknown or raises, an error payload is returned instead
    of propagating the exception — the LLM should be able to recover
    gracefully.
    """
    func = TOOL_FUNCTIONS.get(name)
    if func is None:
        logger.error("Unknown tool requested: %s", name)
        return json.dumps({"error": f"Unknown tool: {name}"})

    try:
        result = func(**arguments)
        return json.dumps({"result": result}, ensure_ascii=False)
    except Exception as exc:
        logger.error("Tool '%s' raised an error: %s", name, exc, exc_info=True)
        return json.dumps({"error": f"Tool execution failed: {exc}"})
