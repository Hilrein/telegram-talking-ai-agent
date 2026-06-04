import json
import logging
import re
from typing import Any, Optional

from openai import AsyncOpenAI
from rich.console import Console

from .tools.registry import TOOL_DEFINITIONS, execute_tool

logger = logging.getLogger(__name__)
console = Console()

NVIDIA_DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_DEFAULT_MODEL = "nvidia/llama-3.1-nemotron-ultra-253b-v1"

# Safety cap: maximum rounds of tool-call ↔ tool-result exchanges per request.
MAX_TOOL_ROUNDS = 10


class NvidiaClient:
    def __init__(
        self,
        api_key: str,
        model: str = NVIDIA_DEFAULT_MODEL,
        base_url: str = NVIDIA_DEFAULT_BASE_URL,
    ):
        self.model = model
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
        )

    async def __aenter__(self) -> "NvidiaClient":
        return self

    async def __aexit__(self, *args) -> None:
        await self._client.close()

    # ── Simple chat (unchanged) ──────────────────────────────────

    async def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> str:
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            content = response.choices[0].message.content
            return content or ""

        except Exception as e:
            logger.error("NVIDIA NIM API error: %s", e)
            console.print(f"[red]NVIDIA NIM API Error: {e}[/red]")
            raise

    # ── Agent chat with function-calling loop ────────────────────

    async def agent_chat(
        self,
        messages: list[dict],
        session_id: str,
        agent_repo: Any,
        *,
        tools: Optional[list[dict]] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> str:
        """Send a chat request with tool-calling support.

        The method implements a multi-turn loop:
          1. Send ``messages`` + ``tools`` to the model.
          2. If the model responds with ``tool_calls``, log each call in
             ``agent_history``, dispatch via the registry, log the result,
             append both to the conversation, and repeat from step 1.
          3. Once the model returns a plain text response (no tool_calls),
             log it and return the text to the caller.

        Parameters
        ----------
        messages:
            The conversation history (system + user + prior turns).
        session_id:
            Identifier used to group log entries in ``agent_history``.
        agent_repo:
            An open ``AgentRepository`` instance for logging.
        tools:
            Tool definitions in OpenAI format.  Defaults to the full
            ``TOOL_DEFINITIONS`` from the registry.
        temperature:
            Sampling temperature for the model.
        max_tokens:
            Maximum tokens in the model's response.

        Returns
        -------
        str
            The model's final text answer after all tools have been resolved.
        """
        if tools is None:
            tools = TOOL_DEFINITIONS

        active_messages = list(messages)

        for round_idx in range(MAX_TOOL_ROUNDS):
            try:
                response = await self._client.chat.completions.create(
                    model=self.model,
                    messages=active_messages,
                    tools=tools if tools else None,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except Exception as exc:
                logger.error("NVIDIA NIM API error (agent_chat round %d): %s", round_idx, exc)
                console.print(f"[red]NVIDIA NIM API Error: {exc}[/red]")
                raise

            choice = response.choices[0]
            assistant_message = choice.message

            # ── Case 1: model wants to call tools ────────────────
            if assistant_message.tool_calls:
                # Append the raw assistant message (with tool_calls) to history
                assistant_dict = _message_to_dict(assistant_message)
                active_messages.append(assistant_dict)

                # Log the assistant's tool-call intent
                await agent_repo.save_history_message(
                    session_id=session_id,
                    role="assistant",
                    content=json.dumps(
                        [
                            {
                                "tool_call_id": tc.id,
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            }
                            for tc in assistant_message.tool_calls
                        ],
                        ensure_ascii=False,
                    ),
                )

                # Execute each requested tool
                for tool_call in assistant_message.tool_calls:
                    func_name = tool_call.function.name
                    try:
                        func_args = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError:
                        func_args = {}
                        logger.warning(
                            "Could not parse arguments for tool '%s': %s",
                            func_name,
                            tool_call.function.arguments,
                        )

                    logger.info(
                        "Executing tool '%s' with args: %s",
                        func_name,
                        func_args,
                    )
                    tool_result = execute_tool(func_name, func_args)

                    # Log the tool result
                    await agent_repo.save_history_message(
                        session_id=session_id,
                        role="tool",
                        content=json.dumps(
                            {"tool_call_id": tool_call.id, "name": func_name, "result": tool_result},
                            ensure_ascii=False,
                        ),
                    )

                    # Append the tool response so the model can see it
                    active_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": tool_result,
                    })

                # Continue the loop — the model will process tool results
                continue

            # ── Case 2: model produced a final text answer (or a fallback JSON tool call) ───────
            final_text = assistant_message.content or ""
            
            # Fallback for models (like Gemma-4) that emit JSON instead of native tool_calls
            fallback_tool_executed = False
            
            # Try to find a JSON object in the text
            json_match = re.search(r'\{.*"type"\s*:\s*"function".*\}', final_text, re.DOTALL)
            if not json_match:
                json_match = re.search(r'\{.*"name"\s*:\s*".*?".*\}', final_text, re.DOTALL)
                
            if json_match:
                try:
                    parsed_json = json.loads(json_match.group(0))
                    if parsed_json.get("type") == "function" and "name" in parsed_json:
                        # Log the assistant's tool-call intent
                        await agent_repo.save_history_message(
                            session_id=session_id,
                            role="assistant",
                            content=json.dumps([parsed_json], ensure_ascii=False)
                        )
                        
                        func_name = parsed_json["name"]
                        func_args = parsed_json.get("parameters", {})
                        if isinstance(func_args, str):
                            try:
                                func_args = json.loads(func_args)
                            except json.JSONDecodeError:
                                func_args = {}
                                
                        logger.info("Executing fallback tool '%s' with args: %s", func_name, func_args)
                        tool_result = execute_tool(func_name, func_args)
                        
                        # Log tool result
                        tool_call_id = "call_" + str(round_idx)
                        await agent_repo.save_history_message(
                            session_id=session_id,
                            role="tool",
                            content=json.dumps(
                                {"tool_call_id": tool_call_id, "name": func_name, "result": tool_result},
                                ensure_ascii=False,
                            ),
                        )
                        
                        # Append to active messages
                        active_messages.append({"role": "assistant", "content": final_text})
                        active_messages.append({"role": "user", "content": f"System (Tool Result for {func_name}):\n{tool_result}"})
                        
                        fallback_tool_executed = True
                except json.JSONDecodeError:
                    pass

            if fallback_tool_executed:
                continue

            await agent_repo.save_history_message(
                session_id=session_id,
                role="assistant",
                content=final_text,
            )

            return final_text

        # Exhausted all rounds without a final answer
        logger.warning("agent_chat: hit MAX_TOOL_ROUNDS (%d) without a final answer", MAX_TOOL_ROUNDS)
        return "I'm sorry, I wasn't able to complete the request within the allowed number of steps."


# ── Helpers ──────────────────────────────────────────────────────────

def _message_to_dict(message: Any) -> dict:
    """Convert an OpenAI ChatCompletionMessage to a plain dict.

    Needed because the SDK returns pydantic-like objects that must be
    serialised before appending to the messages list.
    """
    result: dict[str, Any] = {
        "role": message.role,
    }

    if message.content:
        result["content"] = message.content

    if message.tool_calls:
        result["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in message.tool_calls
        ]

    return result

