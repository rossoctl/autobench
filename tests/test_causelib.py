"""Covers `reference/causelib.py`, which is not part of the shipped package.

It is tested here anyway because its output is *published*: it decides the per-cause tables in the
landmark reports under `docs/results/`. Two regressions would be silent and wrong rather than loud —
a `public_errors` category with no label here (a raw slug in a published table), and a drift in the
legacy patterns (a pre-scrub run, such as the archived landmarks, re-generating differently than it
published).
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "reference"))

import causelib as CL  # noqa: E402

from autobench import public_errors as pe  # noqa: E402


def test_every_emittable_category_has_a_label():
    """The cross-module invariant: a new bucket in the Service needs a label here, or tables break."""
    missing = sorted(set(pe.CATEGORIES) - set(CL._SLUG_LABELS))
    assert not missing, f"public_errors can emit {missing} but causelib has no label for them"


def test_no_scrubbed_value_ever_renders_as_a_raw_slug():
    for category in pe.CATEGORIES:
        label = CL.cause(f"{category} (shape deadbeef)")
        assert label == CL._SLUG_LABELS[category]
        assert label != category or category == "other", f"{category} rendered as its raw slug"


def test_labels_lists_every_label_cause_can_return():
    expected = {label for label, _ in CL._LEGACY} | set(CL._SLUG_LABELS.values())
    assert set(CL.LABELS) == expected and len(CL.LABELS) == len(expected)
    assert CL.LABELS[-1] == "other"


def test_the_comparison_generator_explains_every_label():
    """Its cause table looks each label up in a `_meaning` dict, so a missing entry is a KeyError
    the first time a matrix produces that cause — the generator is a script, so this reads its text."""
    src = (pathlib.Path(__file__).resolve().parent.parent / "reference/gen-12run-comparison.py").read_text()
    missing = [label for label in CL.LABELS if f'"{label}":' not in src]
    assert not missing, f"gen-12run-comparison.py has no _meaning entry for {missing}"


@pytest.mark.parametrize(
    "verbatim,expected",
    [
        ("Model endpoint https://gw.example.com/v1/models is unreachable", "health probe (Bug 3)"),
        ("peer closed connection without sending complete message", "transport / gateway"),
        ("Server error '503 Bad Gateway'", "transport / gateway"),
        ("per-task timeout after 600s", "per-task timeout"),
        ("missing assistant content", "agent (upstream defect)"),
        ("Error executing submit", "wrong answer"),
        ("answer does not match", "wrong answer"),
        # Added after 80df31d: each was `other`, or (the judge) misfiled as a wrong answer.
        ("Error executing submit: llmclient: HTTP 502: {\"error\":\"upstream: URLError\"}",
         "IBAC judge call failed"),
        ("A2A task ended in state 'failed': Error: cannot pickle '_asyncio.Task' object",
         "agent (upstream defect)"),
        ("A2A task ended in state 'failed': Error: timed out", "task timed out (unattributed)"),
        # Legacy has no entitlement bucket; it must still say `other`, as the published reports do.
        ("Budget has been exceeded! Current cost: 30001.65", "other"),
    ],
)
def test_legacy_classification(verbatim, expected):
    assert CL.cause(verbatim) == expected


@pytest.mark.parametrize("verbatim", [
    "Error executing submit: llmclient: HTTP 404: {\"detail\":\"Not Found\"}",
    "A2A task ended in state 'failed': Error: cannot pickle '_asyncio.Task' object",
    "A2A task ended in state 'failed': Error: timed out",
])
def test_the_added_legacy_entries_agree_with_the_scrubbed_era(verbatim):
    assert CL.cause(verbatim) == CL.cause(pe.public_error(verbatim))


def test_scrubbed_classification_is_finer_than_legacy():
    """The same failure, both eras: legacy could only say `other`, scrubbed names it."""
    assert CL.cause("Budget has been exceeded! Current cost: 30001.65") == "other"
    assert CL.cause(pe.public_error("Budget has been exceeded! Current cost: 30001.65")) == (
        "gateway budget / entitlement"
    )


def test_is_probe_failure_in_both_eras():
    legacy = "Model endpoint https://gw.example.com/v1/models"
    assert CL.is_probe_failure(legacy)
    assert CL.is_probe_failure(pe.public_error(legacy))
    assert not CL.is_probe_failure("Session terminated")
    assert not CL.is_probe_failure(pe.public_error("Session terminated"))
    assert not CL.is_probe_failure(None)


def test_shape_round_trips_and_is_none_for_legacy():
    scrubbed = pe.public_error("MCP tool error: 'list_tasks'")
    assert CL.shape(scrubbed) == pe.shape_id("MCP tool error: 'list_tasks'")
    assert CL.shape("MCP tool error: 'list_tasks'") is None


def test_cause_tolerates_missing_and_empty():
    for blank in (None, "", "   "):
        assert CL.cause(blank) == "other"
        assert CL.shape(blank) is None


def test_a_shape_published_as_other_is_relabelled_by_its_bucket():
    """`_OTHER_SHAPES` re-files `other` rows an older image published; each entry must name a real
    bucket, and the current classifier must put that shape's message in the same bucket."""
    msg = "A2A task ended in state 'failed': Error: limit_reached (max_actions): steps=16/100, actions=109/100"
    assert pe.public_error(msg) == "action_limit (shape 03deb26f)"
    assert CL.cause("other (shape 03deb26f)") == CL.cause(pe.public_error(msg)) == "agent action/step limit"
    assert CL.cause("other (shape deadbeef)") == "other"
    assert set(CL._OTHER_SHAPES.values()) <= set(pe.CATEGORIES)
