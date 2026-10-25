# Decisions and honest deviations

- **Standard library core.** No third-party dependencies required. TabPFN, Ollama, Temporal, Sentry are optional and
  every fallback is labelled in output (`doctor`, receipts, benchmark mode line).
- **Dashboard uses `http.server`, not FastAPI**: no dependency, no build step, same offline guarantee.
- **TOML config (`slothwatch.toml`)** instead of YAML: `tomllib` is in the standard library.
- **Charts are SVG, not PNG**: no matplotlib dependency; SVGs embed in a DEV post directly.
- **Temporal granularity**: 3 activities (pipeline, notify, decision), not one per rung; the ladder is atomic and idempotent per
  window, which is what makes crash-resume safe. Per-rung timing is in the spans.
- **Temporal and Sentry code is written against their documented APIs but was NOT executed in the build environment**
  (no Temporal server / Sentry DSN available). Idempotent-resume is covered by a unit test (`test_idempotent_window...`),
  not by a real killed worker. Run `make doctor`, then the crash drill in the README, before claiming it.
- **TabPFN was not installed in the build environment.** The TabPFN code path (regressor quantiles for Rung 1, classifier for
  Rung 1.5) is unexecuted here; all published numbers use the `robust_mad` / `knn` fallbacks and say so. Check the TabPFN
  version's current API (`output_type="quantiles"`) and log any change here.
- **Tinker**: dataset generator and evaluation harness are real and tested (self-check = perfect score on reference answers).
  `slothwatch tune train` deliberately does NOT ship an unverified training loop. Check Tinker's supported models first.
- **Bug found and fixed while building**: fuzzy merchant matching merged `SOUTH WASTE` into `SOUTH WATER` (similarity 0.909),
  hiding two true anomalies in the simulation. Grouping is now token-prefix or >=0.95; regression test added.
- **Known weakness**: the robust fallback interval for strongly seasonal bills is wide (electricity 32.6–161.4 in the demo),
  so a 1.5x spike would slip through. TabPFN with month-of-year features should tighten it; measure that, don't assume it.
- **Receipt chain** makes casual edits detectable, not edits by someone who can rewrite everything; note the head hash elsewhere.
