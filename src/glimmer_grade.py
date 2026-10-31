"""Grade a Glimmer training run's saved predictions with our deterministic grader.

Reads data/glimmer/runs/<run-id>/predictions_base.jsonl and predictions_tuned.jsonl (written by the
cookbook starter, one line per eval row, in our eval order) and the eval meta file with gold facts and
transcripts, then prints and saves summary_ours.json. Claude-written, no API calls.

Caveat printed with every result: on the synthetic set (E2), Muse Spark is correct by construction
(a row is kept only when Spark's extraction matches the facts), so E2 compares Glimmer base vs tuned
only. Real voice notes (E1) are the fair test for Muse Spark.

Usage:
    python src/glimmer_grade.py <run-id> --meta data/glimmer/split/eval_e2_meta.jsonl
"""
import argparse
import json
from pathlib import Path

from report_grader import grade, summarise

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id")
    ap.add_argument("--meta", type=Path, required=True)
    args = ap.parse_args()
    run = ROOT / "data" / "glimmer" / "runs" / args.run_id
    meta = [json.loads(x) for x in args.meta.read_text(encoding="utf-8").splitlines() if x.strip()]
    out = {"run_id": args.run_id, "eval_rows": len(meta),
           "caveat": "Synthetic rows are kept only when Muse Spark gets them right, so Spark is 100% here by "
                     "construction; this compares Glimmer base vs tuned. Real voice notes are the fair test for Spark."}
    for label in ("base", "tuned"):
        path = run / f"predictions_{label}.jsonl"
        if not path.exists():
            continue
        preds = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
        assert len(preds) == len(meta), f"{label}: {len(preds)} predictions for {len(meta)} eval rows"
        grades = [grade(p["answer_text"], m["gold"], m["transcript"]) for p, m in zip(preds, meta)]
        out[label] = summarise(grades) | {"truncated": sum(p.get("truncated", False) for p in preds)}
    summary = run / "summary.json"
    if summary.exists():
        s = json.loads(summary.read_text(encoding="utf-8"))
        out["steps"], out["eval_tokens"] = s.get("steps"), s.get("cost", {}).get("evaluation_sampling")
    (run / "summary_ours.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
