"""Run the Glimmer experiments one after another, within a hard Fireworks spend limit.

Fireworks allows one serverless Glimmer session at a time, and a finished session frees its slot only
when it expires, so a run that starts too soon gets HTTP 429 before anything is billed; this driver
waits and retries. After each run it prices the evaluation tokens from the run's summary.json at the
published rates (fireworks.ai/pricing, read 2026-10-31: Muse Glimmer 30B on the Training API, prefill
$1.96 and sampling $4.88 per 1M tokens; LoRA SFT training $0 until Nov 30) and starts no new run once
the total reaches LIMIT_USD. Claude-written.

Usage:
    python src/glimmer_sweep.py
"""
import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "data" / "glimmer" / "runs"
SPLIT = ROOT / "data" / "glimmer" / "split"
COOKBOOK_PY = r"C:\Users\ewenl\fw-cookbook\training\.venv\Scripts\python.exe"
PREFILL, SAMPLE = 1.96 / 1e6, 4.88 / 1e6
LIMIT_USD = 5.0
WAIT_S, TRIES = 120, 30
CAP = ["--max-eval-tokens", "600"]  # base Glimmer rambles to the limit; tuned answers are ~120 tokens
PLAN = [  # run id, train file, extra cookbook arguments
    ("e2-n200-ep1", "train_200.jsonl", CAP),
    ("e2-all-ep1", "train.jsonl", CAP),
    ("e2-all-ep2", "train.jsonl", CAP + ["--epochs", "2"]),
    ("e2-all-ep3", "train.jsonl", CAP + ["--epochs", "3"]),
    ("e2-all-ep2-r16", "train.jsonl", CAP + ["--epochs", "2", "--lora-rank", "16"]),
    ("e2-all-ep2-lr2e4", "train.jsonl", CAP + ["--epochs", "2", "--learning-rate", "2e-4"]),
]


def run_cost(run_id: str) -> float:
    s = RUNS / run_id / "summary.json"
    if not s.exists():
        return 0.0
    tokens = json.loads(s.read_text(encoding="utf-8"))["cost"]["evaluation_sampling"]
    return tokens["prompt_tokens"] * PREFILL + tokens["sampled_tokens"] * SAMPLE


def spent() -> float:
    return sum(run_cost(d.name) for d in RUNS.iterdir() if d.is_dir()) if RUNS.exists() else 0.0


def main() -> None:
    for run_id, train, extra in PLAN:
        if (RUNS / run_id / "summary.json").exists():
            print(f"{run_id}: already done (${run_cost(run_id):.2f})", flush=True)
            continue
        if spent() >= LIMIT_USD:
            print(f"stop: ${spent():.2f} spent, limit ${LIMIT_USD:.2f}", flush=True)
            return
        log = ROOT / "logs" / f"glimmer_{run_id}.log"
        for attempt in range(1, TRIES + 1):
            cmd = [COOKBOOK_PY, "-u", str(ROOT / "src" / "glimmer_train.py"), "--train", str(SPLIT / train),
                   "--eval", str(SPLIT / "eval_e2.jsonl"), "--run-id", run_id, "--", *extra]
            with log.open("w", encoding="utf-8") as f:
                code = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, timeout=3600, cwd=ROOT).returncode
            text = log.read_text(encoding="utf-8", errors="replace")
            if code == 0 and (RUNS / run_id / "summary.json").exists():
                print(f"{run_id}: done, ${run_cost(run_id):.2f}; total ${spent():.2f}", flush=True)
                break
            # retry only a busy slot that refused us before any billed sampling happened
            if "RATE_LIMIT_EXCEEDED" in text and "scored" not in text:
                print(f"{run_id}: slot busy (attempt {attempt}), waiting {WAIT_S} s", flush=True)
                time.sleep(WAIT_S)
                continue
            print(f"{run_id}: failed (exit {code}); see {log}. Stopping the sweep.", flush=True)
            return
        else:
            print(f"{run_id}: slot still busy after {TRIES} tries; stopping.", flush=True)
            return
    print(f"sweep finished; total ${spent():.2f}", flush=True)


if __name__ == "__main__":
    main()
