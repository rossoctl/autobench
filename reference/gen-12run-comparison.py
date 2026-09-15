#!/usr/bin/env python3
"""Compare two 12-run result sets (e.g. OCP vs KinD) from the artifacts run-12.py mirrored.

Usage: gen-12run-comparison.py <A.json> <A-label> <B.json> <B-label> <version> [out.md]

Every number is derived from the mirrored report.ndjson files, nothing transcribed.
"""
import json
import os
import pathlib
import sys
from datetime import datetime, timezone

# Reuse the TOC builder rather than re-deriving GitHub's anchor rules three times over.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gen_toc import build as _toc  # noqa: E402

A, ALAB, B, BLAB, VERSION = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
OUT = pathlib.Path(sys.argv[6]) if len(sys.argv) > 6 else None


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


def f(v, spec="%.0f"):
    return "—" if v is None else spec % v


def lost(x):
    """True when the task's usage-bearing `chat` span was never written (tokens understated).

    A `tokens == 0` test is not sufficient: it only catches reasoning models, whose `max_tokens=1`
    probe fails and records no usage. On claude-sonnet-5 / gemini-2.5-pro the probe *succeeds*, so
    the damaged row carries the probe's own tiny usage (`8/1`, `1/0`) and reads as non-zero. The
    model-independent signal is structural — `llm_count <= 1` with `tool_count >= 2` is impossible,
    because every tool call needs a preceding model turn.

    As of `0.3.5.dev145` the probe is *replaced* (not deleted) by an unbilled `GET /v1/models`
    check, which emits no `chat` span, so a damaged row now drops to zero `chat` spans and `== 0`
    would catch more than it used to. The structural test is kept because it is the one that holds
    on both sides of that version boundary, and this generator compares matrices across it.
    """
    lc = x.get("llm_count") or 0
    tc = x.get("tool_count") or 0
    return (lc > 0 and not x.get("llm_input_tokens")) or (lc <= 1 and tc >= 2)


def load(path):
    d = json.loads(pathlib.Path(path).read_text())
    out = {}
    for r in d["runs"]:
        md = r.get("mirror_dir")
        rows = []
        if md:
            p = pathlib.Path(md) / "report.ndjson"
            if p.exists():
                rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
        man = None
        if md:
            mp = pathlib.Path(md) / "manifest.json"
            if mp.exists():
                man = json.loads(mp.read_text())
        # A health-probe failure is a task the agent's per-task `GET /v1/models` check killed before
        # the model was called. It does leave a report.ndjson row (status=ERROR, llm=0, zero tokens),
        # so it is inside the token medians/means/CVs computed below as a zero-cost outlier — but
        # nothing there says *why*, and it is scored as not-passed, so a leg's pass_rate silently
        # mixes "the agent got it wrong" with "the agent never ran". Counted from run.json because
        # that has one result per task unconditionally, while a task can end with no report.ndjson
        # row at all (appworld timeouts do). See Bug 3 in docs/exgentic-agent-bug-report-*.md.
        probe = 0
        if md:
            rp = pathlib.Path(md) / "run.json"
            if rp.exists():
                probe = sum(1 for x in json.loads(rp.read_text()).get("results", [])
                            if (x.get("error") or "").endswith("/v1/models"))
        # Whether the agent issues a `max_tokens=1` capability probe is an era, not a setting, and it
        # decides whether the `llm` column reads one high per task. Measure it instead of asserting a
        # version: a probe call is a `chat` span with `request_max_tokens == 1`, and this generator is
        # routinely pointed at matrices from either side of the change.
        chats = pchats = 0
        sp = pathlib.Path(md) / "span_report.ndjson" if md else None
        if sp and sp.exists():
            for line in sp.read_text().splitlines():
                if not line.strip():
                    continue
                x = json.loads(line)
                if x.get("kind") == "chat":
                    chats += 1
                    pchats += x.get("request_max_tokens") == 1
        s = r.get("summary") or {}
        iv = [x.get("llm_input_tokens", 0) or 0 for x in rows]
        ov = [x.get("llm_output_tokens", 0) or 0 for x in rows]
        imed, imean, icv = _stats(iv)
        omed, omean, ocv = _stats(ov)
        out[r["n"]] = dict(
            bench=r["bench"], p=s.get("pass_rate"), w=s.get("wall_seconds"), rows=len(rows),
            i=sum(iv), o=sum(ov), imed=imed, imean=imean, icv=icv,
            omed=omed, omean=omean, ocv=ocv,
            par=(r.get("run_request") or {}).get("max_parallel_sessions"),
            keys=([a["key"] for a in man.get("artifacts", [])]
                  + [man["prefix"].rstrip("/") + "/manifest.json"]) if man else [],
            urlroot=next((a["url"][: -len(a["key"])] for a in (man or {}).get("artifacts", [])
                          if a.get("url", "").endswith(a["key"])), ""),
            z=sum(1 for x in rows if lost(x)),
            chats=chats, pchats=pchats,
            probe=probe, total=s.get("total"), passed=s.get("evaluated_pass"),
            # Pass rate over the tasks that actually reached the model. `None` when the probe took
            # every task in the leg (#1 is a single task, so one failure leaves nothing to score) —
            # an unscoreable leg must not be silently reported as 0.0.
            padj=(s["evaluated_pass"] / (s["total"] - probe)
                  if s.get("total") and s.get("evaluated_pass") is not None
                  and s["total"] - probe > 0 else None))
    return out, d.get("base")



def dir_prefix(keys):
    """LCP truncated at a '/' boundary — a usable S3 prefix, not a mid-token fragment."""
    if not keys:
        return ""
    cp = os.path.commonprefix(keys)
    return cp[:cp.rfind("/") + 1] if "/" in cp else cp


def s3_section(sets):
    """sets: [(label, mapping)] -> the 'where the artifacts live' block."""
    o = ["## S3 artifact locations", "",
         "Every number below is derived from artifacts published to S3. Both sides share one bucket "
         "and differ only in the key prefix (the instance's configured `s3.prefix` and encoded "
         "issuer host).", ""]
    roots = sorted({v["urlroot"] for _, m in sets for v in m.values() if v["urlroot"]})
    o += [("| **URL root** | `%s` |" % roots[0]) if len(roots) == 1 else
          ("| **URL roots** | %s |" % ", ".join("`%s`" % r for r in roots))]
    o.insert(-1, "| | |")
    o.insert(-1, "|---|---|")
    for lab, m in sets:
        keys = [k for v in m.values() for k in v["keys"]]
        o.append(f"| **{lab} key prefix** ({len(keys)} objects) | `{dir_prefix(keys)}` |")
    o += ["",
          "Key layout is `<s3.prefix>/<caller>/<iss-host-encoded>/<benchmark>/<run_id>/<artifact>`, "
          "so **benchmark selects cleanly by prefix** but **the model in use does not appear in the "
          "key at all** — gsm8k mixes models across its runs, so no prefix separates them. Resolve "
          "model -> `run_id` from the pass-rate table below when you need per-model objects.", "",
          "| benchmark | " + " | ".join(f"objects {lab}" for lab, _ in sets) + " | sub-prefix |",
          "|---|" + "---:|" * len(sets) + "---|"]
    benches = sorted({v["bench"] for _, m in sets for v in m.values()})
    for b in benches:
        cells, sub = [], ""
        for lab, m in sets:
            ks = [k for v in m.values() if v["bench"] == b for k in v["keys"]]
            cells.append(str(len(ks)))
            root = dir_prefix([k for v in m.values() for k in v["keys"]])
            sub = dir_prefix(ks)[len(root):] or sub
        o.append(f"| {b} | " + " | ".join(cells) + f" | `{sub}` |")
    o += ["",
          "Objects are readable **and listable anonymously**, so treat anything written here as "
          "public. What is exposed: the **keys** carry the caller's username and the Keycloak issuer "
          "host, and `run.json` / `report.ndjson` can carry **exception strings** from failed tasks. "
          "No artifact contains task prompts or model outputs — the record schema is ids, counts and "
          "durations, and `span_report.*` publishes only a fixed whitelist of structural/numeric "
          "span fields."]
    return "\n".join(o)


X, _ = load(A)   # load() still returns the Service base; deliberately discarded, see the header below
Y, _ = load(B)
GENERATED = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

px, py = sum(v["probe"] for v in X.values()), sum(v["probe"] for v in Y.values())
tx, ty = sum(v["total"] or 0 for v in X.values()), sum(v["total"] or 0 for v in Y.values())


def _leg(n):
    """Leg number for a per-task statistics table, flagged when a probe failure contaminates it."""
    return "%d ⚠" % n if X[n]["probe"] or Y.get(n, {}).get("probe") else str(n)


L = [f"# 12-Run Comparison — {ALAB} vs {BLAB}, both Service {VERSION}", "",
     f"**Report generated:** {GENERATED}", "",
     # Platform labels only, never the Service endpoints: these reports are published.
     f"**Platforms compared:** {ALAB} vs {BLAB}", "",
     "Both sides ran the same 12 request bodies, the same Service version, and verified-identical",
     "instance config. Every leg deploys fresh. Task selection is deterministic, so the same",
     "`task_id` is the same task on both platforms, which makes the two sides comparable.",
     "",
     "**That does not make every difference a property of the platform.** A task can also be lost to",
     "the agent's own per-task health probe before it ever reaches the model, and such a task is",
     "scored as not-passed — so a raw `pass_rate` mixes \"the agent got it wrong\" with \"the agent",
     "never ran\". Where that happened it is counted and separated below; read the adjusted column",
     "before attributing anything to the cluster.",
     "",
     "All numbers are derived from the mirrored `report.ndjson` and `run.json` artifacts.", "",
     "<!--TOC-->", "",
     s3_section([(ALAB, X), (BLAB, Y)]), "",
     "## Pass rate, wall time, tokens", "",
     f"| # | bench | p | pass {ALAB} | pass {BLAB} | Δ | wall {ALAB} | wall {BLAB} | {BLAB}/{ALAB} | in {ALAB} | in {BLAB} | in Δ |",
     "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
for n in sorted(X):
    x, y = X[n], Y.get(n, {})
    d = "%+.2f" % (y["p"] - x["p"]) if None not in (x.get("p"), y.get("p")) else "—"
    r = "%.2fx" % (y["w"] / x["w"]) if x.get("w") and y.get("w") else "—"
    di = "same" if x["i"] == y.get("i") else "%+d" % (y.get("i", 0) - x["i"])
    L.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %d | %d | %s |" % (
        _leg(n), x["bench"], x["par"], x["p"], y.get("p"), d,
        f(x["w"]), f(y.get("w")), r, x["i"], y.get("i", 0), di))

if px or py:
    L += ["",
          "**⚠ marks a leg where the agent's health probe took at least one task on one side**, so "
          "the two sides did not run the same set of tasks. Both its `Δ` and its `in Δ` are then "
          "partly bookkeeping, not platform: the lost task contributes no tokens and is scored as "
          "not-passed. The next section quantifies it per leg."]

if px or py:
    L += ["", "## Tasks lost to the agent's health probe", "",
          "As of `exgentic 0.3.5.dev145` every task opens with a `GET /v1/models` reachability check,",
          "capped at a hard 10 s with no retry. A single slow handshake raises",
          "`Model endpoint ... is unreachable` and the task dies **before the model is called**. It is",
          "a latency interaction with the gateway, not a misconfiguration — so it is a property of the",
          "*path to the LLM gateway*, not of the benchmark, the model or the agent's ability. See",
          "Bug 3 in `docs/exgentic-agent-bug-report-20260901.md`.",
          "",
          "It damages two things at once. The task is scored as **not-passed**, so a raw `pass_rate`",
          "cannot distinguish it from a wrong answer. And it leaves a `report.ndjson` row",
          "(`status=ERROR`, `llm=0`, `tool=0`, zero tokens, ~10.5 s), which means it is silently inside",
          "the per-task token medians, means and CVs further down — a zero-cost outlier that pulls the",
          "affected side's token figures **down** and its CV **up**. Where a leg below lost tasks on",
          "one platform only, read its token-distribution row as contaminated rather than as a",
          "platform difference.",
          "",
          f"Totals: **{ALAB} {px} of {tx} tasks** · **{BLAB} {py} of {ty} tasks**"
          + (f" ({100.0 * max(px, py) / max(tx, ty, 1):.0f}% on the worse side)."
             if px != py else "."),
          "",
          "`adj` is `evaluated_pass / (total − probe)` — the pass rate over the tasks that reached the",
          "model. It is the number to compare across platforms; the raw `pass` column above is the",
          "number to quote as the run's result.", "",
          f"| # | bench | tasks | probe {ALAB} | pass {ALAB} | adj {ALAB} | probe {BLAB} | pass {BLAB} | adj {BLAB} |",
          "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for n in sorted(X):
        x, y = X[n], Y.get(n, {})
        if not (x["probe"] or y.get("probe")):
            continue
        L.append("| %d | %s | %s | %d | %s | %s | %d | %s | %s |" % (
            n, x["bench"], x["total"],
            x["probe"], x["p"], f(x["padj"], "%.2f"),
            y.get("probe", 0), y.get("p"), f(y.get("padj"), "%.2f")))
    L += ["",
          "A `—` in an `adj` column means the probe took every task in that leg, leaving nothing to",
          "score; that leg carries no pass-rate signal at all and is excluded from the comparison",
          "below rather than counted as a difference."]

L += ["", "## Token distribution per task", "",
      "For each direction: `median` (robust centre), then `mean`, then `CV` (population sigma / "
      "mean — dimensionless, so spread is comparable across benchmarks whose token counts differ "
      "by orders of magnitude). A mean well above the median means right-skew: a few long tasks "
      "dominate. CV shares its denominator with `mean`, not `median`, and is `—` for single-task "
      "runs.", "",
      "**Which direction varies more is benchmark-dependent, and output usually wins** — measured "
      "at OUT CV > IN CV in 27 of 33 legs across these matrices. On single-turn gsm8k the prompt is "
      "nearly constant (IN CV ~0.06-0.09) while answer length swings with how much the model "
      "reasons (OUT CV 0.4-1.0). Only long-horizon **appworld** inverts it (IN CV 0.30-0.93 above "
      "OUT), because there the tasks differ enormously in turn count and every call re-sends the "
      "whole conversation, so compounding context dominates. tau2 sits between, marginally "
      "output-led."]
# The zero-token row a probe failure leaves behind is inside these statistics, so a leg that lost
# tasks on one side only is not measuring the same set of tasks on both. Flag it in the table rather
# than leave the reader to cross-reference the probe section by leg number.
if px or py:
    L += ["", "**⚠ marks a leg where the health probe took at least one task on one side.** Its "
              "zero-token `ERROR` row is included in these figures, so that side's median and mean "
              "read low and its CV reads high for a reason that has nothing to do with the "
              "platform. Compare those rows only after subtracting that."]
L += ["",
      "### Input tokens", "",
      f"| # | bench | median {ALAB} | mean {ALAB} | CV {ALAB} | median {BLAB} | mean {BLAB} | CV {BLAB} |",
      "|---|---|---:|---:|---:|---:|---:|---:|"]
for n in sorted(X):
    x, y = X[n], Y.get(n, {})
    L.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
        _leg(n), x["bench"],
        f(x["imed"]), f(x["imean"]), f(x["icv"], "%.2f"),
        f(y.get("imed")), f(y.get("imean")), f(y.get("icv"), "%.2f")))

L += ["", "### Output tokens", "",
      f"| # | bench | median {ALAB} | mean {ALAB} | CV {ALAB} | median {BLAB} | mean {BLAB} | CV {BLAB} |",
      "|---|---|---:|---:|---:|---:|---:|---:|"]
for n in sorted(X):
    x, y = X[n], Y.get(n, {})
    L.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
        _leg(n), x["bench"],
        f(x["omed"]), f(x["omean"]), f(x["ocv"], "%.2f"),
        f(y.get("omed")), f(y.get("omean")), f(y.get("ocv"), "%.2f")))

ti, tk = sum(v["i"] for v in X.values()), sum(v["i"] for v in Y.values())
to, ko = sum(v["o"] for v in X.values()), sum(v["o"] for v in Y.values())
wo, wk = sum(v["w"] or 0 for v in X.values()), sum(v["w"] or 0 for v in Y.values())
same = [n for n in X if Y.get(n, {}).get("p") == X[n]["p"]]
diff = [n for n in X if Y.get(n, {}).get("p") != X[n]["p"]]
ident = [n for n in X if Y.get(n, {}).get("i") == X[n]["i"]]
# Compared on the probe-adjusted rate, and only where both sides have one: a leg the probe emptied
# has no rate to agree or disagree with, so it belongs in neither list.
cmpb = [n for n in X if X[n]["padj"] is not None and Y.get(n, {}).get("padj") is not None]
asame = [n for n in cmpb if abs(Y[n]["padj"] - X[n]["padj"]) < 1e-9]
adiff = [n for n in cmpb if n not in asame]
unscored = [n for n in X if X[n]["padj"] is None or Y.get(n, {}).get("padj") is None]
zx, zy = sum(v["z"] for v in X.values()), sum(v["z"] for v in Y.values())
rx, ry = sum(v["rows"] for v in X.values()), sum(v["rows"] for v in Y.values())

L += ["", "## Totals", "",
      f"- input tokens: {ALAB} {ti:,} · {BLAB} {tk:,}",
      f"- output tokens: {ALAB} {to:,} · {BLAB} {ko:,}",
      f"- wall: {ALAB} {wo:.0f}s · {BLAB} {wk:.0f}s",
      f"- rows with lost token attribution: {ALAB} {zx}/{rx} · {BLAB} {zy}/{ry}"
      + ("  — **token capture complete on both sides**" if not zx and not zy else
         "  — **those runs' token totals are understated; pass rates are unaffected**"),
      f"- tasks lost to the health probe: {ALAB} {px}/{tx} · {BLAB} {py}/{ty}"
      + ("  — **every task reached the model on both sides**" if not px and not py else
         "  — **those runs' raw pass rates are depressed by tasks that never ran**"),
      "", "## What matches", "",
      f"- **{len(same)} of {len(X)} {'raw ' if px or py else ''}pass rates identical**: "
      f"{', '.join('#%d' % n for n in same)}."]
# Only worth stating separately when the probe actually took something; with no losses the adjusted
# rate is the raw rate by construction and the bullet would just restate the previous one.
if px or py:
    L += [f"- **{len(asame)} of {len(cmpb)} comparable pass rates identical once health-probe losses "
          f"are excluded**: {', '.join('#%d' % n for n in asame)}."
          + ((" %s had no scoreable task left on one side and %s not compared."
              % (", ".join("#%d" % n for n in unscored),
                 "is" if len(unscored) == 1 else "are")) if unscored else "")]
L += [f"- **{len(ident)} runs have byte-identical input-token totals**: "
      f"{', '.join('#%d' % n for n in ident)} — deterministic task selection plus the same model",
      "  means identical work, the strongest available check that this is a like-for-like comparison",
      "  rather than merely a similar one."]
# A probe-lost task takes its whole token contribution with it, so any leg that lost one cannot be
# byte-identical no matter how well the platforms agree. Report the identical count over the legs
# where the test is actually able to pass, or the headline understates like-for-likeness badly.
clean = [n for n in X if not X[n]["probe"] and not Y.get(n, {}).get("probe")]
cident = [n for n in clean if n in ident]
if len(clean) < len(X):
    L += [f"- Only **{len(clean)} of {len(X)}** legs lost nothing to the probe on either side "
          f"({', '.join('#%d' % n for n in clean)}), and a leg that lost a task cannot be "
          "byte-identical — the missing task takes its tokens with it. Of those "
          f"{len(clean)}, **{len(cident)} match exactly**"
          + (f": {', '.join('#%d' % n for n in cident)}." if cident else ".")
          + ("" if len(cident) == len(clean) else
             " The remainder are %s, nondeterministic per episode and so never expected to match"
             " byte-for-byte." % " and ".join(sorted({X[n]["bench"] for n in clean
                                                      if n not in cident}))
             if all(X[n]["bench"] != "gsm8k" for n in clean if n not in cident)
             else " The remainder include gsm8k, which is deterministic — a mismatch there is worth"
                  " investigating rather than attributing to episode noise.")]
L += ["", "## Where they differ", ""]
_raw = (f"- On the **raw** rate {len(diff)} differ: " if px or py else
        f"- **{len(diff)} of {len(X)} legs differ**: ") + (
    ", ".join("#%d (%+.2f)" % (n, Y[n]["p"] - X[n]["p"])
              for n in diff if Y.get(n, {}).get("p") is not None) or "none")
if px or py:
    L += [(f"- **{len(adiff)} of {len(cmpb)} comparable legs differ on the adjusted rate**: "
           + ", ".join("#%d (%+.2f)" % (n, Y[n]["padj"] - X[n]["padj"]) for n in adiff) + ".")
          if adiff else "- **No comparable leg differs on the adjusted rate.**",
          _raw + ("  — the gap between the two lists is the health probe, not the platform."
                  if len(diff) > len(adiff) else ".")]
else:
    L += [_raw + "."]
L += ["- Interpret small deltas on small runs with care: one task on a 5-task run moves pass_rate by"
      + (" 0.20, and excluding probe losses shrinks the denominator further — a leg reported as"
         " `2/3` moves by 0.33 per task." if px or py else " 0.20.")
      + " tau2 and appworld are additionally nondeterministic per episode.",
      "", "## Caveats on the `llm` column and on token totals", ""]

# Which era each side ran in is measured, not assumed: a capability probe is a `chat` span carrying
# `request_max_tokens == 1`, so the published spans say whether the `llm` column reads one high.
cx, cy = sum(v["chats"] for v in X.values()), sum(v["chats"] for v in Y.values())
qx, qy = sum(v["pchats"] for v in X.values()), sum(v["pchats"] for v in Y.values())
if qx and qy:
    L += [f"**Both sides' `llm` counts read one high per task.** {ALAB} has {qx} of {cx} `chat` "
          f"spans carrying `max_tokens=1` and {BLAB} has {qy} of {cy}: the pre-`0.3.5.dev145` "
          "capability probe, one per task, counted as an LLM call. Subtract one per task to get real "
          "calls. The two sides are still comparable to each other, since both are inflated the same "
          "way."]
elif qx or qy:
    lab, olab = (ALAB, BLAB) if qx else (BLAB, ALAB)
    q, c = (qx, cx) if qx else (qy, cy)
    L += [f"**⚠ The two sides straddle the agent change that retired the capability probe, so their "
          f"`llm` columns are not comparable.** {lab} has {q} of {c} `chat` spans carrying "
          f"`max_tokens=1` — the pre-`0.3.5.dev145` probe, one per task, counted as an LLM call — "
          f"while {olab} has none. {lab} therefore reads one call high per task purely from "
          f"instrumentation. Token totals are affected only trivially (the probe's own usage is 0–8 "
          "tokens), but a per-task `llm` comparison between these two matrices is meaningless."]
else:
    L += ["**The `llm` column counts real LLM calls one-for-one on both sides, with no probe offset "
          "to subtract** — measured: no `chat` span in either matrix carries `max_tokens=1` "
          f"({cx} and {cy} spans checked). Agents up to `0.3.5.dev131` issued a `max_tokens=1` "
          "capability probe that was recorded as a `chat` span, so counts from *those* runs read one "
          "high per task; as of `0.3.5.dev145` it is *replaced* by the unbilled `GET /v1/models` "
          "check, which emits no span. Do not compare `llm` counts across that boundary: a leg looks "
          "like it made one fewer call per task when only the instrumentation changed."]

L += ["",
      "The damage detector used above is unchanged and stays structural — `llm <= 1` alongside",
      "`tool >= 2` is impossible, because every tool call needs a preceding model turn. A bare",
      "\"one `chat` span\" test is now actively wrong: one `chat` span is the *healthy* shape for a",
      "one-shot gsm8k task. `tokens == 0` was never sufficient either — while the probe existed it",
      "was the span that *survived* the loss, carrying its own usage on non-reasoning models",
      "(`claude-sonnet-5` `in=8/out=1`, `gemini-2.5-pro` `in=1/out=0`), so a zero-check missed every",
      "tau2 and appworld case. Any leg counted above has **understated** token totals — its pass rate",
      "remains valid. See `docs/exgentic-agent-bug-report-20260901.md`.",
      "",
      "One more, and it bears on the *output* token and latency columns rather than the `llm` one:",
      "**each LLM gateway caches completions**, keyed on the request body, so a leg that repeats an",
      "earlier leg's task on the same model can be handed back the stored response — same response",
      "`id`, same `usage`. Task selection is deterministic, so within one side the legs sharing a",
      "benchmark do repeat tasks; each side's own report names them. That makes per-call latency and",
      "output tokens non-independent **within** a side. It does not undermine the comparison, because",
      "the two clusters front *different* gateways with independent caches: a figure the two sides",
      "agree on is agreement between two independently cached (or uncached) measurements, not one",
      "measurement counted twice. Latency is not a hit detector either — a measured replay took 3.0 s,",
      "the same as a miss."]

doc = "\n".join(L) + "\n"
doc = doc.replace("<!--TOC-->", _toc(doc))
if OUT:
    OUT.write_text(doc)
    print(f"wrote {OUT} ({len(doc)} bytes)")
else:
    print(doc)
