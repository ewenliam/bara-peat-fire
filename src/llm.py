"""Meta Model API client with a cost ledger, a hard spending cap and a response cache.

Every call is appended to logs/meta_usage.jsonl (tokens, cost, cache hit). Before a call
goes out, its worst-case cost (estimated input + full max_tokens output) is added to the
amount already spent; if that passes META_SPEND_CAP_USD the call is refused.

Responses are cached in cache/meta/ by a hash of (model, messages, tools, max_tokens,
response_format, reasoning_effort), so
rerunning the same agent run costs nothing and can be replayed without an API key.

Set in .env (optional):
    META_SPEND_CAP_USD=20   # default 20; the whole hackathon budget is 150
"""
import hashlib
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from openai.types.chat import ChatCompletion

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "logs" / "meta_usage.jsonl"
CACHE_DIR = ROOT / "cache" / "meta"
DEFAULT_CAP_USD = 20.0

# USD per 1M tokens, from https://dev.meta.ai/docs/pricing-rate-limits (Muse Spark Standard).
PRICES = {
    "muse-spark-1.3": {"input": 1.25, "cached_input": 0.15, "output": 4.25},
}


class BudgetExceeded(RuntimeError):
    pass


def spend_cap() -> float:
    load_dotenv(ROOT / ".env")
    return float(os.getenv("META_SPEND_CAP_USD", DEFAULT_CAP_USD))


def check_budget(worst_usd: float) -> None:
    already, cap = spent_usd(), spend_cap()
    if already + worst_usd > cap:
        raise BudgetExceeded(f"spent ${already:.4f}, this call could cost up to ${worst_usd:.4f}, "
                             f"cap is ${cap:.2f} (META_SPEND_CAP_USD)")


def append_ledger(entry: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def spent_usd() -> float:
    if not LEDGER.exists():
        return 0.0
    with LEDGER.open(encoding="utf-8") as f:
        return sum(json.loads(line)["cost_usd"] for line in f if line.strip())


def call_cost(model: str, prompt: int, cached: int, completion: int) -> float:
    p = PRICES[model]
    return ((prompt - cached) * p["input"] + cached * p["cached_input"]
            + completion * p["output"]) / 1e6


class MetaLLM:
    def __init__(self, use_cache: bool = True):
        load_dotenv(ROOT / ".env")
        self.model = os.environ["META_MODEL"]
        if self.model not in PRICES:
            raise SystemExit(f"no price for {self.model}; add it to PRICES in src/llm.py")
        self.use_cache = use_cache
        self._client = None
        self.run_cost = 0.0
        self.run_calls = []

    @property
    def client(self) -> OpenAI:
        if self._client is None:  # created lazily so cache-only replays need no key
            self._client = OpenAI(api_key=os.environ["META_API_KEY"],
                                  base_url=os.environ["META_BASE_URL"])
        return self._client

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             max_tokens: int = 4000, tag: str = "",
             response_format: dict | None = None,
             reasoning_effort: str | None = None) -> ChatCompletion:
        # max_tokens caps reasoning plus visible output combined (dev.meta.ai/docs/reasoning)
        request = {"model": self.model, "messages": messages, "max_tokens": max_tokens}
        if tools:
            request["tools"] = tools
        if response_format:  # structured output, e.g. {"type": "json_schema", ...}
            request["response_format"] = response_format
        if reasoning_effort:  # minimal, low, medium, high, xhigh, max
            request["reasoning_effort"] = reasoning_effort
        key = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()[:24]
        cached_path = CACHE_DIR / f"{key}.json"

        if self.use_cache and cached_path.exists():
            reply = ChatCompletion.model_validate_json(cached_path.read_text(encoding="utf-8"))
            self._log(tag, key, reply, cost=0.0, cache_hit=True, seconds=0.0)
            return reply

        est_input = len(json.dumps(request)) / 3  # rough chars-per-token, errs high
        check_budget(call_cost(self.model, int(est_input), 0, max_tokens))

        start = time.time()
        reply = self.client.chat.completions.create(**request)
        seconds = time.time() - start
        u = reply.usage
        details = getattr(u, "prompt_tokens_details", None)
        cached = (getattr(details, "cached_tokens", None) or 0) if details else 0
        cost = call_cost(self.model, u.prompt_tokens, cached, u.completion_tokens)

        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cached_path.write_text(reply.model_dump_json(), encoding="utf-8")
        self._log(tag, key, reply, cost=cost, cache_hit=False, seconds=seconds)
        return reply

    def _log(self, tag: str, key: str, reply: ChatCompletion, cost: float, cache_hit: bool,
             seconds: float) -> None:
        u = reply.usage
        out_details = getattr(u, "completion_tokens_details", None)
        entry = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "tag": tag,
            "model": self.model,
            "cache_key": key,
            "cache_hit": cache_hit,
            "prompt_tokens": u.prompt_tokens,
            "completion_tokens": u.completion_tokens,
            "reasoning_tokens": getattr(out_details, "reasoning_tokens", None) if out_details else None,
            "finish_reason": reply.choices[0].finish_reason,
            "seconds": round(seconds, 2),
            "cost_usd": round(cost, 6),
        }
        self.run_cost += cost
        self.run_calls.append(entry)
        append_ledger(entry)


if __name__ == "__main__":
    load_dotenv(ROOT / ".env")
    cap = spend_cap()
    print(f"logged Meta API spend: ${spent_usd():.4f} of cap ${cap:.2f}")
