"""Shared LLM plumbing for StockSage agents -- prompt templating, Claude
calls, defensive JSON parsing. Agents only do reasoning; this is where the
mechanics of that reasoning live so they aren't duplicated across six files.
"""

import asyncio
import json
import os
import re
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv()

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

_client = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def wrap_untrusted(data) -> str:
    """Wraps externally-fetched content (news, web search, filing text) in a
    tag marking it as data to analyze, not instructions to follow -- paired
    with an explicit instruction to that effect in the prompt template
    itself. Reduces prompt-injection risk from fetched text; doesn't
    eliminate it.
    """
    text = data if isinstance(data, str) else json.dumps(data, default=str)
    return f"<untrusted_external_content>\n{text}\n</untrusted_external_content>"


def build_prompt(template_name: str, **variables) -> str:
    template = (_PROMPTS_DIR / template_name).read_text()
    for key, value in variables.items():
        placeholder = "{{" + key + "}}"
        text_value = value if isinstance(value, str) else json.dumps(value, default=str)
        template = template.replace(placeholder, text_value)
    return template


def _extract_text(message: "anthropic.types.Message") -> str:
    return "".join(block.text for block in message.content if block.type == "text")


def parse_json_response(text: str) -> dict:
    """Strip markdown code fences if present, then parse JSON -- Claude's
    "respond with ONLY this JSON" instruction doesn't always survive
    perfectly, so this falls back to extracting the outermost {...} block.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


_RETRYABLE_ERRORS = (
    anthropic.RateLimitError,
    anthropic.InternalServerError,
    anthropic.APITimeoutError,
    anthropic.APIConnectionError,
    anthropic.OverloadedError,
)
_LLM_MAX_RETRIES = 2
_LLM_RETRY_DELAY_SECONDS = 1


async def call_llm(prompt: str, model: str, max_tokens: int = 4096, tools: list | None = None) -> str:
    """Calls Claude and returns the concatenated text of all text-type content
    blocks. Web-search-enabled responses interleave text with server-side
    tool_use/tool_result blocks -- concatenating only the text blocks handles
    both search and non-search agents the same way.

    Retries on transient errors (rate limit, 5xx, overloaded, timeout,
    connection) -- same pattern as fetchers.py's USASpending retry. Does NOT
    retry on 4xx (bad request, auth, etc.) -- that's a code bug, not a
    transient failure, and retrying won't help.
    """
    client = _get_client()
    kwargs = {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
    if tools:
        kwargs["tools"] = tools

    for attempt in range(_LLM_MAX_RETRIES + 1):
        try:
            message = await client.messages.create(**kwargs)
            break
        except _RETRYABLE_ERRORS:
            if attempt >= _LLM_MAX_RETRIES:
                raise
            await asyncio.sleep(_LLM_RETRY_DELAY_SECONDS * (attempt + 1))

    if message.stop_reason == "max_tokens":
        raise RuntimeError(
            f"Claude response truncated at max_tokens={max_tokens} (model={model}) -- raise max_tokens for this call."
        )
    return _extract_text(message)
