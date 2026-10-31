"""Train and test the Muse Glimmer report reader with Fireworks' cookbook starter, on our fixed split.

Runs the cookbook's Serverless Training API path (fw-ai/cookbook commit 57968a2,
training/examples/sft/muse_glimmer_starter.py, Apache 2.0) with one change: its random 40-row
holdout is replaced by our eval file, in our order, so base and tuned Glimmer are sampled on exactly
the rows we choose and none of them is in training. Training is free until Nov 30; evaluation
sampling is billed per token on the Fireworks account. --no-promote is always passed, so no model
is left in the account afterwards. Claude-written.

Run with the cookbook's Python (it has the Fireworks SDK); the key is read from this repo's .env:
    C:/Users/ewenl/fw-cookbook/training/.venv/Scripts/python.exe src/glimmer_train.py \
        --train data/glimmer/v1/split/train.jsonl --eval data/glimmer/v1/split/eval_e2.jsonl --dry-run
Drop --dry-run to launch (billed sampling). Predictions land in data/glimmer/runs/<run-id>.
Arguments after -- go to the cookbook starter unchanged, e.g. -- --epochs 2 --lora-rank 16.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
COOKBOOK = Path(os.environ.get("FW_COOKBOOK", r"C:\Users\ewenl\fw-cookbook\training"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", type=Path, required=True)
    ap.add_argument("--eval", type=Path, required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--dry-run", action="store_true", help="validate and render only; no Fireworks calls")
    args, extra = ap.parse_known_args()
    extra = [a for a in extra if a != "--"]

    key = (dotenv_values(ROOT / ".env").get("FIREWORKS_API_KEY") or "").strip()
    if not key and not args.dry_run:
        raise SystemExit("FIREWORKS_API_KEY is not in .env")
    os.environ["FIREWORKS_API_KEY"] = key
    args.train, args.eval = args.train.resolve(), args.eval.resolve()  # before leaving the repo folder
    os.chdir(COOKBOOK)
    sys.path.insert(0, str(COOKBOOK))
    if os.name == "nt" and "resource" not in sys.modules:
        # The cookbook's memory logger imports the Unix-only resource module, used only to log peak
        # memory (utils/memlog.py). On Windows that log line reports 0; training is unaffected.
        import types
        stub = types.ModuleType("resource")
        stub.RUSAGE_SELF = 0
        stub.getrusage = lambda who: types.SimpleNamespace(ru_maxrss=0)
        sys.modules["resource"] = stub
    import examples.sft.muse_glimmer_starter as starter  # noqa: E402 (needs the cookbook on sys.path)

    original_prepare = starter._prepare
    log_path = ROOT / "data" / "glimmer" / "runs" / args.run_id

    def fixed_prepare(sargs: argparse.Namespace):
        """The starter's prepare, but with our train and eval rows instead of a random split."""
        for path in (args.train, args.eval):
            count, problems = starter._validate_chat_jsonl(path)
            if problems:
                raise SystemExit(f"{path}: {count} rows, problems: {problems[:10]}")
        train = [json.loads(x) for x in args.train.read_text(encoding="utf-8").splitlines() if x.strip()]
        evals = [json.loads(x) for x in args.eval.read_text(encoding="utf-8").splitlines() if x.strip()]
        # reuse the starter's rendering and report on a temporary file holding eval + train, with
        # n_eval = len(evals) and no shuffle (random.Random(seed).shuffle is bypassed below)
        both = log_path / "_combined.jsonl"
        log_path.mkdir(parents=True, exist_ok=True)
        both.write_text("".join(json.dumps(r) + "\n" for r in evals + train), encoding="utf-8")
        sargs.file, sargs.n_eval, sargs.n_train = str(both), len(evals), None
        shuffle = starter.random.Random.shuffle
        starter.random.Random.shuffle = lambda self, x: None  # keep our order: eval rows first
        try:
            out = original_prepare(sargs)
        finally:
            starter.random.Random.shuffle = shuffle
        run_dir, train_ex, eval_ex, tokenizer, renderer, report = out
        assert [r["messages"] for r in eval_ex] == [r["messages"] for r in evals], "eval order changed"
        lengths = [len(renderer.build_generation_prompt(starter._messages(ex, False)).to_ints()) for ex in train_ex + eval_ex]
        report |= {"max_prompt_tokens": max(lengths), "mean_prompt_tokens": round(sum(lengths) / len(lengths))}
        return run_dir, train_ex, eval_ex, tokenizer, renderer, report

    starter._prepare = fixed_prepare
    argv = ["prepare" if args.dry_run else "launch", "--run-id", args.run_id, "--file", str(args.train),
            "--log-path", str(log_path)]
    if not args.dry_run:
        argv += ["--surface", "serverless", "--no-promote", *extra]
    return starter.main(argv)


if __name__ == "__main__":
    sys.exit(main())
