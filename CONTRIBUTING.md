# Contributing

Contributions are welcome. The project is designed to be easy to extend.

## Development setup

```bash
pip install -e ".[dev]"
```

## Quality gate

All of these must pass (CI enforces them):

```bash
ruff check src tests          # lint
ruff format --check src tests # formatting
mypy src                      # strict typing
pytest -q                     # tests
```

## Common extensions

### Add a library to detection

Edit [`src/notebook_observatory/detection/libraries.yaml`](src/notebook_observatory/detection/libraries.yaml) —
add an entry with its import `modules`, a `category`, and optional `pypi` name.
No code change needed. Add a case to `tests/test_detection.py` if it has
multiple module names.

### Add or change a metric

Add a function in
[`analytics/metrics.py`](src/notebook_observatory/analytics/metrics.py) returning
a `[0, 1]` score, wire it into `NotebookMetrics` and `compute_metrics`, document
it in [`docs/METRICS.md`](docs/METRICS.md), and add a test. Because raw features
are stored, you can backfill snapshots with `nbobs aggregate --date <date>`
without re-collecting.

### Add a sampling stratum

Add a `SamplingStrategy` in
[`collectors/sampler.py`](src/notebook_observatory/collectors/sampler.py) and
include it in `build_plan`. Keep the plan deterministic in the run date.

## Guidelines

- Keep modules focused and typed; every public function has a docstring.
- Never break append-only storage semantics — history must remain immutable.
- Be a good API citizen: respect rate limits and the per-run budget.
