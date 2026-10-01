"""The published projection must never carry upstream free text.

Every `_OBSERVED` message below is a real shape taken from the 575 runs already in the public
bucket (hostnames and ids replaced with stand-ins here). They are the regression set: each one is
what the taxonomy was built from, so a refactor that silently drops a bucket fails here.
"""

import json

import pytest

from autobench import public_errors as pe

# (message, expected category) -- the 18 shapes the survey found, plus the credential-bearing ones.
_OBSERVED: list[tuple[str, str]] = [
    ("A2A task ended in state 'failed': Error: timed out", "timed_out"),
    (
        "HTTP Error 502: Failed to fetch agent card from "
        "https://exgentic-a2a-tool-calling-gsm8k-team1.apps.example.com/.well-known/agent-card.json "
        "Server error '502 Bad Gateway' for url '...'",
        "agent_card_unreachable",
    ),
    (
        "A2A task ended in state 'failed': Error: Model openai/azure/gpt-x is not accessible: "
        "TimeoutError()",
        "model_probe_failed",
    ),
    (
        "MCP session to https://exgentic-mcp-tau2-team1.apps.example.com/mcp was poisoned by an "
        "earlier stuck call (tool pod unreachable); failing fast",
        "mcp_session_poisoned",
    ),
    (
        'A2A task ended in state \'failed\': Error: litellm.BadRequestError: OpenAIException - '
        '"auto" tool choice requires at least one tool',
        "tool_choice_unsupported",
    ),
    (
        "A2A task ended in state 'failed': Error: No action with is_message=True found in "
        "self._all_actions",
        "agent_defect",
    ),
    (
        "A2A task ended in state 'failed': Error: Model openai/azure/gpt-x is not accessible: "
        "litellm.AuthenticationError: AuthenticationError: OpenAIException",
        "model_auth_failed",
    ),
    ("A2A task ended in state 'failed': Error: cannot pickle '_asyncio.Task' object", "agent_defect"),
    (
        "A2A task ended in state 'failed': Error: 'dict' object has no attribute 'model_dump'",
        "agent_defect",
    ),
    (
        'A2A task ended in state \'failed\': Error executing message: Failed to run mcp call'
        '{ "error": "Failed to execute action: timed out" }',
        "mcp_call_failed",
    ),
    ("run exceeded timeout of 600s (MCP/agent unreachable?)", "run_timeout"),
    (
        "run was interrupted before completing (3 task(s) recorded before it stopped; partial "
        "results retained)",
        "run_interrupted",
    ),
    ("Session terminated", "session_terminated"),
    (
        "MCP connect to https://exgentic-mcp-gsm8k-team1.apps.example.com/mcp timed out after 30s "
        "(tool pod still warming or unreachable?)",
        "mcp_connect_timeout",
    ),
    ("MCP tool error: 'list_tasks'", "mcp_tool_error"),
    ("per-task timeout after 600s", "task_timeout"),
    ("httpx.RemoteProtocolError: peer closed connection without sending complete message", "transport_gateway"),
    ("Error executing submit: answer does not match", "wrong_answer"),
    ("agent returned missing assistant content", "agent_defect"),
    (
        "Model endpoint https://gw.example.com/v1/models is unreachable",
        "model_probe_failed",
    ),
    # The three the survey found that quote gateway internals -- the reason this module exists.
    (
        "litellm.RateLimitError: Budget has been exceeded! Current cost: 30001.65, "
        "Max budget: 30000.0",
        "budget_exceeded",
    ),
    (
        "litellm.BadRequestError: team not allowed to access model. Team id=3f2504e0-4f89-11d3-"
        "9a0c-0305e82c3301 allowed models=['a', 'b']",
        "model_not_granted",
    ),
    ("openai.RateLimitError: HTTP 429 Too Many Requests", "rate_limited"),
]


@pytest.mark.parametrize("message,expected", _OBSERVED)
def test_observed_messages_classify(message, expected):
    assert pe.classify(message) == expected


def test_no_message_is_none():
    for blank in (None, "", "   ", "\n"):
        assert pe.classify(blank) is None
        assert pe.public_error(blank) is None


def test_every_category_is_declared():
    """`classify` may only ever return a value from the published closed set."""
    for message, _ in _OBSERVED:
        assert pe.classify(message) in pe.CATEGORIES
    assert pe.classify("something nobody has seen before") == "other"
    assert "other" in pe.CATEGORIES


@pytest.mark.parametrize(
    "secret",
    [
        "ete-litellm.ai-models.example.com",   # the gateway hostname
        "exgentic-mcp-tau2-team1.apps.example.com",    # a workload route
        "mlflow.svc.cluster.local",                    # a cluster-internal name
        "30001.65",                                    # the org's spend
        "3f2504e0-4f89-11d3-9a0c-0305e82c3301",        # a team UUID
        "sk-abcdef1234567890",                         # a key, should one ever appear
        "someone@example.com",
    ],
)
def test_published_value_never_contains_the_sensitive_token(secret):
    """The whole point: whatever upstream says, none of it reaches the published string."""
    message = f"litellm error talking to {secret} while running the task"
    published = pe.public_error(message)
    assert published is not None
    assert secret not in published
    # Nor any recognisable fragment of it: the value is category + hash, nothing else.
    category, _, rest = published.partition(" ")
    assert category in pe.CATEGORIES
    assert rest.startswith("(shape ") and rest.endswith(")")


def test_shape_is_stable_across_instances_but_not_reversible():
    """Same template, different hostnames/ids/numbers => same shape id."""
    a = "MCP connect to https://mcp-a.example.com/mcp timed out after 30s"
    b = "MCP connect to https://mcp-b.other.example.org/mcp timed out after 600s"
    assert pe.shape_id(a) == pe.shape_id(b)
    # A different template must differ, or the id would not group anything.
    assert pe.shape_id(a) != pe.shape_id("Session terminated")
    # Hashing the NORMALIZED form is what stops a guessed hostname being confirmable.
    assert pe.shape_id(a) != pe.shape_id(a.replace("timed out after 30s", "timed out after 30 s"))


def test_scrub_run_summary_leaves_the_caller_untouched():
    """The authenticated API serves the same dict's source object -- it must keep the verbatim text."""
    verbatim = "Budget has been exceeded! Current cost: 30001.65, Max budget: 30000.0"
    summary = {
        "run_id": "r1",
        "status": "failed",
        "error": verbatim,
        "results": [
            {"task_id": "t0", "passed": False, "error": verbatim},
            {"task_id": "t1", "passed": True, "error": None},
        ],
    }
    out = pe.scrub_run_summary(summary)

    assert summary["error"] == verbatim                      # original untouched
    assert summary["results"][0]["error"] == verbatim        # nested original untouched
    assert out["error"].startswith("budget_exceeded (shape ")
    assert out["results"][0]["error"].startswith("budget_exceeded (shape ")
    assert out["results"][1]["error"] is None
    assert out["run_id"] == "r1" and out["results"][1]["passed"] is True
    assert "30001.65" not in json.dumps(out)


def test_scrub_records_covers_status_message_and_keeps_a_string():
    rows = [
        {"task_id": "t0", "status_message": "MCP tool error: 'list_tasks'", "llm_count": 3},
        {"task_id": "t1", "status_message": "", "llm_count": 4},
        {"task_id": "t2", "llm_count": 5},
    ]
    out = pe.scrub_records(rows)
    assert rows[0]["status_message"] == "MCP tool error: 'list_tasks'"   # original untouched
    assert out[0]["status_message"].startswith("mcp_tool_error (shape ")
    # Never None: the Parquet schema is inferred from these rows, so the column must stay a string.
    assert out[1]["status_message"] == ""
    assert "status_message" not in out[2]
    assert out[0]["llm_count"] == 3


def test_specific_patterns_outrank_the_generic_timeout():
    """`timed out` appears inside several specific messages; the specific bucket must win."""
    assert pe.classify("per-task timeout after 600s") == "task_timeout"
    assert pe.classify("run exceeded timeout of 600s") == "run_timeout"
    assert pe.classify("MCP connect to https://x.example.com/mcp timed out after 30s") == (
        "mcp_connect_timeout"
    )
    assert pe.classify("A2A task ended in state 'failed': Error: timed out") == "timed_out"


def test_budget_outranks_rate_limited():
    """litellm raises a budget failure AS a RateLimitError, so order decides which bucket wins."""
    assert pe.classify("litellm.RateLimitError: Budget has been exceeded! Current cost: 1.0") == (
        "budget_exceeded"
    )


def test_unclassified_is_logged_with_the_verbatim_text(caplog):
    """`other` says little, so the pod log must carry what to add a bucket for."""
    with caplog.at_level("WARNING", logger="autobench.public_errors"):
        published = pe.public_error("a brand new upstream failure nobody has bucketed")
    assert published.startswith("other (shape ")
    assert "add a bucket" in caplog.text
    assert "a brand new upstream failure" in caplog.text  # verbatim, to the pod log only
