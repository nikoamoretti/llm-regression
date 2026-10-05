# Grok and Astra longitudinal regression harness

This repository measures **observable end-to-end coding performance** of
`grok-4.6` and `gpt-6-astra` on the same frozen private coding workload.

The canonical experiment is:

```text
two models × four efforts × two tracks × frozen task versions × repeated time windows
```

| Dimension | Canonical values |
|---|---|
| Model | `grok-4.6`, `gpt-6-astra` |
| Effort | `low`, `medium`, `high`, `xhigh` |
| Track | `model_only`, `agentic` |
| Outcome | strict task success |
| Compare | same model + same effort + same track + same task version |
| Unit | task, not attempt |

It answers:

> For this exact model surface, reasoning effort, task, tool environment,
> grader, and budget, has observable task success changed relative to a locked
> historical baseline?

It does **not** prove that xAI or OpenAI changed model weights. A measured drop
is a change in strict task success under a controlled configuration.

Astra `max` is an auxiliary series and is never blended into the four-level
comparison. Codex / `gpt-5.6-sol` remains an optional unblended product series.

## What this measures

- Frozen private coding tasks
- Hidden graders that the model cannot see
- Strict task success after the attempt exits
- Independent time series for each model × effort × track
- Quality given valid execution, operational availability, and end-to-end success

## What this does not prove

- That a provider “nerfed” a model
- That weights changed
- That Grok and Astra are interchangeable products

Never write “the model degraded by 12%” when the measurement is
“strict task success decreased by 12 percentage points on this benchmark.”

There is no blended Grok score and no blended Astra score. Longitudinal
questions stay inside one series. Cross-model comparison is optional and
separate.

## Tracks

`model_only` materializes a pristine workspace, sends a deterministic
lexicographic snapshot, permits **no tools**, requires a patch or structured
JSON patch, applies it, then grades.

`agentic` exposes only six local tools:

```text
list_files
read_file
search_text
write_file
apply_patch
run_command
```

Both models call those function definitions. The coordinator executes every
tool locally. `run_command` takes an argv array; it is never `shell=True`.
Neither model receives a native code-execution environment.

## Providers

Both canonical models use Responses-style APIs:

```text
https://api.x.ai/v1/responses
https://api.openai.com/v1/responses
```

Keys:

```text
XAI_API_KEY
OPENAI_API_KEY
# optional alias, still recorded as OpenAI:
ASTRA_API_KEY
```

Requests use `store: false`. Astra requests do **not** send `temperature` or
`top_p`. Seeds (`schedule_seed`, `bootstrap_seed`, `fixture_seed`,
`grader_seed`) randomize the experiment and freeze the task environment. They
do not make the LLM deterministic.

## Pairing

Pair slots are built before any model is called. A pair key includes suite,
task, track, effort, repeat, schedule seed, and protocol version. It does
**not** include model or date. Model order inside a block is randomized.

## Quality vs availability

| Metric | Definition |
|---|---|
| Quality given valid execution | `quality_pass / (quality_pass + quality_fail)` |
| Operational availability | valid model executions / scheduled attempts |
| End-to-end success | `quality_pass / all scheduled attempts` |

Infrastructure failures stay visible. They are not mixed into model-quality
statistics. Harness bugs are `harness_fail`. A requested model/effort that
cannot be verified is `invalid_configuration`.

Repeated attempts on one task are **not** extra tasks. Suite score is the mean
of per-task means.

## Task immutability

```text
tasks/<TASK_ID>/v1/{task.yaml,prompt.md,fixture/}
private_graders/<TASK_ID>/v1/{grader,hidden tests,gold.patch,negatives/}
```

A locked `v1` is content-hashed. Changing it is corruption; create `v2`.

## Hidden graders

This repository is public, so the benchmark tasks' graders (hidden tests, gold
patches, negatives) are not in it: anything published can end up in a future
model's training data. `private_graders/` holds only the README and the two
demo tasks' graders, which are public on purpose and in no benchmark suite.

- Frozen tasks: `scripts/fetch_graders.sh` copies their graders from the
  private `nikoamoretti/llm-regression-archive`, and `python -m
  runner.verify_hashes` checks them against the hashes in each `task.yaml`.
- Mined tasks: `runner mine` rebuilds their graders from the private source
  repositories.

Without them `runner evaluate` refuses to run a task, and the validators fail
unless given `--allow-missing-graders`. The daily run fetches and checks them,
and runs the gold and negative self-test, before it grades anything.

## Baseline procedure

Gold, fake, synthetic, and `source=none` runs are labeled
`scientific_data=false` and **cannot** become a scientific baseline.

```bash
python -m runner baseline qualify --runs run_A,run_B,run_C
python -m runner baseline create \
  --runs run_A,run_B,run_C \
  --name grok-4.6-xhigh-agentic-canary-v1 \
  --model grok-4.6 \
  --effort xhigh \
  --track agentic \
  --suite canary
python -m runner baseline lock grok-4.6-xhigh-agentic-canary-v1
```

Do this separately for every series. A locked baseline cannot be edited.
Incompatible identities are rejected unless `--force` is used, which labels
the report **NON-CANONICAL**.

Do not lock the first successful afternoon as the scientific baseline.

## Statistical method

Primary inference is a **seeded hierarchical bootstrap** of paired task-level
differences (`family → task → attempt`, or `task → attempt` when families are
too few). Wilson intervals are descriptive only.

Final reports use ≥ 10,000 bootstrap iterations. The seed is stored on the
analysis record.

## Commands

Offline verification:

```bash
python -m runner doctor --offline
python -m runner test-graders --suite canary --fast
python -m runner evaluate --suite canary --source gold --tracks model_only --models grok-4.6 --efforts xhigh --fresh
```

The gold evaluate exercises plumbing. It is not a baseline.

Live paired canary:

```bash
export XAI_API_KEY=...
export OPENAI_API_KEY=...   # or ASTRA_API_KEY

python -m runner doctor --live --models grok-4.6,gpt-6-astra --tracks model_only,agentic --effort xhigh

python -m runner evaluate \
  --suite canary \
  --models grok-4.6,gpt-6-astra \
  --efforts low,medium,high,xhigh \
  --tracks model_only,agentic \
  --repeats 1 \
  --schedule-seed 20260911 \
  --blocked-randomization \
  --fresh
```

Optional Codex / Sol series (never mixed with Grok/Astra):

```bash
python -m runner doctor --track codex_product --model gpt-5.6-sol --effort max --live
python -m runner evaluate --suite canary --track codex_product --model gpt-5.6-sol --efforts max
```

Optional Claude Code product series (`claude_code_product`, never mixed with
API or Codex series). Claude Code runs headless on your Claude subscription,
with the frozen tool set `Bash, Read, Edit, Write, Glob, Grep`, `--restricted`,
no MCP servers, no skills, and no saved sessions. `low`–`xhigh` are the
comparison efforts; `max` is auxiliary.

### Containers

Scientific attempts run in pinned containers (Docker, or Docker Desktop on a
Mac). `python -m runner images build` builds `docker/Dockerfile.toolchain`
(base images pinned by multi-arch digest) and records the image IDs in
`artifacts/images.json`:

- `toolchain`: Python 3.12, Node 22, Go 1.23, Rust 1.85. Every hidden grader
  runs here, in a fresh container with no network, as your user. Task
  manifests keep their frozen `sol-regression-*@sha256:local` names, which
  resolve to this image.
- `claude-code`: the toolchain plus the Claude Code CLI at a pinned version
  (`--claude-code-version`, default `2.1.286`). Each attempt gets a fresh
  container: only the task workspace is mounted, HOME and the Claude config are
  an empty tmpfs (your `~/.claude`, `CLAUDE.md`, memory and plugins never reach
  the model), and the subscription token arrives as `CLAUDE_CODE_OAUTH_TOKEN`
  by name, never in argv.
- Egress: agent containers sit on an internal Docker network whose only exit is
  a CONNECT proxy (`runner/egress_proxy.py`) allowing the hosts in
  `configs/egress.yaml` (`api.anthropic.com:443`). Bash in the agent cannot
  reach anything else. `doctor --live` reports every destination the probe used.

A scientific attempt without its containers is `invalid_configuration` before
the agent runs, so no usage is spent. `LLMREG_ALLOW_HOST=1` opts into host
execution instead (hermetic `CLAUDE_CONFIG_DIR` + token, or
`CLAUDE_ALLOW_USER_CONFIG=1` for your own config, recorded as `user_config`).
Image IDs, CLI version, egress allowlist and runtime are recorded with every
run; rebuilding the images is a deliberate change to the measurement.

Fail closed: `ANTHROPIC_API_KEY` is always stripped, and an attempt that
reports API-key auth or any model other than the one requested (including a
served fallback) is `invalid_configuration`. Claude Code does not echo the
applied effort, so `verified_effort` stays empty.

```bash
python -m runner images build            # once per CLI version; Docker must be running
claude setup-token                       # once, on the Max subscription
export CLAUDE_CODE_OAUTH_TOKEN=...       # keep out of shell history and the repo

python -m runner doctor --track claude_code_product --model claude-opus-5-5 --effort xhigh --live
python -m runner evaluate --suite canary --track claude_code_product \
  --models claude-opus-5-5 --efforts xhigh --repeats 1 --fresh
python -m runner egress down             # remove the proxy and network when done
```

Behind a TLS-inspecting proxy, build with
`--build-network host --extra-ca /path/to/ca.pem` (the CA is a build secret,
not stored in the image).

Subscription usage limits apply: an attempt that hits one is `infra_fail`
(`usage_limit`), not a quality failure. Measure one effort before sweeping all
five. Container integration tests: `LLMREG_DOCKER_TESTS=1 pytest tests/test_containers.py`.

Other commands:

```bash
python -m runner compare --baseline grok-4.6-xhigh-agentic-canary-v1 --current <run id>
python -m runner power --baseline grok-4.6-xhigh-agentic-canary-v1 --effort xhigh --minimum-drop-pp 8 --target-power 0.80
python -m runner dashboard build
```

## Sizing and calibration

Decide what drop you need to detect before collecting data. A window of
attempts is compared with a reference window of the same size; at 80% power and
5% two-sided, with tasks passing about half the time:

| Attempts per window | Smallest detectable drop |
|---|---|
| 10 (canary, 1 repeat) | ~63 points |
| 30 | ~36 points |
| 100 | ~20 points |
| 171 | 15 points |
| 389 | 10 points |

```bash
# Run each task a few times, then keep only tasks that carry signal.
python -m runner evaluate --suite canary --track claude_code_product \
  --models claude-opus-5-5 --efforts xhigh --repeats 5 --fresh
python -m runner calibrate --series claude-opus-5-5/xhigh/claude_code_product \
  --out artifacts/calibration/opus-xhigh.json

# What can your usage budget detect? Use attempts per week, or an
# API-equivalent dollar budget with the per-attempt cost recorded so far.
python -m runner plan --attempts-per-week 60 --calibration artifacts/calibration/opus-xhigh.json
python -m runner plan --budget-usd-per-week 150 --series claude-opus-5-5/xhigh/claude_code_product
```

`calibrate` reports each task's strict pass rate (Wilson 95% interval), mean
partial score, and a verdict: `keep` (20–80% by default), `too_easy`,
`too_hard`, or `insufficient`. Gold, fake and other non-scientific attempts are
excluded unless `--any-source` is passed. Tasks a series always passes or always
fails cannot show a change; they only spend usage.

## Drift monitoring

`runner monitor` writes `artifacts/monitor/index.html` (light and dark, phone
and desktop, hover readouts, table views) and `monitor.json` for every series,
from graded scientific attempts only:

- daily and trailing-window share of attempts passed, with Wilson intervals;
- the latest window against a reference window (default: the first window),
  as a mean of per-task differences with a bootstrap CI over tasks and a
  sign-flip p-value;
- a risk-adjusted Bernoulli CUSUM: each attempt is scored against its own
  task's reference pass rate, so a shift in which tasks ran is not read as
  drift. The alarm threshold comes from an exact Markov-chain run-length
  calculation for `--arl0` (default: one false alarm per ~2000 attempts when
  nothing changed), so checking daily does not inflate false alarms. Each
  report states how many attempts the chart needs to flag a 10- or 20-point
  drop, and warns when the reference has too few attempts per task for the
  per-task rates to be trusted.

```bash
python -m runner monitor                         # every series in the database
python -m runner monitor --series claude-opus-5-5/xhigh/claude_code_product \
  --reference 2026-10-02:2026-10-15 --window-days 7
```

The exit code is 1 when any series alarmed in its latest window, so a scheduled
job can notify on it. An alarm reads "strict task success decreased on this
benchmark", never "the model degraded": pinned containers rule out harness
changes, not provider-side ones you cannot see.

## Mining tasks from your own history

Realistic, private tasks come from your own repositories: a bug-fix commit
whose change includes tests. `runner mine` turns one into a frozen task:

- fixture: the repository at the fix commit's parent (`git archive`)
- hidden tests: the fix commit's versions of its test files
- gold patch: the fix without its tests; negative: an empty patch
- grader: runs `--functional-command` on the hidden tests (and an optional
  `--regression-command` for the visible suite) inside the toolchain image.
  With `{junit}` every testcase counts and skipped tests count as failures.
  Test configuration (`conftest.py`, `pytest.ini`, jest/vitest/mocha configs) is
  restored to its pre-fix state and new copies are deleted, so an agent cannot
  make the hidden tests skip.

```bash
python -m runner mine --repo ~/code/my-service --commit 1a2b3c4 --id MINE-SVC-01 \
  --functional-command "python3 -m pytest -q {tests} --junitxml {junit}"
```

`mine` then runs the grader self-test: the hidden tests must fail on the fixture
and pass with the gold patch, or the task is not usable. The prompt is drafted
from the commit message and usually describes the fix; rewrite it as an issue
report before marking the task reviewed, then add the id to a suite. Run
`calibrate` before relying on a new task.

The toolchain image has Python 3.12 with pytest, Node 22 (`node --test`),
Go 1.23 and Rust 1.85, and graders run with no network.

### Task dependencies

Tests that need third-party Python packages pin them in the task:
`--uv-export` exports the fix commit's `uv.lock` (hash-locked, all extras), or
`--requirements FILE` takes any pinned pip file. The task stores it as
`environment/requirements.txt`, and its sha256 is recorded in the manifest.

```bash
python -m runner images build-env   # every task that pins requirements
```

`build-env` installs each distinct requirements file on top of the toolchain
image (the grader image) and the Claude Code image (the agent image), so the
agent works with the same packages the grader tests with. The images are
recorded under `environments` in `artifacts/images.json`. An environment built
on an older base image or `docker/Dockerfile.environment` counts as stale and
must be rebuilt. A task whose environment is missing is `invalid_configuration`
before the agent starts. It never falls back to the bare toolchain, where its
imports would fail and look like model failures. Agents still cannot install
anything at run time, because their only egress is the model API.

### Mined suites

Mined tasks copy the source repository, so only their recipes are committed:
`configs/mined.yaml` lists the repository, commit, test command and prompt
(`configs/mined/<ID>.md`), and `configs/mined.lock.json` records the content
hashes of each materialized task. The materialized tasks are gitignored. Mining is
deterministic, so materializing on another machine reproduces the same
frozen tasks:

```bash
python -m runner mine --batch configs/mined.yaml --repos-dir ~/code   # mine, check the lock, self-test
python -m runner images build-env
python -m runner evaluate --suite mined                                # claude-opus-5-5, claude_code_product
```

When reviewing a mined prompt, look for placeholder data in the hidden tests (fake hashes, ids, URLs, stored digests that do not match). A careful solution may validate such values the way the codebase does elsewhere and fail for no real reason. The prompt must then say that the value is accepted as-is. The first `high` run exposed this in two hard tasks.

The batch run fails if a task differs from the lock. `--record` accepts a
deliberate change, which is a new task version as far as history is concerned.
The `mined` suite has 22 tasks from `forecastlab`: 18 single-commit fixes and 4 hard composite tasks (`base:` in the recipe). Each composite task spans a chain of 2–6 related commits and 240–680 changed lines, starting from the tree before the first commit. Their prompts are
written as issue reports that state the contract the hidden tests check
(names, error codes, schema shapes) without describing the fix, and they await
owner review.

## How the agent worked

People who say a model "got nerfed" usually mean behaviour, not capability:
it stops without checking its work, leaves placeholder code, asks for
permission, or says it's done when it isn't. `python -m runner behavior`
derives these from each attempt's stored Claude Code event stream and
workspace diff, using fixed, versioned rules (`behavior-v4`) and no model calls:

| Flag | Meaning |
|---|---|
| `no_verification` | never ran the tests or the changed code |
| `not_reverified` | edited code after its last check |
| `deferred_to_user` | asked the user instead of acting (the runs are headless) |
| `false_success_claim` | said the work was done, but the hidden tests failed |
| `unverified_test_claim` | claimed tests pass without running any |
| `placeholder_code` | left placeholder or elided code in the diff |
| `weakened_tests` | skipped, xfailed or deleted tests |
| `gave_up` | said it could not finish, or ran out of turns |

It also records tool calls, turns, test runs, tool errors, output tokens,
subagent use, real throttling (rate-limit events whose status is not
`allowed`), and peak 5-hour and 7-day plan usage. The rules were checked
against real transcripts. Ad-hoc verification scripts count as checks, and
"I passed `fields`" is not a claim about tests. Each rule has tests in both
directions. The dashboard compares the latest two runs.

## Quality and clarity scores

Strict pass says whether the hidden tests pass. It does not say whether the
change is well made or honestly explained. `python -m runner judge` scores
every graded attempt on a fixed rubric (`quality-v1`): correctness beyond the
tests, design, scope, code clarity, and the accuracy of the agent's final
message, each 1–5, plus a list of claims the diff does not support.

- The judge runs through Claude Code on the subscription with every tool
  disabled and a JSON schema enforced, in the same locked-down container.
- It never sees which model wrote the candidate. The default judge is
  `claude-sonnet-5-5` at medium effort, a different model from the one under test, so
  it isn't grading its own style (`LLMREG_JUDGE_MODEL` / `LLMREG_JUDGE_EFFORT` override it). It
  ranks attempts like `claude-fable-5-1` did, within about 0.6 points, at about 1/7 of the usage. The
  dashboard only shows scores from the current judge, so trends never mix judges.
- The reference solution is shown as one acceptable approach, not the
  answer key.
- `--calibrate TASK...` scores the reference solution and an empty change
  with a false claim; a usable judge separates them by at least 2 points.
  `--repeats N` measures how consistent the judge is.
- Scores go in their own `judgments` table and are never blended into strict
  pass rates.
- Comparing products: the judge is a Claude model, so it may favour Claude's
  style when it scores Grok against Opus. Track each product against its own
  history first; read cross-product quality gaps of a few tenths as noise, and
  lean on strict pass for the head-to-head.

## Grok 4.7 on Cursor

A second product series measures Grok 4.7 as the Cursor CLI serves it
(`cursor_product` track, suite `product-grok`, the same tasks as `product`).
It answers the same question for Cursor users: did the model I use get worse?

- **Pinned CLI.** `images build` downloads one Cursor CLI build
  (`DEFAULT_CURSOR_VERSION` in `runner/images.py`) and refuses it unless its
  sha256 matches. There is no host mode, and the download host is not on the egress
  allowlist, so the CLI cannot update itself mid-series.
- **Same sandbox as Claude Code.** A fresh container per attempt, only the
  workspace mounted, an empty HOME, the key passed by name, and its own egress
  proxy limited to Cursor's API hosts (`configs/egress.yaml`). The CLI also tries
  github.com and registry.npmjs.org; both stay blocked, because GitHub access
  could reach a mined task's upstream fix. Attempts work without them.
- **Same tools, no web.** Claude Code runs without web tools, and a web search
  could find a mined task's upstream fix. The CLI runs without `--force`, with
  shell commands, reads and edits pre-approved, so it rejects web search and web
  fetch. `--force` would approve them despite deny rules: on the first live run
  Grok searched the web and found the upstream fix. Any attempt that still
  reaches the web is `invalid_configuration` (`web_access`) and never graded.
- **Verified model.** The model id is `grok-4.7-<effort>` (override with
  `LLMREG_CURSOR_MODEL`). An attempt whose stream names another model, or a
  different reasoning level, is `invalid_configuration`.
  `python -m runner doctor --track cursor_product --effort high --live` lists the
  account's models and runs one tiny probe.
- **Same measurements.** Behaviour metrics and the quality judge read Cursor's
  event stream with the same rules. Cursor reports tokens but no cost.

Setup: create a key in the Cursor dashboard (Integrations → User API Keys) on a
plan that includes Grok 4.7, then set it as `CURSOR_API_KEY` for the scheduled
run. Without it, the Cursor series is simply skipped.

## Scheduled cloud runs

The product series run unattended in a Claude Code cloud session, on the
Claude subscription and the Cursor plan (not the APIs). A daily routine wakes one
long-lived session that has both repositories attached and runs
`scripts/cloud_run.sh` there. A fresh session per run does not work: it has no
repositories attached and cannot attach them. The script does the following:

1. starts Docker and builds the pinned images (the CLI version stays pinned);
2. clones the source repositories, materializes the mined tasks from
   `configs/mined.yaml`, checks them against the lock and self-tests every
   task, so a broken environment fails before any subscription usage;
3. builds the dependency images;
4. restores the results database from the `results` branch
   (`python -m runner results pull`);
5. runs today's quarter of the `product` suite at `high` (and, when `CURSOR_API_KEY` is set, the same
   tasks of `product-grok` right after it), one attempt per task with several in
   parallel. `runner rotation` deals the tasks into 4 groups, hardest first, so every task runs once
   every 4 days. The composite hard tasks cost the most, so each of them runs once a week instead,
   one per weekday from Monday (`--composites-daily` puts them back in the daily rotation). A week
   costs about a quarter of running the full suite every day, and the drift monitor already adjusts
   for task mix;
6. derives behaviour metrics from each transcript and judges every graded
   attempt of the day's runs with the quality rubric;
7. runs the drift monitor, builds the results site (`python -m runner site`), and pushes the
   database, `runs.jsonl`, the monitor report and the site back to the `results` branch
   (`results push`).

`results push` refuses if the branch moved after the pull, so a run can never
overwrite attempts it didn't see. Each run builds its images fresh from pinned
digests and versions, and the image IDs it used are recorded with the run.

Cloud sessions reach the API through a TLS-inspecting gateway. `LLMREG_EXTRA_CA`
mounts that gateway's CA bundle into the agent container as
`NODE_EXTRA_CA_CERTS`. That adds trust and never relaxes certificate
verification. Egress is still limited to `api.anthropic.com:443` (Cursor's API hosts for the
Cursor series).

One-time setup: run `claude setup-token` on a machine logged in to the
subscription, then add the token to the cloud environment as the environment
variable `CLAUDE_CODE_OAUTH_TOKEN`. Until it is set, the script stops at its
first check, before any work.

## CI

GitHub Actions runs offline tests only. Live jobs must not execute untrusted
pull-request code with provider secrets. Real measurement belongs on a trusted
local or protected scheduled runner.

CI has no hidden graders. The harness tests and the grader self-test run on the
public `demo` suite, and the manifest checks pass `--allow-missing-graders`.

## Results site

`python -m runner site` writes a static report to `artifacts/site/`: the page
in `runner/site/` plus `data.json`. It answers one question per product: is
it degrading, and where? Each product is compared with its own first week:

- a verdict (degrading, possible degradation, no degradation, or too early
  while the baseline week runs) that names the checks that moved;
- a map of every check, baseline → last 7 days: pass rate overall, by
  difficulty, by source and by kind of task; the drift monitor; reviewer
  scores; lazy habits and test runs; attempts lost to outages and limits.
  Each check reads Worse (p < 0.002), Watch (p < 0.02), OK or Better. Pass
  rates compare each recent attempt with its own task's baseline record, so a
  week of harder tasks is not read as a drop (`runner/site/checks.js`, tested
  by `tests/test_site_checks_node.mjs`);
- where it shows: the tasks that failed in the last 7 days with their
  baseline record and the reviewer's note, and passes whose score fell a
  full point;
- the daily pass rate, every task, and the method.

`results push` stores it as `site/` on the `results` branch.

The Vercel project `nerf-watch` (team Yard Logix) deploys only that branch:
root directory `site`, no build step, and every other branch skipped by its
ignore command (code branches carry a placeholder `site/README.md` so the
root directory exists and the skip runs). Vercel blocks the git-triggered
deploy of each results commit, because the commit author is not a member of
the team, so the daily run ends by printing `DEPLOY SITE: results@<sha>` and
the session deploys that commit through the Vercel API (project `nerf-watch`,
target production, `gitSource` github `nikoamoretti/llm-regression`, ref
`results`). That updates https://nerf-watch.vercel.app. Vercel
Authentication covers every deployment, the production address included, so
the site opens only for members of the team. Keep that protection on "all
deployments": the default covers previews only, and this site deploys to
production. To look at a build locally, run
`python -m http.server -d artifacts/site`.

## API-series dashboard

Homepage: **Grok and Astra Longitudinal Monitor**. Each model × effort × track
has its own card. Availability is shown separately from quality. Codex / Sol
appears only as an optional unblended section.
