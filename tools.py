"""Client-side tools: we define the schema, Claude decides when to call them,
and OUR code runs them. Claude never touches the bank's systems directly.
"""
import datetime
import voyageai

vo = voyageai.Client()
STALE_AFTER_DAYS = 180

TOOLS = [
    {
        "name": "get_transfer_status",
        "description": (
            "Look up the status of one of the signed-in user's transfers. "
            "Use when the user asks where their money is or why a transfer is pending."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "transfer_id": {
                    "type": "string",
                    "description": "Transfer ID; omit to get the user's most recent transfers",
                }
            },
        },
    },
    {
        "name": "search_help_docs",
        "description": (
            "Search the bank's approved help documentation. Use for policy and "
            "how-to questions (limits, timelines, fees). Answer only from the results."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "escalate_to_human",
        "description": "Hand the conversation to a human agent. Use when unsure, or the user asks.",
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    },
]


def get_transfer_status(ctx, transfer_id: str | None = None) -> dict:
    # IMPORTANT: user_id comes from the authenticated session (ctx),
    # never from the model, so the bot can only see this user's transfers.
    return ctx.transfers_api.lookup(user_id=ctx.user_id, transfer_id=transfer_id)


def search_help_docs(ctx, query: str, k: int = 5) -> list[dict]:
    qvec = vo.embed([query], model="voyage-3", input_type="query").embeddings[0]
    rows = ctx.db.execute(
        """SELECT title, url, content, last_updated
           FROM doc_chunks
           ORDER BY embedding <=> %s::vector    -- cosine distance
           LIMIT %s""",
        (str(qvec), k),
    ).fetchall()
    now = datetime.datetime.now(datetime.timezone.utc)
    return [
        {
            "title": title,
            "url": url,
            "content": content,
            "possibly_stale": (now - updated).days > STALE_AFTER_DAYS,
        }
        for title, url, content, updated in rows
    ]


def escalate_to_human(ctx, reason: str) -> dict:
    ticket = ctx.support_api.create_ticket(
        user_id=ctx.user_id, conversation_id=ctx.conversation_id, reason=reason
    )
    ctx.db.execute(
        "UPDATE conversations SET status = 'escalated' WHERE id = %s", (ctx.conversation_id,)
    )
    return {"ticket_id": ticket.id, "message": "A support agent will follow up."}


HANDLERS = {
    "get_transfer_status": get_transfer_status,
    "search_help_docs": search_help_docs,
    "escalate_to_human": escalate_to_human,
}
