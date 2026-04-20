import logging
import os
import shutil
import uuid
from typing import List, Optional

from bson import ObjectId
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from db import get_templates_collection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/templates", tags=["templates"])

STATIC_DIR = "static"
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class TemplateOut(BaseModel):
    id: str
    name: str
    preview_image: Optional[str]
    latex_code: str

    class Config:
        populate_by_name = True


def _doc_to_out(doc: dict) -> TemplateOut:
    return TemplateOut(
        id=str(doc["_id"]),
        name=doc.get("name", ""),
        preview_image=doc.get("preview_image_path"),
        latex_code=doc.get("latex_code", ""),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("", response_model=List[TemplateOut])
async def list_templates():
    """Return all templates."""
    col = get_templates_collection()
    docs = list(col.find({}))
    return [_doc_to_out(d) for d in docs]


@router.get("/{template_id}", response_model=TemplateOut)
async def get_template(template_id: str):
    """Return a single template by ID."""
    col = get_templates_collection()
    try:
        oid = ObjectId(template_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid template ID")
    doc = col.find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Template not found")
    return _doc_to_out(doc)


@router.post("/upload", response_model=TemplateOut, status_code=201)
async def upload_template(
    name: str = Form(...),
    latex_code: str = Form(...),
    preview_image: Optional[UploadFile] = File(None),
):
    """
    Upload a new template.

    Accepts multipart/form-data with:
    - name (str)
    - latex_code (str)
    - preview_image (file, optional)
    """
    image_path: Optional[str] = None

    if preview_image is not None:
        if preview_image.content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported image type: {preview_image.content_type}",
            )
        ext = os.path.splitext(preview_image.filename or "image.png")[1] or ".png"
        filename = f"{uuid.uuid4()}{ext}"
        dest = os.path.join(STATIC_DIR, filename)
        os.makedirs(STATIC_DIR, exist_ok=True)
        try:
            with open(dest, "wb") as f:
                shutil.copyfileobj(preview_image.file, f)
        except Exception as e:
            logger.error("Failed to save preview image: %s", e)
            raise HTTPException(status_code=500, detail="Failed to save preview image")
        image_path = f"/static/{filename}"
        logger.info("Saved preview image to %s", dest)

    col = get_templates_collection()
    doc = {
        "name": name,
        "latex_code": latex_code,
        "preview_image_path": image_path,
    }
    result = col.insert_one(doc)
    doc["_id"] = result.inserted_id
    logger.info("Inserted template %s with id %s", name, result.inserted_id)
    return _doc_to_out(doc)


@router.delete("/{template_id}", status_code=204)
async def delete_template(template_id: str):
    """Delete a template by ID."""
    col = get_templates_collection()
    try:
        oid = ObjectId(template_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid template ID")
    result = col.delete_one({"_id": oid})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Template not found")
