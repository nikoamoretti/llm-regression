#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const graderRoot = path.dirname(fileURLToPath(import.meta.url));
const workspace = process.env.WORKSPACE || "/workspace";

function runGroup(name) {
  const dir = path.join(graderRoot, "hidden_tests", name);
  if (!fs.existsSync(dir)) {
    return { passed: 0, total: 0, tests: [], log: "" };
  }
  const files = fs.readdirSync(dir).filter((f) => f.endsWith(".mjs") || f.endsWith(".ts"));
  let passed = 0;
  let total = 0;
  const tests = [];
  let log = "";
  for (const file of files) {
    const result = spawnSync(
      process.execPath,
      ["--experimental-strip-types", path.join(dir, file)],
      {
        cwd: workspace,
        encoding: "utf8",
        env: {
          ...process.env,
          WORKSPACE: workspace,
          NODE_PATH: workspace,
        },
      },
    );
    log += result.stdout + result.stderr;
    const ok = result.status === 0;
    total += 1;
    if (ok) passed += 1;
    tests.push({
      id: `${name}/${file}`,
      group: name,
      passed: ok,
      failure_signature: ok ? null : (result.stderr || result.stdout).slice(0, 400),
    });
  }
  return { passed, total, tests, log: log.slice(-4000) };
}

const functional = runGroup("functional");
const regression = runGroup("regression");
const constraints = runGroup("constraints");
const payload = {
  functional: { passed: functional.passed, total: functional.total },
  regression: { passed: regression.passed, total: regression.total },
  constraints: { passed: constraints.passed, total: constraints.total },
  forbidden_changes: [],
  strict_pass:
    functional.total > 0 &&
    functional.passed === functional.total &&
    regression.passed === regression.total &&
    (constraints.total === 0 || constraints.passed === constraints.total),
  tests: [...functional.tests, ...regression.tests, ...constraints.tests],
};
const text = JSON.stringify(payload);
process.stdout.write(text + "\n");
fs.writeFileSync(path.join(workspace, ".grade.json"), text);
process.exit(payload.strict_pass ? 0 : 1);
