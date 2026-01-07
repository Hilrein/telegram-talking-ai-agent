

import re
import json
from datetime import datetime
from collections import Counter
from typing import Optional
from dataclasses import dataclass, asdict

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from ..database.repository import Repository, Message, StyleProfile
from .qwen_oauth import QwenClient


console = Console()


@dataclass
class StyleMetrics:
    avg_message_length: float
    avg_words_per_message: float
    emoji_frequency: float
    top_emojis: list[str]
    punctuation_style: dict[str, float]
    capitalization_ratio: float
    common_phrases: list[str]
    message_count_analyzed: int


class StyleAnalyzer:
    
    EMOJI_PATTERN = re.compile(
        "["
        "\U0001F600-\U0001F64F"
        "\U0001F300-\U0001F5FF"
        "\U0001F680-\U0001F6FF"
        "\U0001F1E0-\U0001F1FF"
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "]+",
        flags=re.UNICODE
    )
    
    def __init__(self, repository: Repository, qwen_client: QwenClient):
        self.repo = repository
        self.qwen = qwen_client
    
    async def analyze(
        self,
        contact_id: int,
        messages: list[Message],
        force_refresh: bool = False
    ) -> dict:
        if not force_refresh:
            cached = await self.repo.get_style_profile(contact_id)
            if cached and cached.message_count >= len(messages) * 0.9:
                console.print("[dim]Using cached style profile[/dim]")
                return cached.style_json
        
        my_messages = [m for m in messages if m.is_outgoing]
        
        if len(my_messages) < 10:
            console.print("[yellow]Warning: Less than 10 messages for analysis[/yellow]")
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
        ) as progress:
            task = progress.add_task("[cyan]Analyzing communication style...", total=None)
            
            metrics = self._calculate_metrics(my_messages)
            
            progress.update(task, description="[cyan]AI analyzing writing style...")
            qualitative = await self._ai_analyze_style(my_messages)
            
            progress.update(task, completed=True)
        
        style_profile = {
            "metrics": asdict(metrics),
            "qualitative": qualitative,
            "sample_messages": [m.text for m in my_messages[-20:]],
        }
        
        await self.repo.save_style_profile(StyleProfile(
            contact_id=contact_id,
            style_json=style_profile,
            analyzed_at=datetime.now(),
            message_count=len(my_messages)
        ))
        
        return style_profile
    
    def _calculate_metrics(self, messages: list[Message]) -> StyleMetrics:
        if not messages:
            return StyleMetrics(
                avg_message_length=0,
                avg_words_per_message=0,
                emoji_frequency=0,
                top_emojis=[],
                punctuation_style={},
                capitalization_ratio=0,
                common_phrases=[],
                message_count_analyzed=0
            )
        
        texts = [m.text for m in messages]
        
        lengths = [len(t) for t in texts]
        word_counts = [len(t.split()) for t in texts]
        
        all_emojis = []
        for text in texts:
            all_emojis.extend(self.EMOJI_PATTERN.findall(text))
        emoji_counter = Counter(all_emojis)
        
        punct_counts = {"!": 0, "?": 0, ".": 0, ",": 0, "...": 0}
        for text in texts:
            for p in punct_counts:
                punct_counts[p] += text.count(p)
        total_punct = sum(punct_counts.values()) or 1
        punct_ratios = {k: v / total_punct for k, v in punct_counts.items()}
        
        capitalized = sum(1 for t in texts if t and t[0].isupper())
        
        phrases = Counter()
        for text in texts:
            words = text.lower().split()
            for i in range(len(words) - 1):
                phrase = " ".join(words[i:i+2])
                phrases[phrase] += 1
            for i in range(len(words) - 2):
                phrase = " ".join(words[i:i+3])
                phrases[phrase] += 1
        
        common = [p for p, c in phrases.most_common(20) if c >= 3]
        
        return StyleMetrics(
            avg_message_length=sum(lengths) / len(lengths),
            avg_words_per_message=sum(word_counts) / len(word_counts),
            emoji_frequency=len(all_emojis) / len(texts),
            top_emojis=[e for e, _ in emoji_counter.most_common(10)],
            punctuation_style=punct_ratios,
            capitalization_ratio=capitalized / len(texts),
            common_phrases=common[:10],
            message_count_analyzed=len(messages)
        )
    
    async def _ai_analyze_style(self, messages: list[Message]) -> dict:
        sample_texts = [m.text for m in messages[-100:]]
        sample_str = "\n".join(f"- {t}" for t in sample_texts)
        
        system_prompt = """You are a communication style analyst. Analyze the following messages and extract the user's writing style characteristics.

Return a JSON object with these fields:
- formality: "formal", "informal", or "mixed"
- tone: list of 2-3 tone descriptors (e.g., "friendly", "sarcastic", "professional")
- language_features: list of notable features (e.g., "uses slang", "short sentences", "detailed explanations")
- greeting_style: how they typically greet (or "none" if they don't)
- closing_style: how they typically end messages (or "none")
- humor_level: "none", "occasional", or "frequent"
- directness: "direct", "indirect", or "mixed"

Return ONLY valid JSON, no other text."""

        user_prompt = f"""Analyze these messages from a user:

{sample_str}

Return the style analysis as JSON."""

        try:
            response = await self.qwen.chat([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ], temperature=0.3)
            
            response = response.strip()
            if response.startswith("```"):
                response = response.split("\n", 1)[1]
                response = response.rsplit("```", 1)[0]
            
            return json.loads(response)
            
        except Exception as e:
            console.print(f"[yellow]AI analysis failed: {e}, using defaults[/yellow]")
            return {
                "formality": "mixed",
                "tone": ["neutral"],
                "language_features": [],
                "greeting_style": "none",
                "closing_style": "none",
                "humor_level": "occasional",
                "directness": "direct"
            }
    
    def generate_style_prompt(self, style_profile: dict) -> str:
        metrics = style_profile.get("metrics", {})
        qualitative = style_profile.get("qualitative", {})
        samples = style_profile.get("sample_messages", [])
        
        prompt_parts = [
            "You are mimicking a specific person's communication style. Here are their characteristics:",
            ""
        ]
        
        if qualitative:
            prompt_parts.append(f"**Formality:** {qualitative.get('formality', 'mixed')}")
            prompt_parts.append(f"**Tone:** {', '.join(qualitative.get('tone', ['neutral']))}")
            prompt_parts.append(f"**Directness:** {qualitative.get('directness', 'direct')}")
            prompt_parts.append(f"**Humor:** {qualitative.get('humor_level', 'occasional')}")
            
            if features := qualitative.get('language_features'):
                prompt_parts.append(f"**Style features:** {', '.join(features)}")
        
        if metrics:
            avg_len = metrics.get('avg_words_per_message', 10)
            prompt_parts.append(f"\n**Average message length:** ~{int(avg_len)} words")
            
            if emojis := metrics.get('top_emojis'):
                prompt_parts.append(f"**Commonly used emojis:** {' '.join(emojis[:5])}")
            
            if phrases := metrics.get('common_phrases'):
                prompt_parts.append(f"**Common phrases:** {', '.join(phrases[:5])}")
        
        if samples:
            prompt_parts.append("\n**Example messages from this person:**")
            for msg in samples[-5:]:
                prompt_parts.append(f'- "{msg}"')
        
        prompt_parts.extend([
            "",
            "IMPORTANT RULES:",
            "1. Match their message length - keep responses similar in length to their typical messages",
            "2. Use their emoji patterns - include emojis if they use them, avoid if they don't",
            "3. Match their formality and tone exactly",
            "4. Use their common phrases when natural",
            "5. DO NOT be robotic or obviously AI - be natural and conversational",
            "6. Respond in the same language as the conversation",
        ])
        
        return "\n".join(prompt_parts)
