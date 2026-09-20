// Minimal Browserbase round trip: URL in -> cloud browser -> page title + text out.
// First step toward the research agent: Claude will call this as a fetch tool.
//
// Usage:
//   node test-scrape.js [url]        (BROWSERBASE_API_KEY in server/.env)

require("dotenv").config();
const { chromium } = require("playwright-core");

async function scrape(url) {
  // ponytail: raw REST instead of @browserbasehq/sdk, one call doesn't need a client lib
  const res = await fetch("https://api.browserbase.com/v1/sessions", {
    method: "POST",
    headers: { "X-BB-API-Key": process.env.BROWSERBASE_API_KEY, "Content-Type": "application/json" },
    body: "{}",
  });
  if (!res.ok) throw new Error(`session create ${res.status}: ${await res.text()}`);
  const session = await res.json();

  const browser = await chromium.connectOverCDP(session.connectUrl);
  try {
    const page = browser.contexts()[0].pages()[0];
    await page.goto(url, { waitUntil: "domcontentloaded" });
    return {
      sessionId: session.id,
      title: await page.title(),
      text: (await page.innerText("body")).replace(/\s+/g, " ").trim(),
    };
  } finally {
    await browser.close(); // ends the session so it stops billing
  }
}

async function main() {
  if (!process.env.BROWSERBASE_API_KEY) {
    console.error("Missing BROWSERBASE_API_KEY. Set it in server/.env.");
    process.exit(1);
  }
  const url = process.argv[2] || "https://example.com";
  const out = await scrape(url);
  console.log(`SESSION: https://browserbase.com/sessions/${out.sessionId}`);
  console.log(`TITLE:   ${out.title}`);
  console.log(`TEXT:    ${out.text.slice(0, 300)}`);
  if (!out.title) throw new Error("empty page title");
}

main().catch((err) => {
  console.error("Scrape failed:", err.message);
  process.exit(1);
});
