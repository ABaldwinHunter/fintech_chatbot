"""Background indexing job: pull approved Confluence pages, chunk, embed, store.

Run nightly (or on a Confluence webhook when a page changes).
"""
import os
import requests
import psycopg
import voyageai  # Anthropic doesn't ship an embedding model; Voyage is a common pairing

CONFLUENCE = os.environ["CONFLUENCE_BASE_URL"]
AUTH = (os.environ["CONFLUENCE_USER"], os.environ["CONFLUENCE_TOKEN"])
EMBED_MODEL = "voyage-3"
CHUNK_WORDS = 300       # roughly 400 tokens
OVERLAP_WORDS = 50      # overlap so an answer split across a boundary isn't lost

vo = voyageai.Client()


def approved_pages(conn):
    """Only index what's on the allowlist."""
    rows = conn.execute(
        "SELECT source_type, source_id, owner_email FROM approved_sources"
    ).fetchall()
    for source_type, source_id, owner in rows:
        if source_type == "confluence_space":
            resp = requests.get(
                f"{CONFLUENCE}/wiki/rest/api/content",
                params={"spaceKey": source_id, "expand": "body.storage,version", "limit": 100},
                auth=AUTH,
            )
            for page in resp.json()["results"]:
                yield page, owner
        elif source_type == "confluence_page":
            page = requests.get(
                f"{CONFLUENCE}/wiki/rest/api/content/{source_id}",
                params={"expand": "body.storage,version"},
                auth=AUTH,
            ).json()
            yield page, owner


def chunk(text: str) -> list[str]:
    words = text.split()
    step = CHUNK_WORDS - OVERLAP_WORDS
    return [" ".join(words[i:i + CHUNK_WORDS]) for i in range(0, len(words), step)]


def strip_html(html: str) -> str:
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "html.parser").get_text(" ")


def index_page(conn, page: dict, owner: str):
    text = strip_html(page["body"]["storage"]["value"])
    chunks = chunk(text)
    if not chunks:
        return
    # One embedding vector per chunk: similar meaning -> nearby vectors
    vectors = vo.embed(chunks, model=EMBED_MODEL, input_type="document").embeddings

    with conn.transaction():
        # Replace old chunks for this page so edits don't leave stale fragments
        conn.execute("DELETE FROM doc_chunks WHERE page_id = %s", (page["id"],))
        for i, (content, vec) in enumerate(zip(chunks, vectors)):
            conn.execute(
                """INSERT INTO doc_chunks
                   (page_id, title, url, chunk_index, content, embedding, last_updated, owner_email)
                   VALUES (%s, %s, %s, %s, %s, %s::vector, %s, %s)""",
                (
                    page["id"], page["title"],
                    f"{CONFLUENCE}/wiki{page['_links']['webui']}",
                    i, content, str(vec),
                    page["version"]["when"], owner,
                ),
            )


if __name__ == "__main__":
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        for page, owner in approved_pages(conn):
            index_page(conn, page, owner)

