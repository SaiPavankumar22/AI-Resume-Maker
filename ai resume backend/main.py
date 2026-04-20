import logging
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables BEFORE importing routes
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from routes.templates import router as templates_router
from routes.resumes import router as resumes_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

os.makedirs("static", exist_ok=True)
os.makedirs("templates_storage", exist_ok=True)
os.makedirs("generated_pdfs", exist_ok=True)

# Resolve the frontend directory (sibling of this backend folder)
_BACKEND_DIR  = Path(__file__).parent
_FRONTEND_DIR = _BACKEND_DIR.parent / "ai resume-frontend"

app = FastAPI(
    title="AI Resume Builder API",
    description="Backend API for AI-powered resume generation with LaTeX compilation",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API static assets (uploaded images, etc.) ──────────────────────────────
app.mount("/static", StaticFiles(directory="static"), name="static")

# ── API routes (must come BEFORE the catch-all frontend mount) ─────────────
app.include_router(templates_router)
app.include_router(resumes_router)


# ── Convenience HTML routes ───────────────────────────────────────────────
@app.get("/", response_class=FileResponse)
async def serve_index():
    """Serve the template-picker landing page."""
    return FileResponse(str(_FRONTEND_DIR / "index.html"))


@app.get("/editor", response_class=FileResponse)
async def serve_editor():
    """Serve the resume editor page."""
    return FileResponse(str(_FRONTEND_DIR / "editor.html"))


# ── Serve remaining frontend assets (CSS, JS, images) from the frontend dir ─
if _FRONTEND_DIR.is_dir():
    app.mount("/app", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")


@app.get("/health")
async def health():
    return {"status": "ok"}
