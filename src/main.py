import asyncio
import logging
import sys
from datetime import datetime
from rich.live import Live

from dotenv import load_dotenv

from .config import Config, load_config
from .database.repository import Repository, Contact, Message
from .telegram.client import TelegramClient
from .telegram.message_fetcher import MessageFetcher
from .ai.qwen_oauth import QwenClient
from .ai.google_oauth import GoogleClient
from .ai.style_analyzer import StyleAnalyzer
from .ai.response_generator import ResponseGenerator
from .ui.menu import MenuUI, console


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


async def async_main():
    try:
        config = load_config()
    except ValueError as e:
        MenuUI.show_error(str(e))
        console.print("\nPlease copy .env.example to .env and fill in your credentials.")
        return 1
    
    repo = Repository(config.db_path)
    await repo.connect()
    
    try:
        tg_client = TelegramClient(
            config.tg_api_id,
            config.tg_api_hash,
            config.session_path
        )
        await tg_client.connect()
        
        try:
            default_model = config.qwen_default_model
        except ValueError:
            default_model = "qwen-max"
        
        model = await MenuUI.select_model(default_model)
        
        try:
            console.print(f"\n[dim]Using model: {model}[/dim]\n")
            
            if model.startswith("gemini"):
                client_class = GoogleClient
                client_context = GoogleClient(repo, model, config.google_client_secret_path)
            else:
                client_context = QwenClient(repo, model)

            try:
                async with client_context as client:
                    console.print("[dim]Loading contacts...[/dim]")
                    contacts = await tg_client.get_recent_dialogs(limit=30)
                    
                    for contact in contacts:
                        await repo.upsert_contact(contact)
                    
                    selected = await MenuUI.select_contact(contacts)
                    if not selected:
                        console.print("[dim]Cancelled.[/dim]")
                        return 0
                    
                    auto_reply = await MenuUI.ask_auto_reply()
                    wait_time = 0
                    if auto_reply:
                        wait_time = await MenuUI.ask_wait_time()
                    
                    if isinstance(selected, dict):
                        selected = Contact(**selected)
                    
                    console.print(f"\n[bold]Selected: {selected.display_name}[/bold]\n")
                    
                    console.print("[cyan]Fetching message history (last 6 months)...[/cyan]")
                    fetcher = MessageFetcher(tg_client, repo)
                    msg_count = await fetcher.fetch_history(selected.telegram_id, months=6)
                    console.print(f"[green]✓ {msg_count} messages in database[/green]\n")
                    
                    messages = await repo.get_messages(selected.telegram_id)
                    my_messages = [m for m in messages if m.is_outgoing]
                    
                    if len(my_messages) < 5:
                        MenuUI.show_error(
                            f"Not enough outgoing messages ({len(my_messages)}) to analyze your style. "
                            "Please select a contact with more conversation history."
                        )
                        return 1
                    
                    console.print("[cyan]Analyzing your communication style...[/cyan]")
                    analyzer = StyleAnalyzer(repo, client)
                    style_profile = await analyzer.analyze(selected.telegram_id, messages)
                    
                    MenuUI.show_style_profile(style_profile)
                    
                    style_prompt = analyzer.generate_style_prompt(style_profile)
                    generator = ResponseGenerator(client, style_prompt)
                    
                    await run_chat_mode(
                        tg_client=tg_client,
                        repo=repo,
                        fetcher=fetcher,
                        generator=generator,
                        contact=selected,
                        auto_reply=auto_reply,
                        wait_time=wait_time,
                    )
            except Exception as e:
                console.print(f"\n[bold red]Failed to initialize AI client:[/bold red] {e}")
                console.print("[dim]Please check your credentials and try again.[/dim]\n")
                return 1
                
        finally:
            await tg_client.disconnect()
            
    finally:
        await repo.close()
    
    return 0


async def run_chat_mode(
    tg_client: TelegramClient,
    repo: Repository,
    fetcher: MessageFetcher,
    generator: ResponseGenerator,
    contact: Contact,
    auto_reply: bool = False,
    wait_time: int = 0,
) -> None:
    messages = await repo.get_messages(contact.telegram_id)
    last_incoming = next((m for m in reversed(messages) if not m.is_outgoing), None)
    last_outgoing = next((m for m in reversed(messages) if m.is_outgoing), None)
    
    start_action = await MenuUI.select_start_action()
    
    console.print("\n[bold green]🟢 Entering real-time chat mode[/bold green]")
    console.print("[dim]Press Ctrl+C to exit[/dim]\n")
    
    message_queue: asyncio.Queue[Message] = asyncio.Queue()
    
    if start_action == "reply_incoming" and last_incoming:
        await message_queue.put(last_incoming)
    elif start_action == "reply_outgoing" and last_outgoing:
        await message_queue.put(last_outgoing)
    elif start_action != "wait":
        MenuUI.show_info("No suitable message found for selected action. Waiting for new messages.")
    
    async def on_message(event):
        if event.out:
            return
        
        msg = Message(
            telegram_msg_id=event.message.id,
            contact_id=contact.telegram_id,
            text=event.message.message or "",
            is_outgoing=False,
            timestamp=event.message.date.replace(tzinfo=None)
        )
        
        await repo.save_messages([msg])
        await message_queue.put(msg)
    
    tg_client.on_new_message(contact.telegram_id, on_message)
    
    async def process_messages():
        while True:
            try:
                msg = await asyncio.wait_for(message_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            
            timestamp = msg.timestamp.strftime("%H:%M")
            sender_name = "You" if msg.is_outgoing else contact.display_name
            
            view = MenuUI.create_chat_row(
                sender=sender_name,
                user_text=msg.text,
                is_loading=True,
                timestamp=timestamp
            )
            
            current_response = ""
            
            with Live(view, refresh_per_second=10, console=console, transient=True) as live:
                if auto_reply and wait_time > 0:
                    wait_view = MenuUI.create_chat_row(
                        sender=sender_name,
                        user_text=msg.text,
                        ai_text=f"[dim]Waiting {wait_time}s...[/dim]",
                        is_loading=True,
                        timestamp=timestamp
                    )
                    live.update(wait_view)
                    await asyncio.sleep(wait_time)

                context = await fetcher.get_recent_context(contact.telegram_id, limit=20)
                
                current_response = await generator.generate(
                    context_messages=context,
                    incoming_message=msg.text,
                    contact_name=contact.display_name
                )
                
                final_view = MenuUI.create_chat_row(
                    sender=sender_name,
                    user_text=msg.text,
                    ai_text=current_response,
                    is_loading=False,
                    timestamp=timestamp
                )
                live.update(final_view)
            
            console.print(final_view)
            console.rule(style="dim")
            
            if auto_reply:
                await tg_client.send_message(contact.telegram_id, current_response)
                MenuUI.show_success("Auto-replied!")
                
                await repo.save_messages([Message(
                    telegram_msg_id=0,
                    contact_id=contact.telegram_id,
                    text=current_response,
                    is_outgoing=True,
                    timestamp=datetime.now()
                )])
                continue

            while True:
                action = await MenuUI.show_generated_response(current_response, show_panel=False)
                
                if action == "send":
                    await tg_client.send_message(contact.telegram_id, current_response)
                    MenuUI.show_success("Message sent!")
                    
                    await repo.save_messages([Message(
                        telegram_msg_id=0,
                        contact_id=contact.telegram_id,
                        text=current_response,
                        is_outgoing=True,
                        timestamp=datetime.now()
                    )])
                    break 
                    
                elif action == "edit":
                    edited = await MenuUI.edit_response(current_response)
                    if edited.strip():
                        current_response = edited
                        
                        await tg_client.send_message(contact.telegram_id, current_response)
                        MenuUI.show_success("Message sent!")
                        
                        await repo.save_messages([Message(
                            telegram_msg_id=0,
                            contact_id=contact.telegram_id,
                            text=current_response,
                            is_outgoing=True,
                            timestamp=datetime.now()
                        )])
                        break
                        
                elif action == "regenerate":
                    current_response = await generator.generate(
                        context_messages=context,
                        incoming_message=msg.text,
                        contact_name=contact.display_name
                    )
                    
                elif action == "alternatives":
                    options = await generator.generate_multiple(
                        context_messages=context,
                        incoming_message=msg.text,
                        contact_name=contact.display_name,
                        count=3
                    )
                    selected = await MenuUI.select_alternative(options)
                    if selected:
                        current_response = selected
                        await tg_client.send_message(contact.telegram_id, current_response)
                        MenuUI.show_success("Message sent!")
                        break
                        
                elif action == "skip":
                    MenuUI.show_info("Skipped.")
                    break
    
    try:
        await asyncio.gather(
            process_messages(),
            tg_client.run_until_disconnected()
        )
    except KeyboardInterrupt:
        console.print("\n[yellow]Exiting...[/yellow]")


def main():
    try:
        MenuUI.show_welcome()
        
        exit_code = asyncio.run(async_main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        sys.exit(0)


if __name__ == "__main__":
    main()
