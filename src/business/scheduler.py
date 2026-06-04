"""Autonomous task scheduler for the AI agent.

Bridges the ``agent_tasks`` table in the isolated SQLite database with
APScheduler's ``AsyncIOScheduler``, and executes each task by calling
``NvidiaClient.agent_chat`` — so the LLM can autonomously pick and invoke
the right tools.

Lifecycle:
  1. ``init_scheduler()`` — reads active tasks, registers APScheduler jobs,
     starts the scheduler.
  2. ``shutdown_scheduler()`` — gracefully stops the scheduler.
  3. ``add_scheduled_job`` / ``remove_scheduled_job`` — hot-reload tasks
     at runtime (called from a future API layer).
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from ..ai.nvidia_client import NvidiaClient
from ..database.agent_repository import AgentRepository

logger = logging.getLogger(__name__)

# ── Module-level state ──────────────────────────────────────────────
# Kept at module scope so the rest of the app can call
# ``add_scheduled_job`` / ``remove_scheduled_job`` without passing
# references around.

_scheduler: Optional[AsyncIOScheduler] = None
_ai_client: Optional[NvidiaClient] = None
_agent_db_path: Optional[Path] = None

# System prompt used for every autonomous task execution.
_AGENT_SYSTEM_PROMPT = (
    "You are an autonomous AI agent running on the user's PC. "
    "You have access to tools: file search, web search, Git management, "
    "and PostgreSQL backup. When the user gives you a task, choose the "
    "most appropriate tool, execute it, and report the result concisely. "
    "If no tool is needed, just answer the question directly."
)


# ── Public API ──────────────────────────────────────────────────────


async def init_scheduler(
    ai_client: NvidiaClient,
    agent_db_path: Path,
) -> AsyncIOScheduler:
    """Create the scheduler, load active tasks from the DB, and start it.

    Parameters
    ----------
    ai_client:
        An already-initialised ``NvidiaClient`` (with API key & model set).
    agent_db_path:
        Path to the agent SQLite database file (``agent_memory.db``).

    Returns
    -------
    AsyncIOScheduler
        The running scheduler instance (also stored at module level).
    """
    global _scheduler, _ai_client, _agent_db_path

    _ai_client = ai_client
    _agent_db_path = agent_db_path

    _scheduler = AsyncIOScheduler(
        job_defaults={
            "coalesce": True,           # merge missed runs into one
            "max_instances": 1,         # never overlap the same job
            "misfire_grace_time": 600,  # tolerate up to 10 min delay
        },
    )

    # Load persisted tasks
    agent_repo = AgentRepository(agent_db_path)
    await agent_repo.connect()
    try:
        tasks = await agent_repo.get_active_tasks()
        for task in tasks:
            _register_job(task)
            logger.info(
                "Loaded scheduled task id=%d type=%s time=%s",
                task["id"],
                task["task_type"],
                task["execution_time"],
            )
    finally:
        await agent_repo.close()

    _scheduler.start()
    logger.info("Scheduler started with %d active task(s)", len(tasks))
    return _scheduler


async def shutdown_scheduler() -> None:
    """Gracefully shut down the scheduler if it's running."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler shut down")
    _scheduler = None


def add_scheduled_job(task: dict) -> None:
    """Register a new job in the *running* scheduler.

    ``task`` must be a dict with keys ``id``, ``task_type``,
    ``execution_time``, ``prompt``, ``is_active``.
    """
    if _scheduler is None:
        logger.warning("add_scheduled_job called but scheduler is not running")
        return
    _register_job(task)
    logger.info("Added job for task id=%d on-the-fly", task["id"])


def remove_scheduled_job(task_id: int) -> None:
    """Remove a job from the running scheduler by its task ID."""
    if _scheduler is None:
        logger.warning("remove_scheduled_job called but scheduler is not running")
        return

    job_id = _make_job_id(task_id)
    existing = _scheduler.get_job(job_id)
    if existing:
        _scheduler.remove_job(job_id)
        logger.info("Removed job '%s' from scheduler", job_id)
    else:
        logger.debug("Job '%s' not found in scheduler — nothing to remove", job_id)


# ── Worker ──────────────────────────────────────────────────────────


async def run_automated_task(task_id: int, prompt: str) -> None:
    """Execute a single automated task via ``agent_chat``.

    Called by APScheduler when a trigger fires.  Opens its own
    ``AgentRepository`` connection so the job is fully self-contained
    and a failure here never leaks state to other jobs.
    """
    logger.info("▶ Running automated task id=%d", task_id)

    if _ai_client is None or _agent_db_path is None:
        logger.error("Worker called before scheduler was initialised")
        return

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    session_id = f"task_{task_id}_{timestamp}"

    agent_repo = AgentRepository(_agent_db_path)

    try:
        await agent_repo.connect()

        # Log the incoming prompt (as if the "system" is the user)
        await agent_repo.save_history_message(
            session_id=session_id,
            role="user",
            content=prompt,
        )

        messages = [
            {"role": "system", "content": _AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        result = await _ai_client.agent_chat(
            messages=messages,
            session_id=session_id,
            agent_repo=agent_repo,
        )

        logger.info(
            "✔ Task id=%d completed (session=%s). Result preview: %.200s",
            task_id,
            session_id,
            result,
        )

    except Exception as exc:
        # Critical: never let an exception propagate — APScheduler would
        # mark the job as errored, but we want the *scheduler itself* to
        # keep running.
        logger.error(
            "✘ Task id=%d failed: %s",
            task_id,
            exc,
            exc_info=True,
        )
    finally:
        await agent_repo.close()


# ── Internal helpers ────────────────────────────────────────────────


def _make_job_id(task_id: int) -> str:
    return f"agent_task_{task_id}"


def _register_job(task: dict) -> None:
    """Add one APScheduler job for a task dict from the DB."""
    if _scheduler is None:
        return

    task_id: int = task["id"]
    task_type: str = task["task_type"]
    execution_time: Optional[str] = task.get("execution_time")
    prompt: str = task["prompt"]
    job_id = _make_job_id(task_id)

    # Remove a previous version if it exists (idempotent reload)
    if _scheduler.get_job(job_id):
        _scheduler.remove_job(job_id)

    trigger = _build_trigger(task_type, execution_time)

    _scheduler.add_job(
        run_automated_task,
        trigger=trigger,
        id=job_id,
        name=f"Task {task_id}: {prompt[:60]}",
        kwargs={"task_id": task_id, "prompt": prompt},
        replace_existing=True,
    )


def _build_trigger(
    task_type: str,
    execution_time: Optional[str],
) -> CronTrigger | IntervalTrigger:
    """Convert our domain task types into APScheduler triggers.

    Mapping:
      - ``every_hour``  → interval of 1 hour (fires at minute 0)
      - ``every_day``   → cron at HH:MM every day
      - ``every_week``  → cron at HH:MM every Monday
    """
    hour, minute = _parse_time(execution_time)

    if task_type == "every_hour":
        # Fire every hour on the hour.  We use cron so it aligns to
        # wall-clock minutes rather than drifting like IntervalTrigger.
        return CronTrigger(minute=0)

    if task_type == "every_day":
        return CronTrigger(hour=hour, minute=minute)

    if task_type == "every_week":
        return CronTrigger(day_of_week="mon", hour=hour, minute=minute)

    # Fallback — shouldn't happen if the DB CHECK constraint is in place,
    # but just in case: run once a day at midnight.
    logger.warning("Unknown task_type '%s' — defaulting to daily midnight", task_type)
    return CronTrigger(hour=0, minute=0)


def _parse_time(execution_time: Optional[str]) -> tuple[int, int]:
    """Parse an ``"HH:MM"`` string into ``(hour, minute)``.

    Returns ``(0, 0)`` when the string is absent or malformed.
    """
    if not execution_time:
        return 0, 0

    try:
        parts = execution_time.strip().split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        return max(0, min(h, 23)), max(0, min(m, 59))
    except (ValueError, IndexError):
        logger.warning("Could not parse execution_time '%s' — using 00:00", execution_time)
        return 0, 0
