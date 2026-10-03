"""Publication-safe classification of upstream error text.

The S3 bucket is readable **and listable anonymously**, so everything `s3_export` writes is
public. Two fields carried verbatim upstream text into it:

* `run.json` — `RunState.error` and every `results[].error`
* `report.ndjson` / `report.parquet` — `MLflowTraceRecord.status_message`

Unlike `span_report.*`, which publishes a fixed field whitelist, these two were pass-throughs:
whatever the agent, the MCP server or the LLM gateway said landed in the bucket unfiltered. A
survey of the 575 runs already published found 1,056 such strings, and among them 317 references
to 5 internal hostnames (including the LLM gateway's — the exact string a commit had already
removed from the *docs*), 4 spend/budget figures and 4 UUIDs. A pass-through is unbounded in kind:
it publishes whatever a future upstream version decides to put in a message.

**So this module classifies instead of sanitizing, deliberately.** Rewriting a message to remove
the sensitive parts is a redactor, and redactors here have a losing record — each new one has
leaked something its author did not anticipate, because the author has to enumerate every bad
pattern while the message author has to find only one. Classification inverts that: the published
value is drawn from a closed set defined in this file, so a new upstream message can at worst be
classified `other` — never published.

What is published per error, and why each part is safe:

``"<category> (shape <8 hex>)"``

* **category** — one of `CATEGORIES`, every one of them a literal in this file.
* **shape** — `sha256` of the message *after* hostnames, URLs, UUIDs and digits are replaced by
  placeholders, truncated to 8 hex. It is a fingerprint of the message's *template*, so two tasks
  that failed the same way share a shape id, and a run can be correlated with the Service's own
  log without the text. Hashing the **normalized** form rather than the raw message matters: a
  hash of the raw text would let anyone who *guessed* a hostname confirm it by reconstructing the
  string. Normalizing first removes that, at no cost to grouping.

The original text is never discarded — it stays on `RunState`/`MLflowTraceRecord` in memory, so the
authenticated API (`GET /benchmarks/{name}/runs/{id}`, `/report`) still returns it, and the Service
logs it next to its shape id. Only the S3 projection is classified.
"""

from __future__ import annotations

import hashlib
import logging
import re

logger = logging.getLogger(__name__)

# Ordered, most specific first: the first pattern that matches wins. Every bucket below was taken
# from a message actually observed in the 575 published runs, which is why the list is this long and
# this specific -- the whole point is that `other` stays rare enough to be informative. When it does
# appear, read the Service log for that run (the shape id is logged beside the verbatim text) and add
# a bucket here rather than widening what gets published.
_CAUSES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # --- credential / entitlement, i.e. the messages that quote gateway internals -------------
    ("budget_exceeded", ("Budget has been exceeded", "ExceededBudget")),
    ("model_not_granted", ("team not allowed to access model",)),
    ("model_auth_failed", ("is not accessible: litellm.AuthenticationError", "AuthenticationError")),
    ("rate_limited", ("RateLimitError", "HTTP 429", "Too Many Requests")),
    # --- the dev145 per-task model probe (Bug 3); three wordings have shipped -------------------
    ("model_probe_failed", ("/v1/models", "is unreachable", "did not respond after",
                            "is not accessible: TimeoutError")),
    # --- reaching the workloads at all ---------------------------------------------------------
    ("agent_card_unreachable", ("Failed to fetch agent card",)),
    ("mcp_session_poisoned", ("was poisoned by an earlier stuck call",)),
    ("mcp_connect_timeout", ("MCP connect to",)),
    ("mcp_call_failed", ("Failed to run mcp call", "Failed to execute action")),
    ("mcp_tool_error", ("MCP tool error",)),
    ("transport_gateway", ("peer closed connection", "incomplete chunked", "Bad Gateway", "503",
                           "Network communication error")),
    # --- our own deadlines (named before the generic 'timed out' below) ------------------------
    ("task_timeout", ("per-task timeout",)),
    ("run_timeout", ("run exceeded timeout of",)),
    ("run_interrupted", ("run was interrupted before completing",)),
    # --- the agent's own step/action budget: the model kept acting until the agent stopped it
    # ("Error: limit_reached (max_actions): steps=16/100, actions=109/100" on appworld) ---------
    ("action_limit", ("limit_reached (",)),
    # --- defects in the agent / MCP images, not in the model -----------------------------------
    ("agent_defect", ("missing assistant content", "cannot pickle", "has no attribute 'model_dump'",
                      "No action with is_message=True")),
    ("tool_choice_unsupported", ('"auto" tool choice',)),
    ("session_terminated", ("Session terminated",)),
    # --- the AuthBridge IBAC plugin could not get a verdict. It surfaces as "Error executing
    # submit: llmclient: HTTP <code>", so it must precede wrong_answer, which matches that prefix;
    # a 403 "team not allowed" or a 429 is already claimed by the entitlement buckets above. -----
    ("judge_call_failed", ("llmclient: HTTP",)),
    # --- the benchmark scored it wrong (a real result, not an infrastructure failure) ----------
    ("wrong_answer", ("Error executing submit", "does not match")),
    # --- generic, last: 'timed out' appears inside several of the above, so it must not win ----
    ("timed_out", ("timed out", "TimeoutError", "ReadTimeout", "ConnectTimeout")),
)

#: Every value this module can publish. `other` is the catch-all; `None` passes through for no error.
CATEGORIES: tuple[str, ...] = tuple(label for label, _ in _CAUSES) + ("other",)

# Replaced before hashing so the shape id fingerprints the message *template*. Order matters: URLs
# before bare hostnames (a URL contains one), and digits last (they appear inside the others).
_NORMALIZERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"https?://\S+"), "<url>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    # Deliberately no organisation's domain is named here: the greedy character class means a
    # generic `.com`/`.net`/`.org` tail already swallows the whole dotted name, suffix and all.
    (re.compile(r"\b[a-z0-9][a-z0-9\-.]*\.(?:svc\.cluster\.local|svc|local|com|net|org|io)\b", re.I), "<host>"),
    (re.compile(r"\b[\w.\-]+@[\w\-]+\.\w+\b"), "<email>"),
    (re.compile(r"sk-[A-Za-z0-9_\-]{6,}"), "<key>"),
    (re.compile(r"\d+"), "<n>"),
    (re.compile(r"\s+"), " "),
)


def classify(message: str | None) -> str | None:
    """Bucket an error message by *what kind of thing went wrong*.

    Returns `None` for no message, else a member of `CATEGORIES`. A pass-rate delta is only
    interpretable when you know whether the losing side lost a task to the model getting it wrong
    or to a socket closing -- both land in the same `evaluated_pass` denominator.
    """
    if not message or not message.strip():
        return None
    for label, patterns in _CAUSES:
        if any(p in message for p in patterns):
            return label
    return "other"


def shape_id(message: str) -> str:
    """8 hex of `sha256` over the message's normalized template. Never published alone."""
    normalized = message
    for pattern, replacement in _NORMALIZERS:
        normalized = pattern.sub(replacement, normalized)
    return hashlib.sha256(normalized.strip().encode("utf-8")).hexdigest()[:8]


def public_error(message: str | None) -> str | None:
    """The only form of an upstream error message that may be written to the public bucket."""
    category = classify(message)
    if category is None:
        return None
    assert message is not None  # classify() returns None for falsy/blank input
    shape = shape_id(message)
    if category == "other":
        # Unclassified means the taxonomy above has a gap. Log loudly with the verbatim text (the
        # pod log is not public) so the bucket can be added, since the published value says little.
        logger.warning(
            "unclassified task error, shape %s -- add a bucket to public_errors._CAUSES: %s",
            shape, message,
        )
    else:
        logger.info("task error classified %s, shape %s: %s", category, shape, message)
    return f"{category} (shape {shape})"


def scrub_run_summary(summary: dict) -> dict:
    """Return a copy of a `RunState` dump with every error string classified.

    Shallow-copies the parts it rewrites so the caller's dict -- and the `RunState` it came from,
    which the authenticated API still serves verbatim -- is left alone.
    """
    out = dict(summary)
    if "error" in out:
        out["error"] = public_error(out.get("error"))
    results = out.get("results")
    if isinstance(results, list):
        scrubbed = []
        for task in results:
            if isinstance(task, dict) and "error" in task:
                task = dict(task)
                task["error"] = public_error(task.get("error"))
            scrubbed.append(task)
        out["results"] = scrubbed
    return out


def scrub_records(record_dicts: list[dict]) -> list[dict]:
    """Return copies of trace-record dicts with `status_message` classified.

    Applied before the token-row projection and before Parquet, so all of `report.ndjson`,
    `report.parquet` and `token_report.*` inherit it from one place.
    """
    out = []
    for record in record_dicts:
        if isinstance(record, dict) and record.get("status_message"):
            record = dict(record)
            # Keep the field a non-null string: the Parquet schema is inferred from these rows, and
            # an all-None column in one run would type differently than a string column in another.
            record["status_message"] = public_error(record["status_message"]) or ""
        out.append(record)
    return out
