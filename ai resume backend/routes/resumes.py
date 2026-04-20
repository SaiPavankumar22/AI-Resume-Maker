import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from bson import ObjectId
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from db import get_resumes_collection, get_templates_collection
from latex import compile_latex
from llm import generate_latex


class CompileRequest(BaseModel):
    latex_code: str

logger = logging.getLogger(__name__)

router = APIRouter(tags=["resumes"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    template_id: str
    user_data: Dict[str, Any]
    chat_instruction: Optional[str] = ""
    latex_code: Optional[str] = ""


class GenerateResponse(BaseModel):
    resume_id: str
    latex_code: str
    pdf_url: Optional[str]
    compilation_success: bool
    log: Optional[str] = None
    engine: Optional[str] = None   # "pdflatex" | "tectonic" | "xhtml2pdf" | "none"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_template_or_404(template_id: str) -> dict:
    col = get_templates_collection()
    logger.info("Looking up template with ID: %s (type: %s)", template_id, type(template_id))
    try:
        oid = ObjectId(template_id)
    except Exception as e:
        logger.error("Failed to parse template_id as ObjectId: %s", e)
        raise HTTPException(status_code=400, detail=f"Invalid template_id: {template_id}")
    doc = col.find_one({"_id": oid})
    if not doc:
        logger.warning("Template not found for ObjectId: %s", oid)
        raise HTTPException(status_code=404, detail="Template not found")
    return doc


def _get_resume_or_404(resume_id: str) -> dict:
    col = get_resumes_collection()
    try:
        oid = ObjectId(resume_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid resume ID")
    doc = col.find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Resume not found")
    return doc


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/generate", response_model=GenerateResponse, status_code=200)
async def generate_resume(req: GenerateRequest):
    """
    1. Fetch template from DB
    2. Call LLM to fill template with user data
    3. Compile LaTeX → PDF
    4. Store result in DB
    5. Return latex_code + pdf_url
    """
    logger.info("Generate request received with template_id: %s", req.template_id)
    
    # 1. Fetch template
    template = _get_template_or_404(req.template_id)
    logger.info("Generating resume from template '%s'", template.get("name"))

    # 2. LLM
    try:
        latex_code = generate_latex(
            template_latex=template["latex_code"],
            user_data=req.user_data,
            chat_instruction=req.chat_instruction or "",
            current_latex_code=req.latex_code or "",
        )
    except Exception as e:
        logger.error("LLM error: %s", e)
        raise HTTPException(status_code=502, detail=f"LLM error: {e}")

    # 3. Compile
    compile_result = compile_latex(latex_code)
    pdf_path = compile_result.get("pdf_path")
    compilation_success = compile_result["success"]

    if not compilation_success:
        logger.warning(
            "LaTeX compilation failed (job %s). Returning latex anyway.",
            compile_result["job_id"],
        )

    # 4. Store in DB
    col = get_resumes_collection()
    doc = {
        "template_id": req.template_id,
        "user_data": req.user_data,
        "chat_instruction": req.chat_instruction,
        "latex_code": latex_code,
        "pdf_path": pdf_path,
        "compilation_success": compilation_success,
        "compile_log": compile_result.get("log", ""),
        "compile_engine": compile_result.get("engine"),
        "created_at": datetime.now(tz=timezone.utc),
    }
    result = col.insert_one(doc)
    resume_id = str(result.inserted_id)
    logger.info(
        "Stored resume %s (compilation=%s)", resume_id, compilation_success
    )

    pdf_url = f"/download/{resume_id}" if compilation_success else None

    return GenerateResponse(
        resume_id=resume_id,
        latex_code=latex_code,
        pdf_url=pdf_url,
        compilation_success=compilation_success,
        log=compile_result.get("log") if not compilation_success else None,
        engine=compile_result.get("engine"),
    )


@router.post("/compile")
async def compile_resume(req: CompileRequest):
    """
    Compile LaTeX code to PDF (for live preview).
    Used when user edits LaTeX or after LLM modifications.
    Does NOT store in DB.
    """
    try:
        compile_result = compile_latex(req.latex_code)
        pdf_path = compile_result.get("pdf_path")
        
        return {
            "success": compile_result["success"],
            "pdf_url": f"/preview_pdf/{compile_result['job_id']}" if compile_result["success"] else None,
            "job_id": compile_result["job_id"],
            "log": compile_result.get("log", ""),
            "engine": compile_result.get("engine"),
        }
    except Exception as e:
        logger.error("Compilation error: %s", e)
        return {
            "success": False,
            "pdf_url": None,
            "job_id": None,
            "log": str(e),
        }


@router.get("/preview_pdf/{job_id}")
async def preview_pdf(job_id: str):
    """Serve a compiled PDF from the temporary job directory."""
    import os
    from pathlib import Path
    
    pdf_path = Path("generated_pdfs") / job_id / "resume.pdf"
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF not found")
    
    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        headers={"Content-Disposition": "inline"},
    )


@router.get("/preview/{resume_id}")
async def preview_resume(resume_id: str):
    """Return the PDF inline (for browser preview)."""
    resume = _get_resume_or_404(resume_id)
    pdf_path = resume.get("pdf_path")
    if not pdf_path:
        raise HTTPException(
            status_code=404,
            detail="PDF not available. Compilation may have failed.",
        )
    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline"},
    )


@router.get("/download/{resume_id}")
async def download_resume(resume_id: str):
    """Return the PDF as a downloadable attachment."""
    resume = _get_resume_or_404(resume_id)
    pdf_path = resume.get("pdf_path")
    if not pdf_path:
        raise HTTPException(
            status_code=404,
            detail="PDF not available. Compilation may have failed.",
        )
    filename = f"resume_{resume_id}.pdf"
    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/resumes/{resume_id}")
async def get_resume(resume_id: str):
    """Fetch resume metadata by ID."""
    resume = _get_resume_or_404(resume_id)
    resume["_id"] = str(resume["_id"])
    resume["created_at"] = resume.get("created_at", "").isoformat() if resume.get("created_at") else None
    return resume


@router.get("/resumes")
async def list_resumes(limit: int = 20, skip: int = 0):
    """List all resumes (most recent first)."""
    col = get_resumes_collection()
    docs = list(
        col.find({}, {"compile_log": 0})
        .sort("created_at", -1)
        .skip(skip)
        .limit(limit)
    )
    for d in docs:
        d["_id"] = str(d["_id"])
        if d.get("created_at"):
            d["created_at"] = d["created_at"].isoformat()
    return docs
