import json
import logging
import os

from openai import OpenAI

logger = logging.getLogger(__name__)

_client: OpenAI | None = None


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
- Do NOT use \\shell, \\write18, or any shell-escape commands"""


def build_user_prompt(template_latex: str, user_data: dict, chat_instruction: str) -> str:
    user_data_str = json.dumps(user_data, indent=2, ensure_ascii=False)
    parts = [
        "=== BASE TEMPLATE ===",
        template_latex,
        "",
        "=== USER DATA (JSON) ===",
        user_data_str,
    ]
    if chat_instruction and chat_instruction.strip():
        parts += ["", "=== ADDITIONAL INSTRUCTIONS ===", chat_instruction.strip()]
    parts += [
        "",
        "Generate the final LaTeX resume. Output ONLY the LaTeX code, nothing else.",
    ]
    return "\n".join(parts)


def generate_latex(template_latex: str, user_data: dict, chat_instruction: str = "") -> str:
    """
    Call the Nebius LLM to produce a filled-in LaTeX resume.

    Returns the raw LaTeX string.
    Raises on API errors.
    """
    user_prompt = build_user_prompt(template_latex, user_data, chat_instruction)

    logger.info("Calling Nebius LLM for resume generation")
    response = _get_client().chat.completions.create(
        model="google/gemma-2-9b-it-fast",
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

    logger.info("LLM returned %d characters of LaTeX", len(latex_output))
    return latex_output
