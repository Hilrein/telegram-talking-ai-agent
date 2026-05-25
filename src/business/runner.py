"""
Business bot runner.

Starts the Bot API long-polling loop and dispatches updates
to the BusinessHandler. Also runs a periodic expiry task.
Launches the Mini App (FastAPI + ngrok) alongside the bot.
"""

import asyncio
import logging
import sys

import uvicorn
from pyngrok import ngrok as pyngrok_ngrok, conf as ngrok_conf
from rich.console import Console
from rich.panel import Panel
from dotenv import load_dotenv

from ..config import load_config
from ..database.repository import Repository
from ..ai.nvidia_client import NvidiaClient
from ..api_server import app as fastapi_app, configure as configure_api
from .bot_api import BotApiClient
from .handler import BusinessHandler
from .pending_store import PendingStore

logger = logging.getLogger(__name__)
console = Console()

ALLOWED_UPDATES = [
    "business_connection",
    "business_message",
    "edited_business_message",
    "deleted_business_messages",
    "callback_query",
    "message",
]


async def run_business_bot() -> int:
    """Main entry point for the business bot mode."""
    try:
        config = load_config()
    except ValueError as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        return 1

    if not config.bot_token:
        console.print("[bold red]Error:[/bold red] BOT_TOKEN is required for business mode.")
        return 1

    if not config.business_owner_chat_id:
        console.print("[bold red]Error:[/bold red] BUSINESS_OWNER_CHAT_ID is required.")
        return 1

    # ── Initialize components ───────────────────────────────────
    repo = Repository(config.db_path)
    await repo.connect()

    bot = BotApiClient(config.bot_token)
    pending = PendingStore(timeout_minutes=config.pending_timeout_minutes)

    # ── Start ngrok tunnel ──────────────────────────────────────
    miniapp_public_url = ""
    ngrok_tunnel = None

    if config.ngrok_authtoken:
        try:
            ngrok_conf.get_default().auth_token = config.ngrok_authtoken
            ngrok_tunnel = pyngrok_ngrok.connect(config.miniapp_port, "http")
            miniapp_public_url = ngrok_tunnel.public_url
            if miniapp_public_url.startswith("http://"):
                miniapp_public_url = miniapp_public_url.replace("http://", "https://", 1)
            console.print(f"[bold green]🌐 Mini App URL:[/bold green] {miniapp_public_url}")
        except Exception as e:
            console.print(f"[yellow]⚠️  ngrok failed (Mini App will be local only): {e}[/yellow]")
    else:
        console.print(f"[yellow]ℹ️  NGROK_AUTHTOKEN not set — Mini App available locally only: http://localhost:{config.miniapp_port}[/yellow]")
        miniapp_public_url = f"http://localhost:{config.miniapp_port}"

    try:
        # Verify bot token
        me = await bot.get_me()
        bot_name = me.get("first_name", "Bot")
        bot_username = me.get("username", "")

        console.print()
        console.print(Panel.fit(
            f"[bold cyan]🤖 Telegram Business Bot[/bold cyan]\n"
            f"[dim]Bot: {bot_name} (@{bot_username})[/dim]\n"
            f"[dim]Owner chat: {config.business_owner_chat_id}[/dim]\n"
            f"[dim]Pending timeout: {config.pending_timeout_minutes} min[/dim]\n"
            f"[dim]Context: last {config.context_limit} msgs / {config.context_months} months[/dim]\n"
            f"[dim]Mini App: {miniapp_public_url}[/dim]",
            border_style="cyan"
        ))
        console.print()

        if not config.nvidia_api_key:
            console.print("[bold red]Error:[/bold red] NVIDIA_API_KEY is required.")
            return 1

        model = config.business_ai_model
        console.print(f"[dim]Using AI model: {model}[/dim]")

        ai_context = NvidiaClient(
            api_key=config.nvidia_api_key,
            model=model,
            base_url=config.nvidia_base_url,
        )

        async with ai_context as ai_client:
            handler = BusinessHandler(
                bot=bot,
                repo=repo,
                ai_client=ai_client,
                owner_chat_id=config.business_owner_chat_id,
                pending_store=pending,
                style_prompt=config.business_style_prompt,
                context_limit=config.context_limit,
                context_months=config.context_months,
            )

            # Configure FastAPI with shared repo
            # connection_id is not known at startup; handler will set it on first connect
            configure_api(
                repo=repo,
                owner_name=config.owner_name,
            )

            # Notify owner that bot is online
            await bot.send_message(
                config.business_owner_chat_id,
                f"🟢 <b>Business бот запущен</b>\n\n"
                f"Бот: @{bot_username}\n"
                f"Mini App: {miniapp_public_url}",
            )

            console.print("[bold green]🟢 Business bot is running[/bold green]")
            console.print("[dim]Press Ctrl+C to stop[/dim]\n")

            # Run polling, expiry, and API server concurrently
            await asyncio.gather(
                _poll_loop(bot, handler),
                _expiry_loop(handler),
                _serve_miniapp(config.miniapp_port),
            )

    except KeyboardInterrupt:
        console.print("\n[yellow]Stopping business bot...[/yellow]")
    except Exception as e:
        console.print(f"\n[bold red]Fatal error:[/bold red] {e}")
        logger.exception("Business bot crashed")
        return 1
    finally:
        if ngrok_tunnel:
            try:
                pyngrok_ngrok.disconnect(ngrok_tunnel.public_url)
            except Exception:
                pass
        try:
            await bot.send_message(
                config.business_owner_chat_id,
                "🔴 <b>Business бот остановлен</b>",
            )
        except Exception:
            pass
        await bot.close()
        await repo.close()

    return 0


async def _poll_loop(bot: BotApiClient, handler: BusinessHandler) -> None:
    """Long-polling loop for Bot API updates."""
    logger.info("Starting Bot API polling loop")

    while True:
        try:
            updates = await bot.get_updates(
                allowed_updates=ALLOWED_UPDATES,
                poll_timeout=30,
            )

            for update in updates:
                try:
                    await handler.handle_update(update)
                except Exception as e:
                    logger.error("Error handling update %s: %s", update.get("update_id"), e, exc_info=True)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Poll loop error: %s", e, exc_info=True)
            await asyncio.sleep(3)


async def _expiry_loop(handler: BusinessHandler, interval: int = 30) -> None:
    """Periodically expire old pending responses."""
    while True:
        try:
            await asyncio.sleep(interval)
            await handler.handle_expired()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Expiry loop error: %s", e, exc_info=True)


async def _serve_miniapp(port: int) -> None:
    """Run the FastAPI Mini App server as an asyncio task."""
    server_config = uvicorn.Config(
        app=fastapi_app,
        host="0.0.0.0",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(server_config)
    try:
        await server.serve()
    except asyncio.CancelledError:
        pass


def main_business():
    """CLI entry point for `tg-agent` command."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    console.print()
    console.print(Panel.fit(
        "[bold cyan]Telegram Business Chat Automation[/bold cyan]\n"
        "[dim]AI-powered auto-responder with per-user memory[/dim]",
        border_style="cyan",
    ))
    console.print()

    try:
        exit_code = asyncio.run(run_business_bot())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        sys.exit(0)
