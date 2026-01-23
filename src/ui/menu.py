from typing import Optional

from InquirerPy import inquirer
from InquirerPy.base.control import Choice
from InquirerPy.separator import Separator
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.spinner import Spinner

from ..config import QWEN_MODELS, GEMINI_MODELS
from ..database.repository import Contact


console = Console()


class MenuUI:
    
    @staticmethod
    def show_welcome() -> None:
        console.print()
        console.print(Panel.fit(
            "[bold cyan]Telegram AI Agent[/bold cyan]\n"
            "[dim]Mimics your communication style[/dim]",
            border_style="cyan"
        ))
        console.print()
    
    @staticmethod
    async def select_contact(contacts: list[Contact]) -> Optional[Contact]:
        if not contacts:
            console.print("[red]No contacts found![/red]")
            return None
        
        choices = []
        for c in contacts:
            label = c.display_name
            if c.username:
                label += f" (@{c.username})"
            if not c.is_user:
                label += " [group]"
            choices.append(Choice(value=c, name=label))
        
        choices.append(Choice(value=None, name="← Cancel"))
        
        result = await inquirer.select(
            message="Select contact to chat with:",
            choices=choices,
            pointer="→",
            amark="✓",
        ).execute_async()
        
        return result
    
    @staticmethod
    async def select_model(default: str = "qwen3-max") -> str:
        choices = []
        choices.append(Separator("--- Qwen Models ---"))
        for model_id, description in QWEN_MODELS:
            label = f"{model_id} - {description}"
            choices.append(Choice(value=model_id, name=label))
            
        choices.append(Separator("--- Gemini Models ---"))
        for model_id, description in GEMINI_MODELS:
            label = f"{model_id} - {description}"
            choices.append(Choice(value=model_id, name=label))
        
        result = await inquirer.select(
            message="Select AI model:",
            choices=choices,
            default=default,
            pointer="→",
            amark="✓",
        ).execute_async()
        
        return result
    
    @staticmethod
    def confirm(message: str, default: bool = True) -> bool:
        return inquirer.confirm(message=message, default=default).execute()
    
    @staticmethod
    def create_chat_row(
        sender: str,
        user_text: str,
        ai_text: Optional[str] = None,
        is_loading: bool = False,
        timestamp: Optional[str] = None
    ) -> Table:
        grid = Table.grid(expand=True, padding=(0, 2))
        grid.add_column(ratio=1)
        grid.add_column(ratio=1)
        
        # User Panel (Left)
        user_header = f"[bold blue]{sender}[/]"
        if timestamp:
            user_header += f" [dim]{timestamp}[/dim]"
        user_panel = Panel(user_text, title=user_header, border_style="blue")
        
        # AI Panel (Right)
        if is_loading:
            ai_content = Spinner("dots", text="Thinking...", style="yellow")
            ai_panel = Panel(ai_content, title="[bold yellow]AI Agent[/]", border_style="yellow")
        elif ai_text is not None:
            ai_panel = Panel(ai_text, title="[bold yellow]AI Agent[/]", border_style="yellow")
        else:
            ai_panel = Panel("", title="[bold yellow]AI Agent[/]", border_style="dim yellow")
            
        grid.add_row(user_panel, ai_panel)
        return grid

    @staticmethod
    def show_message(
        sender: str,
        text: str,
        is_incoming: bool = True,
        timestamp: Optional[str] = None
    ) -> None:
        style = "blue" if is_incoming else "green"
        direction = "←" if is_incoming else "→"
        
        header = f"[bold {style}]{direction} {sender}[/bold {style}]"
        if timestamp:
            header += f" [dim]{timestamp}[/dim]"
        
        console.print(header)
        console.print(Panel(text, border_style=style, padding=(0, 1)))
        console.print()
    
    @staticmethod
    async def show_generated_response(
        text: str, 
        options: Optional[list[str]] = None,
        show_panel: bool = True
    ) -> str:
        if show_panel:
            console.print("\n[bold yellow]📝 Generated Response:[/bold yellow]")
            console.print(Panel(text, border_style="yellow", padding=(0, 1)))
        else:
            console.print() # Just a spacer
        
        if options:
            console.print("\n[dim]Alternative responses available[/dim]")
        
        choices = [
            Choice(value="send", name="✓ Send this response"),
            Choice(value="edit", name="✎ Edit before sending"),
            Choice(value="regenerate", name="↻ Regenerate"),
        ]
        
        if options:
            choices.append(Choice(value="alternatives", name="◇ Show alternatives"))
        
        choices.append(Choice(value="skip", name="✗ Skip (don't respond)"))
        
        return await inquirer.select(
            message="What would you like to do?",
            choices=choices,
            pointer="→",
            amark="✓",
        ).execute_async()
    
    @staticmethod
    async def edit_response(current: str) -> str:
        return await inquirer.text(
            message="Edit response:",
            default=current,
            multiline=True,
        ).execute_async()
    
    @staticmethod
    async def select_alternative(options: list[str]) -> Optional[str]:
        choices = [Choice(value=opt, name=opt[:80] + "..." if len(opt) > 80 else opt) for opt in options]
        choices.append(Choice(value=None, name="← Back"))
        
        return await inquirer.select(
            message="Select alternative:",
            choices=choices,
            pointer="→",
            amark="✓",
        ).execute_async()

    @staticmethod
    async def select_start_action() -> str:
        choices = [
            Choice(value="reply_incoming", name="Reply to last incoming message"),
            Choice(value="reply_outgoing", name="Reply to my last message"),
            Choice(value="wait", name="Wait for new messages"),
        ]
        
        return await inquirer.select(
            message="Starting action:",
            choices=choices,
            default="wait",
            pointer="→",
            amark="✓",
        ).execute_async()
    
    @staticmethod
    async def ask_auto_reply() -> bool:
        return await inquirer.confirm(
            message="Enable auto-reply?",
            default=False
        ).execute_async()

    @staticmethod
    async def ask_wait_time() -> int:
        result = await inquirer.number(
            message="Average wait time (seconds):",
            min_allowed=0,
            max_allowed=300,
            default=5,
            validate=lambda x: x.isdigit() and int(x) >= 0
        ).execute_async()
        return int(result)
    
    @staticmethod
    def show_style_profile(style: dict) -> None:
        console.print("\n[bold cyan]📊 Your Communication Style Analysis[/bold cyan]\n")
        
        metrics = style.get("metrics", {})
        qualitative = style.get("qualitative", {})
        
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Property", style="cyan")
        table.add_column("Value")
        
        if qualitative:
            table.add_row("Formality", qualitative.get("formality", "N/A"))
            table.add_row("Tone", ", ".join(qualitative.get("tone", [])))
            table.add_row("Directness", qualitative.get("directness", "N/A"))
            table.add_row("Humor", qualitative.get("humor_level", "N/A"))
        
        if metrics:
            table.add_row("Avg. message length", f"{metrics.get('avg_words_per_message', 0):.1f} words")
            table.add_row("Emoji usage", f"{metrics.get('emoji_frequency', 0):.2f} per message")
            
            if emojis := metrics.get("top_emojis"):
                table.add_row("Top emojis", " ".join(emojis[:5]))
            
            table.add_row("Messages analyzed", str(metrics.get("message_count_analyzed", 0)))
        
        console.print(table)
        console.print()
    
    @staticmethod
    def show_error(message: str) -> None:
        console.print(f"[bold red]Error:[/bold red] {message}")
    
    @staticmethod
    def show_info(message: str) -> None:
        console.print(f"[cyan]ℹ[/cyan] {message}")
    
    @staticmethod
    def show_success(message: str) -> None:
        console.print(f"[green]✓[/green] {message}")
