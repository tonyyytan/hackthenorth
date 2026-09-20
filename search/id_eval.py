"""How accurate is identification, on the data we actually have?

    python id_eval.py

Three questions, measured against server.py's own gallery and THRESHOLD -- this
imports server.py rather than reimplementing the matching, so a change there
shows up here:

  1. Face, known person   leave-one-shot-out: hide one photo, must still be them.
  2. Face, stranger       hide every photo of a person, must return nobody.
                          This is the one that matters: a wrong name on a judge's
                          head is worse than no name at all.
  3. Name tag             render each roster name as a badge, read it back, and
                          match it against the *whole* 41-name roster, not a toy
                          one -- near-miss names (two Ryans, two Mikes) are where
                          difflib picks the wrong person.

Synthetic badges are an upper bound: a real tag is angled, creased and lit badly.
Face numbers are real photos, but many people have one shot, so treat the
stranger rate as the honest headline.
"""
import sys

import numpy as np

import badge
import server

THRESH = server.THRESHOLD


def face_eval():
    """Leave-one-out over the real gallery. Returns (genuine, stranger) result lists."""
    names, G, owner = server.NAMES, server.GALLERY, server.OWNER
    shots_per = np.bincount(owner, minlength=len(names))
    genuine, stranger = [], []

    for i in range(len(G)):
        me = owner[i]
        solo = shots_per[me] == 1
        # solo: drop the person entirely -> they are a stranger to the gallery.
        # otherwise: drop just this shot -> they should still be recognised.
        keep = (owner != me) if solo else (np.arange(len(G)) != i)
        sims = G[keep] @ G[i]
        best = np.full(len(names), -1.0, dtype="float32")
        np.maximum.at(best, owner[keep], sims)
        j = int(best.argmax())
        got = names[j] if best[j] >= THRESH else None
        row = (names[me], got, round(float(best[j]), 3))
        (stranger if solo else genuine).append(row)
    return genuine, stranger


def badge_eval(roster):
    """Every roster name, rendered clean and blurred, read back and matched."""
    results = []
    for pid, name in roster.items():
        for blur in (False, True):
            lines = badge.read_lines(badge._render_badge(name, blur))
            got, score = badge.match_roster(lines, roster)
            results.append((pid, blur, got, score))
    return results


def main():
    roster = {pid: p["name"] for pid, p in server.PROFILES.items() if p.get("name")}
    print(f"\ngallery: {len(server.GALLERY)} shots of {len(server.NAMES)} people"
          f" | roster: {len(roster)} names | THRESHOLD={THRESH}\n")

    genuine, stranger = face_eval()
    hits = [r for r in genuine if r[1] == r[0]]
    misses = [r for r in genuine if r[1] != r[0]]
    wrong = [r for r in genuine if r[1] not in (r[0], None)]
    print(f"FACE, known person   {len(hits)}/{len(genuine)} recognised"
          f"   ({len(wrong)} wrong name, {len(misses) - len(wrong)} no match)")
    for want, got, s in misses:
        print(f"   {want:22s} -> {str(got):22s} {s}")
    if hits:
        scores = sorted(r[2] for r in hits)
        print(f"   correct-match scores: min {scores[0]:.3f}, median {scores[len(scores) // 2]:.3f}"
              f"  (headroom over THRESHOLD: {scores[0] - THRESH:+.3f})")

    false_accept = [r for r in stranger if r[1] is not None]
    print(f"FACE, stranger       {len(stranger) - len(false_accept)}/{len(stranger)} correctly unknown"
          f"   (nearest score max {max((r[2] for r in stranger), default=0):.3f} vs {THRESH})")
    for want, got, s in false_accept:
        print(f"   {want:22s} -> {str(got):22s} {s}   FALSE ACCEPT")

    badges = badge_eval(roster)
    ok = [r for r in badges if r[2] == r[0]]
    bad = [r for r in badges if r[2] != r[0]]
    print(f"NAME TAG             {len(ok)}/{len(badges)} matched"
          f"   ({sum(1 for r in bad if r[2])} wrong person, {sum(1 for r in bad if not r[2])} unread)")
    for pid, blur, got, score in bad:
        print(f"   {pid:22s} blur={blur!s:5s} -> {str(got):22s} {score}")

    # A stranger's tag must not be forced onto the nearest roster name.
    imposters = ["Zbigniew Wrzeszcz", "Hiroko Tanaka", "Olumide Adebayo"]
    forced = [n for n in imposters
              if badge.match_roster(badge.read_lines(badge._render_badge(n)), roster)[0]]
    print(f"NAME TAG, stranger   {len(imposters) - len(forced)}/{len(imposters)} correctly unmatched"
          f"{'   ' + str(forced) if forced else ''}\n")

    # Regressions, not targets: a wrong name is the failure mode worth failing on.
    assert not wrong, f"face matched the wrong person: {wrong}"
    assert not false_accept, f"stranger accepted as someone enrolled: {false_accept}"
    assert not forced, f"stranger's badge forced onto a roster name: {forced}"
    assert len(hits) / max(len(genuine), 1) >= 0.9, "known-face recall fell below 90%"
    assert len(ok) / len(badges) >= 0.9, "badge read accuracy fell below 90%"
    print("ok")


if __name__ == "__main__":
    sys.exit(main())
