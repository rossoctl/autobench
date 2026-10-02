"""Bucket a task's error by *what kind of thing went wrong* — for artifacts of either era.

A pass-rate delta between platforms is only interpretable if you know whether the losing side lost
a task to the model getting it wrong or to a socket closing. Both live in the same `evaluated_pass`
denominator and read identically in every table the generators produce.

**There are two artifact eras, and this module reads both.** Until the Service gained
`src/autobench/public_errors.py`, `run.json`'s `error` fields and `report.ndjson`'s `status_message`
carried the upstream text verbatim into the public bucket; from that image on they carry
``"<category> (shape <8 hex>)"`` instead, because the bucket is anonymously listable and the
verbatim strings were publishing internal hostnames, a team UUID and the org's spend.

So:

* a **scrubbed** value is parsed — the category is authoritative, no guessing needed;
* a **verbatim** value falls through to `_LEGACY`, whose patterns and labels are kept byte-identical
  to what `gen-12run-comparison.py` carried inline, so re-running a generator over a pre-scrub
  landmark run reproduces exactly the table it published. Do not "tidy" that table.

The scrubbed era classifies more finely than the legacy patterns could — `public_errors` has 20
buckets drawn from a survey of all 1,056 strings in the bucket, where the inline table had 5. Labels
below therefore agree with the legacy ones where the bucket means the same thing, and introduce new
labels only where the legacy answer would have been `other`. A cross-era comparison is the one place
to be careful; a comparison *within* one 12-run (which is what the generators do) is unaffected.
"""

from __future__ import annotations

import re

# Byte-identical to the table that lived in gen-12run-comparison.py. Ordered: first match wins, so
# the specific infrastructure signatures are tested before the generic ones.
_LEGACY: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("health probe (Bug 3)", ("/v1/models",)),
    ("transport / gateway", ("peer closed connection", "incomplete chunked", "503",
                             "Network communication error")),
    ("per-task timeout", ("per-task timeout",)),
    ("agent (upstream defect)", ("missing assistant content",)),
    ("wrong answer", ("Error executing submit", "does not match")),
)

# `public_errors` category -> display label. Every category that module can emit must appear here, or
# `cause()` would silently report a raw slug in a published table.
_SLUG_LABELS: dict[str, str] = {
    # same meaning as a legacy bucket -> same label, so the two eras line up
    "model_probe_failed": "health probe (Bug 3)",
    "transport_gateway": "transport / gateway",
    "agent_card_unreachable": "transport / gateway",
    "task_timeout": "per-task timeout",
    "agent_defect": "agent (upstream defect)",
    "tool_choice_unsupported": "agent (upstream defect)",
    "wrong_answer": "wrong answer",
    # finer than the legacy table could express; legacy would have said "other" for all of these
    "budget_exceeded": "gateway budget / entitlement",
    "model_not_granted": "gateway budget / entitlement",
    "model_auth_failed": "gateway budget / entitlement",
    "rate_limited": "gateway budget / entitlement",
    "mcp_session_poisoned": "MCP unreachable",
    "mcp_connect_timeout": "MCP unreachable",
    "mcp_call_failed": "MCP unreachable",
    "mcp_tool_error": "MCP unreachable",
    "run_timeout": "run-level timeout",
    "run_interrupted": "run interrupted",
    "session_terminated": "session terminated",
    "timed_out": "task timed out (unattributed)",
    "other": "other",
}

# Every label `cause()` can return, in display order: the legacy buckets first (their published
# order), then the labels only the scrubbed era can produce, `other` last. A generator iterates this
# rather than keeping its own list, which is how gen-12run-comparison.py lost its table in 80df31d.
LABELS: tuple[str, ...] = tuple(dict.fromkeys(
    [label for label, _ in _LEGACY] + [v for v in _SLUG_LABELS.values() if v != "other"] + ["other"]))

_SCRUBBED = re.compile(r"^([a-z_]+) \(shape ([0-9a-f]{8})\)$")


def parse_scrubbed(msg: str | None) -> tuple[str, str] | None:
    """`("category", "shape")` when the value is a scrubbed one, else `None`."""
    m = _SCRUBBED.match((msg or "").strip())
    return (m.group(1), m.group(2)) if m else None


def cause(msg: str | None) -> str:
    """Display label for an error string from either era. `"other"` when nothing matches."""
    parsed = parse_scrubbed(msg)
    if parsed:
        return _SLUG_LABELS.get(parsed[0], "other")
    text = msg or ""
    for label, patterns in _LEGACY:
        if any(p in text for p in patterns):
            return label
    return "other"


def is_probe_failure(msg: str | None) -> bool:
    """A task the agent's per-task `GET /v1/models` check killed before the model was called.

    Kept as its own predicate because it is a *denominator* correction, not just a label: such a
    task leaves a `report.ndjson` row with zero tokens that skews every per-task statistic, so the
    dev145 matrices need a `total - probe_failures` denominator. The legacy test was
    `error.endswith("/v1/models")`, which a scrubbed value can never satisfy — hence this module.
    """
    parsed = parse_scrubbed(msg)
    if parsed:
        return parsed[0] == "model_probe_failed"
    return (msg or "").endswith("/v1/models")


def shape(msg: str | None) -> str | None:
    """The shape id, for grouping identical failures or correlating with the Service log."""
    parsed = parse_scrubbed(msg)
    return parsed[1] if parsed else None
