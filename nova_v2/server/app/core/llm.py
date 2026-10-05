"""
llm.py - the one model client NOVA uses.

Every model call goes through here, over the OpenAI chat-completions protocol,
so the model is whatever LLM_BASE_URL serves: the self-hosted Qwen on
the self-hosted Qwen in deployment (deploy/compose.yml), anything compatible in
development.

Two shapes of call:
    complete()  free text back (the phraser, extractor and titler parse their
                own JSON out of it, as they always have).
    parse()     a pydantic model back, for the callers that used Anthropic's
                messages.parse (the notes summariser and the Persona judge).

The tool loop in intent_surface.py uses client() directly, because it needs the
whole response - tool calls, finish_reason, usage - not just the text.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from typing import Any, Iterator, Optional, TypeVar

from openai import BadRequestError, OpenAI
from pydantic import BaseModel, ValidationError

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:18020/v1")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "") or "local"
MODEL = os.environ.get("LLM_MODEL", "qwen3.8-27b")

# Qwen thinks before answering. Free-text calls let it: the server's reasoning
# parser returns the thinking separately (message.reasoning_content) and the
# answer alone as content. With thinking switched off this model still
# reasons, but as plain text in the reply, or stops after a word or two - so
# "off" is only safe where the reply is grammar-constrained JSON (parse()),
# which leaves no room for either.
THINKING: dict[str, Any] = {"chat_template_kwargs": {"enable_thinking": True}}
NO_THINKING: dict[str, Any] = {"chat_template_kwargs": {"enable_thinking": False}}

# Thinking spends completion tokens before the answer starts; this much on
# top of what the answer itself needs.
THINKING_TOKENS = 2048

M = TypeVar("M", bound=BaseModel)


class LLMUnavailable(RuntimeError):
    """The model couldn't give a usable answer: unreachable, cut off by the
    token limit, or still not valid JSON after one retry."""


@lru_cache(maxsize=8)
def client(timeout: float = 60.0, max_retries: int = 1) -> OpenAI:
    """A client per (timeout, retries) pair, shared - the voice path wants a
    short leash, the background passes a long one."""
    return OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY,
                  timeout=timeout, max_retries=max_retries)


# --- voice first ---------------------------------------------------------------
# One GPU serves every call, and two at once slow both: a warm voice step took
# 6.4 s instead of 2.4 s beside a background request. So work nobody is
# waiting on holds back while a voice turn runs - before each call, not
# during one, since a request the server has started can't be paused. The
# wait is capped so nothing starves, and so an ambient /event still answers
# inside the phone's 60 s read timeout.
VOICE_YIELD_MAX_S = 15.0

_voice_turns = 0
_voice_idle = threading.Condition()
_in_voice_turn: ContextVar[bool] = ContextVar("in_voice_turn", default=False)


@contextmanager
def voice_turn() -> Iterator[None]:
    """A voice turn is running for the length of the block. Model calls made
    inside it never wait; everyone else's wait for it (yield_to_voice). New
    threads don't inherit the mark, so work a turn hands to a thread waits too."""
    global _voice_turns
    token = _in_voice_turn.set(True)
    with _voice_idle:
        _voice_turns += 1
    try:
        yield
    finally:
        with _voice_idle:
            _voice_turns -= 1
            _voice_idle.notify_all()
        _in_voice_turn.reset(token)


def yield_to_voice(max_wait: float = VOICE_YIELD_MAX_S) -> None:
    """Wait, at most max_wait seconds, until no voice turn is running. A call
    from inside a voice turn goes straight through."""
    if _in_voice_turn.get():
        return
    deadline = time.monotonic() + max_wait
    with _voice_idle:
        while _voice_turns:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            _voice_idle.wait(remaining)


_THINK = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def strip_thinking(text: str | None) -> str:
    """Drop thinking that reached the reply text, for a server without a
    reasoning parser: a <think>...</think> block, everything before a lone
    </think> (the opening tag was in the prompt), and an unclosed block."""
    text = _THINK.sub("", text or "")
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    if "<think>" in text:
        text = text.split("<think>", 1)[0]
    return text.strip()


def complete(
    system: str,
    user: str,
    *,
    max_tokens: int = 1024,
    temperature: float = 0.2,
    timeout: float = 60.0,
    max_retries: int = 1,
    llm: Optional[OpenAI] = None,
    model: Optional[str] = None,
) -> str:
    """One system + user exchange; the reply's text. `max_tokens` is for the
    answer; the model's thinking gets THINKING_TOKENS on top."""
    yield_to_voice()
    response = (llm or client(timeout, max_retries)).chat.completions.create(
        model=model or MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=max_tokens + THINKING_TOKENS,
        temperature=temperature,
        extra_body=THINKING,
    )
    choice = response.choices[0]
    if choice.finish_reason == "length":
        raise LLMUnavailable(f"reply hit max_tokens={max_tokens}")
    return strip_thinking(choice.message.content)


def extract_json(text: str) -> str:
    """The JSON in a reply: code fences and think blocks removed, then from the
    first { or [ to the matching last } or ]. Models add a sentence around
    JSON even when asked not to."""
    text = strip_thinking(text)
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        return text
    start = min(starts)
    end = text.rfind("}" if text[start] == "{" else "]")
    return text[start:end + 1] if end > start else text[start:]


def parse(
    system: str,
    user: str,
    model_cls: type[M],
    *,
    max_tokens: int = 1024,
    timeout: float = 60.0,
    max_retries: int = 1,
    llm: Optional[OpenAI] = None,
    model: Optional[str] = None,
) -> M:
    """A reply validated into `model_cls`.

    Asks for the schema through response_format (llama.cpp, vLLM and SGLang
    all constrain decoding to it); a server that rejects response_format gets
    the schema in the prompt instead. Either way the reply is validated, and a
    reply that doesn't validate is retried once with the error attached."""
    llm = llm or client(timeout, max_retries)
    schema = model_cls.model_json_schema()
    system_full = (
        f"{system}\n\nReply with only a JSON object matching this JSON Schema, "
        f"and nothing else:\n{json.dumps(schema)}"
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_full},
        {"role": "user", "content": user},
    ]
    response_format: Optional[dict[str, Any]] = {
        "type": "json_schema",
        "json_schema": {"name": model_cls.__name__, "schema": schema},
    }
    last_error = ""
    for attempt in range(2):
        yield_to_voice()
        kwargs: dict[str, Any] = dict(
            model=model or MODEL, messages=messages, max_tokens=max_tokens,
            temperature=0, extra_body=NO_THINKING,
        )
        if response_format is not None:
            kwargs["response_format"] = response_format
        try:
            response = llm.chat.completions.create(**kwargs)
        except BadRequestError:
            if response_format is None:
                raise
            # This server doesn't do structured output; the prompt carries it.
            response_format = None
            kwargs.pop("response_format")
            response = llm.chat.completions.create(**kwargs)
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise LLMUnavailable(f"reply hit max_tokens={max_tokens}")
        raw = choice.message.content or ""
        try:
            return model_cls.model_validate_json(extract_json(raw))
        except ValidationError as exc:
            last_error = str(exc)
            if attempt == 0:
                messages = [
                    *messages,
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": (
                        "That reply didn't match the schema:\n"
                        f"{last_error[:800]}\nReply again with only the corrected JSON."
                    )},
                ]
    raise LLMUnavailable(f"no valid {model_cls.__name__} after a retry: {last_error[:200]}")


# --- tool calls written as text ---------------------------------------------

_TOOL_CALL_TEXT = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)


def tool_calls_in_text(text: str | None) -> list[dict[str, Any]]:
    """Tool calls a server left in the reply text as Qwen's own
    <tool_call>{"name":..., "arguments":...}</tool_call> markup instead of
    returning them as tool_calls. Each comes back in the OpenAI message shape,
    with a made-up id. Blocks that aren't a JSON object with a name are
    skipped."""
    calls = []
    for body in _TOOL_CALL_TEXT.findall(text or ""):
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = _xml_tool_call(body)
            if data is None:
                continue
        if not isinstance(data, dict) or not isinstance(data.get("name"), str):
            continue
        arguments = data.get("arguments", {})
        calls.append({
            "id": f"call_{uuid.uuid4().hex[:12]}",
            "type": "function",
            "function": {
                "name": data["name"],
                "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments),
            },
        })
    return calls


_XML_FUNCTION = re.compile(r"<function=([\w.-]+)>(.*?)(?:</function>|$)", re.DOTALL)
_XML_PARAMETER = re.compile(r"<parameter=([\w.-]+)>\n?(.*?)\n?</parameter>", re.DOTALL)


def _xml_tool_call(body: str) -> dict[str, Any] | None:
    """Qwen3-Coder's tool-call markup, which the served parser (qwen3_coder)
    normally turns into tool_calls itself:
        <function=name><parameter=key>value</parameter>...</function>
    Values that read as JSON (numbers, lists, objects) are decoded; the rest
    stay strings."""
    match = _XML_FUNCTION.search(body)
    if not match:
        return None
    arguments: dict[str, Any] = {}
    for key, raw in _XML_PARAMETER.findall(match.group(2)):
        raw = raw.strip()
        try:
            value = json.loads(raw)
            arguments[key] = value if not isinstance(value, str) else raw
        except json.JSONDecodeError:
            arguments[key] = raw
    return {"name": match.group(1), "arguments": arguments}


def reasoning_of(message: Any) -> str:
    """The thinking a server returned apart from the answer. vLLM has called
    the field reasoning_content and, more recently, reasoning."""
    for name in ("reasoning_content", "reasoning"):
        value = getattr(message, name, None)
        if value is None:
            value = (getattr(message, "model_extra", None) or {}).get(name)
        if isinstance(value, str) and value:
            return value
    return ""


def without_tool_call_text(text: str | None) -> str:
    return _TOOL_CALL_TEXT.sub("", text or "").strip()
