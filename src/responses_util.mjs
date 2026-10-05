// Generic Responses-API text/hash helpers used by optional Node tests.
import crypto from "node:crypto";

export function stableHash(value) {
  const json = JSON.stringify(value, Object.keys(value).sort());
  return crypto.createHash("sha256").update(json).digest("hex");
}

export function extractText(response) {
  const chunks = [];
  for (const item of response.output ?? []) {
    if (item.type !== "message") continue;
    for (const content of item.content ?? []) {
      if (content.type === "output_text" && typeof content.text === "string") {
        chunks.push(content.text);
      }
    }
  }
  return chunks.join("\n");
}

export function costUsdFromTicks(costUsdTicks) {
  if (costUsdTicks == null) return null;
  return Number(costUsdTicks) / 10_000_000_000;
}
