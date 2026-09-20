// Person research, precomputed: people.db profile -> Claude researches via Browserbase -> people.db.
// Browserbase does all the web access: Search API finds pages, Fetch API reads them, and a real
// cloud browser session renders the JS-heavy ones Fetch can't. Claude only decides what to read.
// Results land in people.db as `concise_research` (3-5 caption bullets, model-written) and
// `verbose_research` (every page and search result Browserbase returned, whole, no summary).
// The same raw pages are also rowed out in the `sources` table, one row per URL.
//
// Usage:
//   node research.js                     research everyone not yet researched
//   node research.js <person-id>         (re)research one person, e.g. tom-alterman
//   node research.js "Jane Doe" "Figma, Designer"   someone not in people.db yet:
//                                        adds the row, then researches them

const path = require("path");
// __dirname, not cwd: this has to work when run from the repo root too.
// keys live in server/.env, the one file both halves of the project read
require("dotenv").config({ path: path.join(__dirname, "..", "server", ".env"), quiet: true });
const { DatabaseSync } = require("node:sqlite");
const Anthropic = require("@anthropic-ai/sdk").default;
const { chromium } = require("playwright-core");

const MAX_TURNS = 8; // cost/latency cap on tool rounds per person
const PAGE_CHARS = 6000; // page text handed back to Claude per read
// verbose_research is prompt input, not an archive: michael-gibson's raw sources came to
// 185KB, which would swamp any talking-point call. 16000 matches the sibling knob
// TALKING_POINT_TRANSCRIPT_CHARS so research and transcript get comparable room. The
// untruncated pages stay in the `sources` table -- see verbose() for the whole thing.
const VERBOSE_CHARS = 16000;
const PARALLEL = 5; // people researched at once (Anthropic rate limit, not Browserbase's 25)
const BB = "https://api.browserbase.com/v1";
const BB_HEADERS = { "X-BB-API-Key": process.env.BROWSERBASE_API_KEY, "Content-Type": "application/json" };

const TOOLS = [
  {
    name: "search",
    description: "Web search. Returns titles + URLs; LinkedIn post titles here are often the only LinkedIn data you'll get.",
    input_schema: { type: "object", properties: { query: { type: "string" } }, required: ["query"] },
  },
  {
    name: "read",
    description: "Read a web page as text. LinkedIn and some sites block this; don't retry them.",
    input_schema: { type: "object", properties: { url: { type: "string" } }, required: ["url"] },
  },
  {
    name: "answer",
    description: "Finish. Call exactly once with the result.",
    input_schema: {
      type: "object",
      properties: {
        found: { type: "boolean", description: "true only if sources clearly match this name AND role/company" },
        bullets: {
          type: "array",
          items: { type: "string" },
          description: "3-5 bullets, <= 12 words each: role/company, notable work, interests. Caption-sized.",
        },
      },
      required: ["found", "bullets"],
    },
  },
];

async function bb(endpoint, body) {
  const res = await fetch(`${BB}/${endpoint}`, { method: "POST", headers: BB_HEADERS, body: JSON.stringify(body) });
  if (!res.ok) throw new Error(`browserbase ${endpoint} ${res.status}: ${(await res.text()).slice(0, 200)}`);
  return res.json();
}

async function search(query) {
  const { results } = await bb("search", { query: query.slice(0, 200), numResults: 8 });
  return results.map((r) => `${r.title} | ${r.url}`).join("\n") || "No results.";
}

async function read(url) {
  if (!/^https?:\/\//.test(url)) return "Error: url must start with http:// or https://";
  const page = await bb("fetch", { url, format: "markdown", allowRedirects: true });
  if (page.statusCode !== 200) return `Blocked or failed (HTTP ${page.statusCode}).`;
  const text = String(page.content || "");
  // ponytail: short Fetch output = page is rendered by JS, so open a real browser; 500 chars is a guess
  return text.length > 500 ? text : await browse(url); // full text; the caller cuts the model's copy
}

async function browse(url) {
  const session = await bb("sessions", {});
  const browser = await chromium.connectOverCDP(session.connectUrl);
  try {
    const page = browser.contexts()[0].pages()[0];
    await page.goto(url, { waitUntil: "networkidle", timeout: 20000 }).catch(() => {});
    return (await page.innerText("body")).replace(/\s+/g, " ").trim();
  } finally {
    await browser.close(); // ends the session so it stops billing
  }
}

async function research(profile, log = () => {}) {
  const sources = []; // raw page text, kept for the sources table
  const client = new Anthropic({
    apiKey: process.env.ANTHROPIC_API_KEY,
    defaultHeaders: process.env.ANTHROPIC_WORKSPACE_ID
      ? { "anthropic-workspace-id": process.env.ANTHROPIC_WORKSPACE_ID }
      : undefined,
  });
  const known = Object.entries(profile)
    .filter(([k, v]) => !["id", "verbose_research", "concise_research"].includes(k) && v)
    .map(([k, v]) => `${k}: ${v}`);
  const messages = [
    {
      role: "user",
      content:
        `Today is ${new Date().toDateString()}. I'm about to meet this person at Hack the North 2026:\n` +
        `${known.join("\n")}\n\nSearch for them, read the best 1-3 sources, then call answer. ` +
        "Only use facts from pages about THIS person (same name and role/company); if you can't " +
        "confirm that, call answer with found=false. bullets must be a real array of 3-5 separate "
        + "strings, not one joined string. Be fast: few searches, few reads, " +
        "and issue independent searches/reads in the same turn.",
    },
  ];

  for (let turn = 0; turn < MAX_TURNS; turn++) {
    const response = await client.messages.create({
      model: "claude-sonnet-5",
      max_tokens: 1024,
      thinking: { type: "disabled" }, // speed over depth: each step is a simple "what to read next"
      output_config: { effort: "low" }, // fewer, consolidated tool calls
      tools: TOOLS,
      // last turn: force the answer instead of another search
      tool_choice: turn === MAX_TURNS - 1 ? { type: "tool", name: "answer" } : { type: "any" },
      messages,
    });
    messages.push({ role: "assistant", content: response.content });

    const calls = response.content.filter((b) => b.type === "tool_use");
    const done = calls.find((c) => c.name === "answer");
    if (done) return { ...done.input, sources };
    // parallel calls run at once; all results go back in one message
    const results = await Promise.all(
      calls.map(async (call) => {
        log(`${call.name} ${call.input.query || call.input.url}`);
        try {
          const isRead = call.name === "read";
          const full = isRead ? await read(call.input.url) : await search(call.input.query);
          sources.push({ url: isRead ? call.input.url : `search:${call.input.query}`, text: full });
          // the model sees a slice; people.db keeps everything Browserbase returned
          return { type: "tool_result", tool_use_id: call.id, content: isRead ? full.slice(0, PAGE_CHARS) : full };
        } catch (err) {
          return { type: "tool_result", tool_use_id: call.id, content: `Error: ${err.message}`, is_error: true };
        }
      })
    );
    messages.push({ role: "user", content: results });
  }
  throw new Error("no answer within MAX_TURNS");
}

// The model does not reliably hand back a real array here: sometimes it is one joined
// string, sometimes a JSON array as text, and sometimes that is still wrapped in raw
// tool-call markup (`<parameter name="bullets">[...]`) which otherwise lands in the
// database verbatim. Unwrap, parse if it looks like a list, else split on lines.
// Browserbase returns page text with HTML entities intact, and the model copies them
// straight into bullets ("VP Engineering &amp; GM Canada"), which then renders literally
// on the caption box. Node has no stdlib decoder and this is not worth a dependency.
function unescapeHtml(s) {
  return String(s)
    .replace(/&(amp|lt|gt|quot|apos|nbsp|#39|#x27|#x2F);/g, (m, e) => ({
      amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " ", "#39": "'", "#x27": "'", "#x2F": "/",
    })[e])
    .replace(/&#(\d+);/g, (m, n) => String.fromCharCode(+n));
}

function toBullets(v) {
  let items = v;
  if (!Array.isArray(items)) {
    const s = String(items ?? "").replace(/<\/?parameter[^>]*>/g, "").trim();
    items = s.startsWith("[") ? tryJson(s) : s.split("\n");
  }
  return items.map((b) => unescapeHtml(b).replace(/^[-*]\s*/, "").trim()).filter(Boolean);
}

function tryJson(s) {
  try {
    const parsed = JSON.parse(s);
    return Array.isArray(parsed) ? parsed : [s];
  } catch {
    return [s];
  }
}

function selftest() {
  const eq = (got, want, label) => {
    const [a, b] = [JSON.stringify(got), JSON.stringify(want)];
    if (a !== b) throw new Error(`${label}: got ${a}, want ${b}`);
  };
  eq(toBullets(["a", "b"]), ["a", "b"], "array passes through");
  eq(toBullets('<parameter name="bullets">["a", "b"]'), ["a", "b"], "tool markup unwrapped");
  eq(toBullets('["a", "b"]'), ["a", "b"], "json array as string");
  eq(toBullets("- a\n* b\n\n"), ["a", "b"], "lines with bullet chars");
  eq(toBullets(undefined), [], "missing");
  eq(toBullets(["VP Engineering &amp; GM Canada"]), ["VP Engineering & GM Canada"], "html entity");
  eq(toBullets(["a &lt;b&gt; c &#39;d&#39;"]), ["a <b> c 'd'"], "more entities");
  console.log("ok");
}

// Same rule as seed_profiles.person_id: one id scheme everywhere, never two.
function personId(name) {
  return name.toLowerCase().replace(/'/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}


function openDb() {
  const db = new DatabaseSync(path.join(__dirname, "..", "people.db"));
  const cols = db.prepare("PRAGMA table_info(people)").all().map((c) => c.name);
  for (const col of ["verbose_research", "concise_research"]) {
    if (!cols.includes(col)) db.exec(`ALTER TABLE people ADD COLUMN ${col} TEXT`);
  }
  // Own table, not a people column: server.py does SELECT * and json.dumps the whole row
  // into brain.py's prompt, and page text would swamp it.
  db.exec("CREATE TABLE IF NOT EXISTS sources (id TEXT, url TEXT, text TEXT, fetched_at TEXT)");
  return db;
}

// The verbose half of the research contract: every page and search result Browserbase
// returned, whole and unsummarised. It is NOT a people.db column on purpose -- server.py
// does SELECT * and brain.py puts the whole profile in every prompt, so a 30KB blob per
// person would ride along on every LLM call. Read it from `sources` only when asked.
function verbose(db, id) {
  return db
    .prepare("SELECT url, text FROM sources WHERE id = ? ORDER BY rowid")
    .all(id)
    .map((s) => `# ${s.url}\n${s.text}`)
    .join("\n\n");
}


async function main() {
  for (const key of ["ANTHROPIC_API_KEY", "BROWSERBASE_API_KEY"]) {
    if (!process.env[key]) {
      console.error(`Missing ${key}. Set it in server/.env.`);
      process.exit(1);
    }
  }
  const db = openDb();
  const arg = process.argv[2];
  if (arg === "--selftest") { selftest(); db.close(); return; }
  if (arg === "--verbose") {  // dump the raw sources for one person, no API calls
    const out = verbose(db, personId(process.argv[3] || ""));
    if (!out) console.error(`No stored sources for '${process.argv[3]}'. Research them first.`);
    else process.stdout.write(out + "\n");
    db.close();
    return;
  }
  const id = arg && personId(arg);
  let people = id
    ? db.prepare("SELECT * FROM people WHERE id = ?").all(id)
    : arg
      ? []
      : db.prepare("SELECT * FROM people WHERE concise_research IS NULL").all();
  // Not in the roster yet: add them rather than making the caller write SQL first.
  // personId() is seed_profiles.person_id, so "Jane Doe" and "jane-doe" are one row.
  if (arg && people.length === 0) {
    const name = arg.includes(" ") ? arg : null;
    if (!name) {
      console.error(`No person '${id}' in people.db. Pass a full name to add them: `
        + `node research.js "Jane Doe" "Figma, Designer"`);
      process.exit(1);
    }
    db.prepare("INSERT INTO people (id, name, role) VALUES (?, ?, ?)")
      .run(id, name, process.argv[3] || "");
    console.log(`added ${id} to people.db`);
    people = db.prepare("SELECT * FROM people WHERE id = ?").all(id);
  }
  console.log(`Researching ${people.length} people, ${PARALLEL} at a time...`);

  const save = db.prepare("UPDATE people SET verbose_research = ?, concise_research = ? WHERE id = ?");
  const dropSources = db.prepare("DELETE FROM sources WHERE id = ?");
  const saveSource = db.prepare("INSERT INTO sources (id, url, text, fetched_at) VALUES (?, ?, ?, datetime('now'))");
  let failed = 0;
  for (let i = 0; i < people.length; i += PARALLEL) {
    await Promise.all(
      people.slice(i, i + PARALLEL).map(async (p) => {
        const t = Date.now();
        try {
          const r = await research(p, (msg) => console.log(`  [${p.id}] +${((Date.now() - t) / 1000).toFixed(1)}s ${msg}`));
          // found=false stores "" (tried, nothing trustworthy) so a wrong person never reaches the caption
          // the model sometimes swaps string and array for these, and sqlite binds neither undefined nor arrays
          const bullets = toBullets(r.bullets).join("\n");
          // verbose is never model output: it is every page and search result Browserbase
          // returned, whole. Stored even when found=false -- it is the record of what was read.
          const raw = r.sources.map((s) => `# ${s.url}\n${s.text}`).join("\n\n").slice(0, VERBOSE_CHARS);
          save.run(raw, r.found ? bullets : "", p.id);
          dropSources.run(p.id);
          for (const s of r.sources) saveSource.run(p.id, s.url, s.text);
          console.log(`${r.found ? "OK  " : "MISS"} ${p.id} (${((Date.now() - t) / 1000).toFixed(0)}s)`
            + `  verbose ${raw.length} chars from ${r.sources.length} sources`);
          if (r.found) console.log(bullets.split("\n").map((b) => `     - ${b}`).join("\n"));
        } catch (err) {
          failed++; // left NULL, so the next run retries it
          console.log(`FAIL ${p.id}: ${err.message}`);
        }
      })
    );
  }
  db.close();
  if (failed) process.exitCode = 1; // not process.exit(): it crashes Node on Windows with sockets still open
}

module.exports = { search, read, research, verbose, toBullets, openDb };

if (require.main === module) {
  main().catch((err) => {
    console.error("Research failed:", err.message);
    process.exit(1);
  });
}
