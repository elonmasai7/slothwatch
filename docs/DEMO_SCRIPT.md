# 90-second demo script

Use the generated fictional data only. Start with `make benchmark` once so the dashboard has the scenario comparison, then use `make demo` or open the dashboard in another terminal. A 90-second sequence:

| Time | Shot | Action / caption | Expected proof |
|---|---|---|---|
| 0–12 s | Today | Run `slothwatch-demo run --scenario clean-month`. Caption: “No surprises; nothing needs you.” | Exact quiet receipt line: `Nothing needs you. Go back to sleep. (276 rows checked, 0 surprises. Receipt #...)` (receipt number increments across local runs). |
| 12–28 s | Scenario injection | Run `slothwatch-demo run --scenario everything-wrong`. Caption: “Synthetic anomalies, no personal data.” | A doubled subscription, duplicate charge, electricity spike and creeping insurance rise are labelled as planted anomalies in demo output. |
| 28–48 s | Ladder | Open the Ladder tab and point from Rung 0 to forecast to learning to the explainer. | Per-run rung counts, Gemma/template wake-ups and human pings. Default mode explicitly says local template fallback; only select `--use-ollama` when a local model is running. |
| 48–65 s | Review queue | Show an `act` alert and its draft. Click Ignore or Snooze; use Approve only to demonstrate outbox copy. | Only an explicit human decision changes the status. Approved text is written to `.slothwatch/outbox/approved-<id>.txt`; nothing is sent. |
| 65–78 s | Receipts | Open Receipts, click verify, show the receipt hash. Run `slothwatch verify` in terminal. | Database chain and daily JSON mirrors validate. `slothwatch canary` gives the planted synthetic canary a visible catch result. |
| 78–90 s | Benchmark / score | Show the benchmark comparison and Sloth Score. | Report compares the ladder with a controlled all-row-explainer baseline. Tokens are explicitly estimated in template mode, not claimed as Gemma measurements. |

For a time-compressed diary run, use `slothwatch-demo run --scenario everything-wrong --fast --seconds 60`; it prints one line per simulated day. The tour command is `slothwatch-demo tour`. Exact timings vary by machine; this is a shot list, not an assertion of measured video duration.
