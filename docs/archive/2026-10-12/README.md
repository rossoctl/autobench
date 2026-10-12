# Archive — AutoBench documentation as of 2026-10-12, before v1.36

A frozen snapshot, kept for reference only. **Read the current documents in [`docs/`](../../)
instead**; nothing here is maintained.

It holds the documents whose figures were measured on **AutoBench v1.35** (the 12-run pair of
2026-10-04, OpenShift ykt3 and KinD), as they stood just before the v1.36 pair of 2026-10-12
replaced those figures:

| here | what it was |
|---|---|
| `DEVELOPER_GUIDE.md` / `.pdf` | the Developer Guide, with its v1.35 cost and latency figures |
| `BENCHMARKS_PRIMER.md` | the primer, with its figures measured on v1.35 |
| `AutoBench.pptx` / `.pdf` | the deck, on v1.35 numbers. Built by `docs/deck/make_autobench_pptx.py` at commit `e6bbe74`: check that commit out to rebuild it, because running the current generator writes the current deck |
| `img/` | the cost charts and `takeaways.json` those documents embed |
| `results/v1.35-2026-10-04/` | the v1.35 12-run reports (OpenShift, KinD, comparison), published until 2026-10-12, plus copies of the v1.35 plugin-study reports so this snapshot's links resolve |

**The v1.35 plugin study is not retired.** It is still the published AuthBridge study, in
[`docs/results/v1.35-2026-10-04/`](../../results/v1.35-2026-10-04/), and
[`PLUGIN_OVERHEAD.md`](../../PLUGIN_OVERHEAD.md) still rests on it. Only the 12-run figures moved on.

The documents link to each other with relative paths, so links that point inside this folder still
resolve. Links to the repository's code, scripts and other docs may not: they name files as they
were then.
