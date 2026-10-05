# Archive — AutoBench documentation as of 2026-10-04, before v1.35

A frozen snapshot, kept for reference only. **Read the current documents in [`docs/`](../../)
instead**; nothing here is maintained, and nothing here describes the Service as it is now.

What it covers that the current documents deliberately do not:

- **AutoBench v1.28 through v1.34.** The current documents and results describe v1.35 only.
- **The cross-cluster deployment**, with the Service on one OpenShift cluster driving workloads on
  another (ykt3 → ykt2). The current guides cover single-cluster deployments only: KinD, and
  OpenShift with the Service and its workloads on the same cluster.

| here | what it was |
|---|---|
| `DEVELOPER_GUIDE.md` / `.pdf`, `ADMIN_GUIDE.md` / `.pdf` | the guides, both deployment shapes |
| `BENCHMARKS_PRIMER.md` | the primer, with its figures measured on v1.28 |
| `PLUGIN_OVERHEAD.md` | the designed AuthBridge plugin study, v1.28 |
| `12_RUNS_CROSS_CLUSTER.md` / `.pdf` | the 12-run with the ykt3 Service driving ykt2 workloads (2026-08) |
| `AutoBench.pptx` / `.pdf`, `generate_pptx.py` | the deck and the script that built it, on v1.28 numbers |
| `results/v1.28-2026-09-15/`, `results/v1.33-2026-10-03/` | the landmark results published until 2026-10-04 |
| `img/` | the cost charts those documents embed |

The documents link to each other with relative paths, so links that point inside this folder
still resolve. Links to the repository's code, scripts and other docs may not: they name files as
they were then. `generate_pptx.py` is kept so the archived deck stays reproducible; running it writes
to the current `docs/AutoBench.pptx`, so don't.
