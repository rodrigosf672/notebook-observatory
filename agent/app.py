"""Ask-the-Observatory: a small, data-grounded Q&A agent.

A Gradio chat app for a Hugging Face Space (free CPU tier). It loads a compact
JSON snapshot of the Notebook Observatory datasets and a small open-weight
instruction model (Qwen2.5-0.5B-Instruct), then answers questions about the
computational-notebook ecosystem grounded strictly in that snapshot.

The model is deliberately small so it runs on the free CPU tier. All the
quantitative grounding lives in the system prompt built from real data, so the
model's job is phrasing and light reasoning, not recall of facts it never saw.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

MODEL_ID = os.environ.get("NBOBS_AGENT_MODEL", "Qwen/Qwen2.5-0.5B-Instruct")
CONTEXT_PATH = Path(__file__).parent / "observatory_context.json"
MAX_NEW_TOKENS = 384

# --------------------------------------------------------------------------- #
# Grounding context
# --------------------------------------------------------------------------- #


def _load_context() -> dict:
    try:
        return json.loads(CONTEXT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _format_context(ctx: dict) -> str:
    """Render the dataset snapshot as compact, model-readable facts."""
    if not ctx:
        return "No observatory data is currently loaded."
    lines: list[str] = []
    d = ctx.get("daily_census", {})
    if d:
        lines.append(
            f"LATEST DAILY CENSUS ({d.get('latest_date')}): "
            f"{d.get('notebooks_collected')} notebooks sampled, "
            f"{d.get('notebooks_parsed')} parsed successfully, "
            f"across {d.get('n_daily_snapshots')} daily snapshot(s) to date."
        )
        lines.append("Most-used libraries in the latest daily sample (% of parsed notebooks):")
        for r in d.get("top_libraries", [])[:15]:
            lines.append(
                f"  - {r['library']} ({r['category']}): "
                f"{r['adoption_pct']:.1f}% ({r['notebook_count']} notebooks)"
            )
        struct = d.get("structure", {})
        if struct:
            lines.append("Structure of the latest daily sample (means / percentages):")
            _labels = {
                "mean_total_cells": "avg cells/notebook",
                "mean_code_cells": "avg code cells",
                "mean_markdown_cells": "avg markdown cells",
                "mean_total_lines": "avg lines of code",
                "mean_outputs": "avg outputs",
                "pct_with_output": "% with saved output",
                "pct_executed_in_order": "% executed top-to-bottom",
                "pct_with_widgets": "% using interactive widgets",
                "pct_with_python_version": "% declaring a Python version",
            }
            for k, label in _labels.items():
                v = struct.get(k)
                if v is not None:
                    suffix = "%" if k.startswith("pct_") else ""
                    lines.append(f"  - {label}: {v:g}{suffix}")
        q = d.get("quality_indices_mean", {})
        if q:
            lines.append(
                "Quality indices (0-1 means; higher = more of that property): "
                + ", ".join(f"{k.replace('_', ' ')} {v:.2f}" for k, v in q.items() if v is not None)
            )
        pv = d.get("python_version_share_pct", {})
        if pv:
            lines.append(
                "Python-version share (% of notebooks declaring one): "
                + ", ".join(f"{ver}: {val:g}%" for ver, val in sorted(pv.items()) if val)
            )
    c = ctx.get("cohorts", {})
    if c:
        lines.append("")
        lines.append(
            f"HISTORICAL CREATION-YEAR COHORTS ({c.get('span')}): "
            f"{c.get('n_cohorts')} cohorts, {c.get('notebooks_per_cohort')} notebooks each. "
            "Each cohort samples notebooks whose repository was CREATED that year, "
            "measured as they exist today (a creation cohort, not a historical "
            "snapshot; subject to survivorship bias)."
        )
        lines.append("Library adoption (% of parsed notebooks) by notebook creation year:")
        summaries = c.get("library_trend_summaries", {})
        if summaries:
            lines.append(
                "Library trend summaries (prefer these exact statements when asked "
                "how a library's adoption changed over time):"
            )
            for lib, summary in summaries.items():
                lines.append(f"  - {summary}")
        lines.append("Full adoption series by creation year (for detailed lookups):")
        by_year = c.get("library_adoption_pct_by_creation_year", {})
        for lib, series in by_year.items():
            pts = ", ".join(
                f"{y}:{v:.0f}%" for y, v in sorted(series.items()) if v is not None
            )
            lines.append(f"  - {lib}: {pts}")
        sq = c.get("structure_and_quality_by_creation_year", {})
        if sq:
            lines.append("Notebook structure & quality by creation year:")
            for metric, series in sq.items():
                pts = ", ".join(
                    f"{y}:{v:g}" for y, v in sorted(series.items()) if v is not None
                )
                lines.append(f"  - {metric.replace('_', ' ')}: {pts}")
    return "\n".join(lines)


CONTEXT = _load_context()
CONTEXT_TEXT = _format_context(CONTEXT)

SYSTEM_PROMPT = (
    "You are the Notebook Observatory assistant. You answer questions about the "
    "public computational-notebook (Jupyter) ecosystem using ONLY the dataset "
    "facts provided below. These come from the Notebook Observatory, an automated "
    "daily census of public notebooks on GitHub.\n\n"
    "Rules:\n"
    "- Ground every quantitative claim in the facts below. Quote the actual "
    "percentages and years.\n"
    "- If a question cannot be answered from these facts, say so plainly and "
    "point the user to the datasets, rather than inventing numbers.\n"
    "- Note the survivorship-bias caveat when discussing historical cohort trends.\n"
    "- Be concise and specific.\n\n"
    "=== OBSERVATORY DATA ===\n"
    f"{CONTEXT_TEXT}\n"
    "=== END DATA ==="
)

# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #

_MODEL = None
_TOKENIZER = None


def _get_model():
    global _MODEL, _TOKENIZER
    if _MODEL is None:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        _TOKENIZER = AutoTokenizer.from_pretrained(MODEL_ID)
        _MODEL = AutoModelForCausalLM.from_pretrained(
            MODEL_ID, torch_dtype=torch.float32
        )
        _MODEL.eval()
    return _MODEL, _TOKENIZER


def respond(message: str, history: list[dict]) -> str:
    """Generate a grounded reply to the user's message."""
    if not message or not message.strip():
        return "Ask me something about the notebook ecosystem — e.g. which libraries dominate, or how PyTorch adoption changed by creation year."
    model, tok = _get_model()
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    for turn in history[-6:]:
        role = turn.get("role")
        content = turn.get("content")
        if role in ("user", "assistant") and content:
            msgs.append({"role": role, "content": content})
    msgs.append({"role": "user", "content": message.strip()})

    # apply_chat_template may return a bare tensor or a BatchEncoding dict
    # depending on the transformers version; normalise to input_ids + mask.
    import torch

    enc = tok.apply_chat_template(
        msgs,
        add_generation_prompt=True,
        return_tensors="pt",
        return_dict=True,
    )
    input_ids = enc["input_ids"]
    attention_mask = enc.get("attention_mask")
    prompt_len = input_ids.shape[1]

    with torch.no_grad():
        # Greedy decoding: for factual Q&A over the grounding numbers, sampling
        # introduces avoidable numeric errors. Deterministic is more faithful.
        out = model.generate(
            input_ids=input_ids,
            attention_mask=attention_mask,
            max_new_tokens=MAX_NEW_TOKENS,
            do_sample=False,
            repetition_penalty=1.15,
            pad_token_id=tok.eos_token_id,
        )
    text = tok.decode(out[0][prompt_len:], skip_special_tokens=True)
    return text.strip()


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #

EXAMPLES = [
    "Which libraries are most used in notebooks right now?",
    "How has PyTorch adoption changed across notebook creation years?",
    "When did the transformers library first appear, and how fast is it growing?",
    "Compare TensorFlow and PyTorch adoption over time.",
    "What does the dataset NOT cover?",
]


def build_demo():  # type: ignore[no-untyped-def]
    """Construct the Gradio UI. Imported lazily so prompt logic stays testable."""
    import gradio as gr

    with gr.Blocks(title="Ask the Notebook Observatory", theme=gr.themes.Soft()) as demo:
        gr.Markdown(
            "## 🔭 Ask the Notebook Observatory\n"
            "A small open-weight model (Qwen2.5-0.5B-Instruct) grounded in real "
            "[Notebook Observatory](https://github.com/rodrigosf672/notebook-observatory) "
            "data. Ask about library adoption, creation-year trends, or the dataset's scope. "
            "Runs on free CPU — replies take a few seconds."
        )
        gr.ChatInterface(
            fn=respond,
            type="messages",
            examples=EXAMPLES,
            cache_examples=False,
        )
    return demo


if __name__ == "__main__":
    build_demo().queue(max_size=16).launch(show_error=True)
