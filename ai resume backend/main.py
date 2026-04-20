import logging
import os
from dotenv import load_dotenv

# Load environment variables BEFORE importing routes
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(templates_router)
app.include_router(resumes_router)


@app.get("/")
async def root():
    return {
        "message": "AI Resume Builder API",
        "version": "1.0.0",
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    return {"status": "ok"}
