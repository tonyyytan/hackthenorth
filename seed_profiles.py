"""Pre-fill people.db with the published judge roster and our own team.

Profiles only -- no faces. A row here does nothing until someone enrolls their
face (python enroll.py <id>), which only they can do, in person. That is the
whole consent model: the name is public program info, the face is opt-in.

    python seed_profiles.py

At the table:  python enroll.py alana-goyal   (profile already waiting)
"""
import re

from enroll import FIELDS, save_profile

# Published Hack the North 2026 judging roster: "Name | Company, Title"
JUDGES = """
Tom Alterman        | GoodLeap, Director of Product
Advait Maybhate     | Warp, Software Engineer
Nabil Fahel         | Communitech, Vice President
Jen Dewalt          | Tokay.io, Co-founder and CPO
Mike Kirkup         | Elderella, Co-founder
Albert Chen         | Two Small Fish Ventures, Partner
Alroy Almeida       | BDC Capital, Pre-Seed/Seed Investor
Alana Goyal         | Basecase, Founder
Alex Marley         | Upfront Ventures, Investor
Devon Galloway      | Garage Capital, General Partner
Michael Gibson      | 1517 Fund, Co-Founder
Sahand Sojoodi      | 8090 Solutions, VP Engineering and GM Canada
Nish Sinnadurai     | Cerebras Systems, SVP Engineering
Brooke Joseph       | OpenAI, Member of Technical Staff
Ishaan Dey          | Google DeepMind, Software Engineer
Mike McCauley       | Garage Capital, Cofounder and GP
Eli Aleyner         | Docker, VP of Product Strategy and Alliances
Charlie O'Neill     | Baseten, Co-Head of Base Labs
Aron Levitz         | Foolish Entertainment, CEO and Co-founder
Dylan Itzikowitz    | South Park Commons, Partner
Nick Rubin          | First Round, Investor
Krish Shah          | The Bot Company, Member of Technical Staff
Bogdan Frusina      | Dejero, Founder and CTO
Mathurah Ravigulan  | Netflix, Design Engineer
Catherine Yeo       | Altara, Co-Founder
Ryan Fox            | Super.com, CTO
Michelle Lim        | Flint, CEO and Co-Founder
Ulkar Akhundzada    | Mappedin, Principal Forward Deployed Engineer
Jeff Nguyen         | BobaTalks, Founder
Arif Virani         | Apple, AI/ML - prev. CEO, DarwinAI
Zachary Laberge     | Omen AI, Founder and CEO
Mudith Jayasekara   | Baseten, Co-Head of Model Training
Taeuk Kang          | Rox, AI Product Lead
Prem Kalevar        | N49P Ventures, Venture Partner
Umesh Khanna        | SpaceXAI, Talent Engineering
Brian Master        | Aramco, Senior Software Engineer
"""

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
    for line in JUDGES.strip().splitlines():
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
    seeded = [(pid, v) for pid, v in rows()]
    ids = [p for p, _ in seeded]
    assert len(ids) == len(set(ids)), f"duplicate id: {[i for i in ids if ids.count(i) > 1]}"
    for pid, vals in seeded:
        assert set(vals) == set(FIELDS), f"{pid}: field mismatch"
        save_profile(pid, vals)
    print(f"seeded {len(seeded)} profiles ({len(seeded) - len(TEAM)} judges, {len(TEAM)} team)")
    print("none have faces yet -- run  python enroll.py <id>  for each person, in person")
