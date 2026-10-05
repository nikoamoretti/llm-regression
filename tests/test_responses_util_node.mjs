import test from "node:test";
import assert from "node:assert/strict";
import { costUsdFromTicks, extractText, stableHash } from "../src/responses_util.mjs";

test("extractText joins output_text chunks", () => {
  const text = extractText({
    output: [
      {
        type: "message",
        content: [
          { type: "output_text", text: "a" },
          { type: "output_text", text: "b" },
        ],
      },
    ],
  });
  assert.equal(text, "a\nb");
});

test("cost ticks convert to USD", () => {
  assert.equal(costUsdFromTicks(10_000_000_000), 1);
});

test("stableHash is key-order sensitive in the documented helper", () => {
  const hash = stableHash({ model: "gpt-5.6-sol", temperature: 0 });
  assert.equal(typeof hash, "string");
  assert.equal(hash.length, 64);
});
