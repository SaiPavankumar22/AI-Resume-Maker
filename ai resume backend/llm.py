import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

logger = logging.getLogger(__name__)

_client: OpenAI | None = None

load_dotenv(dotenv_path=Path(__file__).with_name(".env"))
NEBIUS_MODEL = os.environ.get("NEBIUS_MODEL", "google/gemma-3-27b-it")


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        key = os.environ.get("NEBIUS_API_KEY")
        if not key:
            raise RuntimeError(
                "NEBIUS_API_KEY is not set. Export it to use AI resume generation."
            )
        _client = OpenAI(
            base_url="https://api.tokenfactory.nebius.com/v1/",
            api_key=key,
        )
    return _client

SYSTEM_PROMPT = """You are a professional resume writer and LaTeX expert.

STRICT RULES:
- Output ONLY valid LaTeX code
- No explanations, no markdown fences, no preamble text
- Keep formatting clean and ATS-friendly
- Use simple packages only: geometry, fontenc, inputenc, hyperref, enumitem, titlesec, parskip
- Ensure it compiles without errors with pdflatex
- Do NOT use \\shell, \\write18, or any shell-escape commands
- NEVER use internal LaTeX macros that contain @ in their name (e.g. \\check@nocorr@, \\@, \\@ne, \\@tempa, etc.). These are internal TeX primitives and will cause a fatal compile error.
- Do NOT use \\nocorrlist, \\nocorr, or microtype internals in the document body.
- Special characters in plain text (& % $ # _ { } ~ ^) MUST be escaped with a backslash.
- Prefer \\textbf{}, \\textit{}, \\underline{} for emphasis. Never use @-based commands."""


def build_user_prompt(
    template_latex: str,
    user_data: dict,
    chat_instruction: str,
    current_latex_code: str = "",
) -> str:
    user_data_str = json.dumps(user_data, indent=2, ensure_ascii=False)
    parts = [
        "=== BASE TEMPLATE ===",
        template_latex,
        "",
        "=== USER DATA (JSON) ===",
        user_data_str,
    ]
    if current_latex_code and current_latex_code.strip():
        parts += [
            "",
            "=== CURRENT RESUME LATEX ===",
            current_latex_code.strip(),
        ]
    if chat_instruction and chat_instruction.strip():
        parts += ["", "=== ADDITIONAL INSTRUCTIONS ===", chat_instruction.strip()]
    if current_latex_code and current_latex_code.strip():
        parts += [
            "",
            "If CURRENT RESUME LATEX is provided, edit that existing resume instead of starting from scratch.",
            "Preserve the same template structure and only make the requested changes.",
        ]
    parts += [
        "",
        "Escape LaTeX-sensitive characters that appear in plain text, especially &, %, and _.",
        "Do not leave raw ampersands inside job titles, company names, project names, bullets, or skill text.",
        "Keep existing LaTeX macros intact and only escape plain-text content.",
        "",
        "Generate the final LaTeX resume. Output ONLY the LaTeX code, nothing else.",
    ]
    return "\n".join(parts)


def generate_latex(
    template_latex: str,
    user_data: dict,
    chat_instruction: str = "",
    current_latex_code: str = "",
) -> str:
    """
    Call the Nebius LLM to produce a filled-in LaTeX resume.

    Returns the raw LaTeX string.
    Raises on API errors.
    """
    user_prompt = build_user_prompt(
        template_latex,
        user_data,
        chat_instruction,
        current_latex_code,
    )

    logger.info("Calling Nebius LLM for resume generation with model %s", NEBIUS_MODEL)
    response = _get_client().chat.completions.create(
        model=NEBIUS_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
        max_tokens=4096,
    )

    latex_output = response.choices[0].message.content.strip()

    # Strip any accidental markdown fences
    if latex_output.startswith("```"):
        lines = latex_output.splitlines()
        # Remove first and last fence lines
        lines = [l for l in lines if not l.startswith("```")]
        latex_output = "\n".join(lines).strip()

    latex_output = _sanitize_llm_latex(latex_output)

    logger.info("LLM returned %d characters of LaTeX", len(latex_output))
    return latex_output


import re as _re

# Matches internal LaTeX @-macros like \check@nocorr@, \@ne, \@tempa, \@@, etc.
# These are forbidden in normal document mode and will crash tectonic/pdflatex.
_AT_MACRO_RE = _re.compile(r'\\[A-Za-z@]*@[A-Za-z@]*')


def _sanitize_llm_latex(latex_code: str) -> str:
    """
    Remove or neutralise common LLM mistakes that cause fatal compile errors:
    1. Internal @-macros (\\check@nocorr@, \\@ne, etc.) — replaced with nothing.
    2. Bare \\nocorr / \\nocorrlist commands outside their proper context.
    Returns cleaned LaTeX string.
    """
    # Split on \\begin{document} so we do NOT touch the preamble @ usage
    # (e.g. \\makeatletter in preamble is fine; @ in body is not).
    parts = latex_code.split("\\begin{document}", 1)
    if len(parts) == 2:
        preamble, body = parts
        # Remove forbidden @-macros only from the body
        cleaned_body = _AT_MACRO_RE.sub("", body)
        # Also strip stray \\nocorr / \\nocorrlist if not preceded by % (comment)
        cleaned_body = _re.sub(r'(?<!%)\\nocorrlist\b', "", cleaned_body)
        cleaned_body = _re.sub(r'(?<!%)\\nocorr\b', "", cleaned_body)
        latex_code = preamble + "\\begin{document}" + cleaned_body
    else:
        # No \\begin{document} — sanitise entire string (probably a fragment)
        latex_code = _AT_MACRO_RE.sub("", latex_code)

    return latex_code
