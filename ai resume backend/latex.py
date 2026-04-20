"""
latex.py  —  LaTeX → PDF compilation for AI Resume Builder

PDF engine priority (highest quality first):
  1. pdflatex   — system PATH (TeX Live / MiKTeX)
  2. tectonic   — system PATH, OR auto-downloaded binary (~30 MB, cached)
                  Self-contained TeX engine; auto-fetches required packages.
                  Works on Windows/Linux/Mac with NO system TeX install required.
  3. xhtml2pdf  — pip-only fallback (pandoc → HTML → reportlab)
                  Produces a readable PDF but cannot replicate LaTeX typography.

Auto-download behaviour:
  On first compile, if neither pdflatex nor tectonic is on PATH, the service
  downloads the tectonic binary for the current platform from GitHub releases
  and caches it at ~/.cache/resumebuilder/tectonic[.exe].
  Subsequent compiles reuse the cached binary instantly.
"""

import io
import logging
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

GENERATED_PDFS_DIR = "generated_pdfs"
COMPILE_TIMEOUT = 120   # seconds (tectonic first-run downloads packages)

# ── Python 3.8 compat fix ─────────────────────────────────────────────────────
import hashlib
if sys.version_info < (3, 9):
    _orig_md5 = hashlib.md5
    def _md5_compat(*args, **kwargs):
        kwargs.pop("usedforsecurity", None)
        return _orig_md5(*args, **kwargs)
    hashlib.md5 = _md5_compat  # type: ignore[assignment]


# ── Tectonic auto-download config ─────────────────────────────────────────────
_TECTONIC_VERSION = "0.15.0"
_TECTONIC_CACHE   = Path.home() / ".cache" / "resumebuilder"

_TECTONIC_URLS = {
    "windows-x86_64": (
        f"https://github.com/tectonic-typesetting/tectonic/releases/download/"
        f"tectonic%40{_TECTONIC_VERSION}/"
        f"tectonic-{_TECTONIC_VERSION}-x86_64-pc-windows-msvc.zip",
        "zip", "tectonic.exe",
    ),
    "linux-x86_64": (
        f"https://github.com/tectonic-typesetting/tectonic/releases/download/"
        f"tectonic%40{_TECTONIC_VERSION}/"
        f"tectonic-{_TECTONIC_VERSION}-x86_64-unknown-linux-musl.tar.gz",
        "tar.gz", "tectonic",
    ),
    "linux-aarch64": (
        f"https://github.com/tectonic-typesetting/tectonic/releases/download/"
        f"tectonic%40{_TECTONIC_VERSION}/"
        f"tectonic-{_TECTONIC_VERSION}-aarch64-unknown-linux-musl.tar.gz",
        "tar.gz", "tectonic",
    ),
    "darwin-x86_64": (
        f"https://github.com/tectonic-typesetting/tectonic/releases/download/"
        f"tectonic%40{_TECTONIC_VERSION}/"
        f"tectonic-{_TECTONIC_VERSION}-x86_64-apple-darwin.tar.gz",
        "tar.gz", "tectonic",
    ),
    "darwin-arm64": (
        f"https://github.com/tectonic-typesetting/tectonic/releases/download/"
        f"tectonic%40{_TECTONIC_VERSION}/"
        f"tectonic-{_TECTONIC_VERSION}-aarch64-apple-darwin.tar.gz",
        "tar.gz", "tectonic",
    ),
}


def _platform_key() -> str:
    sys_name = platform.system().lower()
    machine  = platform.machine().lower()
    if sys_name == "windows":
        return "windows-x86_64"
    if sys_name == "darwin":
        return "darwin-arm64" if machine in ("arm64", "aarch64") else "darwin-x86_64"
    return "linux-aarch64" if machine in ("aarch64", "arm64") else "linux-x86_64"


def _get_tectonic() -> Optional[str]:
    """
    Return path to a tectonic binary.
    Order: system PATH → local cache → auto-download.
    Returns None if download fails or platform unsupported.
    """
    found = shutil.which("tectonic")
    if found:
        return found

    exe_name = "tectonic.exe" if platform.system() == "Windows" else "tectonic"
    cached   = _TECTONIC_CACHE / exe_name
    if cached.exists():
        logger.info("Using cached tectonic: %s", cached)
        return str(cached)

    key = _platform_key()
    if key not in _TECTONIC_URLS:
        logger.warning("No tectonic release for platform %s", key)
        return None

    url, fmt, binary_name = _TECTONIC_URLS[key]
    logger.info("Downloading tectonic %s for %s …", _TECTONIC_VERSION, key)
    _TECTONIC_CACHE.mkdir(parents=True, exist_ok=True)

    try:
        archive = _TECTONIC_CACHE / f"tectonic_download.{fmt}"
        urllib.request.urlretrieve(url, archive)

        if fmt == "zip":
            with zipfile.ZipFile(archive) as zf:
                zf.extract(binary_name, _TECTONIC_CACHE)
        else:
            with tarfile.open(archive, "r:gz") as tf:
                member = tf.getmember(binary_name)
                tf.extract(member, _TECTONIC_CACHE)

        archive.unlink(missing_ok=True)

        binary_path = _TECTONIC_CACHE / binary_name
        if platform.system() != "Windows":
            binary_path.chmod(binary_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        logger.info("Tectonic downloaded and cached at %s", binary_path)
        return str(binary_path)

    except Exception as exc:
        logger.warning("Tectonic download failed: %s", exc)
        for p in (_TECTONIC_CACHE / f"tectonic_download.{fmt}", _TECTONIC_CACHE / binary_name):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
        return None


# ── xhtml2pdf fallback CSS — resume-appropriate styling ───────────────────────
_RESUME_CSS = """
<style>
@page {
  size: Letter portrait;
  margin: 54pt 54pt 54pt 54pt;
}
body {
  font-family: Helvetica;
  font-size: 10pt;
  line-height: 14pt;
  color: #000000;
}
h1 {
  font-size: 18pt; font-weight: bold; line-height: 22pt;
  text-align: center; margin-top: 0pt; margin-bottom: 2pt;
}
h2 {
  font-size: 11pt; font-weight: bold; line-height: 14pt;
  border-bottom: 0.5pt solid #000000;
  margin-top: 10pt; margin-bottom: 3pt;
  padding-bottom: 1pt;
  text-transform: uppercase;
}
h3 {
  font-size: 10pt; font-weight: bold; line-height: 13pt;
  margin-top: 6pt; margin-bottom: 1pt;
}
h4, h5, h6 { font-size: 10pt; font-weight: bold; margin-top: 4pt; margin-bottom: 1pt; }
p  { font-size: 10pt; line-height: 14pt; margin-top: 2pt; margin-bottom: 2pt; }
p.contact { text-align: center; font-size: 9.5pt; margin: 1pt 0; }
ul { font-size: 10pt; line-height: 13pt; margin: 2pt 0; padding-left: 14pt; }
li { margin: 1pt 0; }
table { width: 100%; border-collapse: collapse; font-size: 10pt; }
td { padding: 1pt 3pt; vertical-align: top; }
</style>
"""


# ---------------------------------------------------------------------------
# Sanitisation helpers
# ---------------------------------------------------------------------------

_LATEX_SPECIAL = {
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
    "\\": r"\textbackslash{}",
}

_LATEX_SPECIAL_RE = re.compile(
    "(" + "|".join(re.escape(k) for k in _LATEX_SPECIAL.keys()) + ")"
)

# Harshibar-style templates: space after `\titleformat` and `{\titlerule[2pt]}` inside the
# optional argument break titlesec on some engines (e.g. tectonic). Normalize to a single-line form.
_TITLEFORMAT_HARSHIBAR_RE = re.compile(
    r"\\titleformat\s+\{\\section\}\s*\{\s*"
    r"\\bfseries\s*\\vspace\{2pt\}\s*\\raggedright\s*\\large\s*(?:%[^\n]*)?"
    r"\s*\}\s*\{\}\{0em\}\{\}\s*\[\\color\{light-grey\}\s*\{\\titlerule\[2pt\]\}\s*"
    r"\\vspace\{-4pt\}\]",
    re.MULTILINE,
)
def _fix_harshibar_titleformat(latex_code: str) -> str:
    def _repl(_: re.Match) -> str:
        # Use ordinary strings — raw strings still treat \\v and \\t as special escapes.
        # Do not use \\titlerule[2pt] inside titlesec's trailing [...] — the ] closes the
        # outer optional argument early and breaks the preamble (tectonic: Missing \\begin{document}).
        return (
            "\\titleformat{\\section}{\\bfseries\\vspace{2pt}\\raggedright\\large}"
            "{}{0em}{}[\\color{light-grey}\\rule{\\linewidth}{2pt}\\vspace{-4pt}]"
        )

    return _TITLEFORMAT_HARSHIBAR_RE.sub(_repl, latex_code, count=1)


# Same nested-bracket bug if a prior pass left \\titlerule[2pt] inside titlesec's [...].
def _fix_titleformat_titlerule_brackets(latex_code: str) -> str:
    old = "[\\color{light-grey}\\titlerule[2pt]\\vspace{-4pt}]"
    new = "[\\color{light-grey}\\rule{\\linewidth}{2pt}\\vspace{-4pt}]"
    if old in latex_code:
        return latex_code.replace(old, new, 1)
    return latex_code


# Harshibar template sets body text colour in the preamble. That breaks some engines
# (tectonic) with "Missing \\begin{document}"; defer to start of document instead.
_PREAMBLE_BODY_COLOR_RE = re.compile(
    r"(?m)^\\color\{text-grey\}\s*$",
)


def _fix_preamble_body_color(latex_code: str) -> str:
    return _PREAMBLE_BODY_COLOR_RE.sub(
        lambda _m: "\\AtBeginDocument{\\color{text-grey}}",
        latex_code,
        count=1,
    )


# Harshibar heading: "\\ \vspace{-3pt}" before \end{center} is invalid in vertical mode on
# stricter engines (tectonic): "There's no line here to end."
_CENTER_TRAILING_BREAK_RE = re.compile(
    r"(\n\s*)\\\\\s*\\vspace\{-3pt\}(\s*\n\s*\\end\{center\})",
    re.MULTILINE,
)


def _fix_center_trailing_linebreak(latex_code: str) -> str:
    def _repl(m: re.Match) -> str:
        return m.group(1) + "\\vspace{-3pt}" + m.group(2)

    return _CENTER_TRAILING_BREAK_RE.sub(_repl, latex_code, count=1)


_EMPTY_SUBHEADING_LIST_RE = re.compile(
    r"(?ms)^[ \t]*\\section\s*\{[^}]+\}\s*"
    r"\\resumeSubHeadingListStart\s*\\resumeSubHeadingListEnd\s*"
)


def _remove_empty_resume_sections(latex_code: str) -> str:
    return _EMPTY_SUBHEADING_LIST_RE.sub("", latex_code)


def _escape_plaintext_ampersands_in_body(latex_code: str) -> str:
    parts = latex_code.split("\\begin{document}", 1)
    if len(parts) != 2:
        return latex_code

    preamble, body = parts
    body = re.sub(r'(?<!\\)&', r'\\&', body)
    return preamble + "\\begin{document}" + body


_AT_MACRO_BODY_RE = re.compile(r'\\[A-Za-z@]*@[A-Za-z@]*')


def _remove_at_macros_from_body(latex_code: str) -> str:
    """
    Strip internal LaTeX @-macros (e.g. \\check@nocorr@) from the document *body*.
    These are forbidden outside \\makeatletter…\\makeatother and cause a fatal
    'Forbidden control sequence' error in tectonic and pdflatex.
    Preamble is left untouched (\\makeatletter is legitimate there).
    """
    parts = latex_code.split("\\begin{document}", 1)
    if len(parts) == 2:
        preamble, body = parts
        body = _AT_MACRO_BODY_RE.sub("", body)
        body = re.sub(r'(?<!%)\\nocorrlist\b', "", body)
        body = re.sub(r'(?<!%)\\nocorr\b', "", body)
        return preamble + "\\begin{document}" + body
    # No \\begin{document} — sanitise whole string (fragment / plain body)
    return _AT_MACRO_BODY_RE.sub("", latex_code)


def _find_pdflatex() -> Optional[str]:
    """
    Resolve pdflatex executable. shutil.which misses MiKTeX when the server is started
    from an environment without TeX on PATH (common with IDE / GUI launches on Windows).
    """
    w = shutil.which("pdflatex")
    if w:
        return w
    if platform.system() != "Windows":
        return None
    local = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    # Prefer shallow paths first (fast), then search the whole MiKTeX tree (portable / non-standard layouts).
    shallow = [
        Path(local) / "Programs" / "MiKTeX" / "miktex" / "bin" / "x64" / "pdflatex.exe",
        Path(local) / "Programs" / "MiKTeX" / "miktex" / "bin" / "pdflatex.exe",
        Path(program_files) / "MiKTeX" / "miktex" / "bin" / "x64" / "pdflatex.exe",
        Path(program_files) / "MiKTeX" / "miktex" / "bin" / "pdflatex.exe",
    ]
    for p in shallow:
        if p.is_file():
            logger.info("Found pdflatex outside PATH: %s", p)
            return str(p)
    for base in (
        Path(local) / "Programs" / "MiKTeX",
        Path(program_files) / "MiKTeX",
    ):
        if base.is_dir():
            for p in base.rglob("pdflatex.exe"):
                logger.info("Found pdflatex via MiKTeX search: %s", p)
                return str(p)
    return None


def _ensure_windows_fontconfig() -> Optional[str]:
    """
    Tectonic's Windows build uses fontconfig; without a config file it logs
    'Cannot load default config file: No such file: (null)' and font lookups fail.
    """
    if platform.system() != "Windows":
        return None
    fc_path = _TECTONIC_CACHE / "fonts.conf"
    if not fc_path.exists():
        _TECTONIC_CACHE.mkdir(parents=True, exist_ok=True)
        cache_sub = _TECTONIC_CACHE / "fontconfig-cache"
        cache_sub.mkdir(parents=True, exist_ok=True)
        win_fonts = str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts").replace(
            "\\", "/"
        )
        fc_path.write_text(
            f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <dir>{win_fonts}</dir>
  <cachedir>{str(cache_sub).replace(chr(92), "/")}</cachedir>
</fontconfig>
""",
            encoding="utf-8",
        )
    return str(fc_path)


def sanitize_latex(text: str) -> str:
    """Escape LaTeX special characters in plain-text user data."""
    if not isinstance(text, str):
        text = str(text)
    return _LATEX_SPECIAL_RE.sub(lambda m: _LATEX_SPECIAL[m.group()], text)


# ---------------------------------------------------------------------------
# Internal compile helpers
# ---------------------------------------------------------------------------

def _compile_pdflatex(tex_file: Path, job_dir: Path, pdflatex_exe: str) -> None:
    """Run pdflatex twice (two passes for cross-references). Raises on failure."""
    cmd = [
        pdflatex_exe,
        "-interaction=nonstopmode",
        "-no-shell-escape",
        "-output-directory", str(job_dir),
        str(tex_file),
    ]
    log = ""
    for _ in range(2):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=COMPILE_TIMEOUT)
        log = r.stdout + "\n" + r.stderr
    pdf = job_dir / "resume.pdf"
    if not pdf.exists():
        raise RuntimeError(f"pdflatex failed.\n\nLog:\n{log[-3000:]}")


def _compile_tectonic(tex_file: Path, job_dir: Path, tectonic_bin: str) -> None:
    """Run tectonic (auto-downloads missing LaTeX packages on first use)."""
    cache_dir = _TECTONIC_CACHE / "pkg_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "TECTONIC_CACHE_DIR": str(cache_dir)}
    fc = _ensure_windows_fontconfig()
    if fc:
        env["FONTCONFIG_FILE"] = fc
    r = subprocess.run(
        [tectonic_bin, "--outdir", str(job_dir), str(tex_file)],
        capture_output=True,
        timeout=300,   # first run downloads packages — allow 5 min
        cwd=str(job_dir),
        env=env,
    )
    pdf = job_dir / "resume.pdf"
    if not pdf.exists():
        stderr = r.stderr.decode("utf-8", errors="replace") if isinstance(r.stderr, bytes) else r.stderr
        raise RuntimeError(f"tectonic failed.\n\nError:\n{stderr[-3000:]}")


def _compile_xhtml2pdf(latex_code: str, job_dir: Path) -> None:
    """Fallback: pandoc converts LaTeX → HTML, then xhtml2pdf → PDF."""
    import pypandoc
    from xhtml2pdf import pisa

    tex_file  = job_dir / "resume_fallback.tex"
    html_file = job_dir / "resume_fallback.html"
    tex_file.write_text(latex_code, encoding="utf-8")

    try:
        pypandoc.convert_file(
            str(tex_file), "html5",
            outputfile=str(html_file),
            extra_args=["--mathml", "--wrap=none", "--syntax-highlighting=none"],
        )
    except Exception as exc:
        raise RuntimeError(f"pandoc LaTeX→HTML failed: {exc}") from exc

    if not html_file.exists():
        raise RuntimeError("pandoc produced no HTML output.")

    body = html_file.read_text(encoding="utf-8")
    wrapped = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" '
        '"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
        '<meta http-equiv="Content-Type" content="text/html; charset=UTF-8"/>'
        f"{_RESUME_CSS}</head><body>{body}</body></html>"
    )

    pdf_path = job_dir / "resume.pdf"
    with open(pdf_path, "wb") as f:
        result = pisa.CreatePDF(src=wrapped, dest=f, encoding="utf-8")
    if result.err:
        logger.warning("xhtml2pdf: %d warning(s)", result.err)

    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        raise RuntimeError("xhtml2pdf produced an empty PDF.")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compile_latex(latex_code: str) -> dict:
    """
    Compile *latex_code* to PDF using the best available engine.

    Engine priority:
      1. pdflatex  (system PATH)
      2. tectonic  (system PATH, or auto-downloaded — no MiKTeX/TeX Live needed)
      3. xhtml2pdf (pure-Python fallback — layout fidelity is limited)

    Returns:
        {
            "success": bool,
            "pdf_path": str | None,   # absolute path to generated resume.pdf
            "job_id":   str,
            "log":      str,
            "engine":   str,          # "pdflatex" | "tectonic" | "xhtml2pdf" | "none"
        }
    """
    job_id  = str(uuid.uuid4())
    job_dir = (Path(GENERATED_PDFS_DIR) / job_id).resolve()
    job_dir.mkdir(parents=True, exist_ok=True)

    tex_file = job_dir / "resume.tex"
    # Normalise newlines (some clients send legacy \\r\\r\\n, which doubles lines in TeX)
    latex_code = latex_code.replace("\r\n", "\n").replace("\r", "\n")
    latex_code = _fix_harshibar_titleformat(latex_code)
    latex_code = _fix_titleformat_titlerule_brackets(latex_code)
    latex_code = _fix_preamble_body_color(latex_code)
    latex_code = _fix_center_trailing_linebreak(latex_code)
    latex_code = _remove_empty_resume_sections(latex_code)
    latex_code = _escape_plaintext_ampersands_in_body(latex_code)
    latex_code = _remove_at_macros_from_body(latex_code)
    tex_file.write_text(latex_code, encoding="utf-8")

    # ── 1. pdflatex ────────────────────────────────────────────────────────
    pdflatex_exe = _find_pdflatex()
    if pdflatex_exe:
        logger.info("PDF engine: pdflatex (%s) for job %s", pdflatex_exe, job_id)
        try:
            _compile_pdflatex(tex_file, job_dir, pdflatex_exe)
            _cleanup_aux(job_dir)
            return {
                "success": True,
                "pdf_path": str(job_dir / "resume.pdf"),
                "job_id": job_id,
                "log": "Compiled with pdflatex.",
                "engine": "pdflatex",
            }
        except subprocess.TimeoutExpired:
            logger.error("pdflatex timed out for job %s", job_id)
            return _fail(job_id, "pdflatex timed out after 120 s.", "pdflatex")
        except Exception as exc:
            logger.warning("pdflatex failed for job %s: %s", job_id, exc)
            # Don't return yet — try tectonic next

    # ── 2. tectonic ────────────────────────────────────────────────────────
    tectonic_bin = _get_tectonic()
    if tectonic_bin:
        logger.info("PDF engine: tectonic (%s) for job %s", tectonic_bin, job_id)
        try:
            _compile_tectonic(tex_file, job_dir, tectonic_bin)
            _cleanup_aux(job_dir)
            return {
                "success": True,
                "pdf_path": str(job_dir / "resume.pdf"),
                "job_id": job_id,
                "log": "Compiled with tectonic (auto-downloaded engine).",
                "engine": "tectonic",
            }
        except subprocess.TimeoutExpired:
            logger.error("tectonic timed out for job %s", job_id)
            return _fail(job_id, "tectonic timed out after 300 s.", "tectonic")
        except Exception as exc:
            logger.warning("tectonic failed for job %s: %s", job_id, exc)

    # ── 3. xhtml2pdf fallback ──────────────────────────────────────────────
    logger.warning(
        "No real TeX engine found. Falling back to xhtml2pdf — "
        "LaTeX template formatting will NOT be fully preserved. "
        "Install tectonic or pdflatex for correct output."
    )
    try:
        _compile_xhtml2pdf(latex_code, job_dir)
        _cleanup_aux(job_dir)
        return {
            "success": True,
            "pdf_path": str(job_dir / "resume.pdf"),
            "job_id": job_id,
            "log": "Compiled with xhtml2pdf fallback (limited formatting).",
            "engine": "xhtml2pdf",
        }
    except ImportError:
        msg = (
            "No PDF engine available. Install pdflatex (TeX Live / MiKTeX) "
            "OR let the server auto-download tectonic on first compile. "
            "pip install xhtml2pdf pypandoc pypandoc-binary for a basic fallback."
        )
        logger.error(msg)
        return _fail(job_id, msg, "none")
    except Exception as exc:
        logger.error("xhtml2pdf fallback failed for job %s: %s", job_id, exc)
        return _fail(job_id, str(exc), "xhtml2pdf")


def _fail(job_id: str, log: str, engine: str) -> dict:
    return {"success": False, "pdf_path": None, "job_id": job_id, "log": log, "engine": engine}


def _cleanup_aux(job_dir: Path) -> None:
    """Remove auxiliary LaTeX files but keep .tex and .pdf."""
    for ext in (".aux", ".log", ".out", ".toc", ".fls", ".fdb_latexmk", ".synctex.gz"):
        for f in job_dir.glob(f"*{ext}"):
            try:
                f.unlink()
            except OSError:
                pass
