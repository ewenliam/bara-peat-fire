"""Check the Meta Model API key works with one short Bahasa Indonesia request.

The API is OpenAI-SDK compatible. Set in .env:
    META_API_KEY=...
    META_BASE_URL=https://api.meta.ai/v1
    META_MODEL=<Muse Spark model id copied from https://dev.meta.ai/docs/models>
"""
import os

from dotenv import load_dotenv
from openai import OpenAI


def main() -> None:
    load_dotenv()
    for name in ("META_API_KEY", "META_BASE_URL", "META_MODEL"):
        if not os.getenv(name):
            raise SystemExit(f"{name} is missing from .env")

    client = OpenAI(api_key=os.environ["META_API_KEY"], base_url=os.environ["META_BASE_URL"])
    reply = client.chat.completions.create(
        model=os.environ["META_MODEL"],
        messages=[{
            "role": "user",
            "content": "Dalam satu kalimat bahasa Indonesia: mengapa kebakaran gambut sulit dipadamkan?",
        }],
        # Muse Spark reasons before answering and reasoning counts toward max_tokens:
        # at 120 it spent 118 tokens reasoning and returned no content.
        max_tokens=2000,
    )
    print(reply.choices[0].message.content)
    print(f"finish_reason={reply.choices[0].finish_reason}, usage={reply.usage.completion_tokens} tokens")


if __name__ == "__main__":
    main()
