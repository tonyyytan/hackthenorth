// Person research, precomputed: people.db profile -> Claude researches via Browserbase -> people.db.
// Browserbase does all the web access: Search API finds pages, Fetch API reads them, and a real
// cloud browser session renders the JS-heavy ones Fetch can't. Claude only decides what to read.
// Results land in people.db `research` + `opener`; server.py's SELECT * hands them to brain.py and Unity.
//
// Usage:
//   node research.js                 research everyone not yet researched
//   node research.js <person-id>     (re)research one person, e.g. tom-alterman

require("dotenv").config({ quiet: true });
const path = require("path");
const { DatabaseSync } = require("node:sqlite");
const Anthropic = require("@anthropic-ai/sdk").default;
const { chromium } = require("playwright-core");

const MAX_TURNS = 8; // cost/latency cap on tool rounds per person
const PAGE_CHARS = 6000; // page text handed back to Claude per read
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
        summary: { type: "string", description: "<= 50 words: who they are, what they've built/done recently" },
        opener: { type: "string", description: "<= 25 words: one specific conversation opener" },
      },
      required: ["found", "summary", "opener"],
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
  return (text.length > 500 ? text : await browse(url)).slice(0, PAGE_CHARS);
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
  const client = new Anthropic({
    apiKey: process.env.ANTHROPIC_API_KEY,
    defaultHeaders: process.env.ANTHROPIC_WORKSPACE_ID
      ? { "anthropic-workspace-id": process.env.ANTHROPIC_WORKSPACE_ID }
      : undefined,
  });
  const known = Object.entries(profile)
    .filter(([k, v]) => !["id", "research", "opener"].includes(k) && v)
    .map(([k, v]) => `${k}: ${v}`);
  const messages = [
    {
      role: "user",
      content:
        `Today is ${new Date().toDateString()}. I'm about to meet this person at Hack the North 2026:\n` +
        `${known.join("\n")}\n\nSearch for them, read the best 1-3 sources, then call answer. ` +
        "Only use facts from pages about THIS person (same name and role/company); if you can't " +
        "confirm that, call answer with found=false. Be fast: few searches, few reads, " +
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
    if (done) return done.input;
    // parallel calls run at once; all results go back in one message
    const results = await Promise.all(
      calls.map(async (call) => {
        log(`${call.name} ${call.input.query || call.input.url}`);
        try {
          const content = call.name === "search" ? await search(call.input.query) : await read(call.input.url);
          return { type: "tool_result", tool_use_id: call.id, content };
        } catch (err) {
          return { type: "tool_result", tool_use_id: call.id, content: `Error: ${err.message}`, is_error: true };
        }
      })
    );
    messages.push({ role: "user", content: results });
  }
  throw new Error("no answer within MAX_TURNS");
}

function openDb() {
  const db = new DatabaseSync(path.join(__dirname, "..", "people.db"));
  const cols = db.prepare("PRAGMA table_info(people)").all().map((c) => c.name);
  for (const col of ["research", "opener"]) {
    if (!cols.includes(col)) db.exec(`ALTER TABLE people ADD COLUMN ${col} TEXT`);
  }
  return db;
}

async function main() {
  for (const key of ["ANTHROPIC_API_KEY", "BROWSERBASE_API_KEY"]) {
    if (!process.env[key]) {
      console.error(`Missing ${key}. Set it in server/.env.`);
      process.exit(1);
    }
  }
  const db = openDb();
  const id = process.argv[2];
  const people = id
    ? db.prepare("SELECT * FROM people WHERE id = ?").all(id)
    : db.prepare("SELECT * FROM people WHERE research IS NULL").all();
  if (id && people.length === 0) {
    console.error(`No person '${id}' in people.db.`);
    process.exit(1);
  }
  console.log(`Researching ${people.length} people, ${PARALLEL} at a time...`);

  const save = db.prepare("UPDATE people SET research = ?, opener = ? WHERE id = ?");
  let failed = 0;
  for (let i = 0; i < people.length; i += PARALLEL) {
    await Promise.all(
      people.slice(i, i + PARALLEL).map(async (p) => {
        const t = Date.now();
        try {
          const r = await research(p, (msg) => console.log(`  [${p.id}] +${((Date.now() - t) / 1000).toFixed(1)}s ${msg}`));
          // found=false stores "" (tried, nothing trustworthy) so a wrong person never reaches the caption
          save.run(r.found ? r.summary : "", r.found ? r.opener : "", p.id);
          console.log(`${r.found ? "OK  " : "MISS"} ${p.id} (${((Date.now() - t) / 1000).toFixed(0)}s)`);
          if (r.found) console.log(`     ${r.summary}\n     -> ${r.opener}`);
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

module.exports = { search, read, research };

if (require.main === module) {
  main().catch((err) => {
    console.error("Research failed:", err.message);
    process.exit(1);
  });
}
