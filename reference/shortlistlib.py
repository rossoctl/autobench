#!/usr/bin/env python3
"""Split a task's `chat` spans into tool-*selection* calls and the *assistant* calls they serve.

Shared by `gen-shortlist-audit.py` (which writes the prose docs' `<!-- shortlist -->` block),
`gen-12run-report.py` (§8) and `gen-12run-comparison.py`, the way `costlib.py` is shared by the
cost generators: the classification rule is a claim about the data, so it lives in exactly one
place and every report that repeats it repeats the same one.

WHAT IS BEING COUNTED. The agent image (`exgentic-a2a-tool_calling`) defaults to
`enable_tool_shortlisting = True, max_selected_tools = 30`. When the MCP advertises more than 30
tools, the agent inserts an *extra* LLM call per turn: it sends the full tool inventory — every
name and description — and asks the model to rank it, then passes only the winners' schemas to the
call that actually decides the next action. Nothing upstream of us works this way, and
`report.ndjson` folds both kinds into one `llm_count`, so the split is only visible in the spans.

HOW A SELECTION CALL IS IDENTIFIED — structurally, never by token size. Within one task, order the
`chat` and tool spans by start time. A `chat` immediately followed by another `chat` is a selection
call; a `chat` followed by a tool span, or ending the task, is the assistant call whose `tool_call`
fired that tool. Token size would be the tempting discriminator and it is the wrong one: it assumes
the very cost asymmetry the audit sets out to measure, and it would silently reclassify a leg whose
tool inventory shrank.

WHY THE RULE IS TRUSTWORTHY HERE. It would also catch a text-only assistant turn that happens to
precede another model call, so it needs a control — and the matrix supplies one. `gsm8k` and `tau2`
expose fewer tools than the threshold, so shortlisting cannot fire on them and they must measure
exactly zero. They do. `appworld` is above the threshold and pairs 1:1, every assistant turn
preceded by a selection call. Read a non-zero figure on a sub-threshold benchmark as the rule
misfiring, not as a discovery.

`request_max_tokens == 1` chat spans are excluded: those are the capability probes agents up to
`exgentic 0.3.5.dev131` issued once per task, not turns. Tool spans are matched on `kind == "tool"`
(equivalent to a `name` starting `execute_tool` on every matrix measured — 0 disagreements across
the v1.28 pair, both ways), and `counted` is deliberately ignored, because a tool span the
aggregator cannot see still marks the boundary between two model turns.
"""
from __future__ import annotations

THRESHOLD = 30      # the agent's `max_selected_tools` default, read off the image

KEYS = ("tasks", "sel", "asst", "sel_in", "sel_out", "as_in", "as_out")


def new_acc() -> dict:
    return {k: 0 for k in KEYS}


def roles(spans) -> list[tuple[dict, str]]:
    """[(chat span, "sel" | "as")] for ONE task's spans, in start-time order.

    "sel" is a tool-selection call, "as" the assistant call it precedes. Returns [] for a task with
    no usable chat span (one that died before its first model call, which appworld produces
    routinely).
    """
    seq = sorted((s for s in spans
                  if s.get("kind") in ("chat", "tool")
                  and s.get("request_max_tokens") != 1),
                 key=lambda s: s.get("start_time") or "")
    out = []
    for i, s in enumerate(seq):
        if s.get("kind") != "chat":
            continue
        nxt = seq[i + 1] if i + 1 < len(seq) else None
        out.append((s, "sel" if (nxt is not None and nxt.get("kind") == "chat") else "as"))
    return out


def tally(spans, acc: dict | None = None) -> dict:
    """Fold ONE task's spans into counters. Tasks with no chat span are not counted as tasks."""
    acc = new_acc() if acc is None else acc
    rs = roles(spans)
    if not rs:
        return acc
    acc["tasks"] += 1
    for span, role in rs:
        acc["sel" if role == "sel" else "asst"] += 1
        acc[f"{'sel' if role == 'sel' else 'as'}_in"] += span.get("input_tokens") or 0
        acc[f"{'sel' if role == 'sel' else 'as'}_out"] += span.get("output_tokens") or 0
    return acc


def tally_rows(rows, acc: dict | None = None) -> dict:
    """Fold a whole leg's `span_report.ndjson` rows (any number of tasks) into counters."""
    acc = new_acc() if acc is None else acc
    by_task: dict = {}
    for s in rows:
        by_task.setdefault(s.get("task_id"), []).append(s)
    for spans in by_task.values():
        tally(spans, acc)
    return acc


def merge(accs) -> dict:
    out = new_acc()
    for a in accs:
        for k in KEYS:
            out[k] += a.get(k, 0)
    return out


def pct(part: int, whole: int) -> str:
    return "0%" if not whole else f"{round(100 * part / whole)}%"


def share_in(a: dict) -> str:
    return pct(a["sel_in"], a["sel_in"] + a["as_in"])


def share_out(a: dict) -> str:
    return pct(a["sel_out"], a["sel_out"] + a["as_out"])
