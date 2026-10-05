"""Covers `reference/shortlistlib.py`, which is not part of the shipped package.

It is tested here anyway because its output is *published*: it decides the tool-selection table in
the prose docs and in every 12-run report. The rule is structural — a chat followed by another
chat is a selection call — so anything that puts two chats back to back without being a selection
turn makes a sub-threshold benchmark look like it shortlists.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "reference"))

import shortlistlib as SL  # noqa: E402


def span(kind, t, **kw):
    return {"kind": kind, "start_time": f"2026-10-04T20:00:{t:02d}+00:00", **kw}


def test_selection_turn_pairs_with_its_assistant_call():
    spans = [span("chat", 1, input_tokens=12000), span("chat", 2, input_tokens=5000),
             span("tool", 3), span("chat", 4, input_tokens=5200)]
    assert [r for _, r in SL.roles(spans)] == ["sel", "as", "as"]


def test_a_failed_call_the_agent_retried_is_not_a_selection_call():
    """tau2 on v1.35: the gateway answered InternalServerError, the agent retried 0.5 s later.

    The failed span is followed by the retry, so without the exclusion it reads as "sel".
    """
    spans = [span("chat", 1, status_code="ERROR", input_tokens=None),
             span("chat", 2, status_code="OK", input_tokens=5054),
             span("tool", 3), span("chat", 4, status_code="OK", input_tokens=5137)]
    assert [r for _, r in SL.roles(spans)] == ["as", "as"]
    acc = SL.tally(spans)
    assert acc["sel"] == 0 and acc["asst"] == 2


def test_capability_probe_is_not_a_turn():
    spans = [span("chat", 1, request_max_tokens=1), span("chat", 2, input_tokens=320),
             span("tool", 3)]
    assert [r for _, r in SL.roles(spans)] == ["as"]
