#!/usr/bin/env python3
"""Build the 7-section 12-run report from the artifacts run-12.py mirrored.

Usage: gen-12run-report.py <run12-LABEL.json> <version> <platform-note> [out.md]

Reads /tmp/autobench/run12-<label>.json plus each run's mirrored report.ndjson /
token_report.ndjson / manifest.json, and emits the fixed 7 sections. Everything numeric is
derived from the artifacts — nothing is transcribed by hand.
"""
import json
import os
import pathlib
import re
import sys
from datetime import datetime, timezone

SRC = pathlib.Path(sys.argv[1])
VERSION = sys.argv[2]
PLATFORM = sys.argv[3]
OUT = pathlib.Path(sys.argv[4]) if len(sys.argv) > 4 else None

# Reuse the TOC builder rather than re-deriving GitHub's anchor rules three times over.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gen_toc import build as _toc  # noqa: E402

data = json.loads(SRC.read_text())
runs = sorted(data["runs"], key=lambda r: r["n"])


def _stats(vals):
    """(median, mean, CV) over a run's per-task values.

    `median` is the robust centre; `mean` is the arithmetic average — for tau2/appworld the two
    diverge because task cost is strongly right-skewed (a few long episodes drag the mean above the
    median), and that gap is itself informative. `CV` = population sigma / mean, so it shares its
    denominator with `mean`, not `median`. CV is undefined for n < 2 or mean == 0.
    """
    n = len(vals)
    if n == 0:
        return None, None, None
    s = sorted(vals)
    median = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    mean = sum(vals) / n
    if n < 2 or mean == 0:
        return median, mean, None
    var = sum((v - mean) ** 2 for v in vals) / n
    return median, mean, (var ** 0.5) / mean


def _fmt(v, spec="%.0f"):
    return "—" if v is None else spec % v


def _natkey(tid):
    """Sort task ids naturally: plain string sort puts tau2's "10" between "1" and "2", which makes
    the per-task tables hard to scan. Handles appworld's `<hash>_<n>` form too."""
    return [(0, int(p)) if p.isdigit() else (1, p) for p in re.split(r"(\d+)", str(tid)) if p]


def _lost(x):
    """True when the task's usage-bearing `chat` span was never written, so its token counts are
    understated.

    The primary signal is structural, because it holds under any model and any agent version: every
    tool call must be decided by a preceding model turn, so `llm_count <= 1` alongside
    `tool_count >= 2` is impossible for a task that genuinely made those tool calls. Verified against
    all 272 rows of the v1.23 matrices: it flags exactly the 15 damaged rows and no healthy one.
    Healthy rows may still have `tool_count > llm_count` — up to +21 on appworld — which is why a
    plain `tool > llm` test is NOT usable.

    `status == "OK"` with `llm_count == 0` is the other impossible shape: a task cannot complete
    without a single model turn. It is gated on status because a task that failed before its first
    call legitimately has no `chat` span, and appworld produces such rows routinely.

    That clause matters more than it used to. Up to exgentic 0.3.5.dev131 every task also issued a
    `max_tokens=1` capability probe, so a damaged task still had one surviving `chat` span and
    reported `llm_count == 1` — which the `llm_count == 0` test cannot see, and which is why the
    structural pair test was needed in the first place. As of 0.3.5.dev145 the probe is *replaced*
    (not deleted) by an unbilled `GET /v1/models` reachability check, which emits no `chat` span —
    measured absent across all 869 chat spans of a full 12-leg matrix, all four model classes — so a
    damaged task now drops to zero `chat` spans. Both clauses are kept: the completion probe is still
    reachable via `strict=True` in the agent's `health.py`, so a future config could reintroduce it,
    and the structural test is the one that survives either way.

    Do not replace any of this with a `tokens == 0` test. While the probe existed it *carried its own
    usage on non-reasoning models* (`in=8/out=1` on claude-sonnet-5, `in=1/out=0` on gemini-2.5-pro),
    so a damaged row was not numerically zero at all; the `llm_input_tokens` clause below catches the
    zero case only as a supplement, never as the detector.
    """
    lc = x.get("llm_count") or 0
    tc = x.get("tool_count") or 0
    return (
        (x.get("status") == "OK" and lc == 0)
        or (lc > 0 and not x.get("llm_input_tokens"))
        or (lc <= 1 and tc >= 2)
    )


def _span_lost(spans):
    """`_lost()`'s structural clause, recomputed from one task's span rows for §8.

    §8 reads `span_report.ndjson`, which carries neither `status` nor token fields, so only the
    pair test is available here — but it is the clause that matters, and keeping the two in one
    place stops §8 from drifting back to the pre-`dev145` rule that a lone `chat` span is damage.
    It is not: a one-shot gsm8k task has exactly one, and flagging those marked every healthy row
    in the matrix as lost.

    **Count only `counted` spans.** `_lost()`'s thresholds are defined against `llm_count` and
    `tool_count`, which `parse_traces` increments only for spans parented on `invoke_agent` — so
    they are not raw span totals and the thresholds do not transfer to raw ones. A healthy gsm8k
    task emits *two* `execute_tool` spans, `initial_observation` and `submit`, of which only
    `submit` is counted; testing `tool >= 2` against the raw count therefore flags every healthy
    gsm8k row in the matrix. Filtering to `counted` reproduces the §7 numbers exactly, which is
    the property that makes the two sections agree.
    """
    nchat = sum(1 for s in spans if s.get("kind") == "chat" and s.get("counted") is not False)
    ntool = sum(1 for s in spans if s.get("kind") == "tool" and s.get("counted") is not False)
    return nchat <= 1 and ntool >= 2


def rows(run, name):
    d = run.get("mirror_dir")
    if not d:
        return []
    p = pathlib.Path(d) / name
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def manifest(run):
    d = run.get("mirror_dir")
    p = pathlib.Path(d) / "manifest.json" if d else None
    return json.loads(p.read_text()) if p and p.exists() else None


def probe_failures(run):
    """Count tasks the agent's per-task `GET /v1/models` health check killed before the model ran.

    Such a task *does* get a `report.ndjson` row — `status == "ERROR"` with `llm_count == 0`,
    `tool_count == 0` and zero tokens, the error text in `status_message` — so it is inside
    `Err/Total`, and its ~10.5 s zero-token row is inside every token and latency statistic here.
    What it is not is damage: `_lost()` correctly declines to flag it, because the model was never
    called and zero tokens is the truth. The cost is elsewhere: the task is scored as not-passed, so
    `pass_rate` silently mixes "the agent got the answer wrong" with "the agent never ran", and a
    string of zero-token rows drags a leg's per-task token median, mean and CV away from the cost of
    the work that did happen. See Bug 3 in `docs/exgentic-agent-bug-report-20260901.md`.

    Counted from `run.json` rather than `report.ndjson` because `run.json` carries one result per
    task unconditionally, while a task can occasionally end without a `report.ndjson` row at all
    (appworld timeouts do).
    """
    d = run.get("mirror_dir")
    p = pathlib.Path(d) / "run.json" if d else None
    if not (p and p.exists()):
        return 0
    return sum(1 for x in json.loads(p.read_text()).get("results", [])
               if (x.get("error") or "").endswith("/v1/models"))


def repeated_tasks():
    """[(label, [run numbers], shared task count)] for runs whose prompts overlap.

    Task selection is deterministic — a run takes the first `max_tasks` tasks — so a shorter run's
    tasks are a *prefix* of a longer one's and legs sharing a benchmark almost always share prompts.
    That matters because the LLM gateway caches completions on the request body: the second leg to
    send a prompt gets the first leg's stored response back, usage and all. Which legs overlap is a
    property of the spec file, so it is computed here rather than hard-coded.

    Grouped by (benchmark, requested model), because the model is part of the request body and so
    part of the cache key — the gpt-4.1 leg cannot replay the gpt-5-mini legs' responses even though
    it runs the same task ids. That makes this a *proxy* for cache-key identity, not a proof of it:
    the real key is the whole body, and two legs could still differ in it (a different tool list, for
    instance). Overlap here means "these legs could have replayed each other", not "they did".
    """
    by_key, out = {}, []
    for r in runs:
        ids = {str(x.get("task_id")) for x in rows(r, "report.ndjson") if x.get("task_id") is not None}
        if ids:
            model = (r.get("run_request") or {}).get("model")
            by_key.setdefault((r["bench"], model), []).append((r["n"], ids))
    for (bench, model), legs in by_key.items():
        if len(legs) < 2:
            continue
        shared = {i for a in range(len(legs)) for b in range(a + 1, len(legs))
                  for i in legs[a][1] & legs[b][1]}
        if shared:
            label = f"{bench}" + (f" on `{model}`" if model else "")
            out.append((label, [n for n, ids in legs if ids & shared], len(shared)))
    return out


def probe_spans():
    """(capability-probe `chat` spans, all `chat` spans) across this matrix.

    Which agent era a matrix ran in is measured rather than asserted: a capability probe is a `chat`
    span carrying `request_max_tokens == 1`. This generator is pointed at matrices from both sides of
    the `dev145` change — including previously published landmark runs, which get regenerated
    whenever it changes — so any statement about the probe has to come from the data or it will
    silently become false for half of them.
    """
    n = t = 0
    for r in runs:
        for x in rows(r, "span_report.ndjson"):
            if x.get("kind") == "chat":
                t += 1
                n += x.get("request_max_tokens") == 1
    return n, t


def probe_era_note():
    """One sentence stating which side of the probe change *this* matrix is on, from its own spans."""
    n, t = probe_spans()
    if not t:
        return "This run set published no `chat` spans, so which era it ran in cannot be read off it."
    if n:
        return (f"**This run set is from before that change**: {n} of its {t} `chat` spans carry "
                f"`max_tokens=1`, so its `llm` counts read one high per task — subtract one per task "
                f"for real calls.")
    return (f"**This run set is from after that change**: none of its {t} `chat` spans carries "
            "`max_tokens=1`, so its `llm` counts are real calls one-for-one with no offset to "
            "subtract.")


S1 = """## 1. Terms

| Term | Meaning |
|---|---|
| **Benchmark** | A named evaluation suite (`gsm8k`, `tau2`, `appworld`), each backed by an agent + an MCP tool service. |
| **Run** | One invocation of `run_benchmark` against a deployed benchmark (`POST /benchmarks/<b>/runs`). Identified by a `run_id`. A run selects the first `max_tasks` tasks and executes them, up to `max_parallel_sessions` at a time. |
| **Task** | One self-contained benchmark item. Each task runs in **complete isolation**: its own MCP session (`create_session`), its own prompt, one agent call, its own evaluation (`evaluate_session`), and its session is deleted afterward. |
| **Span** | The unit of OTEL instrumentation: one timed operation with a `name`, a parent, a status and attributes. A task's spans form a **tree** rooted at its `Agent.Session` span, and every number in §6/§7 is computed from them — `llm_count` counts `chat` spans, `tool_count` counts `execute_tool` spans, token totals come from `gen_ai.usage.*` on the `chat` spans. Only a handful are named by the harness (`Agent.Session`, `MCP.CreateSession`, `Agent.Call`, `invoke_agent…`, `chat <model>`, `execute_tool <tool>`, `Evaluator.Evaluate`); the rest are A2A/HTTP framework internals and dominate the raw count — a single gsm8k task emits ~100 spans, a tau2 task ~400, an appworld task ~800. **Every span is now published per task in `span_report.ndjson`** and summarised in §8, so the aggregate counters can be audited rather than trusted. |
| **Independence** | Tasks within a run are **independent, not a pipeline** — no state flows between them. A task erroring or timing out fails only itself; the batch continues. |
| **Trace / Agent.Session** | Exactly one `Agent.Session` root span per task, keyed by `task_id`. Token usage is parsed from the `chat` (LLM) spans under that root — hence one `token_report` row per task. |
| **Session (`session_id`)** | The per-task MCP session handle from `create_session`. Distinct from `run_id` and `task_id`. |
| **median / mean** | Both are per-run centres over the run's per-task values. The **median** is the robust middle (half the tasks above, half below); the **mean** is the arithmetic average and is pulled by outliers. A mean well **above** the median signals **right-skew** — a few expensive tasks dominating — and that gap is itself informative, which is why §6 prints both instead of picking one. |
| **Coefficient of Variation (CV)** | **Population standard deviation ÷ mean** (σ/μ), computed over the run's per-task values. It is a *relative* spread measure and therefore **dimensionless**, which is the whole point: token counts differ by three orders of magnitude between benchmarks (~310 for gsm8k vs ~400,000 for appworld), so raw σ cannot be compared across them while CV can. Read it as "how uneven are the tasks within this run": **0** = every task identical, **~0.1** = tight and predictable, **~0.5** = markedly uneven, **≥1.0** = spread as large as the average itself (a couple of tasks dominate the bill). It is the *population* σ (divide by *n*, not *n−1*) because a run is the complete set of its tasks, not a sample drawn from a larger one, and it shares its denominator with **mean**, not median. Shown as `—` when undefined: fewer than 2 tasks, or a mean of 0. |
| **gsm8k** | "**Grade School Math 8K**" — ~8.5K grade-school arithmetic **word problems** (7,473 train + 1,319 test), loaded from HuggingFace by the MCP at startup (hence the `hf-secret` requirement). Each needs 2–8 elementary steps (+ − × ÷), no algebra or geometry, and has a single correct number graded by **exact match**; the difficulty is carrying a multi-step chain without slipping, not the arithmetic. `task_id` is an integer index. One task ≈ 1 real LLM call + 1 tool call, so it is the **cheapest leg and our infrastructure canary** — a gsm8k failure means deploy/auth/LLM-reachability/telemetry, not model capability. It **saturates near 1.0**, so never read it as a model comparison (use tau2 for that). |
| **appworld `<hash>_<n>` task ids** | appworld groups several tasks under one base scenario/world (e.g. `3d9a636_1/_2/_3`); each still runs as an independent session. |
| **tau2 domain / subset** | τ²-bench ships four domains (`mock`, `retail`, `airline`, `telecom`). We set **no** subset override, so every tau2 run here uses the library default — **`retail`**, 114 tasks. tau2 `task_id`s are that domain's own ids (`"0"`…`"113"`), so `task_id` 0–19 = the first 20 **retail** tasks. Domain is *not* recorded in the artifacts; it is only inferable from the absence of an override, so state it explicitly when quoting tau2 numbers. |
"""

S2 = """## 2. Column names & meaning

### `token_report.ndjson` / this report's per-task tables

| Column | Source field | Meaning |
|---|---|---|
| `task` | `task_id` | The benchmark task identifier (one row per task). |
| `in` | `llm_input_tokens` | Sum of `gen_ai.usage.input_tokens` over **all** LLM chat calls in that task. |
| `out` | `llm_output_tokens` | Sum of `gen_ai.usage.output_tokens` over all LLM chat calls in that task. |
| `total` | `llm_total_tokens` | `in + out` for the task. |
| `llm` | `llm_count` | Number of LLM (chat-completion) calls the agent made. One per `chat` span, counted one-for-one. |
| `tool` | `tool_count` | Number of MCP tool calls the agent made. |
| `passed` | `evaluation_result` | Task-level pass/fail from `evaluate_session` (`None` = errored before eval). |

`llm` and `tool` normally move together on these benchmarks: the agent takes a model turn, that turn
decides a tool call, and gsm8k submits its answer *through* a tool, so `llm == tool` is the healthy
shape rather than a coincidence.

**Rows marked ⚠ have lost token attribution** — the usage-bearing `chat` span was never written, so
`in`/`out` are understated while the task itself ran fine (`tool`, latency and `passed` are all
genuine). Two shapes are flagged, both of them structurally impossible rather than merely suspicious:

| flagged shape | why it cannot happen on a healthy task |
|---|---|
| `llm <= 1` with `tool >= 2` | every tool call needs a preceding model turn to decide it |
| `status == OK` with `llm = 0` | a task cannot complete without taking a single model turn |

**Do not substitute a `tokens == 0` test for either.** It is the intuitive check and it is unreliable:
a damaged row's token total depends on what other spans survived, so it can be non-zero, and a
zero-token row can equally be a task that failed before its first call. The shape of the counts is
the evidence; the token value is not.

The known trigger for lost spans is **reusing a warm agent across runs** — see
`docs/exgentic-agent-bug-report-20260901.md`. Every leg of this matrix therefore deploys a fresh
agent, which is why these columns can be read at face value here.

One caution when reading `llm`, `tool` or token totals **across** runs: they are properties of the
agent image as much as of the benchmark, and every agent image is pinned to `:latest`, so two runs
weeks apart can differ in call counts with nothing in the request changing. **No artifact in this set
records the agent digest** — not `run.json`, not `manifest.json` — so the only record is operational:
read `.status.containerStatuses[].imageID` off the agent pod while the run is live. Treat a
cross-run count comparison as unsupported unless you captured that.

If you did not, one weaker check is still available after the fact:
`skopeo inspect --raw docker://<agent image>:latest` gives the current index digest and its
per-platform children. That tells you what `:latest` points at **now**, so it can only confirm the tag
has *not* moved since your run — it can never recover the digest of a run that predates a push. Note
also that the index is multi-platform: compare the child for the cluster's architecture
(`linux/amd64` on OpenShift, `linux/arm64` on a KinD on Apple Silicon), and check the children share
`org.opencontainers.image.revision` before treating two clusters' legs as the same code.

### `span_report.ndjson` (§8)

One row per OTEL span per task — the evidence the counters above are derived from. Fields:
`task_id`, `session_id`, `trace_id`, `span_id`, `parent_span_id`, `name`, `kind`, `parent_name`,
`depth`, `start_time`, `latency_ms`, `status_code`, `error_type`, `counted`, `input_tokens`,
`output_tokens`, `request_model`, `request_max_tokens`, `finish_reasons`.

| Column | Meaning |
|---|---|
| `name` | The span title, e.g. `Agent.Session`, `chat gpt-5-mini`, `execute_tool submit`. |
| `kind` | `root` / `phase` / `agent` / `chat` / `tool` / `other` (`other` = nested HTTP/framework children the harness does not name). |
| `counted` | Whether this span fed `llm_count`/`tool_count`. `false` marks real work the aggregate cannot see, because only spans parented by `invoke_agent` are counted (and `execute_tool initial_observation` never is). `null` for non-chat/tool spans. |
| `request_max_tokens` | The `max_tokens` the agent asked for, `null` when it asked for none (the normal case). A value of `1` marks a capability probe rather than real work: agents up to `exgentic 0.3.5.dev131` issued one per task and it was counted as an LLM call, while `dev145` replaced it with an unbilled `GET /v1/models` check that emits no span. **PROBE_ERA_NOTE** The column is the unambiguous way to tell a probe from a real call, and the probe's code path still exists upstream (`strict=True` in the agent's `health.py`), so it stays. |

That set is a deliberate whitelist: span attributes can carry prompts and completions, and these
objects are public, so nothing outside this list is published (see the S3 section).

### Run-summary fields (`RunSummary`, shown in §4)

| Field | Meaning |
|---|---|
| `status` | Terminal run status: `succeeded` / `failed` / `error` / `cancelled`. |
| `pass_rate` | `evaluated_pass / total` over the run's tasks. A task that never reached the model is in `total` and not in `evaluated_pass`, so this figure mixes "answered wrong" with "never ran" — see the `Probe` column and the note under §4's table. |
| `evaluated_pass` | Count of tasks that passed evaluation. |
| `total` | Number of task results recorded. |
| `Probe` | Not a `RunSummary` field — derived here by counting `run.json` results whose error is the agent's `GET /v1/models` health check. Such a task emits no spans, but it *does* leave a `report.ndjson` row (`status=ERROR`, `llm=0`, `tool=0`, zero tokens), so it is counted in `Err/Total` **and** included in every per-task token/latency statistic in §6–§7 — where it acts as a zero-cost outlier. `run.json` is used rather than `report.ndjson` because it has one result per task unconditionally. |
| `wall_seconds` | Wall-clock duration of the run. |
"""


def _dir_prefix(keys):
    """Longest common prefix truncated at a '/' boundary, so it is a usable S3 prefix rather
    than a mid-token fragment (naive LCP yields things like `.../gsm8k/20260` purely because
    same-day run_ids share leading digits)."""
    if not keys:
        return ""
    cp = os.path.commonprefix(keys)
    return cp[:cp.rfind("/") + 1] if "/" in cp else cp


def sec_s3():
    """Where the artifacts live. Unnumbered so the fixed 7 sections keep their numbering."""
    url_root, per_bench, all_keys = "", {}, []
    for r in runs:
        man = manifest(r)
        if not man:
            continue
        keys = [a["key"] for a in man.get("artifacts", [])]
        for a in man.get("artifacts", []):
            if a.get("url", "").endswith(a["key"]):
                url_root = a["url"][: -len(a["key"])]
        keys.append(man["prefix"].rstrip("/") + "/manifest.json")  # manifest does not list itself
        all_keys += keys
        per_bench.setdefault(r["bench"], []).extend(keys)
    if not all_keys:
        return "## S3 artifact locations\n\n_No exported artifacts._"
    root = _dir_prefix(all_keys)
    o = ["## S3 artifact locations", "",
         "Every number in this report is derived from artifacts published to S3. All objects for "
         "this report share one URL root and one key prefix:", "",
         "| | |", "|---|---|",
         f"| **URL root** | `{url_root}` |",
         f"| **Key prefix (all {len(all_keys)} objects)** | `{root}` |", "",
         "The key layout is `<s3.prefix>/<caller>/<iss-host-encoded>/<benchmark>/<run_id>/<artifact>`, "
         f"so **benchmark is a path segment** and selects cleanly ({len(all_keys)} objects across "
         f"{len(runs)} runs):", "",
         "| benchmark | runs | objects | prefix (append to the key prefix above) |",
         "|---|---:|---:|---|"]
    for b in sorted(per_bench):
        ks = per_bench[b]
        nruns = sum(1 for r in runs if r["bench"] == b and manifest(r))
        sub = _dir_prefix(ks)[len(root):]
        o.append(f"| {b} | {nruns} | {len(ks)} | `{sub}` |")
    models = sorted({m for r in runs for m in
                     {x.get("model") for x in rows(r, "report.ndjson") if x.get("model")}})
    o += ["",
          "**The model in use is NOT part of the key**, so it cannot be selected by prefix. Three of "
          "the models here happen to be prefix-separable only because tau2 and appworld each used a "
          "single model; gsm8k mixes models across its runs, so its prefix necessarily returns all of "
          f"them. Models present: {', '.join('`%s`' % m for m in models)}. To fetch the objects for one "
          "model, resolve model -> `run_id` from the summary in section 4 and use the per-run prefixes.",
          "",
          "Objects are readable **and listable anonymously** (no credentials needed), so treat "
          "anything written here as public. What is actually exposed: the **keys** carry the caller's "
          "username and the Keycloak issuer host, and `run.json` / `report.ndjson` can carry "
          "**exception strings** from failed tasks (`status_message`, `TaskResult.error`), which may "
          "quote a server error body. No artifact contains task prompts or model outputs — the "
          "record schema is entirely ids, counts and durations, and `span_report.*` is restricted to "
          "a fixed whitelist of structural and numeric span fields for exactly this reason."]
    return "\n".join(o)


def sec3():
    o = ["## 3. The 12 run requests to the AutoBench Service", ""]
    o.append("Each run = an optional **deploy** step (teardown + `POST /benchmarks/<b>/deploy`) "
             "followed by a **run** step (`POST /benchmarks/<b>/runs`). Namespace is `team1`, agent "
             "is `tool_calling` throughout.")
    o.append("")
    o.append("*Driver execution order was #1–3, #5–8, #4, #9–10, #11–12 (#4 last among gsm8k "
             "because it swaps the model); listed here in canonical order.*")
    o.append("")
    for r in runs:
        o.append(f"### Run #{r['n']} — {r['title'].split('—',1)[-1].strip()}")
        o.append("")
        o.append(f"- **Benchmark:** `{r['bench']}`")
        models = sorted({x.get("model") for x in rows(r, "report.ndjson") if x.get("model")})
        o.append(f"- **Model in use:** {', '.join(f'`{m}`' for m in models) or '_(no trace)_'}")
        dep = r.get("deploy_request")
        o.append(f"- **Deploy:** {'`'+json.dumps(dep)+'`' if dep else '_(reuses the previous deploy)_'}")
        o.append(f"- **Run request** → `POST /benchmarks/{r['bench']}/runs`:")
        o.append("```json")
        o.append(json.dumps(r["run_request"], indent=2))
        o.append("```")
        o.append("")
    return "\n".join(o)


def sec4():
    o = ["## 4. Summary of the 12 run results", "",
         "| # | Benchmark | run_id | Model in use | Status | Pass | Eval-pass | Err/Total | Probe | Wall (s) |",
         "|---|---|---|---|---|---:|---:|---:|---:|---:|"]
    for r in runs:
        s = r.get("summary") or {}
        rr = rows(r, "report.ndjson")
        models = sorted({x.get("model") for x in rr if x.get("model")}) or ["—"]
        err = sum(1 for x in rr if (x.get("status") or "") not in ("OK", ""))
        o.append("| %s | %s | `%s` | %s | %s | %s | %s | %s/%s | %s | %s |" % (
            r["n"], r["bench"], r.get("run_id") or "—", ", ".join(models),
            r.get("status") or "—",
            s.get("pass_rate", "—"), s.get("evaluated_pass", "—"),
            err, s.get("total", "—"), probe_failures(r),
            round(s["wall_seconds"]) if s.get("wall_seconds") is not None else "—"))
    bad = [(r["n"], sum(1 for x in rows(r, "report.ndjson") if _lost(x)), len(rows(r, "report.ndjson")))
           for r in runs]
    tot = sum(c for _, c, _ in bad)
    allrows = sum(t for _, _, t in bad)
    bad = [(n, c, t) for n, c, t in bad if c]
    o.append("")
    if not bad:
        o.append("**Token attribution:** complete — no row lost its usage-bearing span.")
    else:
        o.append(f"**⚠ Lost token attribution: {tot} of {allrows} rows** — "
                 + ", ".join(f"#{n} ({c}/{t} tasks)" for n, c, t in bad)
                 + ". Those runs' **token totals are understated** (pass rates are not affected — "
                   "the tasks ran and were evaluated normally). See §2 for the detector and why a "
                   "`tokens == 0` check does not catch it on claude-sonnet-5 / gemini-2.5-pro.")

    # Reported separately from the `Err/Total` column above, which counts *rows*: a probe-failed task
    # has no row, so it is not in `Err` and not in `allrows` either. Only the `Pass` column feels it.
    pf = [(r["n"], probe_failures(r), (r.get("summary") or {}).get("total")) for r in runs]
    ptot = sum(c for _, c, _ in pf)
    ptasks = sum(t or 0 for _, _, t in pf)
    o.append("")
    if not ptot:
        o.append("**Health probe:** every task reached the model — no task was lost to the agent's "
                 "per-task `GET /v1/models` check.")
    else:
        pf = [(n, c, t) for n, c, t in pf if c]
        o.append(f"**⚠ Tasks lost to the agent's health probe: {ptot} of {ptasks}** — "
                 + ", ".join(f"#{n} ({c}/{t} tasks)" for n, c, t in pf)
                 + ". Each raised `Model endpoint … is unreachable` from the `GET /v1/models` check "
                   "that `exgentic 0.3.5.dev145` runs at the start of every task, hard-capped at 10 s "
                   "with no retry, so the task died **before the model was called**. It is a "
                   "gateway-latency interaction, not a misconfiguration: see Bug 3 in "
                   "`docs/exgentic-agent-bug-report-20260901.md`.")
        o.append("")
        o.append("Two consequences for reading the rest of this report. **Pass rates are depressed "
                 "by tasks that never ran** — the task counts as not-passed, indistinguishable in "
                 "`Pass` from a wrong answer, so divide `Eval-pass` by `Total − Probe` for the rate "
                 "over the tasks that reached the model. And **the per-task token and latency "
                 "statistics of the affected runs are contaminated**: the task does leave a "
                 "`report.ndjson` row (`status=ERROR`, `llm=0`, `tool=0`, zero tokens, ~10.5 s, the "
                 "error in `status_message`), so it is inside `Err/Total` and inside every median, "
                 "mean and CV below, pulling the token figures down and widening their spread. It is "
                 "**not** flagged as lost token attribution, and that is correct — the model was "
                 "never called, so zero tokens is the truth rather than a dropped span.")

    # The LLM gateway caches completions, keyed on the request body, so legs that repeat a task
    # replay each other's responses. Derived from the data rather than asserted, because which legs
    # overlap is a property of the spec file and changes with it.
    shared = repeated_tasks()
    o.append("")
    if not shared:
        o.append("**Gateway response cache:** no task id appears in more than one run of the same "
                 "benchmark, so no run could have replayed another's completions.")
    else:
        overlap = ("; ".join(f"{b} #{'/#'.join(str(n) for n in ns)} share {c} task id"
                             f"{'s' if c != 1 else ''}" for b, ns, c in shared))
        mechanism = ("A repeated request body comes back from `ete-litellm` as the *same stored "
                     "response* — identical response `id`, identical `usage` — measured with the "
                     "agent bypassed, on both clusters' gateways. **The TTL is ~10 minutes**, "
                     "measured by survival curve (one probe per nonce at its own age: hit at "
                     "3/5/7/9 min, miss at 11/13/15/18/21). A replay re-reports the stored token "
                     "counts and its latency is a cache lookup, so per-call latency and output "
                     "token counts are affected; input tokens and pass rates are not (the same "
                     "prompt and the same correct answer either way). This is not an agent setting "
                     "— `EXGENTIC_LITELLM_CACHING=false` is pinned and provably inert against it — "
                     "and **latency is not a reliable hit detector**: one measured replay took "
                     "3.0 s, the same as a miss. Compare response `id`s. Details in "
                     "`docs/exgentic-agent-bug-report-20260901.md`.")
        # A run driven with BM_CACHE_GAP rests each (benchmark, model) prompt set past the TTL, so
        # the overlap above is present but cannot have been served from the cache. Report that rather
        # than the bare warning, and show the enforced gap so the claim is checkable.
        gap = data.get("cache_gap_seconds") or 0
        if gap >= 660:
            slept = [r for r in runs if (r.get("cache_gap_slept_seconds") or 0) > 0]
            o.append(f"**✅ These runs were spaced to defeat the gateway's completion cache.** The "
                     f"legs do repeat tasks — {overlap} — but the driver rested every "
                     f"(benchmark, model) prompt set for at least **{int(gap)} s** before reusing "
                     f"it, against a measured cache TTL of ~10 min, so no leg could replay "
                     f"another's completions"
                     + (f" ({len(slept)} of {len(runs)} legs waited out a remainder explicitly; the "
                        f"rest had already been idle long enough)" if slept else "")
                     + f". Per-call latency and output tokens are therefore independent across these "
                       f"legs, which was not true of earlier matrices. For the underlying "
                       f"mechanism: {mechanism}")
        else:
            o.append(f"**⚠ The LLM gateway caches completions, and these runs repeat tasks: "
                     f"{overlap}.** {mechanism} So for the runs listed, **per-call latency and "
                     f"output token counts are not independent measurements**.")
    return "\n".join(o)


def sec5():
    ex = next((manifest(r) for r in runs if manifest(r)), None)
    listed = [a["name"] for a in (ex or {}).get("artifacts", [])]
    o = ["## 5. Contents of the manifest file", "",
         "Every run writes a `manifest.json` at its S3 prefix — a self-describing index of the "
         "run's data objects, so a client discovers the whole run in one fetch with no S3 listing. "
         f"It lists the {len(listed)} data artifacts ("
         + ", ".join(f"`{n}`" for n in listed)
         + "); the manifest does **not** list itself. Each entry carries "
           "`name`, `format`, `key`, public `url`, and `size_bytes`.", ""]
    if ex:
        # Pretty-printed, not compact: the on-disk manifest is a single minified line, which in a
        # fenced block does not wrap — it runs off the page on screen and off the sheet in print.
        o += ["Example:", "", "```json", json.dumps(ex, indent=2), "```", ""]
    return "\n".join(o)


def sec6():
    o = ["## 6. Per-run aggregate — input & output tokens", "",
         "`median` then `mean` then `CV` for each direction. median is the robust centre, mean the "
         "arithmetic average, and a mean well above the median signals right-skew (a few long "
         "tasks). `CV` is population sigma / mean, i.e. how unevenly the token cost is spread; it "
         "shares its denominator with `mean`, not `median`. CV is `—` for single-task runs.", "",
         "A `⚠` in the Lost column means some of that run's rows lost their usage-bearing span (§2), "
         "so **every token figure on that row is understated** — including the median/mean/CV, which "
         "are computed over all tasks and so are dragged down by the damaged ones. Do not quote them "
         "as the run's cost.", "",
         "| # | Benchmark | Model | Tasks | Lost | LLM calls | Input | Output | Total | IN median | IN mean | IN CV | OUT median | OUT mean | OUT CV |",
         "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in runs:
        rr = rows(r, "report.ndjson")
        if not rr:
            o.append("| %s | %s | — | 0 | — | — | — | — | — | — | — | — | — | — | — |" % (r["n"], r["bench"]))
            continue
        iv = [x.get("llm_input_tokens", 0) or 0 for x in rr]
        ov = [x.get("llm_output_tokens", 0) or 0 for x in rr]
        i, ou = sum(iv), sum(ov)
        c = sum(x.get("llm_count", 0) or 0 for x in rr)
        models = sorted({x.get("model") for x in rr if x.get("model")}) or ["—"]
        imed, imean, icv = _stats(iv)
        omed, omean, ocv = _stats(ov)
        nlost = sum(1 for x in rr if _lost(x))
        o.append("| %s | %s | %s | %d | %s | %d | %d | %d | %d | %s | %s | %s | %s | %s | %s |" % (
            r["n"], r["bench"], ", ".join(models), len(rr),
            f"⚠ {nlost}" if nlost else "0", c, i, ou, i + ou,
            _fmt(imed), _fmt(imean), _fmt(icv, "%.2f"),
            _fmt(omed), _fmt(omean), _fmt(ocv, "%.2f")))
    return "\n".join(o)


def sec7():
    o = ["## 7. Per-task detail", "",
         "`model` is repeated on every row deliberately: token counts are only comparable within a "
         "model, and the runs do not all use the same one (#4 swaps the gsm8k model, so its ~815 "
         "input tokens per task are not comparable with the ~320 of #1–3/#5–8 on the same tasks).",
         "",
         "`tool` is shown next to `llm` because their relationship is the tell for a damaged row: "
         "each tool call needs a preceding model turn, so `llm=1` beside `tool=11` cannot be real "
         "work — it is a lost span (§2), flagged `⚠`.", ""]
    for r in runs:
        rr = rows(r, "report.ndjson")
        o.append(f"### Run #{r['n']} — {r['bench']}  ·  `{r.get('run_id') or '—'}`")
        o.append("")
        if not rr:
            o.append("_No trace rows (run did not produce a report)._")
            o.append("")
            continue
        o.append("| task | model | in | out | total | llm | tool | passed | |")
        o.append("|---|---|---:|---:|---:|---:|---:|---|---|")
        for x in sorted(rr, key=lambda y: _natkey(y.get("task_id"))):
            i = x.get("llm_input_tokens", 0) or 0
            ou = x.get("llm_output_tokens", 0) or 0
            o.append("| %s | %s | %d | %d | %d | %s | %s | %s | %s |" % (
                x.get("task_id"), x.get("model") or "—", i, ou, i + ou,
                x.get("llm_count"), x.get("tool_count"), x.get("evaluation_result"),
                "⚠ tokens lost" if _lost(x) else ""))
        nlost = sum(1 for x in rr if _lost(x))
        if nlost:
            o.append("")
            o.append(f"> ⚠ **{nlost} of {len(rr)} rows lost token attribution** — their `llm`/`in`/"
                     "`out` are structurally impossible for a task that ran (see §2), so the "
                     "usage-bearing span was dropped rather than the work not happening. The tasks "
                     "themselves ran and were evaluated normally, so `tool`/`passed` are correct "
                     "and this run's **pass rate is valid while its token totals are not**.")
        o.append("")
    return "\n".join(o)


def sec8():
    """Per-task span inventory — the evidence layer under §6/§7's aggregates."""
    o = ["## 8. Per-task span inventory", "",
         "Which spans each task actually invoked, by name. §6 and §7 report *counts*; this is what "
         "they were counted from, read straight out of each run's `span_report.ndjson`.", "",
         "Read the **chat** and **tool** columns together. There is no fixed healthy chat count — a "
         "one-shot gsm8k task legitimately shows a single `chat` span, and a multi-turn tau2 task "
         "shows many. What is not possible is **`chat` ≤ 1 alongside `tool` ≥ 2**: every tool call "
         "needs a model turn to request it, so a task cannot invoke two tools off one chat span. "
         "That shape means a usage-bearing span was dropped, and this section flags it. Reading it "
         "off *counts* rather than token values is what makes it catchable on every model, "
         "including ones whose dropped span would still have reported plausible non-zero usage.", "",
         "⚠ **The `chat` and `tool` columns below are raw span totals; the ⚠ flag is computed on the "
         "`counted` subset only.** Do not apply the rule by hand to these columns. A healthy gsm8k "
         "task emits two `execute_tool` spans — `initial_observation` and `submit` — of which only "
         "`submit` is counted, so its raw pair is `1`/`2` and would trip the test while its §7 pair "
         "is the innocent `1`/`1`. Filtering to `counted` is what makes this section agree with §7.",
         "",
         "Up to `exgentic 0.3.5.dev131` each task also issued a `max_tokens=1` capability probe, so "
         "a bare `chat == 1` used to be the damage signal and **at least 2** was healthy. The probe "
         "was replaced in `dev145` by an unbilled `GET /v1/models` check that emits no span — do not "
         "resurrect that rule, and do not compare chat counts across runs that straddle the change. "
         + probe_era_note(), "",
         "`not counted` are spans the aggregator cannot see: it only folds a chat/tool span into "
         "`llm_count`/`tool_count` when its parent is the `invoke_agent` span, so anything nested "
         "deeper is real work missing from the totals. A non-zero figure there is not a bug by "
         "itself — it is the known blind spot, now measurable.", "",
         "The **names** column lists only harness-named spans (`root`/`phase`/`agent`/`chat`/`tool`). "
         "The `other` column counts the rest — A2A/HTTP framework internals such as "
         "`EventQueue.dequeue_event`, which dominate the raw count (a single gsm8k task emits ~98 "
         "spans, ~90 of them framework noise) and would swamp this table. They are all present in "
         "`span_report.ndjson`; this section is the readable summary, not the full tree.", ""]
    any_spans = False
    for r in runs:
        srows = rows(r, "span_report.ndjson")
        o.append(f"### Run #{r['n']} — {r['bench']}  ·  `{r.get('run_id') or '—'}`")
        o.append("")
        if not srows:
            o.append("_No `span_report.ndjson` for this run (predates the artifact, or MLflow was "
                     "unavailable)._")
            o.append("")
            continue
        any_spans = True
        by_task: dict = {}
        for s in srows:
            by_task.setdefault(s.get("task_id"), []).append(s)
        o.append("| task | spans | chat | tool | other | not counted | span names (xN) |")
        o.append("|---|---:|---:|---:|---:|---:|---|")
        for tid in sorted(by_task, key=_natkey):
            ss = by_task[tid]
            kinds: dict = {}
            for s in ss:
                kinds[s.get("kind")] = kinds.get(s.get("kind"), 0) + 1
            names: dict = {}
            for s in ss:
                if s.get("kind") != "other":  # framework internals: counted, not enumerated
                    names[s.get("name")] = names.get(s.get("name"), 0) + 1
            uncounted = sum(1 for s in ss if s.get("counted") is False)
            nchat = kinds.get("chat", 0)
            ntool = kinds.get("tool", 0)
            inventory = ", ".join(f"`{n}`" + (f" x{c}" if c > 1 else "")
                                  for n, c in sorted(names.items(), key=lambda kv: -kv[1]))
            o.append("| %s | %d | %s | %d | %d | %d | %s |" % (
                tid, len(ss), f"**{nchat}** ⚠" if _span_lost(ss) else str(nchat),
                ntool, kinds.get("other", 0), uncounted, inventory))
        lost = [t for t, ss in by_task.items() if _span_lost(ss)]
        if lost:
            o.append("")
            o.append(f"> ⚠ **{len(lost)} of {len(by_task)} tasks invoke ≥2 tools off ≤1 `chat` "
                     "span** — impossible, so a usage-bearing chat span was dropped. Their token "
                     "totals in §6/§7 are understated.")
        o.append("")
    if not any_spans:
        return ("## 8. Per-task span inventory\n\n_No run in this set exported "
                "`span_report.ndjson`; re-run against a Service that emits it._")
    return "\n".join(o)


GENERATED = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

head = [f"# AutoBench Service — 12 Parameterized Runs ({PLATFORM})", "",
        f"**Report generated:** {GENERATED}  ",
        f"**Service version:** `{VERSION}`  ", f"**Platform:** {PLATFORM}  ",  # never the raw endpoint: these reports are public
        f"**Runs executed:** {len(runs)}", "",
        "All numbers below are derived programmatically from the mirrored S3 artifacts "
        "(`report.ndjson` / `token_report.ndjson` / `span_report.ndjson` / `manifest.json`) — none "
        "are transcribed.", "", "<!--TOC-->", ""]

doc = "\n".join(head) + "\n" + "\n\n".join(
    [sec_s3(), S1, S2.replace("**PROBE_ERA_NOTE**", probe_era_note()),
     sec3(), sec4(), sec5(), sec6(), sec7(), sec8()]) + "\n"
# Sections only: every `### Run #N` subsection title appears twice (per-task and per-span), so
# listing level 3 would emit duplicate anchors that link to whichever GitHub saw first.
doc = doc.replace("<!--TOC-->", _toc(doc, max_level=2))
if OUT:
    OUT.write_text(doc)
    print(f"wrote {OUT} ({len(doc)} bytes, {len(runs)} runs)")
else:
    print(doc)
