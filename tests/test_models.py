"""The frozen models in a checkout must be byte-identical to the pre-registered ones and must load.

LightGBM's text format stores byte offsets, so a line-ending conversion by Git corrupts a model
(hence `models/*.txt binary` in .gitattributes). No API calls.

Usage:
    python tests/test_models.py
"""
import hashlib
import json
from pathlib import Path

import lightgbm as lgb

ROOT = Path(__file__).resolve().parents[1]


def frozen() -> dict[str, str]:
    return json.loads((ROOT / "outputs" / "test_2023_results.json").read_text(encoding="utf-8"))["frozen_models"]


def test_hashes_match_the_preregistered_test():
    for tag, want in frozen().items():
        got = hashlib.sha256((ROOT / "models" / f"lgbm_{tag}.txt").read_bytes()).hexdigest()
        assert got == want, f"{tag}: {got} is not the frozen {want}"


def test_frozen_models_load():
    for tag in frozen():
        assert lgb.Booster(model_file=str(ROOT / "models" / f"lgbm_{tag}.txt")).num_trees() > 0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
