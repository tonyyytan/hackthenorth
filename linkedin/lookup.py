"""LinkedIn lookup: a name -> their public LinkedIn page, as text for brain.py.

    python linkedin/lookup.py login                     once: sign in by hand
    python linkedin/lookup.py status                    session still good? views left?
    python linkedin/lookup.py "Satya Nadella" "Microsoft CEO"
    python linkedin/lookup.py --save tony-tan           store it in people.db
    python linkedin/lookup.py test                      offline self-check

Two steps, on purpose: a search engine finds the URL (LinkedIn's own search trips its
bot checks within a few queries), then one persistent Playwright profile in
linkedin/user_data/ carries the login cookie so the profile renders instead of the
auth wall. Everything here is read-only and rate limited -- see MIN_GAP_S/DAILY_CAP.
"""
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
USER_DATA = HERE / "user_data"      # the logged-in browser profile; gitignored
STATE = HERE / "state.json"         # rate-limit counters
DB = HERE.parent / "people.db"

MIN_GAP_S = 8        # seconds between profile views
DAILY_CAP = 40       # profile views per day. A LinkedIn account less than a month
                     # old should sit near 15; this is the knob to turn down.
PROFILE_CHARS = 2000 # text handed to the LLM per person; it rides in every brain.py
                     # prompt for that person, so longer is not free
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
PROFILE_RE = re.compile(r"(?:[a-z]{2,3}\.)?linkedin\.com/in/([A-Za-z0-9\-_%]{3,100})")
JUNK_RE = re.compile(r"^(|Message|Follow|Connect|More|Save|Show all.*|See more|"
                     r"Join to view.*|See your mutual.*|Sign in|Join now|.*followers|"
                     r".*connections?|Contact Info|View .*'s full profile)$", re.I)


# ---------------------------------------------------------------- finding the URL

def _urls(html):
    """Every /in/<slug> in a search results page, in order, deduped."""
    out = []
    for slug in PROFILE_RE.findall(urllib.parse.unquote(html)):
        slug = slug.split("%")[0].rstrip("-")
        url = f"https://www.linkedin.com/in/{slug}"
        if slug and url not in out:
            out.append(url)
    return out


def _matches(url, name):
    """Guard: the search can hand back a completely different person."""
    slug = re.sub(r"[^a-z]", "", url.rsplit("/in/", 1)[-1].lower())
    parts = [p for p in (re.sub(r"[^a-z]", "", w.lower()) for w in name.split()) if p]
    if not parts:
        return False
    return parts[-1] in slug and (len(parts) < 2 or parts[0] in slug
                                  or slug.startswith(parts[0][0]))


def _ddg(query):
    req = urllib.request.Request(
        "https://html.duckduckgo.com/html/",
        data=urllib.parse.urlencode({"q": query}).encode(),
        headers={"User-Agent": UA},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")


def _browserbase(query):
    """Fallback search, the same API research.js uses. Skipped when the key is unset."""
    key = os.environ.get("BROWSERBASE_API_KEY") or _env_key("BROWSERBASE_API_KEY")
    if not key:
        return ""
    req = urllib.request.Request(
        "https://api.browserbase.com/v1/search",
        data=json.dumps({"query": query[:200], "numResults": 8}).encode(),
        headers={"X-BB-API-Key": key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def _env_key(name):
    """server/.env is where this repo's keys live (research.js reads the same file)."""
    env = HERE.parent / "server" / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            if k.strip() == name:
                return v.strip().strip('"')
    return ""


def find_profile_url(name, context=""):
    """X-ray search: site:linkedin.com/in "Name" <company/title/city>."""
    query = f'site:linkedin.com/in "{name}" {context}'.strip()
    for engine in (_ddg, _browserbase):
        try:
            hits = _urls(engine(query))
        except Exception as err:                    # engine blocked/down -> try the next
            print(f"  ({engine.__name__} failed: {err})", file=sys.stderr)
            continue
        for url in hits:
            if _matches(url, name):
                return url
    return None


# ------------------------------------------------------------- reading the profile

_pw = _ctx = None


def _context(headful=False):
    global _pw, _ctx
    if _ctx is None:
        from playwright.sync_api import sync_playwright
        _pw = sync_playwright().start()
        _ctx = _pw.chromium.launch_persistent_context(
            str(USER_DATA),
            headless=not headful,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 900},
            user_agent=UA,
        )
    return _ctx


def close():
    """Always safe to call: a half-dead browser must still release user_data/."""
    global _pw, _ctx
    for shut in (getattr(_ctx, "close", None), getattr(_pw, "stop", None)):
        try:
            if shut:
                shut()
        except Exception:
            pass
    _pw = _ctx = None


def _throttle():
    # ponytail: one JSON file, no lock -- fine for one process doing 1-2 lookups at a
    # time. Two machines sharing the account would need a real shared counter.
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    today = time.strftime("%Y-%m-%d")
    if state.get("day") != today:
        state = {"day": today, "count": 0, "last": 0}
    if state["count"] >= DAILY_CAP:
        raise RuntimeError(f"daily cap of {DAILY_CAP} profile views reached")
    wait = MIN_GAP_S - (time.time() - state["last"])
    if wait > 0:
        time.sleep(wait)
    state.update(count=state["count"] + 1, last=time.time())
    STATE.write_text(json.dumps(state))


def fetch_profile(url, headful=False):
    """Visit a profile with the saved session and return its visible text."""
    _throttle()
    page = _context(headful).new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)                 # lazily rendered sections
        if re.search(r"/(authwall|login|checkpoint|uas)", page.url):
            raise RuntimeError("login wall -- run: python linkedin/lookup.py login")
        body = page.inner_text("main" if page.locator("main").count() else "body")
    finally:
        page.close()
    lines = [ln.strip() for ln in body.splitlines() if not JUNK_RE.match(ln.strip())]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()[:PROFILE_CHARS]


def lookup(name, context="", headful=False):
    """name -> {"url", "text"}. Raises if the person or the session can't be found."""
    url = find_profile_url(name, context)
    if not url:
        raise LookupError(f"no LinkedIn profile found for {name!r} {context!r}")
    return {"url": url, "text": fetch_profile(url, headful)}


# -------------------------------------------------------------------- CLI plumbing

def login():
    """Headful browser, you sign in by hand once; the cookie stays in user_data/."""
    try:
        ctx = _context(headful=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
        input("Sign in (incl. 2FA) in the browser window, then press Enter here... ")
        page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        ok = "/feed" in page.url
        print("logged in, session saved" if ok else f"not logged in (landed on {page.url})")
        return ok
    except KeyboardInterrupt:
        print("\ncancelled")
        return False
    finally:
        close()  # Ctrl-C here used to orphan the browser, which then held user_data/ locked


def status():
    """Is the session still good, and how many views are left today?"""
    signed_in = any(c["name"] == "li_at" for c in _context().cookies())  # no page view
    close()
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    used = state.get("count", 0) if state.get("day") == time.strftime("%Y-%m-%d") else 0
    print(f"session: {'logged in' if signed_in else 'LOGGED OUT -- run: login'}")
    print(f"views today: {used}/{DAILY_CAP}")
    return signed_in


def save(pid):
    """Look a roster person up and store it in people.db; server.py's SELECT * does the rest."""
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM people WHERE id = ?", (pid,)).fetchone()
    if row is None:
        raise SystemExit(f"no person '{pid}' in people.db")
    has_col = "linkedin" in row.keys()
    con.close()

    got = lookup(row["name"], row["role"] or "")     # slow + can fail: do it before writing

    con = sqlite3.connect(DB)
    if not has_col:
        con.execute("ALTER TABLE people ADD COLUMN linkedin TEXT")
    con.execute("UPDATE people SET linkedin = ? WHERE id = ?",
                (f"{got['url']}\n{got['text']}", pid))
    con.commit()
    con.close()
    print(f"{pid}: {got['url']} ({len(got['text'])} chars) -- POST /reload to pick it up")


def test():
    """Offline self-check of the two bits that can silently return the wrong person."""
    html = ('<a href="/l/?uddg=https%3A%2F%2Fca.linkedin.com%2Fin%2Fryan-qi-12345">x</a>'
            '<a href="https://www.linkedin.com/in/ryan-qi-12345/details">y</a>'
            '<a href="https://www.linkedin.com/company/acme">z</a>')
    assert _urls(html) == ["https://www.linkedin.com/in/ryan-qi-12345"], _urls(html)
    assert _matches("https://www.linkedin.com/in/ryan-qi-12345", "Ryan Qi")
    assert _matches("https://www.linkedin.com/in/rqi", "Ryan Qi")             # initial+surname
    assert not _matches("https://www.linkedin.com/in/ryan-smith", "Ryan Qi")  # wrong person
    assert not _matches("https://www.linkedin.com/in/tony-tan", "Ryan Qi")
    assert _matches("https://www.linkedin.com/in/charlie-oneill", "Charlie O'Neill")
    print("ok")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
    elif args[0] == "test":
        test()
    elif args[0] == "login":
        sys.exit(0 if login() else 1)
    elif args[0] == "status":
        sys.exit(0 if status() else 1)
    elif args[0] == "--save":
        save(args[1])
    else:
        try:
            got = lookup(args[0], " ".join(args[1:]),
                         headful=bool(os.environ.get("LINKEDIN_HEADFUL")))
            print(got["url"], "\n", got["text"], sep="")
        finally:
            close()
