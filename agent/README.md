---
title: Notebook Observatory Agent
emoji: 🔭
colorFrom: blue
colorTo: green
sdk: gradio
sdk_version: 4.44.0
app_file: app.py
pinned: false
license: mit
short_description: Ask questions about the computational-notebook ecosystem
---

# Ask the Notebook Observatory

A small, data-grounded Q&A agent for the
[Notebook Observatory](https://github.com/rodrigosf672/notebook-observatory) — an
automated daily census of public computational notebooks on GitHub.

It runs **Qwen2.5-0.5B-Instruct** (Apache-2.0, ungated) on the free CPU tier and
answers questions grounded strictly in a compact snapshot of the observatory's
real datasets (`observatory_context.json`): latest-day library adoption and
adoption trends across creation-year cohorts (2014–2026).

The model is intentionally small so it runs for free on CPU; all the quantitative
grounding is injected into the system prompt from real data, so the model phrases
and lightly reasons rather than recalling facts it never saw. It is a research
demo, not an authoritative source — verify against the
[datasets](https://github.com/rodrigosf672/notebook-observatory/tree/main/datasets).

## Updating the grounding data

`observatory_context.json` is regenerated from the live datasets by
`build_context.py` in the main repository's `agent/` directory. Re-run it and
push the refreshed JSON to keep the agent current.
