// Minimal round-trip test: text in -> Claude -> text out.
// Mirrors the shape of ILlmClient.Query(prompt, onResult) from the Unity side
// (Assets/Scripts/LLM/ILlmClient.cs), so this can later be swapped in behind
// a real HTTP endpoint that CaptionPipeline calls instead of StubLlmClient.
//
// Usage:
//   ANTHROPIC_API_KEY=sk-... node test-echo.js "your text here"

require("dotenv").config();
const Anthropic = require("@anthropic-ai/sdk").default;

async function query(prompt) {
  const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });

  const response = await client.messages.create({
    model: "claude-sonnet-5",
    max_tokens: 512,
    messages: [{ role: "user", content: prompt }],
  });

  return response.content
    .filter((block) => block.type === "text")
    .map((block) => block.text)
    .join("");
}

async function main() {
  const input = process.argv.slice(2).join(" ") || "Say hello in one sentence.";

  if (!process.env.ANTHROPIC_API_KEY) {
    console.error("Missing ANTHROPIC_API_KEY. Set it in server/.env or export it before running.");
    process.exit(1);
  }

  console.log(`IN:  ${input}`);
  const output = await query(input);
  console.log(`OUT: ${output}`);
}

main().catch((err) => {
  console.error("Request failed:", err.message);
  process.exit(1);
});
