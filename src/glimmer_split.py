"""Split the verified Glimmer rows into training and a synthetic test set (E2).

E2 takes --n-eval kept rows, balanced over fire states, whose place is said nowhere in the training
rows (no near copies of a test item in training). Writes, in --out: train.jsonl and eval_e2.jsonl
(cookbook chat JSONL), eval_e2_meta.jsonl (gold facts and transcript, same order, for our grader), and
nested learning-curve subsets train_<n>.jsonl (each the first n rows of one fixed shuffle).
Claude-written.

Usage:
    python src/glimmer_split.py data/glimmer/v1 data/glimmer/v1b --out data/glimmer/split --n-eval 100
"""
import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

from field_report import SYSTEM_PROMPT, build_messages
from geocode import name_key
from intake import PLACE_HINTS

CURVE = (100, 200)


def chat_row(s: dict) -> dict:
    user = build_messages(s["transcript"], None, PLACE_HINTS)[1]["content"]
    return {"messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user},
                         {"role": "assistant", "content": json.dumps(s["extraction"])}]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", type=Path, nargs="+")
    ap.add_argument("--n-eval", type=int, default=40)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    rows = [json.loads(x) for src in args.src
            for x in (src / "facts.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len({s["i"] for s in rows}) == len(rows), "sample indices overlap between source folders"
    kept = [s for s in rows if s.get("grade") and s["grade"]["all_fields"] and s["grade"]["grounded"]]
    place = lambda s: name_key(s["gold"]["place_text"]) if s["gold"]["place_text"] else None  # noqa: E731
    uses = defaultdict(int)
    for s in kept:
        uses[place(s)] += 1
    by_state = defaultdict(list)
    for s in kept:
        if place(s) and uses[place(s)] == 1:  # a place used once can leave training entirely
            by_state[s["gold"]["fire_state"]].append(s)
    rng = random.Random(args.seed)
    for group in by_state.values():
        rng.shuffle(group)
    evals = []
    while len(evals) < args.n_eval and any(by_state.values()):
        for state in sorted(by_state):
            if by_state[state] and len(evals) < args.n_eval:
                evals.append(by_state[state].pop())
    eval_ids = {s["i"] for s in evals}
    train = [s for s in kept if s["i"] not in eval_ids]
    train_text = " ".join(s["transcript"].lower() for s in train)
    leaked = [s["gold"]["place_text"] for s in evals if s["gold"]["place_text"].lower() in train_text]
    assert not leaked, f"eval places said in training transcripts: {leaked}"
    args.out.mkdir(parents=True, exist_ok=True)
    for name, data in (("train.jsonl", train), ("eval_e2.jsonl", evals)):
        (args.out / name).write_text("".join(json.dumps(chat_row(s)) + "\n" for s in data), encoding="utf-8")
    (args.out / "eval_e2_meta.jsonl").write_text(
        "".join(json.dumps({"i": s["i"], "gold": s["gold"], "transcript": s["transcript"]}) + "\n" for s in evals),
        encoding="utf-8")
    order = train[:]
    rng.shuffle(order)
    for n in CURVE:
        if n < len(order):
            (args.out / f"train_{n}.jsonl").write_text("".join(json.dumps(chat_row(s)) + "\n" for s in order[:n]),
                                                       encoding="utf-8")
    states = defaultdict(int)
    for s in evals:
        states[s["gold"]["fire_state"]] += 1
    print(f"kept {len(kept)} of {len(rows)}; train {len(train)}, eval_e2 {len(evals)} {dict(states)}")


if __name__ == "__main__":
    main()
