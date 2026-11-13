"""Questions about who made the bot / its developer / its code are answered by CODE, not by the AI:
the reply always tags the developer, never names an AI company or model, and never reveals code, prompts or keys."""
from __future__ import annotations

import os
import re

DEVELOPER_ID = int(os.getenv("DEVELOPER_ID", "8445198778") or 8445198778)

_MAKE = r"(?:made|make|created?|built|build|develop(?:ed)?|coded?|programmed?|designed?|trained|owns?|invented|banaya|banaye|banai|bnaya|bnaye|bnai|banaaya)"
_YOU = r"(?:you|u|ya|tu|tumhe|tumhen|tujhe|tujhko|tumko|aapko|apko|this bot|the bot|ur|your)"
_ROLE = r"(?:developer|dev|creator|owner|maker|author|programmer|coder|founder|admin of (?:you|this bot)|father|dad|papa|boss|master|malik)"

_DEV = [re.compile(p, re.I) for p in (
    rf"\bwho\b.{{0,25}}\b{_MAKE}\b.{{0,25}}\b{_YOU}\b",                 # who made you / who created this bot
    rf"\bwho(?:'s| is| r)?\b.{{0,15}}\b(?:your|ur|the bot'?s?|this bot'?s?)\b.{{0,10}}\b{_ROLE}\b",   # who is your developer
    rf"\b(?:your|ur|tera|tumhara|tumhare|teri|aapka|apka|bot ka|bot ke)\b.{{0,12}}\b{_ROLE}\b",        # your developer / tera owner
    rf"\b{_ROLE}\b.{{0,12}}\b(?:kaun|kon|kaun hai|kon hai|kaun h|kon h|who|name|batao|bata)\b",       # developer kaun hai
    rf"\b(?:kisne|kis ne|kisne ne|kon ne|kaun ne)\b.{{0,25}}\b{_MAKE}\b",                          # kisne banaya
    rf"\b{_MAKE}\b.{{0,10}}\b(?:kisne|kis ne|kon ne)\b",
    rf"\b(?:which|what)\b.{{0,15}}\b(?:company|model|ai|llm|gpt|engine)\b.{{0,25}}\b(?:you|u|are you|powers? you|behind you|use|using)\b",
    rf"\b(?:are|r)\s+(?:you|u)\b.{{0,12}}\b(?:chat\s?gpt|gpt|gemini|claude|llama|bard|copilot|deepseek|openai|anthropic|google)\b",
    rf"\b(?:behind|backend of)\s+(?:you|u|this bot)\b",
)]
_CODE = [re.compile(p, re.I) for p in (
    r"\b(?:source\s?code|your code|ur code|bot code|the code|code of (?:you|this bot)|github|repo(?:sitory)?|script of)\b",
    r"\b(?:system\s?prompt|your prompt|ur prompt|your instructions?|initial prompt|hidden prompt|ignore (?:all |your )?(?:previous |prior )?instructions)\b",
    r"\b(?:api\s?keys?|bot\s?token|\.env|env file|secrets?|database|db file)\b.{0,25}\b(?:give|send|share|show|reveal|tell|de|do|bhej|dikha|batao)\b",
    r"\b(?:give|send|share|show|reveal|tell|de|do|bhej|dikha|batao)\b.{0,25}\b(?:api\s?keys?|bot\s?token|\.env|env file|secrets?|database|your code|source)\b",
)]


# Words that MIGHT mean "who made / owns / runs you" or "show me your code" in some unusual phrasing. A message that has one of them
# (and was not caught above) is double-checked by one tiny AI call. Everything else is normal chat and costs nothing extra.
_LOOSE = re.compile(
    r"\b(?:made|make|maker|created?|creator|built|build|develop\w*|dev|devs|coded?|coder|program\w*|engineer\w*|owner|owns?|own|train\w*|"
    r"behind|runs?|running|hosts?|hosted|operat\w*|boss|master|papa|father|dad|brain|author|company|model|llm|provider|powered|powers?|backend|founder|code|source|prompt|instruction\w*|github|repo|token|keys?|api|env|database|"
    r"banaya\w*|bnaya\w*|banai|banaye|kisne|malik|maalik|rachayita|nirmata)\b", re.I)

# The AI itself naming a company / model as its maker => never send that; the fixed developer answer goes out instead.
_LEAK = re.compile(
    r"(?:\b(?:made|created|trained|built|developed|powered|run|backed|designed)\s+(?:and\s+\w+\s+)?by\s+(?:google|openai|anthropic|meta|mistral(?:\s?ai)?|groq|cloudflare|microsoft|deepseek|alibaba|xai|nvidia)\b|"
    r"\b(?:i\s*(?:am|'m)|as|this is|i\s+run\s+on|i\s+am\s+powered\s+by)\s+(?:a\s+|an\s+)?(?:large\s+language\s+model|chat\s?gpt|gpt-?\d\w*|gemini|claude|llama\s?\d*|bard|copilot|deepseek|mistral|qwen)\b|"
    r"\b(?:language model|llm)\s+(?:trained|developed|made|created)\s+by\b)", re.I)


def maybe_identity(text: str) -> bool:
    """Cheap pre-check: could this message be an identity / code question worded in an unusual way?"""
    return bool(_LOOSE.search(text or ""))


def leaks_provider(answer: str) -> bool:
    return bool(_LEAK.search(answer or ""))


def classify(text: str) -> str | None:
    """'code' = wants code / prompt / keys, 'dev' = asks who made / owns the bot, None = normal chat."""
    t = " ".join((text or "").split())
    if not t:
        return None
    if any(r.search(t) for r in _CODE):
        return "code"
    if any(r.search(t) for r in _DEV):
        return "dev"
    return None


def dev_reply(kind: str, tag: str) -> str:
    """tag = HTML mention of the developer."""
    if kind == "code":
        return f"🔒 My code, prompts and settings are private and I can't share them. For anything about me, ask my developer {tag} 🦋"
    return f"💖 I was made by my developer {tag}! Built with love for this community. 🦋\nIf you want to know more about me, ask {tag} directly."
