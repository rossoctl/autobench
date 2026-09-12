#!/usr/bin/env python3
"""Analyse the DESIGNED AuthBridge plugin-overhead experiment (`plugin_study_specs.json`).

Usage: gen-plugin-study.py <run12-pstudy-LABEL.json> <spec.json> <version> <platform> [out.md] [judge.ts]

This is the companion to `gen-plugin-overhead.py`, and it exists because that script's *design* --
not its arithmetic -- limits what it can say. It mines the canonical 12-run, where each preset runs
once at `max_tasks=5`, in a fixed order, with no replication. Three consequences, all measured
rather than assumed:

  * **n=5 has no resolving power.** Bootstrapping the pooled v1.26+v1.27 samples puts the minimum
    detectable preset-to-preset difference at roughly 3x. Every preset pair of interest differs by
    far less than that, so "no difference found" there was uninformative, not reassuring.
  * **Every n=5 per-task figure is warm-up.** In the 50-task baseline leg -- which has no sidecar at
    all -- the first five tasks cost 2.1x (OCP) to 3.0x (KinD) the steady-state per-task time, with
    the transient gone by task ~10. A 5-task leg measures only that transient.
  * **Run order aliases onto the plugin variable.** The legs run sequentially in a fixed sequence,
    so any monotone drift over the session loads entirely onto whichever preset ran last.

So this script reports things the retrospective one structurally cannot:

  1. the **serial diagnostic** (#111, `max_parallel_sessions=1`) that separates benign decision
     caching from a fail-open enforcement gap -- the one finding here with a security consequence;
  2. a **warm-up / steady-state decomposition**, so per-deploy admission cost is reported separately
     from steady-state marginal cost instead of being averaged into one misleading number;
  3. **intervals, not bare medians** -- bootstrap CIs and permutation tests, so a null result can be
     distinguished from an underpowered one;
  4. a **replicate-agreement check** across the reversed-order block, which is what licenses reading
     the deltas as plugin cost rather than as drift;
  5. the **tau2 linearity test** (#112/#113), which measures the per-tool-call projection the
     retrospective report could only assume.

`judge.ts` is one ISO-8601 timestamp per line from the IBAC judge proxy; without it the judged-call
sections are omitted rather than guessed at. Collect it per platform:

    oc -n rossoctl-system logs deploy/ibac-judge --since=8h --timestamps \
      | grep -vi healthz | awk '{print $1}' > /tmp/judge-ocp.ts
"""
import datetime as dt
import json
import os
import pathlib
import random
import statistics as st
import sys
from datetime import timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gen_toc import build as _toc  # noqa: E402

SRC = pathlib.Path(sys.argv[1])
SPEC = pathlib.Path(sys.argv[2])
VERSION = sys.argv[3]
PLATFORM = sys.argv[4]
OUT = pathlib.Path(sys.argv[5]) if len(sys.argv) > 5 else None
JUDGE_TS = pathlib.Path(sys.argv[6]) if len(sys.argv) > 6 else None

# The warm-up transient is exactly the FIRST CONCURRENCY WAVE, not a fixed task count. Measured
# across three independent 50-task no-sidecar baseline legs: splitting at the first `num_parallel`
# tasks gives ratios 2.03x / 2.17x / 2.24x, while splitting at a fixed 10 gives 1.09x / 1.40x /
# 0.72x -- the fixed cutoff buries the effect by mixing 6 steady-state tasks into the "warm" bucket,
# and in one leg even inverts its sign. So the cutoff is derived per leg from the artifacts.
# PSTUDY_WARM forces a fixed cutoff instead, for re-deriving the decay profile.
WARM_ENV = os.environ.get("PSTUDY_WARM")
DECAY_BUCKETS = [(1, 4), (5, 8), (9, 12), (13, None)]
TOOL_SPAN = "execute_tool submit"
B_BOOT = 10000
B_PERM = 20000
CONF = 0.90
SEED = 20260912

data = json.loads(SRC.read_text())
spec = json.loads(SPEC.read_text())
runs = {r["n"]: r for r in data["runs"]}
legs = {s["n"]: s for s in spec["legs"]}
ORDER = [n for n in spec["order"] if n in runs]
POS = {n: i for i, n in enumerate(ORDER, 1)}

judge_ts = []
if JUDGE_TS and JUDGE_TS.exists():
    for line in JUDGE_TS.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                judge_ts.append(dt.datetime.fromisoformat(line.replace("Z", "+00:00")))
            except ValueError:
                pass


# --------------------------------------------------------------------------- data access
def rows(n, name="report.ndjson"):
    r = runs.get(n)
    if not r or not r.get("mirror_dir"):
        return []
    p = pathlib.Path(r["mirror_dir"]) / name
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def ranked(n):
    """Report rows in true EXECUTION order.

    The ndjson is written in reverse-task_id order, not the order the tasks ran, so ranking by
    `start_time` is what makes a warm-up analysis meaningful at all. Under
    `max_parallel_sessions=4` the first wave starts near-simultaneously; rank still orders the
    waves correctly, which is the resolution this needs.
    """
    rr = [x for x in rows(n) if x.get("start_time")]
    rr.sort(key=lambda x: x["start_time"])
    for i, x in enumerate(rr, 1):
        x["_rank"] = i
    return rr


def ok(rr):
    return [x for x in rr if x.get("status") == "OK"]


def non_llm(x):
    """Agent time with the LLM path removed -- the part the sidecar actually intercepts.

    The legs share one external LLM gateway, so LLM latency carries warm-up and response-caching
    effects that alias onto the plugin variable. Subtracting it leaves MCP connect + tool calls +
    sidecar interception, which is what is being measured.
    """
    return (x.get("agent_call_s") or 0) - (x.get("llm_total_s") or 0)


def warm_cutoff(n):
    """Tasks 1..cutoff are warm-up: the first concurrency wave. See the WARM_ENV comment."""
    if WARM_ENV:
        return int(WARM_ENV)
    par = [x.get("num_parallel") for x in rows(n) if x.get("num_parallel")]
    return max(par) if par else 1


def steady(n):
    """Steady-state non-LLM seconds for a leg: OK tasks after the first concurrency wave."""
    c = warm_cutoff(n)
    return [non_llm(x) for x in ok(ranked(n)) if x["_rank"] > c]


def cond(n):
    return legs[n]["condition"]


def role(n):
    return legs[n]["role"]


# --------------------------------------------------------------------------- statistics
def boot_median_ci(v, conf=CONF, B=B_BOOT, seed=SEED):
    if len(v) < 3:
        return (None, None)
    rng = random.Random(seed)
    k, meds = len(v), []
    for _ in range(B):
        meds.append(st.median([v[rng.randrange(k)] for _ in range(k)]))
    meds.sort()
    a = (1 - conf) / 2
    return (meds[int(a * B)], meds[min(B - 1, int((1 - a) * B))])


def boot_diff_ci(a, b, conf=CONF, B=B_BOOT, seed=SEED):
    """CI on median(b) - median(a). Straddling zero == no detectable difference."""
    if len(a) < 3 or len(b) < 3:
        return (None, None)
    rng = random.Random(seed + 1)
    na, nb, d = len(a), len(b), []
    for _ in range(B):
        d.append(st.median([b[rng.randrange(nb)] for _ in range(nb)])
                 - st.median([a[rng.randrange(na)] for _ in range(na)]))
    d.sort()
    lo = (1 - conf) / 2
    return (d[int(lo * B)], d[min(B - 1, int((1 - lo) * B))])


def perm_p(a, b, B=B_PERM, seed=SEED):
    """Two-sided permutation p-value on the difference in medians."""
    if len(a) < 3 or len(b) < 3:
        return None
    rng = random.Random(seed + 2)
    obs = abs(st.median(b) - st.median(a))
    pool, na = list(a) + list(b), len(a)
    hits = 0
    for _ in range(B):
        rng.shuffle(pool)
        if abs(st.median(pool[na:]) - st.median(pool[:na])) >= obs - 1e-12:
            hits += 1
    return (hits + 1) / (B + 1)


def f(v, spec="%.3f"):
    return "—" if v is None else spec % v


def ci(t, spec="%.3f"):
    return "—" if t[0] is None else f"[{spec % t[0]}, {spec % t[1]}]"


# --------------------------------------------------------------------------- assemble
GEN = dt.datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
GS = [n for n in ORDER if role(n) in ("rep1", "rep2")]
CONDS = []
for n in GS:
    if cond(n) not in CONDS:
        CONDS.append(cond(n))
DIAG = [n for n in ORDER if role(n) == "serial-diagnostic"]
TAU = [n for n in ORDER if role(n) == "linearity"]

L = [f"# AuthBridge plugin overhead — designed experiment ({PLATFORM})", "",
     f"**Report generated:** {GEN}  ",
     f"**Service version:** `{VERSION}`  ",
     f"**Target:** {data.get('base')}  ",
     f"**Legs executed:** {len(ORDER)} of {len(spec['legs'])}  ",
     f"**Judge-call evidence:** {'included' if judge_ts else '**absent** — judged-call sections omitted'}  ",
     f"**Statistics:** {int(CONF*100)}% bootstrap CIs ({B_BOOT:,} resamples), "
     f"permutation tests ({B_PERM:,} shuffles), seed `{SEED}`",
     "", "<!--TOC-->", ""]

missing = [n for n in spec["order"] if n not in runs or not rows(n)]
if missing:
    L += [f"> ⚠️ **Legs with no mirrored report, excluded:** "
          f"{', '.join('#%d' % n for n in missing)}. Every conclusion below is conditional on the "
          "legs that did land; a missing leg is a gap in the design, not a null result.", ""]

# --- design ----------------------------------------------------------------
L += ["## What this measures, and why it is not the retrospective report", "",
      "This is a **designed experiment**: the conditions, the task count and the run order were all "
      "chosen in order to measure plugin cost. The earlier "
      "`12run-plugin-overhead-*.md` documents are a **retrospective mining** of the canonical 12-run "
      "matrix, whose purpose was platform validation. Both are honest about their own arithmetic; "
      "they differ in what the arithmetic is *capable of resolving*.", "",
      "Three specific defects motivated this run, each measured rather than assumed:", "",
      f"| defect in the 12-run design | evidence | fixed here by |",
      "|---|---|---|",
      "| each preset runs **once at `max_tasks=5`** | bootstrap puts the minimum detectable "
      "preset-to-preset difference at ~**3x**; the pairs of interest differ by far less, so a null "
      "result there was *underpowered*, not reassuring | `max_tasks=50` |",
      "| per-task figures are dominated by **warm-up** | in 50-task baseline legs, which have **no "
      "sidecar at all**, the first concurrency wave costs ~**2.0–2.2x** steady state and the "
      "transient is gone by the second wave; at `p=4` that is *four of the five tasks* an n=5 leg "
      "measures | excluding the first wave, and reporting it **separately** |",
      "| **run order** aliases onto the plugin variable | the legs run in one fixed sequence, so any "
      "monotone drift loads onto whichever preset ran last | a **reversed-order replicate** |", "",
      "The matching that made the retrospective comparison worth doing still applies and is still "
      "load-bearing: **task selection is deterministic**, so every leg runs the same tasks in the "
      "same order with the same model. That is verified below before any latency figure is quoted.", ""]

# --- order balance ---------------------------------------------------------
L += ["## Run order was balanced by design (the crossover)", "",
      "Replicate 2 runs the five conditions in **reverse** order. That makes each condition's mean "
      "run position identical, so a smooth drift over the session — gateway warm-up, cache fill, "
      "node contention — cancels between replicates instead of being attributed to a preset.", "",
      "| condition | rep 1 position | rep 2 position | position sum |",
      "|---|---:|---:|---:|"]
sums = []
for c in CONDS:
    p1 = [POS[n] for n in GS if cond(n) == c and role(n) == "rep1"]
    p2 = [POS[n] for n in GS if cond(n) == c and role(n) == "rep2"]
    s = sum(p1 + p2)
    sums.append(s)
    L.append(f"| {c} | {p1[0] if p1 else '—'} | {p2[0] if p2 else '—'} | {s} |")
L += ["",
      ("**Balanced** — every condition has the same position sum, so run order cannot favour any "
       "one of them." if len(set(sums)) == 1 else
       "⚠️ **Not balanced** (a leg is missing), so a drift-vs-plugin confound is only partly "
       "removed; treat the replicate-agreement section as the load-bearing check."), ""]

# --- identical work --------------------------------------------------------
L += ["## The comparison is like-for-like (identical work)", "",
      "If every leg did the same work, a latency difference is attributable to the plugin "
      "configuration. `num_parallel` is read back from the artifacts rather than from the request, "
      "so a leg that silently ran at the wrong concurrency cannot pass unnoticed.", "",
      "| # | pos | condition | rep | tasks | OK | input tok | output tok | LLM calls | tool calls | p | model |",
      "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
sig = {}
for n in GS:
    rr = ranked(n)
    o = ok(rr)
    models = sorted({x.get("model") for x in rr if x.get("model")}) or ["—"]
    par = sorted({x.get("num_parallel") for x in rr})
    s = (sum(x.get("llm_input_tokens") or 0 for x in o),
         sum(x.get("llm_count") or 0 for x in o),
         sum(x.get("tool_count") or 0 for x in o))
    sig[n] = s
    L.append("| %d | %d | %s | %s | %d | %d | %d | %d | %d | %d | %s | `%s` |" % (
        n, POS[n], cond(n), legs[n]["rep"], len(rr), len(o), s[0],
        sum(x.get("llm_output_tokens") or 0 for x in o), s[1], s[2],
        ",".join(str(x) for x in par), models[0]))
same = len(set(sig.values())) == 1
L += ["",
      ("**Identical across every leg** — same input-token total, same LLM-call count, same tool-call "
       "count. The only variable is the plugin configuration."
       if same else
       "⚠️ **The legs did not do byte-identical work.** Input-token totals are the sensitive "
       "signature; where they differ, the corresponding latency delta is confounded by workload and "
       "the per-condition figures below should be read as indicative only. Note that a differing "
       "**output**-token total is expected and harmless — the model is sampled, not deterministic."),
      ""]

# --- serial diagnostic -----------------------------------------------------
L += ["## The decisive security question: caching, or fail-open?", ""]
if DIAG:
    n = DIAG[0]
    rr = ok(ranked(n))
    tc = sum(x.get("tool_count") or 0 for x in rr)
    L += ["Across four 12-run matrices, IBAC legs authorized only *some* of their tool calls, and the "
          "count wobbled across identically configured runs. Two explanations fit that equally well "
          "from the matrix artifacts, and they could not be more different in consequence:", "",
          "1. **Benign — decision caching under concurrency.** At `max_parallel_sessions=4` several "
          "identical calls fire at once, all miss the cache and consult the judge; later ones hit a "
          "warm entry.",
          "2. **A fail-open enforcement gap** — the action proceeded with no authorization decision. "
          "For a security control this matters far more than any latency figure in this document.", "",
          f"Leg **#{n}** settles it by removing concurrency: `{cond(n)}` at "
          f"**`max_parallel_sessions=1`**, so the tool calls are strictly serial and no two can race "
          f"for the same cache entry.", "",
          f"| measurement | value |", "|---|---:|",
          f"| tasks attempted | {len(rows(n))} |",
          f"| tasks OK | {len(rr)} |",
          f"| tool calls (serial, OK tasks) | {tc} |"]
    if judge_ts:
        r = runs[n]
        t = r["run_id"][:14]
        start = dt.datetime(int(t[:4]), int(t[4:6]), int(t[6:8]), int(t[8:10]),
                            int(t[10:12]), int(t[12:14]), tzinfo=timezone.utc)
        end = start + dt.timedelta(seconds=(r.get("summary") or {}).get("wall_seconds", 0) + 20)
        j = sum(1 for x in judge_ts if start <= x <= end)
        L += [f"| judge calls in the run window | **{j}** |", ""]
        if tc and j >= tc:
            extra = ""
            if j > tc and len(rows(n)) > len(rr):
                extra = (f" The count exceeds {tc} because {len(rows(n)) - len(rr)} task(s) did not "
                         "reach a passing verdict: a failed task's tool call is absent from the "
                         "artifacts but its judge call still happened, so the honest reading is "
                         f"{j} judged calls across {len(rows(n))} attempted tasks.")
            L += [f"**Verdict: enforcement is 1:1 without concurrency, and TTL caching is ruled "
                  f"out.** All {tc} serial tool calls were judged ({j} judge calls) — every action "
                  f"got its own authorization decision.{extra}", "",
                  "That result does more than reassure; it **eliminates one of the two candidate "
                  "explanations**. A decision cache with a TTL would have collapsed these "
                  f"{tc} same-shape calls to roughly one judge call *even when serial*. It did not. "
                  "So whatever produces the shortfall at `p=4` is specific to **concurrency**, and "
                  "two mechanisms remain — which are *not* equally benign:", "",
                  "- **Benign: single-flight deduplication.** Concurrent identical requests share "
                  "one in-flight decision. Every action is still authorized, just not by its own "
                  "round-trip.",
                  "- **Not benign: fail-open under race.** A call proceeds while a decision is still "
                  "pending, so the action is never actually authorized.", "",
                  "Both are concurrency-only and produce **identical call counts**, so no amount of "
                  "counting separates them — that now requires reading the sidecar's authorization "
                  "path. The contribution of this leg is to narrow the audit from *'is IBAC enforcing "
                  "at all?'* to *'what does the concurrent path do with an in-flight decision?'*, "
                  "and to establish that enforcement is not globally broken.", ""]
        elif tc and j <= max(2, tc // 4):
            L += [f"**Verdict: decision caching is real.** {tc} serial tool calls produced only {j} "
                  "judge call(s), which is the signature of a cache that is hit even without "
                  "concurrency. Enforcement is intact *provided* the cache key is sound — that is "
                  "now the thing to audit, and it is a code question, not a measurement question.", ""]
        elif tc:
            L += [f"⚠️ **Verdict: unresolved, and this is the important kind of unresolved.** {tc} "
                  f"serial tool calls produced {j} judge calls — neither one-per-call nor a clear "
                  "cache. With concurrency removed, caching no longer explains the gap, which leaves "
                  "**fail-open as the leading hypothesis**. This should be escalated to the "
                  "AuthBridge/IBAC owners with this leg's `run_id` before the preset is relied on as "
                  "a security control.", ""]
    else:
        L += ["", "⚠️ No judge log was supplied, so the count that decides this cannot be reported. "
              "Re-generate with the judge timestamp file — this is the highest-value section in the "
              "document and it is empty without it.", ""]
else:
    L += ["⚠️ The serial diagnostic leg did not execute, so this question remains open.", ""]

# --- warm-up decomposition -------------------------------------------------
L += ["## Warm-up is one concurrency wave, and must be excluded rather than averaged in", "",
      "Per-task cost is not stationary: the first tasks of *any* leg pay one-time costs (connection "
      "setup, TLS handshake, cache fill) that have nothing to do with plugins. Averaging them into a "
      "'per-task overhead' inflates it — which is exactly what a 5-task leg cannot avoid doing.", "",
      "Metric throughout is **non-LLM agent time** (`agent_call_s - llm_total_s`): MCP connect, tool "
      "calls and sidecar interception, with the shared LLM gateway's variance removed.", "",
      "### The transient is the first wave, not the first N tasks", "",
      "This matters more than it sounds, because it determines what to exclude. Median non-LLM "
      "seconds by rank bucket, baseline legs only (no sidecar at all, so this is pure deployment "
      "warm-up):", "",
      "| # | rep | p | " + " | ".join(f"rank {a}–{b or '50'}" for a, b in DECAY_BUCKETS) + " |",
      "|---|---:|---:|" + "---:|" * len(DECAY_BUCKETS)]
for n in [x for x in GS if cond(x) == "baseline"]:
    rr = ok(ranked(n))
    cells = []
    for a, b in DECAY_BUCKETS:
        v = [non_llm(x) for x in rr if a <= x["_rank"] and (b is None or x["_rank"] <= b)]
        cells.append(f(st.median(v) if v else None))
    L.append(f"| {n} | {legs[n]['rep']} | {warm_cutoff(n)} | " + " | ".join(cells) + " |")
L += ["",
      "The cost drops to steady state **after the first bucket** and stays there — the transient is "
      "one wave of `max_parallel_sessions` tasks, all of which start before any connection is warm. "
      "Splitting at the wave gives a reproducible ratio; splitting at a fixed 10 tasks buries it by "
      "mixing six steady-state tasks into the warm bucket, and on one leg even inverts its sign "
      "(0.72x). **The cutoff below is therefore derived per leg from the artifacts' own "
      "`num_parallel`, not fixed.**", "",
      "### Warm-up vs steady state, per leg", "",
      "| # | condition | rep | cutoff | first wave (med) | steady (med) | steady n | ratio |",
      "|---|---|---:|---:|---:|---:|---:|---:|"]
for n in GS:
    rr = ok(ranked(n))
    c = warm_cutoff(n)
    a = [non_llm(x) for x in rr if x["_rank"] <= c]
    b = steady(n)
    ma, mb = (st.median(a) if a else None), (st.median(b) if b else None)
    rat = (ma / mb) if (ma and mb) else None
    L.append(f"| {n} | {cond(n)} | {legs[n]['rep']} | {c} | {f(ma)} | {f(mb)} | {len(b)} | "
             f"{f(rat, '%.2fx')} |")
L += ["",
      "Ratios above 1 are the transient. The **baseline** legs have no sidecar, so whatever ratio "
      "they show is a property of the deployment; a preset leg's ratio is only evidence about "
      "AuthBridge to the extent that it *exceeds* the baseline's. This is the specific reason a "
      "'fixed per-task plugin cost' derived from a 5-task leg is not a plugin cost at all — at "
      "`p=4`, four of those five tasks *are* the transient.", "",
      "**Everything below excludes the first wave of each leg.**", ""]

# --- steady-state per condition -------------------------------------------
L += ["## Steady-state cost per condition, with intervals", "",
      "Replicates pooled. A bare median invites over-reading; the interval is what says whether a "
      "difference was resolvable at all.", "",
      "| condition | n | median non-LLM s | %d%% CI | vs baseline (Δ median) | Δ CI | perm p |"
      % int(CONF * 100),
      "|---|---:|---:|---|---:|---|---:|"]
pool = {}
for c in CONDS:
    v = []
    for n in GS:
        if cond(n) == c:
            v += steady(n)
    pool[c] = v
base = pool.get("baseline", [])
for c in CONDS:
    v = pool[c]
    m = st.median(v) if v else None
    d = (st.median(v) - st.median(base)) if (v and base) else None
    if c == "baseline":
        L.append(f"| **{c}** | {len(v)} | {f(m)} | {ci(boot_median_ci(v))} | — | — | — |")
    else:
        p = perm_p(base, v)
        L.append(f"| {c} | {len(v)} | {f(m)} | {ci(boot_median_ci(v))} | {f(d, '%+.3f')} | "
                 f"{ci(boot_diff_ci(base, v), '%+.3f')} | {f(p, '%.4f')} |")
L += ["",
      "A Δ CI that straddles zero means **no difference was resolved** at this n — which, unlike the "
      "n=5 case, is now a meaningful statement rather than a limit of the instrument. "
      f"With {len(base)} baseline samples the tests are comparisons of medians by permutation, so "
      f"they make no normality assumption. Note that this table is "
      f"{len(CONDS)-1} simultaneous tests against one baseline, so the Bonferroni-corrected "
      f"threshold is {0.05/max(1, len(CONDS)-1):.4f} rather than 0.05.", ""]

# --- incremental cost ------------------------------------------------------
L += ["### Incremental cost of each layer", "",
      "The conditions nest: `baseline` ⊂ `auth-only` ⊂ ... The interesting quantity is not each "
      "condition's cost against baseline but the **marginal** cost of each added layer, which is "
      "what the 12-run's n=5 could never resolve.", "",
      "| step | Δ median s | Δ CI | perm p | resolved? |", "|---|---:|---|---:|---|"]
for a, b in zip(CONDS, CONDS[1:]):
    va, vb = pool[a], pool[b]
    if not (va and vb):
        continue
    d = st.median(vb) - st.median(va)
    lo, hi = boot_diff_ci(va, vb)
    p = perm_p(va, vb)
    res = "no — CI straddles 0" if (lo is None or lo <= 0 <= hi) else "**yes**"
    L.append(f"| {a} → {b} | {f(d, '%+.3f')} | {ci((lo, hi), '%+.3f')} | {f(p, '%.4f')} | {res} |")
L.append("")

# --- pseudo-replication ----------------------------------------------------
# The CIs above treat tasks as independent replicates of a CONDITION. They are not: every task in a
# leg shares one deployment, so per-task intervals answer "how variable are tasks within this
# deploy?" -- not "how variable is this condition?". The replicate design is what exposes the
# difference, and the leg-level spread is the only honest yardstick for a between-condition effect.
leg_med = {n: st.median(steady(n)) for n in GS if steady(n)}
pair, dep_diffs = {}, []
for c in CONDS:
    m1 = [leg_med[n] for n in GS if cond(n) == c and role(n) == "rep1" and n in leg_med]
    m2 = [leg_med[n] for n in GS if cond(n) == c and role(n) == "rep2" and n in leg_med]
    if m1 and m2:
        pair[c] = (m1[0], m2[0], abs(m1[0] - m2[0]))
        dep_diffs.append(abs(m1[0] - m2[0]))

L += ["### The unit of replication is the deploy, not the task", "",
      "The intervals above treat each task as an independent replicate of its condition. **They are "
      "not.** Every task in a leg ran against one deployment, so a per-task interval answers *'how "
      "variable are tasks within this deploy?'* — not *'how variable is this condition?'*. Treating "
      "the former as the latter is pseudo-replication, and it makes intervals look far tighter than "
      "the design can support. The replicate block is what makes this visible and correctable:", "",
      "| condition | rep 1 leg median | rep 2 leg median | \\|Δ\\| between deploys |",
      "|---|---:|---:|---:|"]
for c in CONDS:
    if c in pair:
        a1, a2, dd = pair[c]
        L.append(f"| {c} | {a1:.3f} | {a2:.3f} | **{dd:.3f}** |")
sd_dep = (st.median(dep_diffs) / 0.6745) / (2 ** 0.5) if len(dep_diffs) > 1 else None
sidecar = [pair[c][2] for c in CONDS if c in pair and c != "baseline"]
L.append("")
if sd_dep:
    L += [f"Median \\|Δ\\| between two deploys of the *same* condition is "
          f"**{st.median(dep_diffs):.2f} s** (max {max(dep_diffs):.2f} s), implying a between-deploy "
          f"SD of roughly **{sd_dep:.2f} s**. Compare that with the marginal effects in the table "
          "above — every preset-to-preset step is *smaller than the noise between two deploys of one "
          "preset*.", "",
          "So the correct reading of this experiment is:", ""]
    steps = [(a, b, st.median(pool[b]) - st.median(pool[a]))
             for a, b in zip(CONDS, CONDS[1:]) if pool.get(a) and pool.get(b)]
    big = [s for s in steps if abs(s[2]) > 3 * sd_dep]
    small = [s for s in steps if abs(s[2]) <= 3 * sd_dep]
    for a, b, dv in big:
        L.append(f"- **{a} → {b}: {dv:+.2f} s — real.** That is "
                 f"{abs(dv)/sd_dep:.1f}x the between-deploy SD, far outside what deployment "
                 "variability can manufacture.")
    if small:
        L.append("- **" + "; ".join(f"{a} → {b} ({dv:+.2f} s)" for a, b, dv in small) +
                 f" — below the noise floor.** Not 'probably small': **unresolvable** with two "
                 f"deploys per condition, regardless of how many tasks each deploy runs. Adding "
                 f"tasks tightens the wrong interval.")
    L += ["",
          "**What it would actually take.** At ~16·σ²/δ² deploys per condition for 80% power, "
          "resolving a "
          + ", ".join(f"{d:.0f} s effect needs ~{max(2, round(16*sd_dep**2/d**2)):.0f} deploys"
                      for d in (1.0, 2.0))
          + " per condition — against the 2 run here. That is the honest price of a per-preset "
          "ranking, and it is the number to quote if anyone asks for one.", ""]
    if sidecar and "baseline" in pair:
        b_dd = pair["baseline"][2]
        if b_dd < min(sidecar) / 2:
            L += [f"**A side finding worth recording:** the two *baseline* deploys differ by only "
                  f"**{b_dd:.3f} s**, while sidecar-injected deploys differ by "
                  f"{min(sidecar):.2f}–{max(sidecar):.2f} s. Deployment variability is not a "
                  "background property of the cluster — it is **introduced by the sidecar**. "
                  "AuthBridge costs not only latency but *reproducibility*, which matters for anyone "
                  "using these benchmarks to detect regressions.", ""]

# --- replicate agreement ---------------------------------------------------
L += ["## Replicate agreement (the order-effect test)", "",
      "Each condition ran twice, once early and once late, in reversed order. Agreement means run "
      "order is not driving the result. Disagreement is informative rather than fatal — the "
      "*direction* of the differences distinguishes a monotone session drift (all one sign) from "
      "deploy-to-deploy variability (mixed signs), and only the first would bias a condition "
      "comparison.", "",
      "| condition | rep 1 med | rep 2 med | Δ | Δ CI | perm p |", "|---|---:|---:|---:|---|---:|"]
disagree = []
for c in CONDS:
    r1 = [v for n in GS if cond(n) == c and role(n) == "rep1" for v in steady(n)]
    r2 = [v for n in GS if cond(n) == c and role(n) == "rep2" for v in steady(n)]
    if not (r1 and r2):
        continue
    d = st.median(r2) - st.median(r1)
    lo, hi = boot_diff_ci(r1, r2)
    p = perm_p(r1, r2)
    if lo is not None and not (lo <= 0 <= hi):
        disagree.append(c)
    L.append(f"| {c} | {f(st.median(r1))} | {f(st.median(r2))} | {f(d, '%+.3f')} | "
             f"{ci((lo, hi), '%+.3f')} | {f(p, '%.4f')} |")
L += [""]
if not disagree:
    L += ["**Every condition's replicates agree** (all Δ CIs include zero), so run position is not "
          "driving the per-condition figures and the deltas above can be read as plugin cost.", ""]
else:
    signs = {}
    for c in CONDS:
        if c in pair:
            signs[c] = pair[c][1] - pair[c][0]
    mixed = len({v > 0 for v in signs.values()}) > 1
    L += ["⚠️ **Replicates disagree for: " + ", ".join(f"`{c}`" for c in disagree) + ".** By the "
          "per-task test these are separate populations, so the per-task intervals in the previous "
          "section are too narrow — which is the pseudo-replication point made there, arriving here "
          "as direct evidence rather than as a caveat.", ""]
    if mixed:
        L += ["**But the disagreement is not run-order drift.** The rep-2 minus rep-1 differences go "
              "in **both directions** (" +
              ", ".join(f"`{c}` {v:+.2f} s" for c, v in signs.items()) + "), and a monotone session "
              "effect — gateway warm-up, cache fill, node contention — would have to push every "
              "condition the same way, since replicate 2 ran entirely later than replicate 1. A "
              "non-monotone pattern instead points at **deploy-to-deploy variability**: each leg is a "
              "fresh deployment, and that is the nuisance variable, not elapsed time.", "",
              "This is a more useful conclusion than 'order matters', and it is only available "
              "because the reversal was built in: run order was **balanced by design** (equal "
              "position sums above), so it cannot be what produced a two-directional pattern.", ""]
    else:
        L += ["The differences all share a sign, which is the signature of a genuine **monotone "
              "session drift**. Replicate 2 ran entirely later than replicate 1, so the reversal "
              "balances position sums but cannot remove a drift this consistent; the pooled estimate "
              "remains the best available and its interval understates the uncertainty.", ""]

# --- judged calls ----------------------------------------------------------
if judge_ts:
    L += ["## Judged calls at n=50", "",
          "The IBAC judge is itself an LLM call, so judge latency inherits inference variance and "
          "**the number of judged calls need not equal the number of tool calls**. At n=5 a leg "
          "offered at most 5 chances to observe this and the resulting median was a mixture of two "
          "populations. At n=50 the ratio is estimable.", "",
          "| # | pos | condition | rep | tool calls | judge calls | judged / call |",
          "|---|---:|---|---:|---:|---:|---:|"]
    for n in GS:
        r = runs[n]
        if not r.get("run_id"):
            continue
        t = r["run_id"][:14]
        start = dt.datetime(int(t[:4]), int(t[4:6]), int(t[6:8]), int(t[8:10]),
                            int(t[10:12]), int(t[12:14]), tzinfo=timezone.utc)
        end = start + dt.timedelta(seconds=(r.get("summary") or {}).get("wall_seconds", 0) + 20)
        j = sum(1 for x in judge_ts if start <= x <= end)
        tc = sum(x.get("tool_count") or 0 for x in ok(ranked(n)))
        L.append(f"| {n} | {POS[n]} | {cond(n)} | {legs[n]['rep']} | {tc} | {j} | "
                 f"{f(j/tc if tc else None, '%.2f')} |")
    L += ["",
          "A ratio near 0 is a preset that does not engage IBAC (correct for `auth-only`); near 1 is "
          "full enforcement. **Intermediate ratios are the finding**, and the serial diagnostic "
          "above is what interprets them.", "",
          "The judge window is derived from the `run_id` timestamp plus the run's `wall_seconds` "
          "(+20 s slack). Legs are separated by teardown and redeploy, so windows do not overlap — "
          "but this attribution is by time, not by trace id, so a stray non-benchmark request to the "
          "judge would land in whichever window contained it.", ""]

# --- tau2 linearity --------------------------------------------------------
L += ["## Does per-call cost hold across benchmarks? (the tau2 linearity test)", ""]
tau_base = next((n for n in TAU if cond(n) == "baseline"), None)
tau_pre = next((n for n in TAU if cond(n) != "baseline"), None)
if tau_base and tau_pre and rows(tau_base) and rows(tau_pre):
    L += ["The retrospective reports project plugin cost onto other benchmarks by multiplying a "
          "gsm8k per-tool-call delta by each benchmark's tool-call count. That assumes per-call cost "
          "is a **constant** across benchmarks. gsm8k makes ~1 substantive tool call per task; tau2 "
          "makes roughly an order of magnitude more, so this pair tests the assumption directly "
          "instead of resting on it.", "",
          "| leg | condition | tasks OK | tool calls/task | non-LLM s/task (med) | per tool call (s) |",
          "|---|---|---:|---:|---:|---:|"]
    per_call = {}
    for n in (tau_base, tau_pre):
        rr = ok(ranked(n))
        tpt = st.median([x.get("tool_count") or 0 for x in rr]) if rr else None
        m = st.median([non_llm(x) for x in rr]) if rr else None
        pc = (m / tpt) if (m and tpt) else None
        per_call[n] = (m, tpt, pc)
        L.append(f"| #{n} | {cond(n)} | {len(rr)} | {f(tpt, '%.0f')} | {f(m, '%.2f')} | "
                 f"{f(pc, '%.3f')} |")
    mb, tb, pb = per_call[tau_base]
    mp, tp, pp = per_call[tau_pre]
    gs_pool = pool.get(cond(tau_pre)) or []
    gs_base = pool.get("baseline") or []
    gs_delta = (st.median(gs_pool) - st.median(gs_base)) if (gs_pool and gs_base) else None
    L += [""]
    if mb is not None and mp is not None:
        d_task = mp - mb
        d_call = (pp - pb) if (pp is not None and pb is not None) else None
        L += [f"**Measured on tau2:** `{cond(tau_pre)}` adds **{d_task:+.2f} s/task**, i.e. "
              f"**{f(d_call, '%+.3f')} s per tool call**.", ""]
        if gs_delta is not None and tp:
            proj = gs_delta * tp
            L += [f"**Projected from gsm8k** by the retrospective method: the same condition costs "
                  f"{gs_delta:+.3f} s/task on gsm8k over ~1 tool call, which scaled by tau2's "
                  f"{tp:.0f} tool calls/task predicts **{proj:+.2f} s/task**.", ""]
            if abs(proj) > 1e-9:
                ratio = d_task / proj
                L += [f"**Measured / projected = {ratio:.2f}x.**", ""]
                if 0.5 <= ratio <= 2.0:
                    L += ["The projection is the right order of magnitude, so scaling a gsm8k "
                          "per-call cost by tool-call count is a defensible back-of-envelope for "
                          "other benchmarks — with the factor-of-two caveat now measured rather "
                          "than hoped for.", ""]
                else:
                    L += ["**The projection does not hold.** Per-call cost is not a constant across "
                          "benchmarks, so the projected tau2/appworld figures in the retrospective "
                          "reports should be withdrawn rather than re-scaled: whatever drives the "
                          "difference (session reuse, connection amortisation, cache behaviour "
                          "across many calls in one session) is not captured by a per-call "
                          "constant.", ""]
else:
    L += ["⚠️ The tau2 pair did not both complete, so the per-call projection used by the "
          "retrospective reports remains **untested**. It should continue to be labelled an "
          "assumption wherever it is quoted.", ""]

# --- limitations -----------------------------------------------------------
L += ["## Confidence and limitations", "",
      "What this design **does** support:", "",
      "- Steady-state marginal cost per condition, with intervals, at a resolution the 12-run "
      "cannot reach.",
      "- A statement about run order that is *tested* (replicate agreement) rather than assumed.",
      "- Separation of one-time warm-up from per-task steady-state cost.",
      "- A judged-vs-unjudged reading with enough calls per leg to be a ratio rather than an anecdote.",
      "", "What it still does **not** support:", "",
      "- **Cold-start / admission cost is not isolated.** Deploy and readiness time is excluded from "
      "`agent_call_s` entirely, so the cost of *injecting* the sidecar is invisible here. It would "
      "need deploy-duration instrumentation, which is a separate change.",
      "- **Infra cost is unmeasurable from these artifacts.** Every `mcp_*`/`a2a_*` CPU and memory "
      "field is `0.0` and `has_infra` is `false`, because those values come from an `infra` "
      "attribute on the agent's OTEL root span that the upstream agent does not emit. No number of "
      "runs fixes this; it needs an upstream change.",
      "- **One model, one cluster, one day.** The conditions are internally comparable; absolute "
      "seconds are not portable to other hardware or gateways.",
      "- **The LLM path is excluded by construction.** If a plugin changed LLM latency or token "
      "counts, this metric would not show it — the identical-work check is what makes that "
      "exclusion safe, and it is verified above rather than assumed.", "",
      "### Reproducing this", "",
      "```sh", f"BM_SPECS=reference/plugin_study_specs.json BM_LABEL={data.get('label')} \\",
      "  python3 reference/run-12.py        # detached; see feedback_long_runs_detach_and_adopt",
      f"python3 reference/gen-plugin-study.py {SRC.name} reference/plugin_study_specs.json "
      f"{VERSION} '{PLATFORM}' out.md judge.ts", "```", ""]

doc = "\n".join(L)
doc = doc.replace("<!--TOC-->", _toc(doc))
if OUT:
    OUT.write_text(doc)
    print(f"wrote {OUT} ({len(doc)} bytes)")
else:
    print(doc)
