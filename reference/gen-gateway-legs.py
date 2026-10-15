#!/usr/bin/env python3
"""The same 12-run matrix on two LLM gateways, leg by leg: pass rate, tokens, wall time, dollars.

    uv run --with matplotlib python reference/gen-gateway-legs.py \\
        /tmp/autobench/run12-kind-v136-20261012.json "ETE-Ext" \\
        /tmp/autobench/run12-kind-v136-cb-20261012.json "cb"

Writes, and owns completely:

  docs/img/gateway-tokens.png   tokens per task, by benchmark, one bar pair per benchmark
  docs/img/gateway-wall.png     wall time per leg, one bar pair per leg
  the <!-- gateway-legs --> block in docs/DEVELOPER_GUIDE.md, rewritten in place the way
                                gen_toc.py rewrites <!-- toc -->

Every number comes from the mirrored artifacts each run JSON points at (`run.json` for pass rate,
task counts and wall time; `report.ndjson` for tokens and the model actually called), and every
dollar from reference/model_prices.json via costlib, per gateway: a gateway with its own card is
priced from that card alone. Each chart's one-line message is formatted from the same numbers.

Gateways are named by the label given on the command line — never a URL, a key, or a key hash:
the guide is public. The labels are the ones the deploys published as `llm_gateway`.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import costlib  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
IMG = REPO / "docs" / "img"
DOC = REPO / "docs" / "DEVELOPER_GUIDE.md"
MIRROR = pathlib.Path("/tmp/autobench")

# gen-cost-charts.py's palette, so these figures sit beside the six cost figures as one family.
# Validated as a categorical pair (dataviz validate_palette.js, light): CVD ΔE 20.2, normal 25.4.
INK, NAVY, GRID = "#1F2A37", "#1B3A5C", "#D8DEE5"
SERIES = ("#2E6FB5", "#B55A2E")          # first gateway, second gateway — fixed order, never cycled


def _plt():
    """matplotlib, imported only to draw: the table logic (and its tests) does not need it."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "font.family": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
        "font.size": 9, "axes.edgecolor": GRID, "axes.labelcolor": INK,
        "text.color": INK, "xtick.color": INK, "ytick.color": INK,
        "axes.titlecolor": NAVY, "axes.titlesize": 10.5, "axes.titleweight": "bold",
        "figure.facecolor": "white", "savefig.facecolor": "white",
        "axes.spines.top": False, "axes.spines.right": False,
    })
    return plt
BENCHES = ("gsm8k", "tau2", "appworld")


def model_name(m):
    return costlib.name(m) if m in costlib.PRICES else (m or "—").removeprefix("openai/")


def load(path):
    """One matrix: {leg: {...}}, read from the mirrored artifacts the run JSON names."""
    data = json.loads(pathlib.Path(path).read_text())
    legs = {}
    for r in data["runs"]:
        hits = sorted(MIRROR.glob(f"**/{r['run_id']}/run.json"))
        if not hits:
            raise SystemExit(f"run {r['run_id']} (#{r['n']}) is not mirrored under {MIRROR} — "
                             "run reference/remirror.py first; a missing leg would silently shrink "
                             "every total")
        d = hits[0].parent
        run = json.loads((d / "run.json").read_text())
        rows = [json.loads(x) for x in (d / "report.ndjson").read_text().splitlines() if x.strip()]
        s = run.get("summary") or {}
        gw = (run.get("llm") or {}).get("gateway")
        cost = sum(costlib.cost(x["model"], x["llm_input_tokens"] or 0, x["llm_output_tokens"] or 0,
                                x.get("llm_gateway") or gw) or 0.0 for x in rows)
        unpriced = sum(1 for x in rows if not costlib.price(x["model"], x.get("llm_gateway") or gw))
        legs[r["n"]] = {
            "bench": r["bench"], "run_id": r["run_id"],
            "models": sorted({x["model"] for x in rows if x.get("model")}),
            "pass": s.get("pass_rate"), "attempted": s.get("total"), "rows": len(rows),
            "tin": sum(x["llm_input_tokens"] or 0 for x in rows),
            "tout": sum(x["llm_output_tokens"] or 0 for x in rows),
            "wall": s.get("wall_seconds") or 0.0, "cost": cost, "unpriced": unpriced,
        }
    return legs


def tok(n):
    return f"{n / 1e6:.2f} M" if n >= 1e6 else (f"{n / 1e3:.1f} K" if n >= 1e4 else f"{n:,}")


def usd(x):
    """A dollar figure; an exact zero reads $0.00, as the gateway's own rate is."""
    return "$0.00" if x == 0 else costlib.money(x)


def secs(x):
    return f"{x:,.0f} s"


def per_task(legs, bench):
    rows = [v for v in legs.values() if v["bench"] == bench]
    n = sum(v["rows"] for v in rows)
    return (sum(v["tin"] for v in rows) / n, sum(v["tout"] for v in rows) / n) if n else (0, 0)


def chart_tokens(A, B, la, lb):
    plt = _plt()
    fig, ax = plt.subplots(figsize=(6.4, 2.6))
    y = range(len(BENCHES))
    h = 0.36
    vals = {}
    for k, (legs, lab, col) in enumerate(((A, la, SERIES[0]), (B, lb, SERIES[1]))):
        tot = [sum(per_task(legs, b)) for b in BENCHES]
        vals[lab] = tot
        pos = [i + (k - 0.5) * (h + 0.04) for i in y]
        ax.barh(pos, tot, height=h, color=col, label=lab)
        for p, v in zip(pos, tot):
            ax.text(v * 1.08, p, f"{v:,.0f}", va="center", fontsize=8, color=INK)
    ax.set_xscale("log")
    ax.set_yticks(list(y), BENCHES)
    ax.invert_yaxis()
    ax.set_xlabel("tokens per finished task (input + output), log scale")
    ax.grid(axis="x", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=2, fontsize=8)
    ratios = {b: vals[lb][i] / vals[la][i] for i, b in enumerate(BENCHES)}
    title = f"{lb} spends {ratios['gsm8k']:.1f}× {la}'s tokens on gsm8k, {ratios['tau2']:.2f}× on tau2"
    ax.set_title(title, loc="left")
    fig.savefig(IMG / "gateway-tokens.png", dpi=200, bbox_inches="tight", pad_inches=0.12)
    plt.close(fig)
    return title, ratios


def chart_wall(A, B, la, lb):
    plt = _plt()
    legs = sorted(A)
    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    h = 0.38
    for k, (D, lab, col) in enumerate(((A, la, SERIES[0]), (B, lb, SERIES[1]))):
        pos = [i + (k - 0.5) * (h + 0.04) for i in range(len(legs))]
        ax.barh(pos, [D[n]["wall"] for n in legs], height=h, color=col, label=lab)
    ax.set_xscale("log")
    ax.set_yticks(range(len(legs)), [f"#{n} {A[n]['bench']}" for n in legs])
    ax.invert_yaxis()
    ax.set_xlabel("wall time per leg, seconds, log scale")
    ax.grid(axis="x", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=2, fontsize=8)
    ta, tb = sum(A[n]["wall"] for n in legs), sum(B[n]["wall"] for n in legs)
    # Each leg's own run time (run.json `wall_seconds`): deploys and cache gaps are not in it.
    title = (f"Run time summed over the 12 legs: {tb:,.0f} s on {lb}, {ta:,.0f} s on {la} "
             f"({tb / ta:.2f}×), most of it appworld")
    ax.set_title(title, loc="left")
    fig.savefig(IMG / "gateway-wall.png", dpi=200, bbox_inches="tight", pad_inches=0.12)
    plt.close(fig)
    return title


def block(A, B, la, lb, tok_title, wall_title, ratios):
    legs = sorted(A)
    o = [f"<!-- Regenerate: uv run --with matplotlib python reference/gen-gateway-legs.py "
         f"<run.json> \"{la}\" <run.json> \"{lb}\" -->", ""]
    o.append(f"| leg | benchmark | model on {la} | model on {lb} | pass {la} | pass {lb} "
             f"| tasks finished {la} | tasks finished {lb} |")
    o.append("|---|---|---|---|---:|---:|---:|---:|")
    for n in legs:
        a, b = A[n], B[n]
        o.append(f"| #{n} | {a['bench']} | `{', '.join(map(model_name, a['models']))}` "
                 f"| `{', '.join(map(model_name, b['models']))}` | {a['pass']:.2f} | {b['pass']:.2f} "
                 f"| {a['rows']} / {a['attempted']} | {b['rows']} / {b['attempted']} |")
    o += ["", f"| leg | input {la} | input {lb} | output {la} | output {lb} | total {la} "
              f"| total {lb} | wall {la} | wall {lb} | cost {la} | cost {lb} |",
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for n in legs:
        a, b = A[n], B[n]
        o.append(f"| #{n} {a['bench']} | {tok(a['tin'])} | {tok(b['tin'])} | {tok(a['tout'])} "
                 f"| {tok(b['tout'])} | {tok(a['tin'] + a['tout'])} | {tok(b['tin'] + b['tout'])} "
                 f"| {secs(a['wall'])} | {secs(b['wall'])} | {usd(a['cost'])} "
                 f"| {usd(b['cost'])} |")
    s = lambda D, k: sum(v[k] for v in D.values())  # noqa: E731
    o.append(f"| **all 12 legs** | **{tok(s(A, 'tin'))}** | **{tok(s(B, 'tin'))}** "
             f"| **{tok(s(A, 'tout'))}** | **{tok(s(B, 'tout'))}** "
             f"| **{tok(s(A, 'tin') + s(A, 'tout'))}** | **{tok(s(B, 'tin') + s(B, 'tout'))}** "
             f"| **{secs(s(A, 'wall'))}** | **{secs(s(B, 'wall'))}** "
             f"| **{usd(s(A, 'cost'))}** | **{usd(s(B, 'cost'))}** |")
    lost = {lab: {b: sum(v["attempted"] - v["rows"] for v in D.values() if v["bench"] == b)
                  for b in BENCHES} for D, lab in ((A, la), (B, lb))}
    att = {b: sum(v["attempted"] for v in A.values() if v["bench"] == b) for b in BENCHES}
    unp = s(A, "unpriced") + s(B, "unpriced")
    o += ["", f"![{tok_title}](img/gateway-tokens.png)", "", f"**{tok_title}.**", "",
          f"![{wall_title}](img/gateway-wall.png)", "", f"**{wall_title}.**", "",
          "How to read it:", "",
          f"- **Tokens and wall time cover the tasks that finished.** A task killed by the 600 s "
          f"per-task budget leaves no `report.ndjson` row, so its tokens are missing here and its "
          f"leg's pass rate counts it as failed. appworld lost {lost[la]['appworld']} of "
          f"{att['appworld']} tasks that way on {la} and {lost[lb]['appworld']} of "
          f"{att['appworld']} on {lb}. That is most of why {lb}'s appworld totals are lower: per "
          f"finished task the two are much closer ({sum(per_task(B, 'appworld')):,.0f} tokens on "
          f"{lb} against {sum(per_task(A, 'appworld')):,.0f} on {la}).",
          f"- **The model changes with the gateway on tau2 and appworld**, so those rows compare "
          f"gateway × model combinations, not gateways alone. Only #4 swaps on purpose.",
          f"- **Dollars are posted rates** from `reference/model_prices.json`, per gateway. "
          f"{lb}'s models are self-hosted (`hosted_vllm`) with no per-token price, hence $0.00; "
          f"its own compute is not in that figure."
          + (f" {unp} row(s) had no rate and count as $0." if unp else ""),
          f"- **The IBAC judge stayed on {la}'s `Azure/gpt-4.1`** for legs #6–#8 on both runs; its "
          f"calls are billed but not in the agent's telemetry (§2), so neither cost column includes "
          f"them.",
          f"- **Wall time is that day's.** Both runs were on one KinD node on 2026-10-12 (local "
          f"time; the run ids are UTC), one after the other, so the comparison is fair between them; across days the gateways' latency "
          f"moves (§2).", "",
          f"| leg | run id on {la} | run id on {lb} |", "|---|---|---|"]
    o += [f"| #{n} | `{A[n]['run_id']}` | `{B[n]['run_id']}` |" for n in legs]
    return "\n".join(o) + "\n"


def rewrite(doc, body):
    text = doc.read_text()
    pat = re.compile(r"(<!-- gateway-legs -->\n)(.*?)(<!-- /gateway-legs -->)", re.S)
    if not pat.search(text):
        raise SystemExit(f"{doc.relative_to(REPO)}: no <!-- gateway-legs --> block")
    doc.write_text(pat.sub(lambda m: m.group(1) + body + m.group(3), text, count=1))
    print(f"  {doc.relative_to(REPO)}: gateway-legs block rewritten")


def main():
    if len(sys.argv) != 5:
        raise SystemExit(__doc__.split("\n\n")[1])
    A, B = load(sys.argv[1]), load(sys.argv[3])
    la, lb = sys.argv[2], sys.argv[4]
    if sorted(A) != sorted(B):
        raise SystemExit(f"the two matrices have different legs: {sorted(A)} vs {sorted(B)}")
    IMG.mkdir(parents=True, exist_ok=True)
    tok_title, ratios = chart_tokens(A, B, la, lb)
    wall_title = chart_wall(A, B, la, lb)
    print(f"  docs/img/gateway-tokens.png -- {tok_title}")
    print(f"  docs/img/gateway-wall.png   -- {wall_title}")
    rewrite(DOC, block(A, B, la, lb, tok_title, wall_title, ratios))


if __name__ == "__main__":
    main()
