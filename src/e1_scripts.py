"""Thirty scripted voice notes with known answers (E1, the real-speech test set).

Claude-written. Places are real GeoNames villages within 8 km of a fire cluster on the 2026-10-30 live map
and absent from every Glimmer training transcript, plus the two demo villages, one ambiguous name (two
villages called Tumbang Nusa) and one note with no place. Five notes per fire state. Gold answers follow
the FieldReport rules: a field is set only if the script says it (null or "unknown" otherwise).

Writes docs/recording_scripts.md (what you read aloud) and outputs/glimmer/e1/gold.jsonl (the answers).

Usage:
    python src/e1_scripts.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
U = "unknown"


def g(state, place, area=None, smoke=U, access=U, people=None, crew=None, needs=()):
    return {"fire_state": state, "place_text": place, "lat": None, "lon": None, "area_ha": area,
            "smoke": smoke, "access": access, "people_at_risk": people, "crew_on_site": crew, "needs": sorted(needs)}


SCRIPTS = [
    ("This is the Tambak Bajai patrol. We can see open flames in the peat field behind Tambak Bajai village. "
     "About three hectares are burning and the smoke is thick. We came by motorbike and we are at the fire now. "
     "We need water and more crew.",
     g("active_flames", "Tambak Bajai", 3.0, "thick", "vehicle", crew=True, needs=["water", "crew"])),
    ("Report from Sakakajang. No flames, but smoke is coming out of the ground and the peat is hot. "
     "It is about one hectare. There are houses close to it. We need a water pump.",
     g("smouldering_peat", "Sakakajang", 1.0, people=True, needs=["water"])),
    ("Thick smoke near Mahajandau, but I cannot see where it is coming from. I am watching from the main road "
     "and there is no crew here yet. Please send a crew.",
     g("smoke_only", "Mahajandau", smoke="thick", crew=False, needs=["crew"])),
    ("We checked the burned area near Balui village. Everything is black, but there is no smoke and no flames "
     "anymore. About ten hectares burned. We came in by boat.",
     g("burn_scar", "Balui", 10.0, "none", "boat", crew=True)),
    ("Tabatan Baru patrol here. We went to the hotspot from the satellite map. There is no fire and no smoke, "
     "the ground is cold. We walked in on foot.",
     g("no_fire", "Tabatan Baru", smoke="none", access="foot", crew=True)),
    ("I am near Sedendam village and it is getting dark. I think I see something, maybe smoke, but I am not "
     "sure. I am too far away to tell.",
     g("unclear", "Sedendam", crew=False)),
    ("Ada api! Fire near Tumbang Muroi. Open flames at the forest edge, asap tebal, thick smoke everywhere. "
     "Two families live right next to it. We need medical help and more crew.",
     g("active_flames", "Tumbang Muroi", smoke="thick", people=True, needs=["medical", "crew"])),
    ("Mentangaik patrol. The gambut is smouldering, smoke is rising from the soil, no open fire. Around five "
     "hectares. We reached it by truck. We need an excavator to dig a firebreak.",
     g("smouldering_peat", "Mentangaik", 5.0, access="vehicle", crew=True, needs=["heavy_equipment"])),
    ("There is light smoke over the forest near Muhuran. I cannot see the source on the ground. "
     "No houses nearby.",
     g("smoke_only", "Muhuran", smoke="light", people=False)),
    ("Near Sabuai Timur the fire from last week is out. The ground is burned, there is no smoke now. "
     "About twenty hectares.",
     g("burn_scar", "Sabuai Timur", 20.0, "none")),
    ("We checked the spot near Tuanan. No fire and no smoke. It was just a farmer clearing a field with a "
     "machine, nothing is burning.",
     g("no_fire", "Tuanan", smoke="none", crew=True)),
    ("This is Pelimbangan. The radio is bad. Something is happening by the canal, but I cannot see it well "
     "from here.",
     g("unclear", "Pelimbangan", crew=False)),
    ("Bina Mekar here, uh, we have open fire in the oil palm and the peat next to it, about half a hectare. "
     "We are on site with the pump, but we need more water.",
     g("active_flames", "Bina Mekar", 0.5, crew=True, needs=["water"])),
    ("Bakengbengkel patrol. The peat is smouldering under the ground, light smoke, no flames. We walked there, "
     "it is about two hundred meters from the road. Nobody lives close.",
     g("smouldering_peat", "Bakengbengkel", smoke="light", access="foot", people=False, crew=True)),
    ("Big smoke column east of Bapinang Hulu. Thick smoke, and I do not know where the fire is. "
     "The school is nearby.",
     g("smoke_only", "Bapinang Hulu", smoke="thick", people=True)),
    ("Resak village. The area burned two days ago. Now it is only black ground, no smoke. "
     "We can reach it by car.",
     g("burn_scar", "Resak", smoke="none", access="vehicle")),
    ("False alarm near Gunung Bayan. We are at the location, and there is no fire and no smoke at all.",
     g("no_fire", "Gunung Bayan", smoke="none", crew=True)),
    ("Report from Henda. I see something orange far away. Maybe the sunset, maybe a fire. I cannot say.",
     g("unclear", "Henda", crew=False)),
    ("Fire in Kanamit Barat! Open flames spreading toward the houses. Thick smoke. We need the fire crew "
     "and water right now.",
     g("active_flames", "Kanamit Barat", smoke="thick", people=True, needs=["crew", "water"])),
    ("Handilmicok. The peat is still burning underground after the rain, smoke coming up in many places. "
     "About four hectares. We can only get there by boat.",
     g("smouldering_peat", "Handilmicok", 4.0, access="boat")),
    ("Near Puri there is thin smoke drifting over the river. I cannot see any fire on the ground.",
     g("smoke_only", "Puri", smoke="light")),
    ("Seragam Jaya patrol here. The old burn scar is cold, no flames and no smoke. Nothing to do here. "
     "We are on site.",
     g("burn_scar", "Seragam Jaya", smoke="none", crew=True)),
    ("Bungkap. We checked the hotspot. There is no fire and no smoke, only wet grass. "
     "Access by motorbike is easy.",
     g("no_fire", "Bungkap", smoke="none", access="vehicle", crew=True)),
    ("Telukkajang here. Heavy rain right now and I cannot see anything. I will check again tomorrow.",
     g("unclear", "Telukkajang")),
    ("Satiruk patrol. Flames in the peat swamp forest, about fifty hectares and growing. Asap tebal. "
     "We need heavy equipment and medical help.",
     g("active_flames", "Satiruk", 50.0, "thick", needs=["heavy_equipment", "medical"])),
    ("Smouldering peat near Hanaut, no open flames, thin smoke. Three houses about three hundred meters away. "
     "We are there now with two people.",
     g("smouldering_peat", "Hanaut", smoke="light", people=True, crew=True)),
    ("This is the Kalampangan patrol. Smoke over the canal behind Kalampangan village, but we cannot see what "
     "is burning. Light smoke. We need a crew to check it.",
     g("smoke_only", "Kalampangan", smoke="light", needs=["crew"])),
    ("Bukit Tunggal patrol. We checked the hotspot near Bukit Tunggal. No fire, no smoke, the ground is cold. "
     "The area is clear.",
     g("no_fire", "Bukit Tunggal", smoke="none", crew=True)),
    ("Burned ground near Tumbang Nusa, about eight hectares, no smoke anymore. We came by boat.",
     g("burn_scar", "Tumbang Nusa", 8.0, "none", "boat", crew=True)),
    ("Hello, I think there is a fire somewhere behind the plantation, but I am not sure, and I do not know "
     "the name of this place.",
     g("unclear", None)),
]


def main() -> None:
    gold_path = ROOT / "outputs" / "glimmer" / "e1" / "gold.jsonl"
    gold_path.parent.mkdir(parents=True, exist_ok=True)
    gold_path.write_text("".join(json.dumps({"n": n, "script": s, "gold": gd}) + "\n"
                                 for n, (s, gd) in enumerate(SCRIPTS, 1)), encoding="utf-8")
    lines = ["# Bara recording scripts (30 voice notes)", "",
             "Read each note as a separate WhatsApp voice note, **in this order**, at a normal pace, as if you",
             "were a ranger in the field. Do not say the number. Small slips are fine and useful: real reports",
             "have them. Wait for one note to arrive before sending the next (about 10 seconds).", "",
             "The answers Bara should extract are in `outputs/glimmer/e1/gold.jsonl` (please do not read them",
             "before recording).", ""]
    for n, (s, _) in enumerate(SCRIPTS, 1):
        lines += [f"**{n}.** {s}", ""]
    doc = ROOT / "docs" / "recording_scripts.md"
    doc.parent.mkdir(exist_ok=True)
    doc.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    states = {}
    for _, gd in SCRIPTS:
        states[gd["fire_state"]] = states.get(gd["fire_state"], 0) + 1
    print(f"{len(SCRIPTS)} scripts, per state {states}; wrote {doc.relative_to(ROOT)} and {gold_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
