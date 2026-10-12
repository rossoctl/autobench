#!/usr/bin/env python3
"""Compare benchmark runs across gateway x model combinations: pass rate, tokens and dollars.

    python3 reference/gen-gateway-compare.py /tmp/autobench/kind/... [more dirs] [-o out.md]

Every argument is searched recursively for mirrored runs (a `report.ndjson` with its `run.json`
beside it — what `autobench-cli all` and `run-12.py` mirror). One table row per
(benchmark, gateway, model):

  runs / tasks       distinct run ids, and report rows (one per task)
  pass               evaluated passes / tasks — an errored task counts as not passed
  lost               rows with zero input tokens: tasks that died before their first model call.
                     Kept out of every token and dollar mean, and shown so they are not invisible
  in / out per task  mean tokens over the tasks that made a model call
  $/task, $/pass     at the posted rates in model_prices.json, for that gateway (costlib.price);
                     "unpriced" when the gateway's card has no rate for the model — never guessed
  p50 latency        median total_latency_s per task

The gateway is the `llm_gateway` LABEL the deploy published, never a URL; runs from before
per-deploy gateways carry none and are grouped as "(none)". Prices are posted rates, not an
invoice, and the model is named beside every dollar figure. The IBAC judge's calls and tau2's user
simulator are not in report.ndjson, so neither is in these dollars.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import costlib  # noqa: E402


def load_runs(paths):
    runs = []
    for root in paths:
        for rep in sorted(pathlib.Path(root).expanduser().glob("**/report.ndjson")):
            rj = rep.with_name("run.json")
            meta = json.loads(rj.read_text()) if rj.exists() else {}
            rows = [json.loads(x) for x in rep.read_text().splitlines() if x.strip()]
            runs.append({"dir": rep.parent, "meta": meta, "rows": rows})
    return runs


def group(runs):
    groups: dict[tuple, dict] = {}
    for r in runs:
        llm = r["meta"].get("llm") or {}
        for row in r["rows"]:
            gw = row.get("llm_gateway") or llm.get("gateway") or "(none)"
            key = (row.get("benchmark_name") or r["meta"].get("benchmark"), gw,
                   row.get("model") or llm.get("model") or "unknown")
            g = groups.setdefault(key, {"run_ids": set(), "rows": []})
            g["run_ids"].add(r["meta"].get("run_id") or str(r["dir"]))
            g["rows"].append({**row, "llm_gateway": None if gw == "(none)" else gw})
    return groups


def summarize(key, g):
    bench, gw, model = key
    rows = g["rows"]
    called = [x for x in rows if (x.get("llm_input_tokens") or 0) > 0]
    passes = sum(1 for x in rows if x.get("evaluation_result") is True)
    gateway = None if gw == "(none)" else gw
    rate = costlib.price(model, gateway)
    total = None
    if rate and called:
        total = sum(costlib.cost(model, x["llm_input_tokens"], x.get("llm_output_tokens") or 0,
                                 gateway) for x in called)
    lat = [x["total_latency_s"] for x in rows if isinstance(x.get("total_latency_s"), (int, float))]
    return {
        "bench": bench, "gateway": gw, "model": model, "runs": len(g["run_ids"]),
        "tasks": len(rows), "pass": passes / len(rows) if rows else 0.0,
        "lost": len(rows) - len(called),
        "in": st.mean(x["llm_input_tokens"] for x in called) if called else None,
        "out": st.mean(x.get("llm_output_tokens") or 0 for x in called) if called else None,
        "per_task": total / len(called) if total is not None else None,
        "per_pass": total / passes if total is not None and passes else None,
        "p50": st.median(lat) if lat else None,
    }


def table(summaries):
    def num(v, fmt):
        return "—" if v is None else format(v, fmt)

    def usd(v, priced):
        return "unpriced" if not priced else ("—" if v is None else costlib.money(v))

    o = ["| benchmark | gateway | model | runs | tasks | pass | lost | in/task | out/task "
         "| $/task | $/pass | p50 latency |",
         "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for s in summaries:
        priced = costlib.price(s["model"], None if s["gateway"] == "(none)" else s["gateway"])
        o.append(
            f"| {s['bench']} | `{s['gateway']}` | `{s['model']}` | {s['runs']} | {s['tasks']} "
            f"| {s['pass']:.2f} | {s['lost']} | {num(s['in'], ',.0f')} | {num(s['out'], ',.0f')} "
            f"| {usd(s['per_task'], priced)} | {usd(s['per_pass'], priced)} "
            f"| {num(s['p50'], '.1f')}{' s' if s['p50'] is not None else ''} |")
    return "\n".join(o)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="+", help="directories holding mirrored runs")
    ap.add_argument("-o", "--out", help="write Markdown here instead of stdout")
    a = ap.parse_args()
    runs = load_runs(a.paths)
    if not runs:
        raise SystemExit(f"no mirrored runs (report.ndjson) under: {' '.join(a.paths)}")
    groups = group(runs)
    summaries = [summarize(k, g) for k, g in sorted(groups.items(), key=lambda kv: kv[0])]
    md = "\n".join([
        "# Gateway x model comparison", "",
        f"{len(runs)} run(s), {sum(len(r['rows']) for r in runs)} task row(s). Dollars at the "
        f"posted rates in `reference/model_prices.json` (rate card {costlib.RATE_CARD_DATE}), "
        "per gateway; not an invoice. Excludes the IBAC judge and tau2's user simulator.", "",
        table(summaries), ""])
    if a.out:
        pathlib.Path(a.out).write_text(md)
        print(f"wrote {a.out}", file=sys.stderr)
    else:
        print(md)


if __name__ == "__main__":
    main()
