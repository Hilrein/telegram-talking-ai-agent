"""PostgreSQL backup tool for the autonomous agent.

Calls the system ``pg_dump`` utility via ``subprocess`` to create a SQL
dump of the target database.  Backups are stored in
``{AGENT_DATA_DIR}/backups/`` with timestamped filenames.

The tool reads the database URI from the function argument first; if not
provided, it falls back to the ``POSTGRES_DATABASE_URL`` environment
variable.
"""

import logging
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Resolve AGENT_DATA_DIR at call time (not import time) so env changes
# and tests work correctly.  The helper below is used internally.

def _resolve_backup_dir() -> Path:
    """Return the backup directory, creating it if needed."""
    agent_data_dir = Path(os.getenv("AGENT_DATA_DIR", "./agent-data"))
    backup_dir = agent_data_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def backup_postgresql(db_uri: Optional[str] = None) -> str:
    """Create a ``pg_dump`` backup of a PostgreSQL database.

    Parameters
    ----------
    db_uri:
        A full PostgreSQL connection URI, e.g.
        ``postgresql://user:pass@host:5432/dbname``.
        Falls back to the ``POSTGRES_DATABASE_URL`` env var when omitted.

    Returns
    -------
    str
        A human-readable report containing the backup file path and size,
        or a descriptive error message.
    """
    # ── Resolve the connection string ────────────────────────────
    uri = db_uri or os.getenv("POSTGRES_DATABASE_URL", "")
    if not uri:
        return (
            "Error: no database URI provided. "
            "Pass db_uri or set the POSTGRES_DATABASE_URL environment variable."
        )

    # Validate that the URI looks like a PostgreSQL connection string
    try:
        parsed = urlparse(uri)
        if parsed.scheme not in ("postgresql", "postgres", "postgresql+asyncpg"):
            return f"Error: unsupported URI scheme '{parsed.scheme}'. Expected 'postgresql'."
    except Exception as exc:
        return f"Error: could not parse db_uri — {exc}"

    # ── Check that pg_dump is installed ──────────────────────────
    if shutil.which("pg_dump") is None:
        return (
            "Error: 'pg_dump' is not installed or not on PATH. "
            "Install PostgreSQL client tools to use this tool."
        )

    # ── Build the output path ────────────────────────────────────
    backup_dir = _resolve_backup_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    db_name = parsed.path.lstrip("/") or "database"
    filename = f"backup_{db_name}_{timestamp}.sql"
    output_path = backup_dir / filename

    # ── Run pg_dump ──────────────────────────────────────────────
    # pg_dump accepts a full URI as positional argument.
    # We normalise the scheme to plain "postgresql" so pg_dump accepts it.
    normalised_uri = uri
    if parsed.scheme == "postgresql+asyncpg":
        normalised_uri = "postgresql" + uri[len(parsed.scheme):]

    cmd = [
        "pg_dump",
        "--no-owner",
        "--no-privileges",
        "-f", str(output_path),
        normalised_uri,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5-minute safety cap
        )
    except subprocess.TimeoutExpired:
        return "Error: pg_dump timed out after 5 minutes."
    except FileNotFoundError:
        return "Error: 'pg_dump' executable not found."
    except Exception as exc:
        logger.error("pg_dump subprocess error: %s", exc, exc_info=True)
        return f"Error running pg_dump: {exc}"

    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else "(no stderr)"
        logger.error("pg_dump failed (rc=%d): %s", result.returncode, stderr)
        return f"Error: pg_dump exited with code {result.returncode}.\n{stderr}"

    # ── Report success ───────────────────────────────────────────
    try:
        size_bytes = output_path.stat().st_size
        if size_bytes < 1024:
            size_str = f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            size_str = f"{size_bytes / 1024:.1f} KB"
        else:
            size_str = f"{size_bytes / (1024 * 1024):.2f} MB"
    except OSError:
        size_str = "unknown"

    return (
        f"Backup created successfully.\n"
        f"  File: {output_path}\n"
        f"  Size: {size_str}"
    )
