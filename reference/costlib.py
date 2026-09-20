"""Shared pricing primitives for the cost generators.

`gen-cost-analysis.py` (tables) and `gen-cost-charts.py` (figures) must never disagree about
what a run cost, so the rate card, the row filter and the aggregations live here once. Import
it the way the other generators import `gen_toc`:

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import costlib

Everything is derived from the mirrored `report.ndjson` rows x `model_prices.json`. Nothing in
this module reads the network or a cluster.
"""
from __future__ import annotations

import json
import pathlib
import statistics as st

HERE = pathlib.Path(__file__).resolve().parent
CARD = json.loads((HERE / "model_prices.json").read_text())
PRICES = CARD["models"]
RATE_CARD_DATE = CARD["_rate_card_date"]

# The judge's system prompt is a fixed 1,577 chars / 231 words, read off the rendered
# per-workload ConfigMap (team1/authbridge-config-<workload>, ibac plugin config). The
# ~4 chars/token rule is a floor: the proposed-action block that follows it is not fixed.
JUDGE_PROMPT_CHARS = 1577
JUDGE_VERDICT_TOKENS = 40
JUDGE_MODEL = "openai/Azure/gpt-4.1"
PLUGIN_LEGS = (6, 7, 8)


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


def split_cost(model, tin, tout):
    """(input dollars, output dollars) -- the two terms the charts stack."""
    p = PRICES[model]
    return tin * p["input_per_1m"] / 1e6, tout * p["output_per_1m"] / 1e6


def judge_call_cost():
    """Floor cost of one IBAC judge completion: fixed system prompt in, short verdict out."""
    return cost(JUDGE_MODEL, JUDGE_PROMPT_CHARS / 4, JUDGE_VERDICT_TOKENS)


def name(model):
    """The alias a reader recognises, without litellm's `openai/` routing prefix."""
    return PRICES[model]["public_name"] if model in PRICES else model


def short(model):
    """Chart-axis form: drop the provider path too, since the bar is already labelled."""
    return name(model).split("/")[-1].replace("-2025-08-07", "")


def priced(rows):
    """Rows that can be priced: a real model and a real input-token count.

    A row with `llm_input_tokens == 0` is a task that died before its first model call -- it
    carries `model: unknown` and no cost. Counting it would drag every per-task mean toward
    zero, so it is excluded here and reported separately.
    """
    return [x for x in rows if (x.get("llm_input_tokens") or 0) and x.get("model") in PRICES]


def load(spec_path, mirror):
    """A run-12 spec plus each leg's mirrored rows. A missing mirror is flagged, never skipped."""
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
    legs.sort(key=lambda leg: leg["n"])
    return {"label": spec["label"], "legs": legs}


def require_mirror(platforms):
    """Exit loudly if the mirror was pruned. /tmp does not survive, and a report regenerated
    against a half-present mirror is worse than no report -- it looks complete."""
    missing = [(P["label"], leg["n"]) for P in platforms for leg in P["legs"] if leg["missing"]]
    if not missing:
        return
    have = sum(1 for P in platforms for leg in P["legs"] if not leg["missing"])
    raise SystemExit(
        f"mirror incomplete: {len(missing)} of {len(missing) + have} legs have no "
        f"report.ndjson ({', '.join(f'{lbl} #{n}' for lbl, n in missing[:6])}"
        f"{', ...' if len(missing) > 6 else ''}).\n"
        f"Run:  python3 reference/remirror.py <the run12-*.json you passed>")


BENCH_ORDER = {"gsm8k": 0, "tau2": 1, "appworld": 2}


def agg_bench_model(platforms):
    """Per (benchmark, model): token means, cost means, input's share of the bill.

    Keyed this way and not by benchmark alone because gsm8k runs two models, and pooling them
    describes neither -- their per-task costs differ 4.6x.
    """
    agg = {}
    for P in platforms:
        for leg in P["legs"]:
            for x in priced(leg["rows"]):
                a = agg.setdefault((leg["bench"], x["model"]),
                                   {"in": [], "out": [], "c": [], "ci": [], "co": [],
                                    "lat": [], "passed": 0, "legs": set()})
                ti, to = x["llm_input_tokens"], x.get("llm_output_tokens") or 0
                ci, co = split_cost(x["model"], ti, to)
                a["in"].append(ti); a["out"].append(to)
                a["c"].append(ci + co); a["ci"].append(ci); a["co"].append(co)
                a["lat"].append(x.get("total_latency_s") or 0.0)
                a["passed"] += 1 if x.get("evaluation_result") else 0
                a["legs"].add((P["label"], leg["n"]))
    for a in agg.values():
        a["n"] = len(a["c"])
        a["mean_in"] = st.mean(a["in"]); a["mean_out"] = st.mean(a["out"])
        a["mean_cost"] = st.mean(a["c"])
        a["mean_ci"] = st.mean(a["ci"]); a["mean_co"] = st.mean(a["co"])
        a["median_lat"] = st.median(a["lat"])
        a["in_share_tok"] = a["mean_in"] / (a["mean_in"] + a["mean_out"])
        a["in_share_cost"] = a["mean_ci"] / a["mean_cost"]
        a["pass_rate"] = a["passed"] / a["n"]
    return dict(sorted(agg.items(), key=lambda kv: (BENCH_ORDER.get(kv[0][0], 9), kv[0][1])))


def agg_bench(platforms):
    """Per benchmark, pooling its models -- the ladder rung, as the at-a-glance tables report it."""
    out = {}
    for (bench, model), a in agg_bench_model(platforms).items():
        b = out.setdefault(bench, {"in": [], "out": [], "c": [], "lat": [], "passed": 0,
                                   "models": [], "by_model": {}})
        b["in"] += a["in"]; b["out"] += a["out"]; b["c"] += a["c"]; b["lat"] += a["lat"]
        b["passed"] += a["passed"]; b["models"].append(model)
        b["by_model"][model] = a["n"]
    for b in out.values():
        b["n"] = len(b["c"])
        # The model to NAME when the rung is quoted as one number: gsm8k pools two, and the
        # pooled mean is the busier one's mean to within a few percent (161 rows vs 10).
        b["dom_model"] = max(b["by_model"], key=lambda m: b["by_model"][m])
        b["mean_in"] = st.mean(b["in"]); b["mean_out"] = st.mean(b["out"])
        b["mean_tok"] = b["mean_in"] + b["mean_out"]
        b["mean_cost"] = st.mean(b["c"])
        b["median_lat"] = st.median(b["lat"])
        b["pass_rate"] = b["passed"] / b["n"]
    return dict(sorted(out.items(), key=lambda kv: BENCH_ORDER.get(kv[0], 9)))


def leg_rows(platform):
    """Per leg: priced task count, tokens, cost, passes, and the models it used."""
    out = []
    for leg in platform["legs"]:
        if leg["missing"]:
            continue
        pr = priced(leg["rows"])
        ti = sum(x["llm_input_tokens"] for x in pr)
        to = sum(x.get("llm_output_tokens") or 0 for x in pr)
        c = sum(cost(x["model"], x["llm_input_tokens"], x.get("llm_output_tokens") or 0)
                for x in pr)
        out.append({
            "n": leg["n"], "bench": leg["bench"], "tasks": len(pr),
            "zero_rows": sum(1 for x in leg["rows"] if not (x.get("llm_input_tokens") or 0)),
            "in": ti, "out": to, "tokens": ti + to, "cost": c,
            "passed": sum(1 for x in pr if x.get("evaluation_result")),
            "tool_calls": sum(x.get("tool_count") or 0 for x in leg["rows"]),
            "models": sorted({x["model"] for x in pr}),
        })
    return out


def same_task_model_pairs(platforms):
    """Legs that ran an IDENTICAL task set on a single model, grouped so two models can be
    compared on the same work. Returns [(bench, task_ids, {model: [leg dicts]})]."""
    by = {}
    for P in platforms:
        for leg in P["legs"]:
            pr = priced(leg["rows"])
            if not pr:
                continue
            models = {x["model"] for x in pr}
            if len(models) != 1:
                continue
            model = models.pop()
            ids = frozenset(x["task_id"] for x in pr)
            ci = st.mean([split_cost(model, x["llm_input_tokens"],
                                     x.get("llm_output_tokens") or 0)[0] for x in pr])
            co = st.mean([split_cost(model, x["llm_input_tokens"],
                                     x.get("llm_output_tokens") or 0)[1] for x in pr])
            by.setdefault((leg["bench"], ids), {}).setdefault(model, []).append({
                "label": P["label"], "n": leg["n"],
                "mean_in": st.mean([x["llm_input_tokens"] for x in pr]),
                "mean_out": st.mean([x.get("llm_output_tokens") or 0 for x in pr]),
                "mean_ci": ci, "mean_co": co, "mean_cost": ci + co,
                "pass_rate": sum(1 for x in pr if x.get("evaluation_result")) / len(pr),
            })
    return [(bench, ids, models) for (bench, ids), models in
            sorted(by.items(), key=lambda kv: -len(kv[1])) if len(models) > 1]
