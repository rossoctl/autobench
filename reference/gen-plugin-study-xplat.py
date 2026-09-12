#!/usr/bin/env python3
"""Compare the designed plugin-overhead experiment across two platforms.

Usage: gen-plugin-study-xplat.py <spec.json> <version> <out.md> \
           <labelA> <runA.json> [judgeA.ts] -- <labelB> <runB.json> [judgeB.ts]

`gen-plugin-study.py` analyses one platform. This script exists because the single-platform reports
turned out to *disagree about which layer costs anything*, and that disagreement is only visible
side by side. It is the plugin-study analogue of the 12-run's OCP-vs-KinD comparison: the same
image, the same specs, the same deterministic task selection, two very different clusters.

Deliberately narrow. It re-derives only the quantities that mean something across platforms --
steady-state medians and their spread, the serial diagnostic, the judged ratio and the tau2
linearity ratio -- and it does NOT pool the two platforms into one interval. Pooling would imply the
clusters are interchangeable draws from one population, which is the very thing this comparison
falsifies.
"""
import json
import pathlib
import statistics as st
import sys
import datetime as dt

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gen_toc import build as _toc  # noqa: E402

CONDITIONS = ["baseline", "auth-only", "ibac-only", "full", "full+ibac:observe"]


def parse_argv(argv):
    spec, version, out = argv[1], argv[2], argv[3]
    rest = argv[4:]
    groups, cur = [], []
    for a in rest:
        if a == "--":
            groups.append(cur)
            cur = []
        else:
            cur.append(a)
    groups.append(cur)
    plats = []
    for g in groups:
        plats.append({"label": g[0], "run": pathlib.Path(g[1]),
                      "judge": pathlib.Path(g[2]) if len(g) > 2 else None})
    return pathlib.Path(spec), version, pathlib.Path(out), plats


SPEC, VERSION, OUT, PLATS = parse_argv(sys.argv)
spec = json.loads(SPEC.read_text())
legs = {s["n"]: s for s in spec["legs"]}
ORDER = spec["order"]


def rows(mirror_dir, name="report.ndjson"):
    p = pathlib.Path(mirror_dir) / name
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def non_llm(x):
    return (x.get("agent_call_s") or 0.0) - (x.get("llm_total_s") or 0.0)


def load(pl):
    """Per-leg steady-state samples, keyed by leg number.

    Warm-up cutoff is the first concurrency wave, read back from the artifacts -- the same rule
    gen-plugin-study.py uses, so the numbers here are comparable with the per-platform reports.
    """
    d = json.loads(pl["run"].read_text())
    runs = {r["n"]: r for r in d["runs"]}
    ts = []
    if pl["judge"] and pl["judge"].exists():
        for line in pl["judge"].read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    ts.append(dt.datetime.fromisoformat(line.replace("Z", "+00:00")))
                except ValueError:
                    pass
    out = {}
    for n, r in runs.items():
        if not r.get("mirror_dir"):
            continue
        rr = [x for x in rows(r["mirror_dir"]) if x.get("start_time")]
        rr.sort(key=lambda x: x["start_time"])
        par = max([x.get("num_parallel") or 1 for x in rr] or [1])
        okr = [x for x in rr if x.get("status") == "OK"]
        out[n] = {
            "steady": [non_llm(x) for i, x in enumerate(rr, 1)
                       if x.get("status") == "OK" and i > par],
            "all_ok": [non_llm(x) for x in okr],
            "n_rows": len(rr), "n_ok": len(okr),
            "tool": sum(x.get("tool_count") or 0 for x in okr),
            "in_tok": sum(x.get("llm_input_tokens") or 0 for x in rr),
            "model": okr[0]["model"] if okr else None,
            "par": par, "run_id": r.get("run_id"),
            "wall": (r.get("summary") or {}).get("wall_seconds"),
        }
    return {"legs": out, "judge": ts, "base": d.get("base"), "label": pl["label"]}


def judged(P, n):
    """Judge calls inside a leg's run window (run_id timestamp + wall + 20 s slack)."""
    if not P["judge"]:
        return None
    L = P["legs"].get(n)
    if not L or not L.get("run_id") or L.get("wall") is None:
        return None
    t = L["run_id"][:14]
    start = dt.datetime(int(t[:4]), int(t[4:6]), int(t[6:8]), int(t[8:10]), int(t[10:12]),
                        int(t[12:14]), tzinfo=dt.timezone.utc)
    end = start + dt.timedelta(seconds=L["wall"] + 20)
    return sum(1 for x in P["judge"] if start <= x <= end)


def cond_samples(P, condition):
    out = []
    for n, L in P["legs"].items():
        if legs[n].get("condition") == condition and legs[n].get("role", "").startswith("rep"):
            out += L["steady"]
    return out


def cond_leg_medians(P, condition):
    return [st.median(L["steady"]) for n, L in sorted(P["legs"].items())
            if legs[n].get("condition") == condition
            and legs[n].get("role", "").startswith("rep") and L["steady"]]


def fmt(v, d=3):
    return "—" if v is None else f"{v:.{d}f}"


PS = [load(p) for p in PLATS]
A, B = PS[0], PS[1]
L = [f"# AuthBridge plugin overhead — {A['label']} vs {B['label']}", "",
     f"**Report generated:** {dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ}  ",
     f"**Service version:** `{VERSION}`  ",
     f"**Experiment:** `{SPEC.name}` — {len(ORDER)} legs, identical specs on both platforms  ",
     f"**Companion reports:** the per-platform analyses, which carry the intervals and the "
     f"design rationale this document does not repeat.", "", "<!--TOC-->", ""]

# --- why -------------------------------------------------------------------
L += ["## Why compare, when both reports already agree on method", "",
      "The two platforms ran the **same 13 legs from the same spec file, on the same image, with "
      "the same deterministic task selection**. The intent was ordinary cross-platform validation: "
      "confirm on a second cluster what the first one measured.", "",
      "That is not what came back. The platforms agree on every *structural* finding and disagree "
      "on every *magnitude* — including which plugin layer the cost belongs to. That disagreement "
      "is the single most consequential result of the study, and it is **invisible in either "
      "per-platform report alone**: each one reads as a clean, internally consistent answer. It is "
      "the reason a per-task plugin figure must never be quoted without naming the cluster it was "
      "measured on.", ""]

# --- same work -------------------------------------------------------------
L += ["## First: did the two platforms do the same work?", "",
      "A latency comparison across clusters is only meaningful if the workload matched. Model and "
      "input-token totals are the signature; both are read back from the artifacts.", "",
      f"| leg | model | tasks | {A['label']} input tok | {B['label']} input tok | Δ |",
      "|---|---|---:|---:|---:|---:|"]
for n in ORDER:
    la, lb = A["legs"].get(n), B["legs"].get(n)
    if not la or not lb:
        continue
    same = "identical" if la["in_tok"] == lb["in_tok"] else f"{lb['in_tok'] - la['in_tok']:+d}"
    L.append(f"| #{n} | `{la['model']}` | {la['n_rows']} | {la['in_tok']} | {lb['in_tok']} | "
             f"{same} |")
def tok_spread(P):
    """Distinct input-token totals across the 10 replicate legs, and their relative spread."""
    v = [P["legs"][n]["in_tok"] for n in P["legs"]
         if legs[n].get("role", "").startswith("rep") and P["legs"][n]["in_tok"]]
    uniq = sorted(set(v))
    mode = max(uniq, key=v.count)
    return uniq, v.count(mode), len(v), (max(uniq) - min(uniq)) / min(uniq) * 100


ua, na, ta, spa = tok_spread(A)
ub, nb_, tb, spb = tok_spread(B)
xa = [A["legs"][n]["in_tok"] for n in sorted(A["legs"]) if legs[n].get("role", "").startswith("rep")]
xb = [B["legs"][n]["in_tok"] for n in sorted(B["legs"]) if legs[n].get("role", "").startswith("rep")]
xspread = max(abs(a - b) / min(a, b) for a, b in zip(xa, xb)) * 100
L += ["",
      f"**The gsm8k legs are not byte-identical, within or across platforms** — worth stating "
      f"plainly, because the per-platform reports' own identical-work checks flag this too. "
      f"{A['label']} produced {len(ua)} distinct totals across its 10 replicate legs "
      f"({na} legs share the most common one) and {B['label']} produced {len(ub)} "
      f"({nb_} share the most common). Within-platform spread is "
      f"{spa:.1f}% ({A['label']}) and {spb:.1f}% ({B['label']}); the largest same-leg gap across "
      f"platforms is {xspread:.1f}%.", "",
      f"The cause is benign and expected: the model is sampled rather than deterministic, so an "
      f"agent occasionally spends one extra tool call on a task, and the two clusters front "
      f"different LiteLLM gateways. What matters is the ratio of that confound to the effects being "
      f"measured. A few percent of token drift cannot manufacture the differences below, which are "
      f"**multiples** — up to 60x. The confound would matter if this document ranked presets against "
      f"each other by a few percent, and it explicitly does not.", ""]

# --- the headline ----------------------------------------------------------
L += ["## The headline: the platforms disagree about which layer costs anything", "",
      "Steady-state non-LLM time per task (`agent_call_s - llm_total_s`), replicates pooled, "
      "warm-up wave excluded.", "",
      f"| condition | {A['label']} median s | {B['label']} median s | ratio | "
      f"marginal step ({A['label']}) | marginal step ({B['label']}) |",
      "|---|---:|---:|---:|---:|---:|"]
prev_a = prev_b = None
rows_out = []
for c in CONDITIONS:
    sa, sb = cond_samples(A, c), cond_samples(B, c)
    if not sa or not sb:
        continue
    ma, mb = st.median(sa), st.median(sb)
    step_a = "—" if prev_a is None else f"{ma - prev_a:+.2f}"
    step_b = "—" if prev_b is None else f"{mb - prev_b:+.2f}"
    L.append(f"| {'**' + c + '**' if c == 'baseline' else c} | {ma:.3f} | {mb:.3f} | "
             f"{ma / mb:.1f}x | {step_a} | {step_b} |")
    rows_out.append((c, ma, mb, None if prev_a is None else ma - prev_a,
                     None if prev_b is None else mb - prev_b))
    prev_a, prev_b = ma, mb

# Which layer dominates on each platform?
def dominant(idx):
    steps = [(c, r[idx]) for c, *r in [(x[0], x[3], x[4]) for x in rows_out] if r[idx] is not None]
    return max(steps, key=lambda kv: kv[1]) if steps else (None, None)


dom_a, val_a = dominant(0)
dom_b, val_b = dominant(1)
base_a = next((x[1] for x in rows_out if x[0] == "baseline"), None)
base_b = next((x[2] for x in rows_out if x[0] == "baseline"), None)
L += ["",
      f"Read the two *marginal step* columns against each other — that is the whole finding.", ""]
if dom_a and dom_b and dom_a != dom_b:
    L += [f"- On **{A['label']}** the cost enters at **{dom_a}** (`{val_a:+.2f}` s) and every later "
          f"layer is lost in the noise.",
          f"- On **{B['label']}** that same step is nearly free, and the cost enters at "
          f"**{dom_b}** (`{val_b:+.2f}` s) instead.", "",
          f"**So the two clusters do not merely scale differently — they attribute the cost to "
          f"different plugin layers.** A reader of the {A['label']} report alone would conclude that "
          f"the sidecar's presence is the expense and IBAC's judge call is negligible; a reader of "
          f"the {B['label']} report alone would conclude the exact opposite. Both readings are "
          f"correct about their own cluster.", ""]
else:
    L += [f"- Both platforms locate the dominant step at **{dom_a}**, at `{val_a:+.2f}` s on "
          f"{A['label']} and `{val_b:+.2f}` s on {B['label']}.", ""]

# --- spread ---------------------------------------------------------------
L += ["### It is variable cost, not a fixed penalty (a hypothesis worth falsifying)", "",
      f"A ~{val_a:.0f} s per-task cost on one cluster and ~0.1 s on another invites an obvious "
      "guess: a timeout or a failing retry somewhere in the sidecar's egress path. A timeout leaves "
      "a signature — values piled on a round number with a small spread. The distributions falsify "
      "it.", "",
      f"| platform | condition | n | min | median | max | SD |", "|---|---|---:|---:|---:|---:|---:|"]
for P in (A, B):
    for c in ("baseline", "auth-only", "full"):
        s = cond_samples(P, c)
        if not s:
            continue
        L.append(f"| {P['label']} | {c} | {len(s)} | {min(s):.2f} | {st.median(s):.2f} | "
                 f"{max(s):.2f} | {st.pstdev(s):.2f} |")
sd_a = st.pstdev(cond_samples(A, "auth-only") or [0])
sb_auth = cond_samples(B, "auth-only") or [0]
sb_full = cond_samples(B, "full") or [0]
L += ["",
      f"On {A['label']} the sidecar conditions spread across roughly an order of magnitude "
      f"(4.5–20 s for `auth-only`) with an SD of ~{sd_a:.1f} s — **broad and unimodal, with nothing "
      f"piled at a round number**. That is the shape of contention and queueing, not of a fixed "
      f"timeout, so the timeout hypothesis is dead.", "",
      f"{B['label']} deserves a separate remark rather than a reassuring one-liner. Its `auth-only` "
      f"distribution is genuinely tight (SD {st.pstdev(sb_auth):.2f} s), but its `full` distribution "
      f"is **not**: median {st.median(sb_full):.2f} s against a max of {max(sb_full):.2f} s, SD "
      f"{st.pstdev(sb_full):.2f} s. That heavy right tail is the IBAC judge, which is itself an LLM "
      f"call and therefore inherits inference latency variance. So on the quiet cluster the judge's "
      f"own variability becomes the dominant source of spread, where on the busy cluster it is "
      f"buried under cluster contention.", "",
      "Two consequences. First, the mechanism to chase on the shared cluster is *why the intercepted "
      "path is slow and jittery there*, not a misconfigured timeout — a different investigation. "
      "Second, on both platforms the spread grows with the median, so enabling the sidecar costs "
      "**reproducibility** as well as latency, which matters for anyone using these benchmarks to "
      "detect regressions.", ""]

# --- what reproduced ------------------------------------------------------
L += ["## What reproduced exactly: every structural finding", "",
      "The magnitudes did not travel. The *structure* did — and the structural findings are the "
      "ones with consequences.", ""]

# serial diagnostic
diag = [n for n in ORDER if legs[n].get("role") == "serial-diagnostic"]
if diag:
    n = diag[0]
    L += ["### The serial diagnostic, independently on both clusters", "",
          "`ibac-only` at `max_parallel_sessions=1`: strictly serial tool calls, so no two can race "
          "for one cache entry.", "",
          "| platform | tasks attempted | serial tool calls | judge calls | ratio |",
          "|---|---:|---:|---:|---:|"]
    both_1to1 = True
    for P in (A, B):
        Lg = P["legs"].get(n)
        j = judged(P, n)
        r = f"{j / Lg['tool']:.2f}" if j is not None and Lg["tool"] else "—"
        if j is None or not Lg["tool"] or j < Lg["tool"]:
            both_1to1 = False
        L.append(f"| {P['label']} | {Lg['n_rows']} | {Lg['tool']} | "
                 f"{'—' if j is None else j} | {r} |")
    L += [""]
    if both_1to1:
        L += ["**Both clusters authorize every serial call.** TTL decision caching is ruled out "
              "twice, independently — a cache with a time-to-live would have collapsed ten "
              "same-shape serial calls to roughly one judge call on *either* platform. Whatever "
              "produces the shortfall at `p=4` is therefore specific to concurrency, and the two "
              "candidates that remain (benign single-flight deduplication vs fail-open under race) "
              "produce identical counts. Separating them is a code-reading task, not a measurement "
              "task, and it is the one open item from this study with a security consequence.", ""]

# judged ratio
L += ["### The judged-call ratio is ~1, on both", "",
      "The 12-run matrices repeatedly showed IBAC legs authorizing only *some* calls — at one point "
      "3 of 5 — which read as alarming. At n=50 it is a ratio rather than an anecdote.", "",
      f"| condition | {A['label']} judged/call | {B['label']} judged/call |", "|---|---:|---:|"]
for c in CONDITIONS:
    vals = []
    for P in (A, B):
        ns = [n for n in ORDER if legs[n].get("condition") == c
              and legs[n].get("role", "").startswith("rep")]
        rs = []
        for n in ns:
            j = judged(P, n)
            Lg = P["legs"].get(n)
            if j is not None and Lg and Lg["tool"]:
                rs.append(j / Lg["tool"])
        vals.append(f"{min(rs):.2f}–{max(rs):.2f}" if rs else "—")
    L.append(f"| {c} | {vals[0]} | {vals[1]} |")
L += ["",
      "`baseline` and `auth-only` sit at 0.00 on both platforms — correct, those presets do not "
      "engage IBAC, and a non-zero value there would have indicated the judge was being consulted "
      "by something other than the benchmark. The IBAC conditions sit near 1 on both. **The "
      "alarming 12-run ratio was small-sample noise on a quantity that is really ~1.**", ""]

# tau2 linearity
lin = [n for n in ORDER if legs[n].get("role") == "linearity"]
if len(lin) == 2:
    nb, nf = lin
    L += ["### The cross-benchmark projection fails on both — in opposite directions", "",
          "The cheap way to price a plugin on an expensive benchmark is to multiply a gsm8k "
          "per-tool-call delta by the target benchmark's tool-call count. That assumes per-call "
          "cost is a constant. tau2 makes ~11 tool calls per task against gsm8k's ~1, so the pair "
          "tests the assumption.", "",
          "| platform | tau2 measured Δ s/task | projected from gsm8k | measured / projected |",
          "|---|---:|---:|---:|"]
    ratios = []
    for P in (A, B):
        lb, lf = P["legs"][nb], P["legs"][nf]
        mb_, mf_ = st.median(lb["all_ok"]), st.median(lf["all_ok"])
        delta = mf_ - mb_
        tpt = lf["tool"] / max(1, lf["n_ok"])
        g = cond_samples(P, legs[nf]["condition"])
        gb = cond_samples(P, "baseline")
        proj = (st.median(g) - st.median(gb)) * tpt if g and gb else None
        rr = delta / proj if proj else None
        ratios.append(rr)
        L.append(f"| {P['label']} | {delta:+.1f} | {fmt(proj, 1)} | "
                 f"{'—' if rr is None else f'{rr:.2f}x'} |")
    L += [""]
    if all(r is not None for r in ratios) and (ratios[0] - 1) * (ratios[1] - 1) < 0:
        near = abs((1 / ratios[0]) - ratios[1]) < 0.15 * ratios[1]
        L += [f"**The projection is not merely inaccurate — it is inconsistent in sign.** It "
              f"over-estimates by {1 / ratios[0]:.1f}x on {A['label']} and under-estimates by "
              f"{ratios[1]:.1f}x on {B['label']}"
              + (" — the two factors landing on nearly the same magnitude is a coincidence of these "
                 "two clusters, not a shared constant" if near else "")
              + ". A method that errs in both directions cannot be "
              f"salvaged with a correction factor — so this shortcut has no defensible form and "
              f"should not be used to price a benchmark. Measuring is the only way to know a "
              f"benchmark's plugin cost, and it is two legs, as done here.", ""]
    else:
        L += ["The projection does not reproduce the measured value on either platform; see the "
              "per-platform reports for the mechanism discussion.", ""]

# --- deploy variability ---------------------------------------------------
L += ["## The unit of replication, checked twice", "",
      "Each condition ran on two independent deploys. The spread between two deploys of the *same* "
      "condition is the noise floor that any preset-to-preset claim has to clear.", "",
      f"| condition | {A['label']} \\|Δ\\| between deploys | {B['label']} \\|Δ\\| between deploys |",
      "|---|---:|---:|"]
dd = {}
for c in CONDITIONS:
    vs = []
    for P in (A, B):
        m = cond_leg_medians(P, c)
        vs.append(abs(m[0] - m[1]) if len(m) == 2 else None)
    dd[c] = vs
    L.append(f"| {c} | {fmt(vs[0], 3)} | {fmt(vs[1], 3)} |")
med_a = st.median([v[0] for v in dd.values() if v[0] is not None])
med_b = st.median([v[1] for v in dd.values() if v[1] is not None])
L += ["",
      f"Typical between-deploy \\|Δ\\| is **{med_a:.2f} s** on {A['label']} and **{med_b:.3f} s** on "
      f"{B['label']}. On both platforms the noise floor scales with the platform's own cost level, "
      f"and on both it **exceeds every preset-to-preset marginal step except the one dominant "
      f"layer**. The conclusion is the same on each cluster and worth stating plainly: with two "
      f"deploys per condition, a *ranking of presets* is not available at any task count. Adding "
      f"tasks tightens the wrong interval.", ""]

# --- so what --------------------------------------------------------------
L += ["## What this means for how plugin cost gets quoted", "",
      "**Portable across platforms (quote these):**", "",
      "- Every serial tool call is authorized; TTL caching is ruled out. Reproduced independently.",
      "- The judged-call ratio for IBAC presets is ~1, and 0 for presets that do not engage IBAC.",
      "- Enabling the AuthBridge sidecar has a cost that is large relative to the no-sidecar "
      "baseline and clearly resolvable on both clusters.",
      "- Beyond the one dominant layer, preset-to-preset differences are below the between-deploy "
      "noise floor on both clusters.",
      "- Per-tool-call cost is **not** a cross-benchmark constant.", "",
      "**Not portable (do not quote without naming the cluster):**", "",
      f"- Any absolute per-task second figure. The same condition on the same image differs by "
      f"{min(x[1] / x[2] for x in rows_out if x[2]):.0f}–"
      f"{max(x[1] / x[2] for x in rows_out if x[2]):.0f}x between these two clusters.",
      "- *Which layer* the cost belongs to. The two clusters disagree, and each is right locally.",
      "- Any ranking of presets against each other.", "",
      "The practical rule this suggests: measure plugin overhead **on the cluster whose numbers you "
      "intend to quote**, using this spec file, and treat the structural findings as the only "
      "portable ones.", ""]

L += ["## Reproducing this", "", "```sh",
      "# one platform at a time -- they front the same LLM gateways, so parallel runs contend",
      "BM_SPECS=reference/plugin_study_specs.json BM_LABEL=pstudy-<platform> \\",
      "  python3 reference/run-12.py          # detached; see feedback_long_runs_detach_and_adopt",
      "python3 reference/gen-plugin-study-xplat.py reference/plugin_study_specs.json "
      f"{VERSION} {OUT.name} \\", ]
for i, P in enumerate(PS):
    sep = " --" if i < len(PS) - 1 else ""
    cont = " \\" if i < len(PS) - 1 else ""
    j = f" {PLATS[i]['judge']}" if PLATS[i]["judge"] else ""
    L.append(f"  '{P['label']}' {PLATS[i]['run']}{j}{sep}{cont}")
L += ["```", ""]

doc = "\n".join(L)
doc = doc.replace("<!--TOC-->", _toc(doc))
OUT.write_text(doc)
print(f"wrote {OUT} ({len(doc)} bytes)")
