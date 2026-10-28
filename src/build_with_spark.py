"""Builder loop: Muse Spark writes one module from a spec and its acceptance tests.

Sends the spec and the tests to Muse Spark, takes the single Python code block from the
reply, writes it to the target path, and saves the full prompt and reply under
logs/builds/ so every generated line can be traced to the call that produced it. Spend is
capped per module (on top of the global cap in src/llm.py). Claude writes the specs and
tests; this script never edits them.

Usage:
    python src/build_with_spark.py specs/whatsapp_webhook.md tests/test_whatsapp_webhook.py \
        src/whatsapp_webhook.py --cap 5
    python src/build_with_spark.py ... --feedback logs/builds/whatsapp_webhook_feedback.md
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

from llm import LEDGER, MetaLLM, call_cost, check_budget

ROOT = Path(__file__).resolve().parents[1]
BUILD_LOG = ROOT / "logs" / "builds"
MAX_TOKENS = 8000

SYSTEM = """You are the builder for Bara, a peat fire forecast and crew dispatch tool.
Write exactly one Python module that meets the spec and passes the acceptance tests.
Reply with one ```python code block holding the whole file, then at most five short
bullet lines on design choices. Do not change the tests. Do not add dependencies the spec
does not allow. Use plain ASCII punctuation; never use em dashes."""


def module_spend(tag: str) -> float:
    if not LEDGER.exists():
        return 0.0
    with LEDGER.open(encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    return sum(r["cost_usd"] for r in rows if r["tag"] == tag)


def extract_code(text: str) -> str:
    blocks = re.findall(r"```python\n(.*?)```", text, flags=re.S)
    if len(blocks) != 1:
        raise SystemExit(f"expected one python code block, got {len(blocks)}; see the build log")
    return blocks[0]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", type=Path)
    ap.add_argument("tests", type=Path)
    ap.add_argument("target", type=Path)
    ap.add_argument("--cap", type=float, default=5.0, help="USD cap for this module")
    ap.add_argument("--feedback", type=Path, help="review notes or test output for a retry")
    args = ap.parse_args()

    tag = f"build:{args.target.stem}"
    llm = MetaLLM()
    worst = call_cost(llm.model, 0, 0, MAX_TOKENS)  # output side only; input is small
    spent = module_spend(tag)
    if spent + worst > args.cap:
        raise SystemExit(f"{tag}: spent ${spent:.4f}, cap ${args.cap:.2f}")
    check_budget(worst)

    user = (f"Target file: {args.target.as_posix()}\n\n## Spec\n\n{args.spec.read_text(encoding='utf-8')}"
            f"\n\n## Acceptance tests ({args.tests.as_posix()})\n\n```python\n"
            f"{args.tests.read_text(encoding='utf-8')}```")
    if args.feedback:
        user += f"\n\n## Feedback on the previous attempt\n\n{args.feedback.read_text(encoding='utf-8')}"
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]

    reply = llm.chat(messages, max_tokens=MAX_TOKENS, tag=tag)
    text = reply.choices[0].message.content or ""

    BUILD_LOG.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    log_path = BUILD_LOG / f"{args.target.stem}_{stamp}.md"
    log_path.write_text(f"# Build {tag} {stamp}\n\nmodel: {llm.model}\ncost_usd: {llm.run_cost:.6f}\n"
                        f"finish_reason: {reply.choices[0].finish_reason}\n\n## Prompt\n\n{user}\n\n"
                        f"## Reply\n\n{text}\n", encoding="utf-8")

    code = extract_code(text)
    args.target.write_text(code, encoding="utf-8")
    print(f"wrote {args.target} ({len(code.splitlines())} lines); log {log_path}")
    print(f"this call ${llm.run_cost:.4f}; {tag} total ${module_spend(tag):.4f} of ${args.cap:.2f}")


if __name__ == "__main__":
    sys.exit(main())
