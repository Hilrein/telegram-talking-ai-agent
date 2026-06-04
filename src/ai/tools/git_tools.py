"""Git repository management tool for the autonomous agent.

Wraps ``gitpython`` to expose common Git operations (status, commit, pull,
push) through a single ``git_manager`` function that the LLM can call.

The working directory defaults to the project root (two levels up from this
file: ``src/ai/tools/`` → project root).  It can be overridden via the
``repo_path`` argument.
"""

import logging
from pathlib import Path
from typing import Optional

import git  # gitpython

logger = logging.getLogger(__name__)

# Sensible default: project root
_DEFAULT_REPO_PATH = str(Path(__file__).resolve().parents[3])

SUPPORTED_ACTIONS = ("status", "commit", "pull", "push")


def git_manager(
    action: str,
    commit_message: Optional[str] = None,
    repo_path: Optional[str] = None,
) -> str:
    """Execute a Git action on the repository.

    Parameters
    ----------
    action:
        One of ``"status"``, ``"commit"``, ``"pull"``, ``"push"``.
    commit_message:
        Required when *action* is ``"commit"``.  Ignored otherwise.
    repo_path:
        Path to the Git repository.  Defaults to the project root.

    Returns
    -------
    str
        A human-readable report of the result (or error).
    """
    action = action.strip().lower()
    if action not in SUPPORTED_ACTIONS:
        return f"Error: unknown action '{action}'. Supported: {', '.join(SUPPORTED_ACTIONS)}."

    path = repo_path or _DEFAULT_REPO_PATH

    try:
        repo = git.Repo(path)
    except git.InvalidGitRepositoryError:
        return f"Error: '{path}' is not a valid Git repository."
    except git.NoSuchPathError:
        return f"Error: path '{path}' does not exist."
    except Exception as exc:
        return f"Error opening repository: {exc}"

    try:
        if action == "status":
            return _git_status(repo)
        if action == "commit":
            return _git_commit(repo, commit_message)
        if action == "pull":
            return _git_pull(repo)
        if action == "push":
            return _git_push(repo)
    except git.GitCommandError as exc:
        logger.error("Git command error (%s): %s", action, exc, exc_info=True)
        return f"Git command error: {exc}"
    except Exception as exc:
        logger.error("Unexpected git error (%s): %s", action, exc, exc_info=True)
        return f"Unexpected error during '{action}': {exc}"

    return f"Error: action '{action}' fell through without result."


# ── Action implementations ──────────────────────────────────────────

def _git_status(repo: git.Repo) -> str:
    """Return a formatted status report."""
    lines: list[str] = []

    branch = repo.active_branch.name if not repo.head.is_detached else "(detached HEAD)"
    lines.append(f"Branch: {branch}")

    changed = [item.a_path for item in repo.index.diff(None)]
    staged = [item.a_path for item in repo.index.diff("HEAD")]
    untracked = repo.untracked_files

    if staged:
        lines.append(f"\nStaged ({len(staged)}):")
        for f in staged[:15]:
            lines.append(f"  + {f}")
        if len(staged) > 15:
            lines.append(f"  ... and {len(staged) - 15} more")

    if changed:
        lines.append(f"\nModified ({len(changed)}):")
        for f in changed[:15]:
            lines.append(f"  ~ {f}")
        if len(changed) > 15:
            lines.append(f"  ... and {len(changed) - 15} more")

    if untracked:
        lines.append(f"\nUntracked ({len(untracked)}):")
        for f in untracked[:15]:
            lines.append(f"  ? {f}")
        if len(untracked) > 15:
            lines.append(f"  ... and {len(untracked) - 15} more")

    if not changed and not staged and not untracked:
        lines.append("\nWorking tree is clean.")

    return "\n".join(lines)


def _git_commit(repo: git.Repo, message: Optional[str]) -> str:
    """Stage everything and create a commit."""
    if not message:
        return "Error: commit_message is required for action 'commit'."

    # Stage all changes (tracked + untracked)
    repo.git.add(A=True)

    # Check there is something to commit
    if not repo.index.diff("HEAD") and not repo.untracked_files:
        return "Nothing to commit — working tree is clean after staging."

    commit = repo.index.commit(message)
    return (
        f"Committed successfully.\n"
        f"  Hash: {commit.hexsha[:10]}\n"
        f"  Message: {message}\n"
        f"  Files changed: {commit.stats.total['files']}"
    )


def _git_pull(repo: git.Repo) -> str:
    """Pull from the tracked remote."""
    if not repo.remotes:
        return "Error: no remotes configured."

    origin = repo.remotes.origin
    info = origin.pull()

    summaries: list[str] = []
    for fetch_info in info:
        flags = []
        if fetch_info.flags & fetch_info.FAST_FORWARD:
            flags.append("fast-forward")
        if fetch_info.flags & fetch_info.ERROR:
            flags.append("ERROR")
        if fetch_info.flags & fetch_info.REJECTED:
            flags.append("rejected")
        flag_str = f" ({', '.join(flags)})" if flags else ""
        summaries.append(f"  {fetch_info.ref}{flag_str}")

    return "Pull completed.\n" + "\n".join(summaries) if summaries else "Pull completed (up to date)."


def _git_push(repo: git.Repo) -> str:
    """Push to the tracked remote."""
    if not repo.remotes:
        return "Error: no remotes configured."

    origin = repo.remotes.origin
    info = origin.push()

    summaries: list[str] = []
    for push_info in info:
        flags = []
        if push_info.flags & push_info.ERROR:
            flags.append("ERROR")
        if push_info.flags & push_info.REJECTED:
            flags.append("rejected")
        if push_info.flags & push_info.UP_TO_DATE:
            flags.append("up-to-date")
        flag_str = f" ({', '.join(flags)})" if flags else ""
        summaries.append(f"  {push_info.summary.strip()}{flag_str}")

    return "Push completed.\n" + "\n".join(summaries) if summaries else "Push completed."
