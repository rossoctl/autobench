#!/usr/bin/env python3
"""Cost of a benchmark run at list price, computed from mirrored artifacts.

    python3 reference/gen-cost-analysis.py <run12-A.json> [<run12-B.json>] [--mirror DIR]

Reads each leg's `report.ndjson` out of the local mirror, multiplies its per-task token
counts by `reference/model_prices.json`, and prints the tables the prose docs quote:

  1. the rate card, with the out/in price ratio that decides which model is cheap
  2. per-benchmark cost of one task, per model
  3. per-leg cost of the runs in the matrix
  4. what the same task costs on a different model -- the only fair model comparison,
     because the task set is identical
  5. the two consumers that are billed but NOT in our telemetry (IBAC judge, tau2 user
     simulator), sized so a reader knows which way the totals are wrong

Every number is derived. Nothing here is hand-maintained but the price file, and a leg
whose mirror is missing is reported as missing rather than silently skipped -- `/tmp` gets
pruned, and a silent skip is how a report ends up quoting half a matrix. Run
`reference/remirror.py <run12-*.json>` first if legs come back missing.
"""
import json
import pathlib
import statistics as st
import sys

HERE = pathlib.Path(__file__).resolve().parent
PRICES = json.loads((HERE / "model_prices.json").read_text())["models"]

# The judge's system prompt is a fixed 1,577 chars / 231 words, read off the rendered
# per-workload ConfigMap (team1/authbridge-config-<workload>, ibac plugin config). The
# ~4 chars/token rule is a floor: the proposed-action block that follows it is not fixed.
JUDGE_PROMPT_CHARS = 1577
JUDGE_MODEL = "openai/Azure/gpt-4.1"


def money(x):
    """Dollars at a precision that does not round a real cost to zero."""
    if x == 0:
        return "$0"
    if x < 0.0001:
        return f"${x:.6f}"
    if x < 0.01:
        return f"${x:.5f}"
    if x < 1:
        return f"${x:.4f}"
    return f"${x:,.2f}"


def cost(model, tin, tout):
    p = PRICES.get(model)
    if not p:
        return None
    return (tin * p["input_per_1m"] + tout * p["output_per_1m"]) / 1e6


def load(spec_path, mirror):
    spec = json.loads(pathlib.Path(spec_path).read_text())
    legs = []
    for r in spec["runs"]:
        rid = r.get("run_id")
        hits = sorted(mirror.glob(f"**/{rid}/report.ndjson")) if rid else []
        if not hits:
            legs.append({"n": r["n"], "bench": r["bench"], "title": r["title"],
                         "missing": True, "rows": []})
            continue
        rows = [json.loads(x) for x in hits[0].read_text().splitlines() if x.strip()]
        legs.append({"n": r["n"], "bench": r["bench"], "title": r["title"],
                     "missing": False, "rows": rows,
                     "req": r.get("run_request") or {}})
    return {"label": spec["label"], "legs": legs}


def priced(rows):
    """Rows that can be priced: a real model and a real input-token count.

    A row with `llm_input_tokens == 0` is a task that died before its first model call --
    it carries `model: unknown` and no cost. Counting it would drag every per-task mean
    toward zero, so it is excluded here and reported separately.
    """
    return [x for x in rows if (x.get("llm_input_tokens") or 0) and x.get("model") in PRICES]


def section(title, char="="):
    return ["", char * 78, title, char * 78, ""]


def rate_card():
    L = section("1. Rate card (USD per 1M tokens)")
    L += [f"{'model (as report.ndjson records it)':44} {'in':>7} {'out':>7} {'out/in':>7}"]
    L += ["-" * 68]
    for k, p in sorted(PRICES.items(), key=lambda kv: kv[1]["input_per_1m"]):
        ratio = p["output_per_1m"] / p["input_per_1m"]
        L += [f"{k:44} {p['input_per_1m']:>7.2f} {p['output_per_1m']:>7.2f} {ratio:>6.1f}x"]
    L += ["", "The out/in ratio is what makes a token-cheap model expensive: output is priced",
          "4-8x input everywhere, so a model that reasons at length pays for it twice over."]
    return L


def per_task(platforms):
    L = section("2. One task, by benchmark and model (per-task mean over all legs)")
    agg = {}
    for P in platforms:
        for leg in P["legs"]:
            for x in priced(leg["rows"]):
                key = (leg["bench"], x["model"])
                a = agg.setdefault(key, {"in": [], "out": [], "c": [], "legs": set()})
                a["in"].append(x["llm_input_tokens"])
                a["out"].append(x.get("llm_output_tokens") or 0)
                a["c"].append(cost(x["model"], x["llm_input_tokens"], x.get("llm_output_tokens") or 0))
                a["legs"].add((P["label"], leg["n"]))
    order = {"gsm8k": 0, "tau2": 1, "appworld": 2}
    L += [f"{'bench':9} {'model':30} {'n':>4} {'in tok':>9} {'out tok':>8} "
          f"{'$/task':>10} {'$/100 tasks':>12}"]
    L += ["-" * 88]
    rank = sorted(agg.items(), key=lambda kv: (order.get(kv[0][0], 9), kv[0][1]))
    for (bench, model), a in rank:
        mc = st.mean(a["c"])
        L += [f"{bench:9} {PRICES[model]['public_name']:30} {len(a['c']):>4} "
              f"{st.mean(a['in']):>9,.0f} {st.mean(a['out']):>8,.0f} "
              f"{money(mc):>10} {money(mc * 100):>12}"]
    cheapest, cheap_cost = min(((k, st.mean(v["c"])) for k, v in agg.items()),
                              key=lambda kv: kv[1])
    L += ["", f"Multiples of the cheapest row above "
              f"({cheapest[0]}/{PRICES[cheapest[1]]['public_name']}):"]
    for (bench, model), a in rank:
        L += [f"  {bench:9} {PRICES[model]['public_name']:30} "
              f"{st.mean(a['c']) / cheap_cost:>8,.1f}x"]
    return L, agg, cheap_cost, cheapest


def per_leg(platforms):
    L = section("3. One leg, as run (the matrix's own legs)")
    for P in platforms:
        L += [f"--- {P['label']}", ""]
        L += [f"{'#':>3} {'bench':9} {'tasks':>5} {'in tok':>10} {'out tok':>9} "
              f"{'$ leg':>10}  note"]
        L += ["-" * 78]
        tot = 0.0
        for leg in P["legs"]:
            if leg["missing"]:
                L += [f"{leg['n']:>3} {leg['bench']:9} {'-':>5} {'-':>10} {'-':>9} "
                      f"{'-':>10}  MIRROR MISSING -- run reference/remirror.py"]
                continue
            pr = priced(leg["rows"])
            zin = sum(1 for x in leg["rows"] if not (x.get("llm_input_tokens") or 0))
            ti = sum(x["llm_input_tokens"] for x in pr)
            to = sum(x.get("llm_output_tokens") or 0 for x in pr)
            c = sum(cost(x["model"], x["llm_input_tokens"], x.get("llm_output_tokens") or 0) for x in pr)
            tot += c
            note = f"{zin} zero-token row(s) excluded" if zin else ""
            L += [f"{leg['n']:>3} {leg['bench']:9} {len(pr):>5} {ti:>10,} {to:>9,} "
                  f"{money(c):>10}  {note}"]
        L += ["-" * 78, f"{'':3} {'ALL LEGS':9} {'':5} {'':10} {'':9} {money(tot):>10}", ""]
    return L


def model_compare(platforms):
    """Same benchmark, same task ids, different model -- the only honest comparison."""
    L = section("4. Same tasks, different model")
    by = {}
    for P in platforms:
        for leg in P["legs"]:
            pr = priced(leg["rows"])
            if not pr:
                continue
            models = {x["model"] for x in pr}
            if len(models) != 1:
                continue
            ids = frozenset(x["task_id"] for x in pr)
            by.setdefault((leg["bench"], ids), []).append(
                (P["label"], leg["n"], models.pop(), pr))
    found = False
    for (bench, ids), entries in sorted(by.items(), key=lambda kv: -len(kv[1])):
        if len({e[2] for e in entries}) < 2:
            continue
        found = True
        L += [f"{bench}, the same {len(ids)} task ids:", ""]
        L += [f"  {'model':30} {'platform/leg':22} {'in':>9} {'out':>8} {'$/task':>10}"]
        L += ["  " + "-" * 82]
        rows = []
        for label, n, model, pr in sorted(entries, key=lambda e: e[2]):
            ci = st.mean([x["llm_input_tokens"] for x in pr])
            co = st.mean([x.get("llm_output_tokens") or 0 for x in pr])
            pc = st.mean([cost(model, x["llm_input_tokens"], x.get("llm_output_tokens") or 0)
                          for x in pr])
            rows.append((model, pc))
            L += [f"  {PRICES[model]['public_name']:30} {label + ' #' + str(n):22} "
                  f"{ci:>9,.0f} {co:>8,.0f} {money(pc):>10}"]
        cheap = min(rows, key=lambda r: r[1])
        dear = max(rows, key=lambda r: r[1])
        if cheap[0] != dear[0]:
            L += ["", f"  {PRICES[dear[0]]['public_name']} costs {dear[1] / cheap[1]:,.1f}x "
                      f"{PRICES[cheap[0]]['public_name']} on identical work.", ""]
    if not found:
        L += ["No two legs shared a task set across models.", ""]
    return L


def unbilled(platforms, agg, cheap_cost, cheapest):
    L = section("5. Billed, but not in our telemetry")
    jp = PRICES[JUDGE_MODEL]
    jin = JUDGE_PROMPT_CHARS / 4
    per_call = (jin * jp["input_per_1m"] + 40 * jp["output_per_1m"]) / 1e6
    L += ["The IBAC judge (plugin legs #6-#8) makes ~1 completion per authorized tool call,",
          f"on {jp['public_name']}. Its calls never reach report.ndjson: the sidecar makes them,",
          "not the instrumented agent. Floor per judge call, from the fixed system prompt",
          f"({JUDGE_PROMPT_CHARS} chars ~ {jin:.0f} tokens in) and a one-line JSON verdict (~40 out):",
          f"    {money(per_call)} per call -- input-dominated, and a FLOOR: the proposed-action",
          "    block that follows the system prompt is not fixed and is not counted here.", ""]
    # The judge runs a dearer model than the agent it guards, on a fixed-size prompt. On the
    # cheapest benchmark that inverts the bill: the security check outcosts the work.
    L += [f"That is {per_call / cheap_cost:,.1f}x the cost of the whole "
          f"{cheapest[0]}/{PRICES[cheapest[1]]['public_name']} task it authorizes "
          f"({money(cheap_cost)}) --",
          f"the judge runs a {jp['input_per_1m'] / PRICES[cheapest[1]]['input_per_1m']:,.0f}x "
          f"dearer model on a prompt that does not shrink with the task. On the plugin legs,",
          "IBAC is not overhead on the bill; it IS the bill.", ""]
    for P in platforms:
        for leg in P["legs"]:
            if leg["missing"] or leg["n"] not in (6, 7, 8):
                continue
            calls = sum(x.get("tool_count") or 0 for x in leg["rows"])
            pr = priced(leg["rows"])
            legc = sum(cost(x["model"], x["llm_input_tokens"], x.get("llm_output_tokens") or 0)
                       for x in pr)
            jc = calls * per_call
            L += [f"  {P['label']:14} #{leg['n']} {leg['bench']:9} "
                  f"{calls:>3} tool calls -> >= {money(jc):>9} of judge, on top of "
                  f"{money(legc):>9} of agent ({jc / legc:,.1f}x)"]
    L += ["", "The tau2 user simulator runs the SAME model as the agent under test",
          "(EXGENTIC_SET_BENCHMARK_USER_SIMULATOR_MODEL, registry.py) but inside the MCP pod,",
          "which carries no OTel instrumentation. Its share is unmeasured, and on a",
          "multi-turn benchmark it is not small: it generates one user turn per agent turn.",
          "So every tau2 figure in section 2 and 3 is a LOWER bound on what the leg billed."]
    return L


def main(argv):
    mirror = pathlib.Path("/tmp/autobench")
    specs = []
    i = 0
    while i < len(argv):
        if argv[i] == "--mirror":
            mirror = pathlib.Path(argv[i + 1])
            i += 2
            continue
        specs.append(argv[i])
        i += 1
    if not specs:
        sys.exit(__doc__)
    platforms = [load(s, mirror) for s in specs]

    L = [f"AutoBench -- cost at list price",
         f"rate card {json.loads((HERE / 'model_prices.json').read_text())['_rate_card_date']}, "
         f"mirror {mirror}",
         f"platforms: {', '.join(P['label'] for P in platforms)}"]
    L += rate_card()
    pt, agg, cheap_cost, cheapest = per_task(platforms)
    L += pt
    L += per_leg(platforms)
    L += model_compare(platforms)
    L += unbilled(platforms, agg, cheap_cost, cheapest)
    print("\n".join(L))


if __name__ == "__main__":
    main(sys.argv[1:])
