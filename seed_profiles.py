"""Pre-fill people.db with the published judge roster and our own team.

Profiles only -- no faces. Faces come from  python enroll.py <id>  in person, or
python pull_faces.py  for judges who agreed. Then  node server/research.js
(reseeding keeps research already done).

    python seed_profiles.py

At the table:  python enroll.py alana-goyal   (profile already waiting)
"""
import re
from pathlib import Path

from enroll import FIELDS, save_profile

# Published Hack the North 2026 judging roster, one "Name | Company, Title" per line.
# Gitignored: get judges.txt from a teammate.
JUDGES = Path(__file__).parent / "judges.txt"

TEAM = [
    ("Ryan Qi",     "https://www.linkedin.com/in/ryan-qi/"),
    ("Tony Tan",    "https://www.linkedin.com/in/tony-tan-65167328a/"),
    ("Andrew Deng", "https://www.linkedin.com/in/andrew-deng1/"),
    ("Ashley Moon", "https://www.linkedin.com/in/ashleyswmoon/"),
]


def person_id(name):
    """'Charlie O'Neill' -> 'charlie-oneill'. Must match the faces/<id>/ folder."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower().replace("'", "")).strip("-")


def rows():
    for line in JUDGES.read_text(encoding="utf-8").strip().splitlines():
        name, role = (p.strip() for p in line.split("|"))
        yield person_id(name), {"name": name, "role": role, "bio": "",
                                "links": "", "working_on": "", "looking_for": ""}
    for name, linkedin in TEAM:
        yield person_id(name), {
            "name": name, "role": "Hack the North 2026", "bio": "",
            "links": linkedin,
            "working_on": "AR networking assistant on Quest 3",
            "looking_for": "",
        }


if __name__ == "__main__":
    if not JUDGES.exists():
        raise SystemExit("missing judges.txt (gitignored) -- get it from a teammate")
    seeded = [(pid, v) for pid, v in rows()]
    ids = [p for p, _ in seeded]
    assert len(ids) == len(set(ids)), f"duplicate id: {[i for i in ids if ids.count(i) > 1]}"
    for pid, vals in seeded:
        assert set(vals) == set(FIELDS), f"{pid}: field mismatch"
        save_profile(pid, vals)
    print(f"seeded {len(seeded)} profiles ({len(seeded) - len(TEAM)} judges, {len(TEAM)} team)")
    print("none have faces yet -- run  python enroll.py <id>  for each person, in person")
