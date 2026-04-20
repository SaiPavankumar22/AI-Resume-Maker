# AI Resume Builder — FastAPI Backend

A production-ready FastAPI backend that generates ATS-friendly LaTeX resumes using an LLM, compiles them to PDF, and serves them via REST API.

---

## Stack

| Layer | Technology |
|-------|-----------|
| API | FastAPI + Uvicorn |
| Database | MongoDB (PyMongo) |
| LaTeX compiler | pdflatex (TeX Live / MiKTeX) |
| LLM | Nebius Token Factory (OpenAI-compatible) |

---

## Project Structure

```
resume_builder/
├── main.py                # FastAPI app, CORS, static files
├── db.py                  # MongoDB connection helpers
├── latex.py               # LaTeX compilation + sanitisation
├── llm.py                 # Nebius LLM integration
├── seed.py                # Seed default template into DB
├── requirements.txt
├── .env.example
├── routes/
│   ├── __init__.py
│   ├── templates.py       # GET/POST /templates
│   └── resumes.py         # POST /generate, GET /preview, GET /download
├── static/                # Uploaded template preview images
├── templates_storage/     # (reserved for raw template files)
└── generated_pdfs/        # UUID-named folders with .tex + .pdf
```

---

## Prerequisites

1. **Python 3.10+**
2. **MongoDB** running on `localhost:27017` (MongoDB Compass connects to same URI)
3. **pdflatex** — install TeX Live:
   ```bash
   # Ubuntu / Debian
   sudo apt-get install texlive-latex-base texlive-fonts-recommended texlive-latex-extra

   # macOS
   brew install --cask mactex

   # Windows
   # Install MiKTeX from https://miktex.org/
   ```
4. **Nebius API Key** — sign up at https://tokenfactory.nebius.com

---

## Setup

```bash
# 1. Clone / enter project directory
cd resume_builder

# 2. Create and activate virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env: set NEBIUS_API_KEY and MONGO_URI

export NEBIUS_API_KEY="your_key_here"
export MONGO_URI="mongodb://localhost:27017"
export DB_NAME="resume_builder"

# 5. Seed default template
python seed.py

# 6. Run the server
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Interactive docs: http://localhost:8000/docs

---

## API Reference

### Templates

#### `GET /templates`
Returns all templates.
```json
[
  {
    "id": "...",
    "name": "Default Professional",
    "preview_image": "/static/abc.png",
    "latex_code": "\\documentclass{article}..."
  }
]
```

#### `POST /templates/upload`
Upload a new template (multipart/form-data).

| Field | Type | Required |
|-------|------|----------|
| `name` | string | ✅ |
| `latex_code` | string | ✅ |
| `preview_image` | file (jpg/png) | ❌ |

#### `GET /templates/{id}`
Fetch a single template.

#### `DELETE /templates/{id}`
Delete a template.

---

### Resumes

#### `POST /generate`
Generate a resume.

```json
{
  "template_id": "6761abc123...",
  "user_data": {
    "name": "Jane Doe",
    "email": "jane@example.com",
    "phone": "+1 555 123 4567",
    "location": "San Francisco, CA",
    "linkedin": "linkedin.com/in/janedoe",
    "summary": "Senior software engineer with 8 years experience...",
    "experience": [
      {
        "title": "Senior Software Engineer",
        "company": "Acme Corp",
        "start": "Jan 2020",
        "end": "Present",
        "bullets": ["Led migration to microservices", "Reduced latency by 40%"]
      }
    ],
    "education": [
      {
        "degree": "B.Sc. Computer Science",
        "school": "MIT",
        "year": "2016"
      }
    ],
    "skills": ["Python", "FastAPI", "Kubernetes", "PostgreSQL"]
  },
  "chat_instruction": "Make it concise and focused on backend engineering roles."
}
```

Response:
```json
{
  "resume_id": "...",
  "latex_code": "\\documentclass...",
  "pdf_url": "/download/...",
  "compilation_success": true
}
```

> If compilation fails, `pdf_url` is `null` and `log` contains the pdflatex error output. The `latex_code` is always returned so you can debug or retry.

#### `GET /preview/{resume_id}`
Stream the PDF inline (for embedding in `<iframe>`).

#### `GET /download/{resume_id}`
Download the PDF as an attachment.

#### `GET /resumes`
List all generated resumes (most recent first). Query params: `limit`, `skip`.

#### `GET /resumes/{resume_id}`
Fetch a single resume record.

---

## Security

- `pdflatex` runs with `-no-shell-escape` to prevent code execution
- Compilation is killed after **15 seconds**
- LaTeX special characters (`&`, `%`, `$`, `#`, `_`, etc.) are escaped when inserting raw user strings
- Image uploads are type-checked (jpeg/png/webp/gif only)

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `NEBIUS_API_KEY` | — | **Required.** Nebius Token Factory API key |
| `MONGO_URI` | `mongodb://localhost:27017` | MongoDB connection string |
| `DB_NAME` | `resume_builder` | MongoDB database name |

---

## MongoDB Collections

### `templates`
```json
{
  "_id": ObjectId,
  "name": "string",
  "preview_image_path": "/static/...",
  "latex_code": "\\documentclass..."
}
```

### `resumes`
```json
{
  "_id": ObjectId,
  "template_id": "string",
  "user_data": {},
  "chat_instruction": "string",
  "latex_code": "string",
  "pdf_path": "generated_pdfs/<uuid>/resume.pdf",
  "compilation_success": true,
  "compile_log": "string",
  "created_at": "ISODate"
}
```
