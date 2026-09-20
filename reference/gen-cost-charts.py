#!/usr/bin/env python3
"""The efficiency figures: six charts, one take-away each, all derived from the artifacts.

    uv run --with matplotlib python reference/gen-cost-charts.py \\
        results/v1.28-dev146/run12-ocp-dev146.json \\
        results/v1.28-dev146/run12-kind-dev146.json

Writes, and owns completely:

  docs/img/*.png              the six figures
  docs/img/takeaways.json     {key: {title, takeaway, caption, note}} -- read by
                              docs/generate_pptx.py so a slide caption cannot drift from
                              the figure it sits under
  the <!-- charts --> block in docs/DEVELOPER_GUIDE.md and docs/BENCHMARKS_PRIMER.md,
                              rewritten in place the way gen_toc.py rewrites <!-- toc -->

Each take-away sentence is FORMATTED FROM THE DATA, never typed: that is the whole point of
routing the prose through here rather than writing it under a chart by hand. If a figure and
its sentence ever disagree, this file is broken; the docs are not editable copies.

Requires matplotlib (the only generator that does -- hence `uv run --with`). Everything else
comes from costlib, shared with gen-cost-analysis.py so the tables and the figures price a
run identically.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt          # noqa: E402
from matplotlib.ticker import FuncFormatter, LogLocator  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import costlib  # noqa: E402
from costlib import money, name, short  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
IMG = REPO / "docs" / "img"

# Deck palette (docs/generate_pptx.py), so a figure dropped on a slide does not look imported.
INK, NAVY, BLUE, TEAL, RUST, PURPLE, GRAY = (
    "#1F2A37", "#1B3A5C", "#2E6FB5", "#3D6E70", "#B55A2E", "#8E44AD", "#6B6B6B")
ACCENT, GRID, LTGRAY = "#E87A1E", "#D8DEE5", "#EEEFF1"
BENCH_COLOR = {"gsm8k": TEAL, "tau2": BLUE, "appworld": PURPLE}

plt.rcParams.update({
    "font.family": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
    "font.size": 9, "axes.edgecolor": GRID, "axes.labelcolor": INK,
    "text.color": INK, "xtick.color": INK, "ytick.color": INK,
    "axes.titlecolor": NAVY, "axes.titlesize": 10.5, "axes.titleweight": "bold",
    "figure.facecolor": "white", "savefig.facecolor": "white",
    "axes.spines.top": False, "axes.spines.right": False,
})

TAKEAWAYS: dict[str, dict] = {}


def emit(key, fig, title, takeaway, caption, note=""):
    """Save one figure and register the sentence that must travel with it."""
    IMG.mkdir(parents=True, exist_ok=True)
    path = IMG / f"{key}.png"
    fig.savefig(path, dpi=200, bbox_inches="tight", pad_inches=0.12)
    plt.close(fig)
    TAKEAWAYS[key] = {"title": typo(title), "takeaway": typo(takeaway),
                      "caption": typo(caption), "note": typo(note)}
    print(f"  docs/img/{key}.png  -- {takeaway}")


PLATFORM_NAME = {"ocp": "OpenShift", "kind": "KinD"}


def platform(label):
    """`ocp-dev146` -> `OpenShift`. A platform label, never an endpoint: the bucket is public."""
    return PLATFORM_NAME.get(label.split("-")[0], label)


def legend_below(ax, ncol):
    """Under the x-label. Inside the axes it lands on a bar in every one of these figures."""
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.19), ncol=ncol, frameon=False,
              fontsize=8.5, handlelength=1.4, columnspacing=1.6)


def typo(s):
    """The docs' typography, applied once in emit() so every consumer of a sentence gets the same
    one. The deck reads these strings too, and `4-8x ... -- so` looked like a draft on a slide."""
    s = re.sub(r"(?<=[\d)])x\b", "×", s).replace(" -- ", " — ").replace(" x ", " × ")
    return re.sub(r"(?<=[\d×])-(?=\d)", "–", s)  # ranges take an en dash


def dollars(v, _=None):
    return money(v)


def plural(n, word):
    """The counts here are data-dependent, so a hardcoded `leg(s)` would read as a stub."""
    return word if n == 1 else word + "s"


def barlabel(ax, bars, labels, pad=3, **kw):
    for b, t in zip(bars, labels):
        ax.annotate(t, (b.get_width(), b.get_y() + b.get_height() / 2),
                    xytext=(pad, 0), textcoords="offset points",
                    va="center", ha="left", fontsize=8.2, **kw)


# --------------------------------------------------------------------------- 1. the ladder
def chart_ladder(platforms):
    """Normalised to gsm8k = 1, so the question is which metric climbs FASTEST."""
    b = costlib.agg_bench(platforms)
    base = b["gsm8k"]
    rungs = [k for k in ("tau2", "appworld") if k in b]
    metrics = [("tokens", "mean_tok", GRAY), ("wall-clock", "median_lat", BLUE),
               ("dollars", "mean_cost", ACCENT)]

    fig, ax = plt.subplots(figsize=(6.4, 3.0))
    h, gap = 0.24, 0.30
    ratios = {}
    for i, (lab, field, col) in enumerate(metrics):
        ys, ws = [], []
        for j, r in enumerate(rungs):
            ys.append(j + (i - 1) * (h + 0.02))
            ws.append(b[r][field] / base[field])
            ratios[(r, lab)] = ws[-1]
        bars = ax.barh(ys, ws, height=h, color=col, label=lab, zorder=3)
        barlabel(ax, bars, [f"{w:,.0f}×" for w in ws])

    ax.set_xscale("log")
    ax.set_xlim(1, max(ratios.values()) * 3.2)
    ax.xaxis.set_major_locator(LogLocator(base=10))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}×"))
    ax.set_yticks(range(len(rungs)))
    ax.set_yticklabels([f"{r}\n{short(b[r]['dom_model'])}" for r in rungs], fontweight="bold")
    ax.invert_yaxis()
    ax.set_xlabel(f"multiple of one gsm8k task  ({short(base['dom_model'])}, "
                  f"{base['mean_tok']:,.0f} tokens, {money(base['mean_cost'])}, "
                  f"{base['median_lat']:.1f} s)")
    ax.set_title("One task, relative to a gsm8k task")
    legend_below(ax, 3)
    ax.grid(axis="x", color=GRID, lw=0.6, zorder=0)
    ax.set_axisbelow(True)

    amp = [ratios[(r, "dollars")] / ratios[(r, "tokens")] for r in rungs]
    emit("ladder-amplification", fig,
         "Money climbs the ladder faster than tokens",
         "Money climbs the difficulty ladder faster than tokens do: "
         + ", ".join(f"{r} is {ratios[(r, 'tokens')]:,.0f}x a gsm8k task in tokens but "
                     f"{ratios[(r, 'dollars')]:,.0f}x in dollars" for r in rungs)
         + f" -- so a budget scaled off the token ratios is short by "
           f"{min(amp):,.1f}-{max(amp):,.1f}x.",
         "Each rung is a different model, which is why the dollar bar outruns the token bar: "
         "climbing the ladder also buys a dearer model. Wall-clock climbs SLOWEST of the three "
         "(" + ", ".join(f"{ratios[(r, 'wall-clock')]:,.0f}x {r}" for r in rungs) + "), because "
         "the harder benchmarks parallelize their turns while their bills add up.",
         note=f"gsm8k baseline: {base['mean_tok']:,.0f} tokens, {money(base['mean_cost'])}, "
              f"{base['median_lat']:.1f} s median, over {base['n']} task rows.")


# ------------------------------------------------------- 2. tokens vs money, input vs output
def chart_composition(platforms):
    agg = costlib.agg_bench_model(platforms)
    keys = list(agg)
    fig, ax = plt.subplots(figsize=(6.4, 3.1))
    h = 0.34
    for i, k in enumerate(keys):
        a = agg[k]
        for j, (share, lab) in enumerate(((a["in_share_tok"], "tokens"),
                                          (a["in_share_cost"], "cost"))):
            y = i + (0.5 - j) * (h + 0.04)
            ax.barh([y], [share * 100], height=h, color=GRAY if j == 0 else ACCENT, zorder=3)
            ax.barh([y], [100 - share * 100], left=[share * 100], height=h,
                    color=LTGRAY if j == 0 else "#FBDFC2", zorder=3)
            ax.annotate(f"{lab}: {share * 100:.0f}% in", (1.5, y), va="center", ha="left",
                        fontsize=8.0, color=INK if j == 0 else "#7A3E05", fontweight="bold")
    ax.set_yticks(range(len(keys)))
    ax.set_yticklabels([f"{b}\n{short(m)}" for b, m in keys], fontweight="bold")
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.set_xlabel("input's share (%) \u2014 grey bar: of TOKENS   orange bar: of the BILL")
    ax.set_title("Input's share of the tokens is not its share of the bill")
    ax.grid(axis="x", color=GRID, lw=0.6, zorder=0)
    ax.set_axisbelow(True)

    gaps = {k: agg[k]["in_share_tok"] - agg[k]["in_share_cost"] for k in keys}
    worst = max(gaps, key=lambda k: gaps[k])
    ratios = sorted({p["output_per_1m"] / p["input_per_1m"] for p in costlib.PRICES.values()})
    emit("cost-composition", fig,
         "Read the share of the BILL, not the share of tokens",
         f"Output is priced {ratios[0]:,.0f}-{ratios[-1]:,.0f}x input on every model we use, so "
         f"the token split misstates the bill every time: {worst[0]} on {short(worst[1])} is "
         f"{agg[worst]['in_share_tok'] * 100:.0f}% input by tokens and only "
         f"{agg[worst]['in_share_cost'] * 100:.0f}% by cost.",
         "The gap is the reason a model can win on token count and lose on the invoice. It also "
         "says where to look for savings: only tau2 is genuinely input-dominated in money, so it "
         "is the one benchmark where a cheaper-input model beats a terser one -- everywhere else "
         "verbosity is the thing to control.",
         note="Both bars are per-task means over every priced row of that benchmark and model.")


# -------------------------------------------------------- 3. two models, the identical tasks
def chart_model_choice(platforms):
    pairs = costlib.same_task_model_pairs(platforms)
    if not pairs:
        return
    bench, ids, models = pairs[0]
    order = sorted(models, key=lambda m: sum(l["mean_cost"] for l in models[m]) / len(models[m]))
    mean = {m: {f: sum(l[f] for l in models[m]) / len(models[m])
                for f in ("mean_in", "mean_out", "mean_ci", "mean_co", "mean_cost",
                          "pass_rate")} for m in order}

    cheap, dear = order[0], order[-1]
    fig, ax = plt.subplots(figsize=(6.4, 2.4))
    h = 0.40
    for i, m in enumerate(order):
        a = mean[m]
        ax.barh([i], [a["mean_ci"]], height=h, color=BLUE, zorder=3,
                label="paid for input" if i == 0 else None)
        ax.barh([i], [a["mean_co"]], left=[a["mean_ci"]], height=h, color=ACCENT, zorder=3,
                label="paid for output" if i == 0 else None)
        ax.annotate(f"  {money(a['mean_cost'])}   "
                    f"({a['mean_in']:,.0f} in / {a['mean_out']:,.0f} out tokens, "
                    f"pass {a['pass_rate']:.2f})",
                    (a["mean_cost"], i), va="center", ha="left", fontsize=8.2)
    ax.set_yticks(range(len(order)))
    ax.set_yticklabels([short(m) for m in order], fontweight="bold")
    ax.invert_yaxis()
    ax.set_ylim(len(order) - 0.55, -0.55)
    ax.set_xlim(0, max(a["mean_cost"] for a in mean.values()) * 1.95)
    ax.xaxis.set_major_formatter(FuncFormatter(dollars))
    ax.set_xlabel("dollars per task")
    ax.set_title(f"The same {len(ids)} {bench} tasks, two models")
    legend_below(ax, 2)
    ax.grid(axis="x", color=GRID, lw=0.6, zorder=0)
    ax.set_axisbelow(True)
    ratio = mean[dear]["mean_cost"] / mean[cheap]["mean_cost"]
    ax.annotate(f"{ratio:,.1f}×", xy=(mean[dear]["mean_cost"] * 0.55, 0.5),
                ha="center", va="center", fontsize=11, fontweight="bold", color=INK,
                bbox=dict(boxstyle="round,pad=0.25", fc="white", ec=GRID, lw=0.8))

    legs = {m: [l["mean_cost"] for l in models[m]] for m in order}
    lo = min(legs[dear]) / max(legs[cheap])
    hi = max(legs[dear]) / min(legs[cheap])
    in_mult = mean[dear]["mean_ci"] / mean[cheap]["mean_ci"]
    out_mult = mean[cheap]["mean_co"] / mean[dear]["mean_co"]
    emit("model-choice", fig,
         "Model choice is settled on the input side",
         f"{short(dear)} costs {ratio:,.1f}x "
         f"{short(cheap)} on the identical {len(ids)} {bench} tasks, and the input side decides "
         f"it alone: {in_mult:,.0f}x the input bill, against an output bill the two models split "
         f"within {abs(1 - out_mult) * 100:,.0f}%.",
         f"Mean of {short(dear)}'s {len(legs[dear])} {plural(len(legs[dear]), 'leg')} against "
         f"{short(cheap)}'s "
         f"{len(legs[cheap])}; leg to leg the ratio "
         f"spans {lo:,.1f}x-{hi:,.1f}x, because the reasoning model's output length swings with "
         f"how much it reasons. Note the shape rather than the winner: {short(cheap)} answers in "
         f"one call and emits MORE output, {short(dear)} takes ~3 tool round-trips and sends far "
         f"more input. Which one is cheaper is a property of the price list, not of the models.",
         note="The task set is identical and task selection is deterministic, so this is the one "
              "comparison in the matrix with no workload difference to explain away.")


# ------------------------------------------------------------- 4. where the matrix's bill is
def chart_leg_pareto(platforms):
    P = platforms[0]
    legs = sorted(costlib.leg_rows(P), key=lambda l: -l["cost"])
    total = sum(l["cost"] for l in legs)

    fig, ax = plt.subplots(figsize=(6.4, 3.1))
    xs = range(len(legs))
    ax.bar(xs, [l["cost"] for l in legs], color=[BENCH_COLOR[l["bench"]] for l in legs],
           zorder=3, width=0.72)
    ax.set_yscale("log")
    ax.set_ylim(min(l["cost"] for l in legs) * 0.5, total * 1.4)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: money(v)))
    ax.set_xticks(list(xs))
    ax.set_xticklabels([f"#{l['n']}" for l in legs], fontsize=8.5)
    ax.set_ylabel("dollars per leg (log)")
    ax.set_title(f"Where a 12-run's bill goes \u2014 {platform(P['label'])}, {money(total)} in total")
    ax.grid(axis="y", color=GRID, lw=0.6, zorder=0)
    ax.set_axisbelow(True)

    cum = ax.twinx()
    run, ys = 0.0, []
    for l in legs:
        run += l["cost"]
        ys.append(run / total * 100)
    cum.plot(list(xs), ys, color=INK, lw=1.4, marker="o", ms=3.0, zorder=4)
    cum.set_ylim(0, 105)
    cum.set_ylabel("cumulative % of the bill")
    cum.spines["top"].set_visible(False)
    for i in (0, 1):
        cum.annotate(f"{ys[i]:.0f}%", (i, ys[i]), xytext=(4, -10),
                     textcoords="offset points", fontsize=8.2, fontweight="bold")
    handles = [plt.Rectangle((0, 0), 1, 1, color=BENCH_COLOR[b]) for b in BENCH_COLOR]
    handles.append(plt.Line2D([], [], color=INK, lw=1.4, marker="o", ms=3.0))
    ax.legend(handles, list(BENCH_COLOR) + ["cumulative % of the bill"],
              loc="upper center", bbox_to_anchor=(0.5, -0.13), ncol=4, frameon=False,
              fontsize=8.5, handlelength=1.4, columnspacing=1.6)

    top2 = sum(l["cost"] for l in legs[:2]) / total * 100
    cheap_bench = min(BENCH_COLOR, key=lambda b: sum(l["cost"] for l in legs if l["bench"] == b))
    cheap_n = sum(1 for l in legs if l["bench"] == cheap_bench)
    cheap_share = sum(l["cost"] for l in legs if l["bench"] == cheap_bench) / total * 100
    emit("leg-pareto", fig,
         "Two legs of twelve are most of the bill",
         f"Two of the twelve legs are {top2:.0f}% of a matrix's bill, while all {cheap_n} "
         f"{cheap_bench} legs together are {cheap_share:.1f}% of it -- so budget by benchmark, "
         f"not by task count.",
         "The bars are log-scaled because a linear axis renders every gsm8k leg as nothing at "
         "all, which is itself the finding: there are only two legs worth watching. It is also "
         "why the cheap legs are the ones to iterate on -- a full gsm8k sweep costs less than a "
         "rounding error on one appworld leg.",
         note=f"{platform(P['label'])}; the ordering reproduces on the other platform, the "
              f"absolute "
              f"appworld figures do not (nondeterministic turn counts).")


# --------------------------------------------------------------- 5. efficiency per SUCCESS
def chart_cost_per_pass(platforms):
    b = costlib.agg_bench(platforms)
    keys = list(b)
    fig, ax = plt.subplots(figsize=(6.4, 2.9))
    h = 0.34
    lo = min(b[k]["mean_cost"] for k in keys) * 0.45
    hi = max(b[k]["mean_cost"] / b[k]["pass_rate"] for k in keys if b[k]["pass_rate"])
    right = max(hi, max(b[k]["mean_cost"] for k in keys)) * 22
    ax.set_xscale("log")
    ax.set_xlim(lo, right)
    for i, k in enumerate(keys):
        a = b[k]
        ax.barh([i + (h + 0.04) / 2], [a["mean_cost"]], height=h, color=GRAY, zorder=3,
                label="per task attempted" if i == 0 else None)
        ax.annotate(f" {money(a['mean_cost'])}", (a["mean_cost"], i + (h + 0.04) / 2),
                    va="center", fontsize=8.2, color=GRAY, fontweight="bold")
        if a["pass_rate"]:
            per = a["mean_cost"] / a["pass_rate"]
            ax.barh([i - (h + 0.04) / 2], [per], height=h, color=ACCENT, zorder=3,
                    label="per task PASSED" if i == 0 else None)
            ax.annotate(f" {money(per)}   (pass {a['pass_rate']:.2f})",
                        (per, i - (h + 0.04) / 2), va="center", fontsize=8.2)
        else:
            # Runs off the right edge on purpose: dividing by a zero pass rate has no value to
            # plot, and clipping the bar at some arbitrary width would invent one.
            ax.barh([i - (h + 0.04) / 2], [right], height=h, color=ACCENT,
                    alpha=0.28, hatch="///", edgecolor=ACCENT, zorder=3)
            ax.annotate(f"pass rate {a['pass_rate']:.2f} \u2014 no finite cost per success  >",
                        (right * 0.9, i - (h + 0.04) / 2), va="center", ha="right",
                        fontsize=8.2, fontweight="bold", color="#8A4408")
    ax.set_yticks(range(len(keys)))
    ax.set_yticklabels(keys, fontweight="bold")
    ax.invert_yaxis()
    ax.set_ylim(len(keys) - 0.5, -0.5)
    ax.xaxis.set_major_formatter(FuncFormatter(dollars))
    ax.set_xlabel("dollars (log)")
    ax.set_title("Cost per task, and cost per task that actually PASSED")
    legend_below(ax, 2)
    ax.grid(axis="x", color=GRID, lw=0.6, zorder=0)
    ax.set_axisbelow(True)

    zero = [k for k in keys if not b[k]["pass_rate"]]
    emit("cost-per-pass", fig,
         "Efficiency only means anything per SUCCESS",
         "Dividing by the pass rate is what turns a token count into an efficiency figure: "
         + "; ".join(f"{k} {money(b[k]['mean_cost'])} per task becomes "
                     f"{money(b[k]['mean_cost'] / b[k]['pass_rate'])} per PASS"
                     for k in keys if b[k]["pass_rate"])
         + (f" -- and {', '.join(zero)} has no finite cost per success at all, because it passed "
            f"nothing." if zero else "."),
         "A change that halves your token use and halves your pass rate has gained you nothing, "
         "which is why we never quote tokens per task as an efficiency number on its own. The "
         "unbounded row is not a rendering artifact: it is the honest way to report a benchmark "
         "that runs to completion, records every token, and then fails evaluation.",
         note="Pass rates here are over PRICED rows; the headline rates in the reports use the "
              "tasks attempted, which is a lower number for appworld because a timed-out task "
              "leaves no row.")


# ------------------------------------------------------- 6. the consumer we cannot see billing
def chart_judge(platforms):
    per_call = costlib.judge_call_cost()
    bars = []
    for P in platforms:
        for l in costlib.leg_rows(P):
            if l["n"] in costlib.PLUGIN_LEGS:
                bars.append((f"{platform(P['label'])} #{l['n']}", l["cost"],
                             l["tool_calls"] * per_call))
    if not bars:
        return
    fig, ax = plt.subplots(figsize=(6.4, 3.0))
    h = 0.36
    for i, (lab, agent, judge) in enumerate(bars):
        ax.barh([i], [agent], height=h, color=TEAL, zorder=3,
                label="the agent's own calls" if i == 0 else None)
        ax.barh([i], [judge], left=[agent], height=h, color=RUST, zorder=3,
                label="the IBAC judge (floor)" if i == 0 else None)
        ax.annotate(f"  {money(agent + judge)}   judge is {judge / agent:,.1f}× the agent",
                    (agent + judge, i), va="center", fontsize=8.2)
    ax.set_yticks(range(len(bars)))
    ax.set_yticklabels([b[0] for b in bars], fontweight="bold", fontsize=8.5)
    ax.invert_yaxis()
    ax.set_xlim(0, max(a + j for _, a, j in bars) * 1.85)
    ax.xaxis.set_major_formatter(FuncFormatter(dollars))
    ax.set_xlabel("dollars per leg")
    ax.set_title("The plugin legs: what the agent billed, and what authorizing it billed")
    legend_below(ax, 2)
    ax.grid(axis="x", color=GRID, lw=0.6, zorder=0)
    ax.set_axisbelow(True)

    mult = [j / a for _, a, j in bars]
    gsm = costlib.agg_bench_model(platforms)
    cheapest = min(gsm, key=lambda k: gsm[k]["mean_cost"])
    emit("judge-overhead", fig,
         "On the plugin legs, the check outcosts the work",
         f"One IBAC judge completion costs at least {money(per_call)} -- "
         f"{per_call / gsm[cheapest]['mean_cost']:,.1f}x the entire "
         f"{cheapest[0]} task on {short(cheapest[1])} it is authorizing -- so across legs "
         f"{'/'.join('#' + str(n) for n in costlib.PLUGIN_LEGS)} the judge bills "
         f"{min(mult):,.1f}-{max(mult):,.1f}x what the agent does.",
         f"The judge runs {short(costlib.JUDGE_MODEL)}, the dearest model on our card, against a "
         f"FIXED {costlib.JUDGE_PROMPT_CHARS:,}-character system prompt that does not shrink with "
         f"the task -- so the cheaper the work, the more lopsided this gets. None of it appears in "
         f"report.ndjson: the sidecar makes the call, not the instrumented agent, which is why "
         f"every other figure in this section is a FLOOR.",
         note="Judge cost is tool calls x the floor per call; the proposed-action block that "
              "follows the fixed prompt is not counted. tau2's user simulator is invisible the "
              "same way, inside the uninstrumented MCP pod.")


# --------------------------------------------------------------------------- doc integration
FIG_ORDER = ["ladder-amplification", "cost-composition", "model-choice",
             "leg-pareto", "cost-per-pass", "judge-overhead"]




def markdown_block(rel_prefix, heading="####", keys=None):
    """`keys` selects a subset — the primer carries the two figures a newcomer needs, and points
    at the guide for the other four rather than repeating a budgeting section."""
    L = []
    for i, key in enumerate(keys or FIG_ORDER, 1):
        t = TAKEAWAYS[key]
        L += [f"{heading} Figure {i} \u2014 {t['title']}", "",
              f"![{t['title']}]({rel_prefix}{key}.png)", "",
              f"**{t['takeaway']}**", "", t["caption"], ""]
        if t["note"]:
            L += [f"<sub>{t['note']}</sub>", ""]
    return "\n".join(L).rstrip()


# The primer is a newcomer's doc, not a budgeting one: it takes the ladder figure (why three
# benchmarks) and the per-success figure (how to read any efficiency number), and defers the rest.
PRIMER_FIGS = ["ladder-amplification", "cost-per-pass"]


def rewrite_block(doc: pathlib.Path, body: str):
    """Fill the <!-- charts --> ... <!-- /charts --> region, like gen_toc.py does for the TOC."""
    src = doc.read_text()
    pat = re.compile(r"(<!-- charts -->\n)(.*?)(<!-- /charts -->)", re.S)
    if not pat.search(src):
        print(f"  {doc.relative_to(REPO)}: no <!-- charts --> block, skipped")
        return
    new = pat.sub(lambda m: m.group(1) + "\n" + body + "\n\n" + m.group(3), src)
    if new == src:
        print(f"  {doc.relative_to(REPO)}: charts block already current")
        return
    doc.write_text(new)
    print(f"  {doc.relative_to(REPO)}: charts block rewritten ({body.count('![')} figures)")


def main(argv):
    mirror = pathlib.Path("/tmp/autobench")
    specs = []
    i = 0
    while i < len(argv):
        if argv[i] == "--mirror":
            mirror, i = pathlib.Path(argv[i + 1]), i + 2
            continue
        specs.append(argv[i])
        i += 1
    if not specs:
        sys.exit(__doc__)

    platforms = [costlib.load(s, mirror) for s in specs]
    costlib.require_mirror(platforms)
    print(f"rate card {costlib.RATE_CARD_DATE}, mirror {mirror}, "
          f"platforms {', '.join(P['label'] for P in platforms)}")

    chart_ladder(platforms)
    chart_composition(platforms)
    chart_model_choice(platforms)
    chart_leg_pareto(platforms)
    chart_cost_per_pass(platforms)
    chart_judge(platforms)

    missing = [k for k in FIG_ORDER if k not in TAKEAWAYS]
    if missing:
        sys.exit(f"figures not produced: {missing}")

    (IMG / "takeaways.json").write_text(json.dumps(
        {"_generated_by": "reference/gen-cost-charts.py",
         "_rate_card_date": costlib.RATE_CARD_DATE,
         "_platforms": [P["label"] for P in platforms],
         "order": FIG_ORDER, "figures": TAKEAWAYS}, indent=2) + "\n")
    print("  docs/img/takeaways.json")

    rewrite_block(REPO / "docs" / "DEVELOPER_GUIDE.md", markdown_block("img/"))
    rewrite_block(REPO / "docs" / "BENCHMARKS_PRIMER.md",
                  markdown_block("img/", heading="####", keys=PRIMER_FIGS))
    print("\nNext: docs/generate_pptx.py (reads takeaways.json), then reference/gen_pdf.py")


if __name__ == "__main__":
    main(sys.argv[1:])
