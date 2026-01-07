import asyncio
import logging
import sys
from datetime import datetime

from dotenv import load_dotenv

from .config import Config, load_config
from .database.repository import Repository, Contact, Message
from .telegram.client import TelegramClient
from .telegram.message_fetcher import MessageFetcher
from .ai.qwen_oauth import QwenClient
from .ai.style_analyzer import StyleAnalyzer
from .ai.response_generator import ResponseGenerator
from .ui.menu import MenuUI, console


# Configure logging at the entry point
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


async def async_main(selected_model: str):
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
            model = selected_model
            console.print(f"\n[dim]Using model: {model}[/dim]\n")
            
            async with QwenClient(repo, model) as qwen:
                console.print("[dim]Loading contacts...[/dim]")
                contacts = await tg_client.get_recent_dialogs(limit=30)
                
                for contact in contacts:
                    await repo.upsert_contact(contact)
                
                selected = MenuUI.select_contact(contacts)
                if not selected:
                    console.print("[dim]Cancelled.[/dim]")
                    return 0
                
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
                analyzer = StyleAnalyzer(repo, qwen)
                style_profile = await analyzer.analyze(selected.telegram_id, messages)
                
                MenuUI.show_style_profile(style_profile)
                
                style_prompt = analyzer.generate_style_prompt(style_profile)
                generator = ResponseGenerator(qwen, style_prompt)
                
                await run_chat_mode(
                    tg_client=tg_client,
                    repo=repo,
                    fetcher=fetcher,
                    generator=generator,
                    contact=selected,
                )
                
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
) -> None:
    console.print("\n[bold green]🟢 Entering real-time chat mode[/bold green]")
    console.print("[dim]Press Ctrl+C to exit[/dim]\n")
    
    message_queue: asyncio.Queue[Message] = asyncio.Queue()
    
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
            MenuUI.show_message(
                sender=contact.display_name,
                text=msg.text,
                is_incoming=True,
                timestamp=timestamp
            )
            
            context = await fetcher.get_recent_context(contact.telegram_id, limit=20)
            
            console.print("[dim]Generating response...[/dim]")
            current_response = await generator.generate(
                context_messages=context,
                incoming_message=msg.text,
                contact_name=contact.display_name
            )
            
            while True:
                action = MenuUI.show_generated_response(current_response)
                
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
                    edited = MenuUI.edit_response(current_response)
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
                    selected = MenuUI.select_alternative(options)
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
        
        try:
            config = load_config()
            default_model = config.qwen_default_model
        except ValueError:
            default_model = "coder-model"
        
        selected_model = MenuUI.select_model(default_model)
        
        exit_code = asyncio.run(async_main(selected_model))
        sys.exit(exit_code)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        sys.exit(0)


if __name__ == "__main__":
    main()
