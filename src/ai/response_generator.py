from typing import Optional

from rich.console import Console

from ..database.repository import Message
from .qwen_oauth import QwenClient
from .style_analyzer import StyleAnalyzer


console = Console()


class ResponseGenerator:
    
    def __init__(self, qwen_client: QwenClient, style_prompt: str):
        self.qwen = qwen_client
        self.style_prompt = style_prompt
    
    async def generate(
        self,
        context_messages: list[Message],
        incoming_message: str,
        contact_name: str,
    ) -> str:
        conversation = []
        
        for msg in context_messages[-15:]:
            role = "assistant" if msg.is_outgoing else "user"
            conversation.append({"role": role, "content": msg.text})
        
        conversation.append({"role": "user", "content": incoming_message})
        
        system_prompt = f"""{self.style_prompt}

You are responding to a message from {contact_name}. Generate a natural response that perfectly matches the communication style described above.

The conversation context is provided. Generate ONLY the response message - no explanations, no meta-commentary, just the message as the person would send it."""

        messages = [
            {"role": "system", "content": system_prompt},
            *conversation
        ]
        
        response = await self.qwen.chat(messages, temperature=0.8)
        
        response = response.strip()
        if response.startswith('"') and response.endswith('"'):
            response = response[1:-1]
        if response.startswith("'") and response.endswith("'"):
            response = response[1:-1]
        
        return response
    
    
    async def generate_multiple(
        self,
        context_messages: list[Message],
        incoming_message: str,
        contact_name: str,
        count: int = 3
    ) -> list[str]:
        options = []
        
        for i in range(count):
            temp = 0.7 + (i * 0.15)
            
            conversation = []
            for msg in context_messages[-15:]:
                role = "assistant" if msg.is_outgoing else "user"
                conversation.append({"role": role, "content": msg.text})
            conversation.append({"role": "user", "content": incoming_message})
            
            system_prompt = f"""{self.style_prompt}

You are responding to a message from {contact_name}. Generate a natural response that perfectly matches the communication style described above.

Generate ONLY the response message - no explanations, no meta-commentary."""

            messages = [
                {"role": "system", "content": system_prompt},
                *conversation
            ]
            
            response = await self.qwen.chat(messages, temperature=temp)
            response = response.strip().strip('"\'')
            options.append(response)
        
        return options
