"""Training and eval data for the Muse Glimmer report reader (PLAN.md, decided Oct 31).

1. sample(): seeded facts for one field report. Places are unambiguous Sumatra and Kalimantan
   villages from GeoNames, never a place used in the real eval scripts (EVAL_PLACES).
2. write_transcript(): Muse Spark (low effort) writes a spoken voice note saying exactly those facts.
3. Muse Spark's extract() reads the note back. The row is kept only if report_grader finds every
   field equal to the sampled facts and the quotes grounded; the kept report is the training answer.
   Disagreements go to review.jsonl and never into training.

Output in --out: facts.jsonl (every sample, transcript, grade), train.jsonl (cookbook chat JSONL,
kept rows only), review.jsonl. Claude-written.

Usage:
    python src/glimmer_data.py --n 20 --seed 1 --out data/glimmer/trial
"""
import argparse
import json
import random
from pathlib import Path

from field_report import SYSTEM_PROMPT, build_messages, extract
from geocode import load_gazetteer, name_key
from intake import PLACE_HINTS
from llm import MetaLLM
from places import STUDY_PROVINCES
from report_grader import grade

ROOT = Path(__file__).resolve().parents[1]
# Places in the real eval scripts and the demo: never in training (no test item or near copy)
EVAL_PLACES = {name_key(p) for p in ["Kalampangan", "Bukit Tunggal", "Palangka Raya", "Sebangau", "Tumbang Nusa",
                                     "Jabiren", "Pulang Pisau", "Kereng Bangkirai"]}
PREFIXES = {"Desa", "Kelurahan", "Kampung", "Dusun"}
STATES = {  # fire_state: (weight, what the reporter means, allowed smoke values); equal weights
    "active_flames": (1, "open flames are visible", ["unknown", "light", "thick"]),
    "smouldering_peat": (1, "smoke rising from the ground or peat, no open flame", ["unknown", "light", "thick"]),
    "smoke_only": (1, "smoke or haze, but the reporter cannot see where it comes from on the ground", ["light", "thick"]),
    "burn_scar": (1, "burned ground, with no smoke and no flame now", ["none"]),
    "no_fire": (1, "the reporter checked the spot and sees no fire and no smoke", ["none"]),
    "unclear": (1, "the reporter is not sure what they see (too dark, too far, or the view is blocked) and says so", ["unknown"]),
}
SMOKE = {"light": "thin, light smoke", "thick": "thick smoke", "none": "no smoke at all"}
ACCESS = {"vehicle": ["by motorbike", "by car", "by truck"],
          "boat": ["by boat", "by klotok (a small river boat)", "by speedboat"],
          "foot": ["only on foot", "by walking in"]}
NEEDS = {"crew": "more crew", "water": "water or a water pump", "heavy_equipment": "an excavator or heavy machinery",
         "medical": "medical help"}
# Realism, each added with its own chance (a fact-breaking slip makes the row fail the check)
EXTRAS = [
    (0.4, "Add one sentence that carries no fact, such as the weather, a radio check, the time of day or a low battery."),
    (0.25, "Use one or two Indonesian words naturally, such as asap (smoke), api (fire), gambut (peat) or kebun (field)."),
    (0.3, "Add one or two small speech-to-text slips on words that carry no fact, like a dropped word or a repeated one."),
]
STYLES = ["calm and clear", "rushed, with a few repeated words", "speech-to-text style: all lower case, little punctuation",
          "with filler words like uh and okay", "short and to the point", "starts with a greeting and the patrol name"]


def place_pool() -> list[str]:
    """Spoken names of villages whose name is unique in the gazetteer and not an eval place."""
    gaz = load_gazetteer()
    counts = gaz.drop_duplicates("geonameid")["key"].value_counts()
    vill = gaz[(gaz["kind"] == "village") & gaz["province"].isin(STUDY_PROVINCES)].drop_duplicates("geonameid")
    out = set()
    for name, key in zip(vill["name"], vill["key"]):
        words = name.split()
        spoken = " ".join(words[1:] if words and words[0] in PREFIXES else words)
        if counts.get(key) == 1 and key not in EVAL_PLACES and spoken.replace(" ", "").isalpha() and name_key(spoken) == key:
            out.add(spoken)
    return sorted(out)


def sample(i: int, seed: int, places: list[str]) -> dict:
    """Facts for sample i: a FieldReport-shaped gold answer (no quotes) plus how to say it."""
    rng = random.Random(f"{seed}-{i}")
    state = rng.choices(list(STATES), weights=[w for w, _, _ in STATES.values()])[0]
    gold = {"fire_state": state, "place_text": None if rng.random() < 0.1 else rng.choice(places),
            "lat": None, "lon": None,
            "area_ha": None if state == "unclear" or rng.random() < 0.45 else rng.choice([0.5, 1, 2, 3, 5, 10, 20, 50]),
            "smoke": rng.choice(STATES[state][2]),
            "access": "unknown" if rng.random() < 0.35 else rng.choice(list(ACCESS)),
            "people_at_risk": rng.choice([None, None, True, False]),
            "crew_on_site": rng.choice([True, True, False]),  # first-person notes always imply where the patrol is
            "needs": [] if state == "no_fire" else sorted(rng.sample(list(NEEDS), rng.choice([0, 0, 1, 1, 2])))}
    if gold["area_ha"] is not None and float(gold["area_ha"]).is_integer():
        gold["area_ha"] = float(gold["area_ha"])
    said = [f"What they see: {STATES[state][1]}."]
    if gold["place_text"]:
        said.append(f"Place: near {gold['place_text']} village.")
    if gold["area_ha"] is not None:
        said.append(f"Size: about {gold['area_ha']:g} hectares.")
    if gold["smoke"] != "unknown":
        said.append(f"Smoke: {SMOKE[gold['smoke']]}.")
    if gold["access"] != "unknown":
        said.append(f"Access: the patrol can reach it {rng.choice(ACCESS[gold['access']])}.")
    if gold["people_at_risk"] is not None:
        said.append("People: houses or people are close to the fire." if gold["people_at_risk"]
                    else "People: no houses or people nearby.")
    if gold["crew_on_site"] is not None:
        said.append("Crew: the reporter's patrol is at the spot now." if gold["crew_on_site"]
                    else "Crew: the reporter sees it from a distance; no crew is there.")
    if gold["needs"]:
        said.append("Needs: " + " and ".join(NEEDS[n] for n in gold["needs"]) + ".")
    extras = [text for chance, text in EXTRAS if rng.random() < chance]
    return {"i": i, "seed": seed, "gold": gold, "facts": said, "style": rng.choice(STYLES), "extras": extras}


def write_transcript(s: dict, llm: MetaLLM) -> str:
    prompt = ("Write one English voice note from a forest fire patrol in Indonesian peatland, as a speech-to-text "
              "transcript. Say every fact below, clearly enough that a listener gets each one right, but in the "
              "words a tired ranger would use: never copy the wording of the facts. Say numbers as words. Do not "
              "mention anything that is not listed: no other place, size, smoke, access, people, crew or needs, and "
              f"no coordinates. First person, 2 to 6 short sentences, style: {s['style']}. "
              + " ".join(s.get("extras", [])) + " Output only the transcript.\n\nFacts:\n- " + "\n- ".join(s["facts"]))
    reply = llm.chat([{"role": "user", "content": prompt}], max_tokens=3000, tag="glimmer data write",
                     reasoning_effort="low")
    if reply.choices[0].finish_reason == "length":
        return ""
    return (reply.choices[0].message.content or "").strip().strip('"')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--start", type=int, default=0, help="first sample index (to extend a set)")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "glimmer" / "trial")
    ap.add_argument("--max-usd", type=float, default=2.0, help="stop when this run has spent this much")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    places = place_pool()
    writer, spent, kept = MetaLLM(), 0.0, 0
    with (args.out / "facts.jsonl").open("a", encoding="utf-8") as facts_f, \
         (args.out / "train.jsonl").open("a", encoding="utf-8") as train_f, \
         (args.out / "review.jsonl").open("a", encoding="utf-8") as review_f:
        for i in range(args.start, args.start + args.n):
            if spent >= args.max_usd:
                print(f"stopped at ${spent:.4f} (--max-usd {args.max_usd})")
                break
            s = sample(i, args.seed, places)
            before = writer.run_cost
            s["transcript"] = write_transcript(s, writer)
            spent += writer.run_cost - before
            if not s["transcript"]:
                s["grade"] = None
                review_f.write(json.dumps({**s, "why": "no transcript (length)"}) + "\n")
                facts_f.write(json.dumps(s) + "\n")
                continue
            out = extract(s["transcript"], place_hints=PLACE_HINTS)
            spent += out["cost_usd"]
            s["extraction"] = out["report"]
            s["grade"] = grade(json.dumps(out["report"]) if out["report"] else (out["error"] or ""), s["gold"],
                               s["transcript"])
            facts_f.write(json.dumps(s) + "\n")
            if s["grade"]["all_fields"] and s["grade"]["grounded"]:
                kept += 1
                user = build_messages(s["transcript"], None, PLACE_HINTS)[1]["content"]
                train_f.write(json.dumps({"messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                                       {"role": "user", "content": user},
                                                       {"role": "assistant", "content": json.dumps(out["report"])}]}) + "\n")
            else:
                wrong = [f for f, ok in s["grade"]["fields"].items() if not ok]
                review_f.write(json.dumps({**s, "why": {"wrong_fields": wrong, "grounded": s["grade"]["grounded"]}}) + "\n")
            print(f"{i}: {s['gold']['fire_state']:<16} kept={s['grade']['all_fields'] and s['grade']['grounded']} "
                  f"spent=${spent:.4f}", flush=True)
    print(f"kept {kept} of {args.n}; this run spent ${spent:.4f}")


if __name__ == "__main__":
    main()
