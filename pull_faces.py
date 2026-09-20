"""Enroll faces for people.db rows from public web photos (judges agreed to be in the demo).

    python pull_faces.py              everyone without a faces/<id>/ folder
    python pull_faces.py tom-alterman one person (re-pulls)

Browserbase Search finds pages about "<name> <company>", Browserbase Fetch reads them, and
we take each page's og:image plus any <img> captioned with the person's name. A face is
enrolled only when two *different* photos agree it's the same person (or, with a single
photo, when its caption is the full name) -- a wrong face is worse than no face.
Output: faces/<id>/web_<n>.jpg, gitignored. Then POST /reload on the running server.
"""
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).parent
AGREE = 0.5        # same-person similarity between two photos (server matches live frames at 0.45)
SAME_PHOTO = 0.97  # above this two candidates are the same picture, not two perspectives
MIN_FACE_PX = 40   # smaller faces embed badly
MAX_SHOTS = 4


def letters(s):
    return re.sub(r"[^a-z]", "", html.unescape(s).lower())  # O'Neill == O’Neill == oneill


UA ={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}


def bb_key():
    if os.environ.get("BROWSERBASE_API_KEY"):
        return os.environ["BROWSERBASE_API_KEY"]
    for line in (ROOT / "server" / ".env").read_text().splitlines():
        if line.startswith("BROWSERBASE_API_KEY="):
            return line.split("=", 1)[1].strip()
    sys.exit("Missing BROWSERBASE_API_KEY (env or server/.env)")


def bb(endpoint, body):
    req = urllib.request.Request(f"https://api.browserbase.com/v1/{endpoint}", json.dumps(body).encode(),
                                 {"X-BB-API-Key": KEY, "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=30))


def image_urls(page_url, name):
    """og:image / twitter:image, plus <img> whose alt holds the last name. -> [(url, strong)]"""
    try:
        page = bb("fetch", {"url": page_url, "allowRedirects": True})
    except Exception:
        return []
    if page.get("statusCode") != 200:
        return []
    doc, last, full = str(page.get("content", "")), letters(name.split()[-1]), letters(name)
    # a page whose URL names them (conffab.com/presenter/tom-alterman) is about them,
    # so its og:image counts as captioned
    about_them = last in letters(urllib.parse.urlparse(page_url).path)
    found = []
    for m in re.finditer(r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]*>', doc, re.I):
        src = re.search(r'content=["\']([^"\']+)', m.group(0))
        if src:
            found.append((src.group(1), about_them))
    for m in re.finditer(r"<img[^>]+>", doc, re.I):
        alt = re.search(r'alt=["\']([^"\']*)', m.group(0))
        src = re.search(r'(?:data-src|src)=["\']([^"\']+)', m.group(0))
        if alt and src and last in letters(alt.group(1)):
            found.append((src.group(1), full in letters(alt.group(1)) or about_them))
    return [(urllib.parse.urljoin(page_url, html.unescape(u)), s) for u, s in found
            if not u.lower().endswith(".svg")]


def download(url):
    try:
        data = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15).read()
        return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    except Exception:
        return None


def candidates(person):
    """Web photos of one person -> [(img, url, strong)] with exactly one usable face each."""
    company = (person["role"] or "").split(",")[0]
    query = f"{person['name']} {company} -site:linkedin.com"  # LinkedIn otherwise fills the results
    results = bb("search", {"query": query[:200], "numResults": 10})["results"]
    pages = [r["url"] for r in results if "linkedin.com" not in r["url"]]  # LinkedIn blocks Fetch
    with ThreadPoolExecutor(8) as pool:
        urls = dict(u for found in pool.map(lambda p: image_urls(p, person["name"]), pages) for u in found)
        imgs = list(pool.map(download, urls))
    return [(img, url, strong) for img, (url, strong) in zip(imgs, urls.items()) if img is not None]


def pick(cands, fa):
    """Embed, drop duplicates, keep the largest agreeing group. -> [(img, emb)]"""
    faces = []
    for img, url, strong in cands:
        found = fa.get(img)
        if len(found) != 1 or found[0].bbox[2] - found[0].bbox[0] < MIN_FACE_PX:
            continue
        emb = found[0].normed_embedding
        if all(float(emb @ e) < SAME_PHOTO for _, e, _ in faces):
            faces.append((img, emb, strong))
    if not faces:
        return []
    sims = np.array([[float(a @ b) for _, b, _ in faces] for _, a, _ in faces])
    medoid = int(sims.sum(1).argmax())
    group = [f for f, s in zip(faces, sims[medoid]) if s >= AGREE]
    if len(group) >= 2:
        return [(img, emb) for img, emb, _ in group[:MAX_SHOTS]]
    strong = [(img, emb) for img, emb, s in faces if s]
    return strong[:1]  # one photo captioned with the full name; nothing to cross-check it against


def main():
    import sqlite3
    from insightface.app import FaceAnalysis

    fa = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"],
                      allowed_modules=["detection", "recognition"])
    fa.prepare(ctx_id=-1, det_size=(640, 640))
    con = sqlite3.connect(ROOT / "people.db")
    con.row_factory = sqlite3.Row
    people = [dict(r) for r in con.execute("SELECT id, name, role FROM people")]
    if len(sys.argv) > 1:
        people = [p for p in people if p["id"] == sys.argv[1]]
    else:
        people = [p for p in people if not (ROOT / "faces" / p["id"]).exists()]

    kept = {}
    for p in people:
        try:
            shots = pick(candidates(p), fa)
        except Exception as e:
            print(f"FAIL {p['id']}: {e}")
            continue
        if not shots:
            print(f"MISS {p['id']}: no photo passed the checks")
            continue
        out = ROOT / "faces" / p["id"]
        out.mkdir(parents=True, exist_ok=True)
        for old in out.glob("web_*.jpg"):
            old.unlink()
        for n, (img, _) in enumerate(shots):
            cv2.imwrite(str(out / f"web_{n}.jpg"), img)
        kept[p["id"]] = [e for _, e in shots]
        print(f"OK   {p['id']}: {len(shots)} photo(s)" + ("" if len(shots) > 1 else " (single, name-captioned)"))

    # Two people whose enrolled faces match each other = one of them got the wrong photo.
    ids = list(kept)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            s = max(float(x @ y) for x in kept[a] for y in kept[b])
            if s >= AGREE:
                print(f"WARN {a} and {b} look like the same person ({s:.2f}): check faces/{a} and faces/{b}")
    print(f"\n{len(kept)}/{len(people)} enrolled. Now: curl -X POST http://localhost:8000/reload")


if __name__ == "__main__":
    KEY = bb_key()
    main()
