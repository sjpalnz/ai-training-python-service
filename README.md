# TruSource — AI Training Platform (Python Service)

This is the backend processing service for TruSource. It handles all the heavy-lifting tasks that require file generation, media processing, and document analysis — things that can't run in serverless edge functions.

## What This Service Does

| Capability | What It Produces |
|-----------|-----------------|
| **Document Processing** | Extracts text from PDFs, Word docs, PowerPoints, and other files |
| **Semantic Search** | Embeds document chunks and provides RAG-powered retrieval for Q&A |
| **Slide Decks** | Generates presentations via AI (primary engine + Claude fallback) |
| **PowerPoint** | Converts structured course outlines to themed .pptx files |
| **SCORM Packages** | Creates LMS-ready SCORM 1.2 zip files with interactive quizzes |
| **Voiceover Audio** | Text-to-speech via Deepgram (stock voices) or Qwen (cloned voices) |
| **Video** | Renders narrated video presentations from slides + audio |
| **Google Drive** | Connects, browses, and imports files from Google Drive |
| **LMS Integration** | Pushes SCORM packages to SCORM Cloud and Moodle |

## How It Fits Together

```
User (Browser)
    |
    v
Frontend (Vercel / Next.js)
    |
    +---> Supabase Edge Functions (AI calls, auth, credits)
    |
    +---> This Service (Railway / Flask)
              |
              +---> Document processing & embedding
              +---> Slide deck generation (NotebookLM or Claude)
              +---> PowerPoint & SCORM file generation
              +---> Audio generation (Deepgram / Qwen TTS)
              +---> Video rendering (FFmpeg)
              +---> Google Drive API
              +---> SCORM Cloud / Moodle APIs
```

The frontend calls this service for any task that requires:
- File I/O (generating .pptx, .zip, .mp3, .mp4 files)
- Long-running processing (background jobs with polling)
- External API calls that need server-side secrets
- Heavy computation (embeddings, audio/video processing)

## API Endpoints

### Document Processing
| Route | Method | Purpose |
|-------|--------|---------|
| `/process-documents` | POST | Upload and extract text from files |
| `/search-chunks` | POST | Semantic search over document chunks |
| `/reindex-documents` | POST | Re-embed and index documents |
| `/extract-pptx-text` | POST | Extract text from a .pptx file |

### Presentation Generation
| Route | Method | Purpose |
|-------|--------|---------|
| `/generate-slides-content` | POST | Generate slide deck (NotebookLM or Claude engine) |
| `/generate-powerpoint` | POST | Direct PowerPoint generation from outline |
| `/generate-powerpoint-from-storyboard` | POST | Generate .pptx from storyboard JSON |
| `/generate-scorm-from-storyboard` | POST | Generate SCORM 1.2 .zip from storyboard |
| `/generate-notebooklm-content` | POST | Generate podcast/infographic/video via NotebookLM |

### Audio & Video
| Route | Method | Purpose |
|-------|--------|---------|
| `/generate-voiceover-audio` | POST | Text-to-speech for slide narration |
| `/generate-voiceover-video` | POST | Render narrated video from slides + audio |
| `/preview-voice` | GET/POST | Preview a stock voice (Deepgram TTS) |
| `/enroll-voice` | POST | Clone a custom voice |
| `/list-voices` | POST | List user's cloned voices |

### LMS Integration
| Route | Method | Purpose |
|-------|--------|---------|
| `/push-to-scorm-cloud` | POST | Upload SCORM package to SCORM Cloud |
| `/push-to-moodle` | POST | Upload SCORM package to Moodle |
| `/extract-scorm-cloud-text` | POST | Extract text from LMS course content |
| `/extract-scorm-cloud-media` | POST | Transcribe audio/video from LMS courses |
| `/extract-moodle-course-content` | POST | Fetch and extract Moodle course content |

### Google Drive
| Route | Method | Purpose |
|-------|--------|---------|
| `/check-google-connection` | POST | Verify Google OAuth connection |
| `/list-google-drive-files` | POST | Browse Google Drive folders and files |
| `/check-drive-updates` | POST | Check if imported files have been modified |

### System
| Route | Method | Purpose |
|-------|--------|---------|
| `/health` | GET | Health check |
| `/job-status/<job_id>` | GET | Poll status of async background jobs |
| `/notebooklm-status` | GET | Check if NotebookLM engine is available |

## Background Jobs

Long-running tasks (slide generation, audio, video) use an async pattern:
1. Client sends a request → service creates a job row in the database and returns a `job_id`
2. A background thread processes the work
3. Client polls `/job-status/<job_id>` until status is `completed` or `failed`
4. Completed jobs include file URLs and metadata in the response

## Slide Generation Engines

The service supports two AI engines for slide generation:

- **Primary (NotebookLM)** — Google's NotebookLM generates polished slides with deep document analysis. Uses the `notebooklm-py` library. Typical generation time: 15-60 minutes.
- **Fallback (Claude)** — Anthropic's Claude Sonnet 4.6 generates slide content as structured JSON, converted to PPTX via python-pptx. Much faster (~30-60 seconds) but simpler formatting. Automatically used when NotebookLM is unavailable.

## Development

### Setup
```bash
pip install -r requirements.txt
flask run                    # Development
gunicorn api:app             # Production
```

### Environment Variables
```
SUPABASE_URL=<supabase-url>
SUPABASE_SERVICE_ROLE_KEY=<service-role-key>
ALIBABA_API_KEY=<qwen-tts-key>        # For voice cloning
DEEPGRAM_API_KEY=<deepgram-key>       # For stock voice TTS + previews
ANTHROPIC_API_KEY=<anthropic-key>     # For Claude slide generation fallback
GOOGLE_CLIENT_ID=<google-oauth-id>    # For Google Drive
GOOGLE_CLIENT_SECRET=<google-secret>
```

### Deployment
Deployed on **Railway** via GitHub auto-deploy. The service requires:
- Python 3.11+
- System packages: `poppler-utils`, `ffmpeg`, `libreoffice` (configured in `nixpacks.toml`)

### Key Libraries
- `flask` + `flask-cors` — Web framework
- `python-pptx` — PowerPoint generation
- `reportlab` + `pypdf` — PDF generation
- `fastembed` — Document embedding for semantic search
- `pydub` — Audio processing
- `pdf2image` — PDF to PNG conversion
- `beautifulsoup4` — HTML parsing
- `google-api-python-client` — Google Drive API
- `notebooklm-py` — NotebookLM integration (unofficial)
