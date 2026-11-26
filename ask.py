"""CLI sanity check: ask OptiBot a question against the synced vector store.

    python ask.py "How do I add a YouTube video?"

Uses the Responses API with the OptiBot system prompt (verbatim) and the
file_search tool, then prints the answer and the chunk files it cited.
"""

from __future__ import annotations

import os
import re
import sys

from dotenv import load_dotenv
from openai import OpenAI

from kbsync.vector_store import find_store

SYSTEM_PROMPT = """You are OptiBot, the customer-support bot for OptiSigns.com.
• Tone: helpful, factual, concise.
• Only answer using the uploaded docs.
• Max 5 bullet points; else link to the doc.
• Cite up to 3 "Article URL:" lines per reply."""

DEFAULT_MODEL = "gpt-5.5"  # newer gpt-6.x models emit raw inline citation markers in the Playground
# Inline citation markers (\ue200filecite\ue202...\ue201) that UIs render as chips.
CITATION_MARKER = re.compile("[ \t]*\ue200[^\ue201]*\ue201")


def strip_citation_markers(text: str) -> str:
    return CITATION_MARKER.sub("", text)


def main() -> int:
    load_dotenv()
    question = " ".join(sys.argv[1:]) or "How do I add a YouTube video?"
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY") or os.getenv("API_KEY"))
    store_id = find_store(client, os.getenv("VECTOR_STORE_ID"), os.getenv("VECTOR_STORE_NAME") or "optibot-kb")
    if not store_id:
        print("No vector store found; run main.py first.", file=sys.stderr)
        return 1

    response = client.responses.create(
        model=os.getenv("OPENAI_MODEL") or DEFAULT_MODEL,
        instructions=SYSTEM_PROMPT,
        input=question,
        tools=[{"type": "file_search", "vector_store_ids": [store_id], "max_num_results": 8}],
    )
    print(f"Q: {question}\n\n{strip_citation_markers(response.output_text).strip()}\n")
    cited = []
    for item in response.output:
        for part in getattr(item, "content", None) or []:
            for ann in getattr(part, "annotations", None) or []:
                if ann.type == "file_citation" and ann.filename not in cited:
                    cited.append(ann.filename)
    print("Cited chunk files:", ", ".join(cited) or "(none)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
