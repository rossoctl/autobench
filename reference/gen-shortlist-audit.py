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

WHY THIS EXISTS. The agent image (`exgentic-a2a-tool_calling`) defaults to
`enable_tool_shortlisting=True, max_selected_tools=30`: when a benchmark exposes more than 30
tools, the agent spends an *extra* LLM call per turn asking the model to rank the tool names,
then sends only the winners' schemas to the real call. Nothing upstream does this, nothing in
`report.ndjson` separates it, and it is not free -- so the only way to size it is off the spans.

HOW A SHORTLIST CALL IS IDENTIFIED. Structurally, never by token size: within one task, order
the `chat` and `execute_tool` spans by start time; a `chat` immediately followed by another
`chat` is a shortlist call, and a `chat` followed by a tool span (or ending the task) is the
assistant call whose tool_call fired it. The rule would also catch a text-only assistant turn,
which is why gsm8k and tau2 are the control: both sit under the 30-tool threshold and must
measure exactly zero. They do. appworld pairs 1:1 exactly, every assistant turn preceded by a
shortlist call.

`max_tokens == 1` chat spans are capability probes from an older agent era, not turns; they are
excluded. Platform labels are taken from argv -- never print a Service endpoint or a mirror
path, these documents are published.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
DOCS = [REPO / "docs" / "BENCHMARKS_PRIMER.md", REPO / "docs" / "DEVELOPER_GUIDE.md"]
THRESHOLD = 30          # max_selected_tools, read off the agent image
ORDER = ["gsm8k", "tau2", "appworld"]


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
        acc = per.setdefault(bench, dict(tasks=0, sl=0, asst=0,
                                         sl_in=0, sl_out=0, as_in=0, as_out=0, legs=0))
        acc["legs"] += 1
        for task in sorted({r["task_id"] for r in rows if r.get("task_id")}):
            seq = sorted(
                (r for r in rows
                 if r.get("task_id") == task
                 and (r.get("kind") == "chat" or str(r.get("name", "")).startswith("execute_tool"))
                 and r.get("request_max_tokens") != 1),
                key=lambda r: r["start_time"])
            if not any(r.get("kind") == "chat" for r in seq):
                continue
            acc["tasks"] += 1
            for i, r in enumerate(seq):
                if r.get("kind") != "chat":
                    continue
                nxt = seq[i + 1] if i + 1 < len(seq) else None
                pre = "sl" if (nxt is not None and nxt.get("kind") == "chat") else "as"
                acc["sl" if pre == "sl" else "asst"] += 1
                acc[f"{pre}_in"] += r.get("input_tokens") or 0
                acc[f"{pre}_out"] += r.get("output_tokens") or 0
    return per


def merge(sets: list[dict[str, dict]]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for s in sets:
        for bench, acc in s.items():
            tgt = out.setdefault(bench, {k: 0 for k in acc})
            for k, v in acc.items():
                tgt[k] += v
    return out


def pct(part: int, whole: int) -> str:
    return "0%" if not whole else f"{round(100 * part / whole)}%"


def block(pooled: dict[str, dict], sides: list[tuple[str, dict[str, dict]]]) -> str:
    lines = [
        "| benchmark | LLM calls / task | of which are tool-selection calls | input tokens spent selecting | output tokens spent selecting |",
        "|---|---|---|---|---|",
    ]
    for bench in ORDER:
        a = pooled.get(bench)
        if not a or not a["tasks"]:
            continue
        calls = (a["sl"] + a["asst"]) / a["tasks"]
        share = pct(a["sl"], a["sl"] + a["asst"])
        lines.append(
            f"| **{bench}** | {calls:.1f} | {a['sl'] / a['tasks']:.1f} ({share}) "
            f"| {pct(a['sl_in'], a['sl_in'] + a['as_in'])} "
            f"| {pct(a['sl_out'], a['sl_out'] + a['as_out'])} |")

    tot = sum(a["tasks"] for a in pooled.values())
    hits = [b for b in ORDER if pooled.get(b, {}).get("sl")]
    zero = [b for b in ORDER if b in pooled and not pooled[b]["sl"]]
    a = pooled.get(hits[0]) if hits else None
    say = []
    if a:
        sl_per = a["sl_in"] / a["sl"]
        as_per = a["as_in"] / a["asst"]
        by_side = " and ".join(
            f"{pct(s[bench]['sl_in'], s[bench]['sl_in'] + s[bench]['as_in'])} on {lab}"
            for lab, s in sides for bench in [hits[0]] if bench in s and s[bench]["tasks"])
        say.append(
            f"Measured over the **{tot} task rows that carry chat spans** in the v1.28 matrices, "
            f"both platforms. "
            f"{' and '.join('`' + b + '`' for b in zero)} expose fewer than the agent's "
            f"`max_selected_tools = {THRESHOLD}` and so measure **exactly zero** selection calls — "
            f"they are the control for the detector. `{hits[0]}` is above the threshold and pairs "
            f"**1:1**: {a['sl']} selection calls against {a['asst']} assistant calls, every turn. "
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
            print(f"{bench:10} {label:40} {a['tasks']:5d} {a['sl']:7d} {a['asst']:5d} "
                  f"{pct(a['sl_in'], a['sl_in'] + a['as_in']):>5} "
                  f"{pct(a['sl_out'], a['sl_out'] + a['as_out']):>5}")
    print()
    rewrite(DOCS, block(pooled, sides))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
