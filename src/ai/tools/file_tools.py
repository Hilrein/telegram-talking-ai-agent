"""File system tools for the autonomous agent.

Provides safe, sandboxed operations for searching files on the host PC.
All functions are designed to be called by the LLM via function-calling and
must return JSON-serialisable results.
"""

import fnmatch
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Directories that should never be traversed — they are either enormous,
# irrelevant, or may cause hangs when enumerated.
IGNORED_DIRS: frozenset[str] = frozenset({
    "node_modules",
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    ".env",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".next",
    ".nuxt",
    "site-packages",
    "$Recycle.Bin",
    "System Volume Information",
    "Windows",
    "ProgramData",
})

MAX_RESULTS = 20


def search_files_on_pc(
    filename_pattern: str,
    search_path: str = ".",
) -> list[str]:
    """Search for files matching *filename_pattern* under *search_path*.

    The pattern uses shell-style wildcards (``fnmatch``):
      - ``*``  matches everything
      - ``?``  matches any single character
      - ``[seq]`` matches any character in *seq*

    Examples:
      - ``"*.txt"``     → all ``.txt`` files
      - ``"backup*"``   → files whose name starts with "backup"
      - ``"report"``    → files named exactly "report"

    Parameters
    ----------
    filename_pattern:
        Shell-style glob pattern applied to each file **name** (not path).
    search_path:
        Root directory to start the recursive search.  Defaults to the
        current working directory.

    Returns
    -------
    list[str]
        Up to ``MAX_RESULTS`` absolute paths of matching files.
    """
    root = Path(search_path).resolve()
    results: list[str] = []

    if not root.is_dir():
        logger.warning("search_files_on_pc: '%s' is not a directory", root)
        return [f"Error: '{search_path}' is not a valid directory."]

    try:
        _walk_and_match(root, filename_pattern, results)
    except Exception as exc:
        logger.error("search_files_on_pc unexpected error: %s", exc)
        results.append(f"Error during search: {exc}")

    return results


def _walk_and_match(
    root: Path,
    pattern: str,
    results: list[str],
) -> None:
    """Recursively walk *root*, collecting files whose name matches *pattern*.

    Skips ignored directories and gracefully handles permission errors.
    Stops early once ``MAX_RESULTS`` matches have been collected.
    """
    try:
        entries = sorted(root.iterdir())
    except PermissionError:
        logger.debug("Permission denied: %s", root)
        return
    except OSError as exc:
        logger.debug("OS error reading %s: %s", root, exc)
        return

    for entry in entries:
        if len(results) >= MAX_RESULTS:
            return

        try:
            if entry.is_dir():
                if entry.name in IGNORED_DIRS:
                    continue
                _walk_and_match(entry, pattern, results)

            elif entry.is_file():
                if fnmatch.fnmatch(entry.name, pattern):
                    results.append(str(entry))

        except PermissionError:
            logger.debug("Permission denied: %s", entry)
        except OSError as exc:
            logger.debug("OS error accessing %s: %s", entry, exc)


def read_file(filepath: str) -> str:
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            if len(content) > 10000:
                return content[:10000] + '... [TRUNCATED]'
            return content
    except Exception as e:
        return f'Error reading file: {e}'

def list_directory(dir_path: str = '.') -> list[str]:
    try:
        root = Path(dir_path).resolve()
        if not root.is_dir():
            return [f'Error: {dir_path} is not a directory']
        entries = []
        for entry in sorted(root.iterdir()):
            if entry.name not in IGNORED_DIRS:
                entries.append(f'[{str("DIR") if entry.is_dir() else str("FILE")}] {entry.name}')
        return entries
    except Exception as e:
        return [f'Error listing directory: {e}']
