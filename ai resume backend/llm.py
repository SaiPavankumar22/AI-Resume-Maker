import json
import logging
import os
import re as _re
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


# ---------------------------------------------------------------------------
# System prompts — two distinct modes
# ---------------------------------------------------------------------------

# Used for FRESH GENERATION (fill a template with user data)
_SYSTEM_GENERATE = """\
You are a professional resume writer and LaTeX expert.

Your ONLY job is to fill in the provided LaTeX TEMPLATE with the user's personal data.

CRITICAL RULES — read every point before writing a single character:
1. OUTPUT ONLY RAW LATEX CODE. No explanations, no markdown, no ```latex fences.
2. PRESERVE THE TEMPLATE EXACTLY:
   - Keep the EXACT \\documentclass, ALL \\usepackage lines, ALL custom commands (\\newcommand, \\renewcommand, \\def), and ALL \\begin{document}…\\end{document} structure.
   - Do NOT add, remove, or reorder any packages.
   - Do NOT remove any custom \\newcommand or environment definitions.
   - Do NOT switch to a simpler template. Copy the template structure character-for-character.
3. ONLY REPLACE placeholder text with the user's actual data (names, dates, descriptions, skills, etc.).
4. WHERE THE TEMPLATE HAS PLACEHOLDER SECTIONS the user did not fill, leave them blank or remove only the empty placeholder lines — do NOT invent fake data.
5. ESCAPE special characters that appear in plain text: & → \\&, % → \\%, $ → \\$, # → \\#, _ → \\_, ~ → \\textasciitilde{}, ^ → \\textasciicircum{}.
6. NEVER use internal LaTeX @-macros (\\check@nocorr@, \\@ne, etc.) outside \\makeatletter blocks — these cause fatal compile errors.
7. Do NOT use \\write18, \\shell, or any shell-escape commands.
8. The output must compile with pdflatex or tectonic without errors.\
"""

# Used for CHAT / SURGICAL EDITS (modify existing generated LaTeX)
_SYSTEM_EDIT = """\
You are a LaTeX expert making a TARGETED EDIT to an existing resume.

CRITICAL RULES:
1. OUTPUT ONLY THE COMPLETE, MODIFIED LATEX FILE. No explanations, no markdown fences.
2. Make ONLY the change described in the instruction. Change nothing else.
3. PRESERVE everything not mentioned: \\documentclass, \\usepackage, custom commands, formatting, all other sections, whitespace style.
4. If adding new content, insert it in the most logical place without disturbing surrounding code.
5. ESCAPE special characters in plain text: & → \\&, % → \\%, $ → \\$, # → \\#, _ → \\_.
6. NEVER use internal LaTeX @-macros (\\check@nocorr@, \\@ne, etc.) outside \\makeatletter blocks.
7. Return the FULL file — the user needs the complete LaTeX to recompile.\
"""


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def _build_generate_prompt(template_latex: str, user_data: dict) -> str:
    """Prompt for filling a fresh template with user data."""
    user_data_str = json.dumps(user_data, indent=2, ensure_ascii=False)
    return "\n".join([
        "=== LATEX TEMPLATE (preserve this structure exactly) ===",
        template_latex.strip(),
        "",
        "=== USER DATA (replace template placeholders with this) ===",
        user_data_str,
        "",
        "Fill in the template above with the user data. Keep every \\usepackage,",
        "every custom command, and the full document structure intact.",
        "Only replace placeholder text. Output ONLY the final LaTeX code.",
    ])


def _build_edit_prompt(current_latex: str, instruction: str, user_data: dict) -> str:
    """Prompt for a surgical chat edit on already-generated LaTeX."""
    user_data_str = json.dumps(user_data, indent=2, ensure_ascii=False)
    return "\n".join([
        "=== CURRENT RESUME LATEX (your starting point) ===",
        current_latex.strip(),
        "",
        "=== USER PROFILE DATA (for reference if you need to add/update content) ===",
        user_data_str,
        "",
        "=== INSTRUCTION (apply ONLY this change) ===",
        instruction.strip(),
        "",
        "Apply the instruction above to the LaTeX. Change ONLY what the instruction describes.",
        "Return the COMPLETE modified LaTeX file. Output ONLY the LaTeX code.",
    ])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_latex(
    template_latex: str,
    user_data: dict,
    chat_instruction: str = "",
    current_latex_code: str = "",
) -> str:
    """
    Call the Nebius LLM to produce LaTeX for the resume.

    Mode selection:
    - If ``chat_instruction`` is non-empty AND ``current_latex_code`` is non-empty
      → SURGICAL EDIT mode: minimally modify the existing code per the instruction.
    - Otherwise
      → FRESH GENERATE mode: fill the template with user_data from scratch.

    Returns the raw LaTeX string.
    Raises on API errors.
    """
    has_instruction = bool(chat_instruction and chat_instruction.strip())
    has_current = bool(current_latex_code and current_latex_code.strip())

    if has_instruction and has_current:
        # ── Surgical edit: chatbot changes an existing resume ─────────────
        mode = "surgical-edit"
        system_prompt = _SYSTEM_EDIT
        user_prompt = _build_edit_prompt(current_latex_code, chat_instruction, user_data)
    else:
        # ── Fresh generate: fill the template with user data ──────────────
        mode = "fresh-generate"
        system_prompt = _SYSTEM_GENERATE
        user_prompt = _build_generate_prompt(template_latex, user_data)

    logger.info(
        "Calling Nebius LLM [mode=%s] with model %s", mode, NEBIUS_MODEL
    )

    response = _get_client().chat.completions.create(
        model=NEBIUS_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,   # lower temp = more faithful to instructions
        max_tokens=4096,
    )

    latex_output = response.choices[0].message.content.strip()

    # Strip any accidental markdown fences the model may add
    if latex_output.startswith("```"):
        lines = latex_output.splitlines()
        lines = [l for l in lines if not l.startswith("```")]
        latex_output = "\n".join(lines).strip()

    latex_output = _sanitize_llm_latex(latex_output)

    logger.info(
        "LLM [mode=%s] returned %d characters of LaTeX", mode, len(latex_output)
    )
    return latex_output


# ---------------------------------------------------------------------------
# Post-processing sanitizer
# ---------------------------------------------------------------------------

# Matches internal LaTeX @-macros like \check@nocorr@, \@ne, \@tempa, \@@, etc.
# These are forbidden in normal document mode and will crash tectonic/pdflatex.
_AT_MACRO_RE = _re.compile(r'\\[A-Za-z@]*@[A-Za-z@]*')


def _sanitize_llm_latex(latex_code: str) -> str:
    """
    Remove or neutralise common LLM mistakes that cause fatal compile errors:
    1. Internal @-macros (\\check@nocorr@, \\@ne, etc.) — removed from body only.
    2. Bare \\nocorr / \\nocorrlist commands outside their proper context.
    Preamble is left untouched so \\makeatletter blocks work correctly.
    """
    parts = latex_code.split("\\begin{document}", 1)
    if len(parts) == 2:
        preamble, body = parts
        body = _AT_MACRO_RE.sub("", body)
        body = _re.sub(r'(?<!%)\\nocorrlist\b', "", body)
        body = _re.sub(r'(?<!%)\\nocorr\b', "", body)
        return preamble + "\\begin{document}" + body
    # No \begin{document} — sanitise entire string (fragment)
    return _AT_MACRO_RE.sub("", latex_code)
