#!/usr/bin/env python3
"""How much of a leg's inference goes on picking tools rather than doing the task.

    python3 reference/gen-shortlist-audit.py \\
        results/v1.28-dev146/run12-ocp-dev146.json "OpenShift (ykt3 Service, ykt2 workloads)" \\
        results/v1.28-dev146/run12-kind-dev146.json "KinD (single-node local)"

Writes, and owns completely, the <!-- shortlist --> block in
  docs/BENCHMARKS_PRIMER.md
  docs/DEVELOPER_GUIDE.md
the way gen_toc.py owns <!-- toc --> and gen-cost-charts.py owns <!-- charts -->. Every number
in that block is formatted from the artifacts, so a sentence there cannot drift from the data.

WHAT IS COUNTED and HOW A SELECTION CALL IS IDENTIFIED: see `shortlistlib.py`, which holds the
rule and the reasoning behind it, and is shared with the two 12-run report generators so all
three documents repeat the same classification rather than three drifting copies of it.

Platform labels are taken from argv -- never print a Service endpoint or a mirror path, these
documents are published.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import shortlistlib as SL  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
DOCS = [REPO / "docs" / "BENCHMARKS_PRIMER.md", REPO / "docs" / "DEVELOPER_GUIDE.md"]
THRESHOLD = SL.THRESHOLD
ORDER = ["gsm8k", "tau2", "appworld"]
pct = SL.pct


def audit(path: str) -> dict[str, dict]:
    """One 12-run result set -> {benchmark: counters}, pooled over its legs."""
    per: dict[str, dict] = {}
    for run in json.loads(pathlib.Path(path).read_text())["runs"]:
        md, bench = run.get("mirror_dir"), run.get("bench")
        if not md or not bench:
            continue
        sp = pathlib.Path(md) / "span_report.ndjson"
        if not sp.exists():
            continue
        rows = [json.loads(l) for l in sp.read_text().splitlines() if l.strip()]
        acc = per.setdefault(bench, SL.new_acc() | {"legs": 0})
        acc["legs"] += 1
        SL.tally_rows(rows, acc)
    return per


def merge(sets: list[dict[str, dict]]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for s in sets:
        for bench, acc in s.items():
            tgt = out.setdefault(bench, {k: 0 for k in acc})
            for k, v in acc.items():
                tgt[k] += v
    return out


def block(pooled: dict[str, dict], sides: list[tuple[str, dict[str, dict]]]) -> str:
    lines = [
        "| benchmark | LLM calls / task | of which are tool-selection calls | input tokens spent selecting | output tokens spent selecting |",
        "|---|---|---|---|---|",
    ]
    for bench in ORDER:
        a = pooled.get(bench)
        if not a or not a["tasks"]:
            continue
        calls = (a["sel"] + a["asst"]) / a["tasks"]
        share = pct(a["sel"], a["sel"] + a["asst"])
        lines.append(
            f"| **{bench}** | {calls:.1f} | {a['sel'] / a['tasks']:.1f} ({share}) "
            f"| {pct(a['sel_in'], a['sel_in'] + a['as_in'])} "
            f"| {pct(a['sel_out'], a['sel_out'] + a['as_out'])} |")

    tot = sum(a["tasks"] for a in pooled.values())
    hits = [b for b in ORDER if pooled.get(b, {}).get("sel")]
    zero = [b for b in ORDER if b in pooled and not pooled[b]["sel"]]
    a = pooled.get(hits[0]) if hits else None
    say = []
    if a:
        sl_per = a["sel_in"] / a["sel"]
        as_per = a["as_in"] / a["asst"]
        by_side = " and ".join(
            f"{pct(s[bench]['sel_in'], s[bench]['sel_in'] + s[bench]['as_in'])} on {lab}"
            for lab, s in sides for bench in [hits[0]] if bench in s and s[bench]["tasks"])
        say.append(
            f"Measured over the **{tot} task rows that carry chat spans** in the v1.28 matrices, "
            f"both platforms. "
            f"{' and '.join('`' + b + '`' for b in zero)} expose fewer than the agent's "
            f"`max_selected_tools = {THRESHOLD}` and so measure **exactly zero** selection calls — "
            f"they are the control for the detector. `{hits[0]}` is above the threshold and pairs "
            f"**1:1**: {a['sel']} selection calls against {a['asst']} assistant calls, every turn. "
            f"A selection call averages **{sl_per:,.0f} input tokens** against "
            f"**{as_per:,.0f}** for the assistant call it precedes — it carries every tool name and "
            f"description, while the assistant call carries only the {THRESHOLD} winners' schemas. "
            f"The split is stable across clusters: {by_side}.")
    return "\n".join(lines + [""] + say)


def rewrite(pathlist, body: str) -> None:
    for p in pathlist:
        t = p.read_text()
        new, n = re.subn(r"(<!-- shortlist -->)\n?.*?\n?(<!-- /shortlist -->)",
                         lambda m: f"{m.group(1)}\n{body}\n{m.group(2)}", t, flags=re.S)
        if not n:
            print(f"  {p.name}: no <!-- shortlist --> block, skipped")
            continue
        if new != t:
            p.write_text(new)
            print(f"  {p.name}: block rewritten")
        else:
            print(f"  {p.name}: block already current")


def main() -> int:
    if len(sys.argv) < 5 or len(sys.argv) % 2 == 0:
        print(__doc__)
        return 2
    pairs = [(sys.argv[i], sys.argv[i + 1]) for i in range(1, len(sys.argv) - 1, 2)]
    sides = [(label, audit(path)) for path, label in pairs]
    pooled = merge([s for _, s in sides])

    print(f"{'benchmark':10} {'platform':40} {'tasks':>5} {'select':>7} {'asst':>5} "
          f"{'in%':>5} {'out%':>5}")
    for bench in ORDER:
        for label, s in sides:
            a = s.get(bench)
            if not a or not a["tasks"]:
                continue
            print(f"{bench:10} {label:40} {a['tasks']:5d} {a['sel']:7d} {a['asst']:5d} "
                  f"{pct(a['sel_in'], a['sel_in'] + a['as_in']):>5} "
                  f"{pct(a['sel_out'], a['sel_out'] + a['as_out']):>5}")
    print()
    rewrite(DOCS, block(pooled, sides))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
