"""The agent loop. Our backend owns the loop: send messages to Claude,
run any tools it asks for, send results back, repeat until it answers.
"""
import json
import re
import time
import anthropic
from tools import TOOLS, HANDLERS

client = anthropic.Anthropic()
MODEL = "claude-sonnet-5-5"
PROMPT_VERSION = "v3"
MAX_TOOL_ROUNDS = 5          # guardrail against runaway loops / cost
INACTIVITY_MINUTES = 30      # conversations auto-expire after this

SYSTEM = """You are the support assistant for Acme Bank.
- Answer policy questions ONLY from search_help_docs results, and cite the doc URL.
- If a result is marked possibly_stale, say the info may be out of date.
- If nothing relevant comes back, say you don't know and offer a human.
- Never ask for full account numbers, passwords, or SSNs.
- You can view transfers but cannot move money."""


def redact(text: str) -> str:
    """Strip obvious PII before anything is persisted."""
    text = re.sub(r"\b\d{3}-\d{2}-\d{4}\b", "[SSN]", text)
    text = re.sub(r"\b\d{9,17}\b", "[ACCOUNT_NUMBER]", text)
    return text


def load_history(db, conversation_id) -> list[dict]:
    """Same call powers 'reopen the browser / switch to phone and resume'."""
    rows = db.execute(
        "SELECT role, content_json FROM messages WHERE conversation_id = %s ORDER BY seq",
        (conversation_id,),
    ).fetchall()
    return [{"role": role, "content": content} for role, content in rows]


def save_message(db, conversation_id, role, content):
    db.execute(
        """INSERT INTO messages (conversation_id, seq, role, content_json)
           VALUES (%s, (SELECT COALESCE(MAX(seq), 0) + 1 FROM messages
                        WHERE conversation_id = %s), %s, %s)""",
        (conversation_id, conversation_id, role, json.dumps(content)),
    )
    db.execute(
        "UPDATE conversations SET last_message_at = now() WHERE id = %s", (conversation_id,)
    )


def log_request(db, conversation_id, response, latency_ms):
    db.execute(
        """INSERT INTO llm_requests (conversation_id, model, prompt_version,
               input_tokens, output_tokens, latency_ms, stop_reason)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (conversation_id, MODEL, PROMPT_VERSION,
         response.usage.input_tokens, response.usage.output_tokens,
         latency_ms, response.stop_reason),
    )


def handle_user_message(ctx, user_text: str) -> str:
    messages = load_history(ctx.db, ctx.conversation_id)
    user_msg = [{"type": "text", "text": redact(user_text)}]
    messages.append({"role": "user", "content": user_msg})
    save_message(ctx.db, ctx.conversation_id, "user", user_msg)

    for _ in range(MAX_TOOL_ROUNDS):
        start = time.monotonic()
        response = client.messages.create(
            model=MODEL, max_tokens=1024, system=SYSTEM, tools=TOOLS, messages=messages
        )
        log_request(ctx.db, ctx.conversation_id, response,
                    int((time.monotonic() - start) * 1000))

        assistant_content = [block.model_dump() for block in response.content]
        messages.append({"role": "assistant", "content": assistant_content})
        save_message(ctx.db, ctx.conversation_id, "assistant", assistant_content)

        if response.stop_reason != "tool_use":
            # Final answer: return the text to the frontend
            return "".join(b.text for b in response.content if b.type == "text")

        # Claude asked for one or more tools: run them, send results back
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                try:
                    result = HANDLERS[block.name](ctx, **block.input)
                    tool_results.append({"type": "tool_result",
                                         "tool_use_id": block.id,
                                         "content": json.dumps(result, default=str)})
                except Exception as e:
                    tool_results.append({"type": "tool_result",
                                         "tool_use_id": block.id,
                                         "content": f"Tool failed: {e}",
                                         "is_error": True})
        messages.append({"role": "user", "content": tool_results})
        save_message(ctx.db, ctx.conversation_id, "user", tool_results)

    return "Sorry, I'm having trouble with this one. Let me connect you with a person."


def expire_idle_conversations(db):
    """Cron job: answers 'how do we know the conversation ended?'"""
    db.execute(
        """UPDATE conversations SET status = 'expired'
           WHERE status = 'open'
             AND last_message_at < now() - make_interval(mins => %s)""",
        (INACTIVITY_MINUTES,),
    )


def record_feedback(db, conversation_id, resolved: bool):
    """End-of-chat thumbs up/down -> resolution rate metric."""
    db.execute(
        "UPDATE conversations SET resolved = %s, status = 'closed' WHERE id = %s",
        (resolved, conversation_id),
    )
