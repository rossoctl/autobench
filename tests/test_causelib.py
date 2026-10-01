"""Covers `reference/causelib.py`, which is not part of the shipped package.

It is tested here anyway because its output is *published*: it decides the per-cause tables in the
landmark reports under `docs/results/`. Two regressions would be silent and wrong rather than loud —
a `public_errors` category with no label here (a raw slug in a published table), and a drift in the
legacy patterns (a pre-scrub landmark run re-generating differently than it published).
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
        # Legacy had no bucket for these; it must still say `other`, as the published reports do.
        ("Budget has been exceeded! Current cost: 30001.65", "other"),
        ("A2A task ended in state 'failed': Error: timed out", "other"),
    ],
)
def test_legacy_classification_is_unchanged(verbatim, expected):
    assert CL.cause(verbatim) == expected


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
