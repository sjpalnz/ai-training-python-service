from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import json
import os
import threading
from datetime import datetime
from generate_powerpoint import generate_powerpoint_file
from generate_scorm import generate_scorm_package


# ── Google Drive helpers ──────────────────────────────────────────────────────

def get_drive_credentials(refresh_token: str):
    """Exchange a refresh token for a valid Google Credentials object."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri='https://oauth2.googleapis.com/token',
        client_id=os.environ.get('GOOGLE_OAUTH_CLIENT_ID'),
        client_secret=os.environ.get('GOOGLE_OAUTH_CLIENT_SECRET')
    )
    creds.refresh(Request())
    return creds


def build_drive_service(refresh_token: str):
    """Return an authenticated Google Drive v3 service."""
    from googleapiclient.discovery import build
    return build('drive', 'v3', credentials=get_drive_credentials(refresh_token))

app = Flask(__name__)
CORS(app)  # Allow requests from Supabase

# Directory for generated files
OUTPUT_DIR = '/tmp/generated_files'
os.makedirs(OUTPUT_DIR, exist_ok=True)


def _embed_in_background(doc_id: str, client_id: str, filename: str, extracted_text: str):
    """Run RAG chunking + embedding in a background thread so the HTTP response
    is not blocked by the (potentially slow) embedding step."""
    try:
        from embeddings import embed_texts, chunk_text
        supabase = get_supabase_client()
        supabase.table('document_chunks').delete().eq('document_id', doc_id).execute()
        chunks = chunk_text(extracted_text)
        embeddings = embed_texts(chunks)
        chunk_rows = [
            {'document_id': doc_id, 'client_id': client_id, 'chunk_index': i,
             'chunk_text': c, 'embedding': e}
            for i, (c, e) in enumerate(zip(chunks, embeddings))
        ]
        for batch_start in range(0, len(chunk_rows), 100):
            supabase.table('document_chunks').insert(chunk_rows[batch_start:batch_start + 100]).execute()
        print(f"[RAG] {len(chunks)} chunks indexed for {filename}")
    except Exception as embed_err:
        print(f"[RAG] Warning: could not index chunks for {filename}: {embed_err}")


def _prewarm_embedding_model():
    """Load the fastembed model at startup so the first import request is fast."""
    try:
        from embeddings import get_model
        get_model()
        print("[startup] Embedding model pre-loaded and ready.")
    except Exception as e:
        print(f"[startup] Warning: could not pre-load embedding model: {e}")

threading.Thread(target=_prewarm_embedding_model, daemon=True).start()

def verify_jwt(req):
    """Verify a Supabase-issued JWT via the Supabase auth API and return the user UUID, or None if invalid."""
    auth_header = req.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return None
    token = auth_header[7:]
    try:
        supabase = get_supabase_client()
        response = supabase.auth.get_user(token)
        return response.user.id
    except Exception:
        return None


def get_supabase_client():
    """Create and return a Supabase client using environment variables"""
    from supabase import create_client
    supabase_url = os.environ.get('SUPABASE_URL')
    supabase_key = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
    if not supabase_url or not supabase_key:
        raise Exception('SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY environment variables required')
    return create_client(supabase_url, supabase_key)

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'AI Training Platform - Python Service',
        'timestamp': datetime.now().isoformat()
    })

# --- Supabase-integrated endpoints (called by Supabase Edge Functions) ---

@app.route('/generate-powerpoint-from-storyboard', methods=['POST'])
def generate_ppt_from_storyboard():
    """
    Fetch storyboard from Supabase, generate real PowerPoint, upload to storage, save to DB.
    Expected JSON: { "storyboard_id": "uuid", "theme_id": "corporate", "template_url": null }
    """
    import urllib.request
    template_tmpfile = None
    infographic_tmpfile = None
    try:
        data = request.json
        storyboard_id = data.get('storyboard_id')
        theme_id      = data.get('theme_id', 'corporate')
        template_url  = data.get('template_url')   # optional user .pptx template

        if not storyboard_id:
            return jsonify({'error': 'storyboard_id is required'}), 400

        supabase = get_supabase_client()

        # Fetch storyboard and related course
        result = supabase.table('storyboards').select('*, courses(*)').eq('id', storyboard_id).single().execute()
        storyboard = result.data

        if not storyboard:
            return jsonify({'error': 'Storyboard not found'}), 404

        course_data = storyboard['content_json']

        # Download user template if provided
        template_path = None
        if template_url:
            template_tmpfile = os.path.join(OUTPUT_DIR, f"tpl_{storyboard_id[:8]}.pptx")
            urllib.request.urlretrieve(template_url, template_tmpfile)
            template_path = template_tmpfile

        # Check for existing infographic to embed
        infographic_tmpfile = None
        try:
            infographic_result = supabase.table('generated_files') \
                .select('file_url') \
                .eq('course_id', storyboard['course_id']) \
                .eq('file_type', 'infographic') \
                .order('created_at', desc=True) \
                .limit(1) \
                .execute()
            if infographic_result.data:
                infographic_tmpfile = os.path.join(OUTPUT_DIR, f"infographic_{storyboard_id[:8]}.png")
                urllib.request.urlretrieve(infographic_result.data[0]['file_url'], infographic_tmpfile)
        except Exception as e:
            print(f"[PPT] Warning: could not fetch infographic for embedding: {e}")
            infographic_tmpfile = None

        # Generate PowerPoint file
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"course_{storyboard_id[:8]}_{timestamp}.pptx"
        filepath = os.path.join(OUTPUT_DIR, filename)
        generate_powerpoint_file(course_data, filepath, theme_id=theme_id, template_path=template_path, infographic_path=infographic_tmpfile)

        # Upload to Supabase Storage
        storage_path = f"powerpoints/{filename}"
        with open(filepath, 'rb') as f:
            file_bytes = f.read()

        supabase.storage.from_('course-files').upload(
            path=storage_path,
            file=file_bytes,
            file_options={"content-type": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}
        )

        # Get public URL
        public_url = supabase.storage.from_('course-files').get_public_url(storage_path)

        # Save to generated_files table
        file_record = supabase.table('generated_files').insert({
            'course_id': storyboard['course_id'],
            'file_type': 'powerpoint',
            'file_url': public_url,
            'file_size': len(file_bytes)
        }).execute()

        # Cleanup local files
        os.remove(filepath)
        if template_tmpfile and os.path.exists(template_tmpfile):
            os.remove(template_tmpfile)
        if infographic_tmpfile and os.path.exists(infographic_tmpfile):
            os.remove(infographic_tmpfile)

        return jsonify({
            'success': True,
            'file_id': file_record.data[0]['id'],
            'download_url': public_url,
            'filename': filename
        })

    except Exception as e:
        if template_tmpfile and os.path.exists(template_tmpfile):
            os.remove(template_tmpfile)
        if infographic_tmpfile and os.path.exists(infographic_tmpfile):
            os.remove(infographic_tmpfile)
        return jsonify({'error': str(e)}), 500


@app.route('/generate-scorm-from-storyboard', methods=['POST'])
def generate_scorm_from_storyboard():
    """
    Fetch storyboard from Supabase, generate SCORM package, upload to storage, save to DB.
    Expected JSON: { "storyboard_id": "uuid" }
    """
    try:
        data = request.json
        storyboard_id = data.get('storyboard_id')

        if not storyboard_id:
            return jsonify({'error': 'storyboard_id is required'}), 400

        supabase = get_supabase_client()

        # Fetch storyboard and related course
        result = supabase.table('storyboards').select('*, courses(*)').eq('id', storyboard_id).single().execute()
        storyboard = result.data

        if not storyboard:
            return jsonify({'error': 'Storyboard not found'}), 404

        course_data = storyboard['content_json']

        # Check for existing podcast and infographic to embed
        podcast_url = None
        infographic_url = None
        try:
            podcast_result = supabase.table('generated_files') \
                .select('file_url') \
                .eq('course_id', storyboard['course_id']) \
                .eq('file_type', 'podcast') \
                .order('created_at', desc=True) \
                .limit(1) \
                .execute()
            if podcast_result.data:
                podcast_url = podcast_result.data[0]['file_url']

            infographic_result = supabase.table('generated_files') \
                .select('file_url') \
                .eq('course_id', storyboard['course_id']) \
                .eq('file_type', 'infographic') \
                .order('created_at', desc=True) \
                .limit(1) \
                .execute()
            if infographic_result.data:
                infographic_url = infographic_result.data[0]['file_url']
        except Exception as e:
            print(f"[SCORM] Warning: could not fetch media for embedding: {e}")

        # Generate SCORM package
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"scorm_{storyboard_id[:8]}_{timestamp}.zip"
        filepath = os.path.join(OUTPUT_DIR, filename)
        generate_scorm_package(course_data, filepath, podcast_url=podcast_url, infographic_url=infographic_url)

        # Upload to Supabase Storage
        storage_path = f"scorm/{filename}"
        with open(filepath, 'rb') as f:
            file_bytes = f.read()

        supabase.storage.from_('course-files').upload(
            path=storage_path,
            file=file_bytes,
            file_options={"content-type": "application/zip"}
        )

        # Get public URL
        public_url = supabase.storage.from_('course-files').get_public_url(storage_path)

        # Save to generated_files table
        file_record = supabase.table('generated_files').insert({
            'course_id': storyboard['course_id'],
            'file_type': 'scorm',
            'file_url': public_url,
            'file_size': len(file_bytes)
        }).execute()

        # Cleanup local file
        os.remove(filepath)

        return jsonify({
            'success': True,
            'file_id': file_record.data[0]['id'],
            'download_url': public_url,
            'filename': filename
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# --- Google Drive endpoints ---

@app.route('/check-google-connection', methods=['POST'])
def check_google_connection():
    """
    Check whether a user has Google Drive credentials stored.
    Expects JSON: { user_id }
    Returns: { connected: bool }
    """
    try:
        data = request.json or {}
        user_id = data.get('user_id')
        if not user_id:
            return jsonify({'connected': False}), 200
        supabase = get_supabase_client()
        creds_row = supabase.table('user_google_credentials') \
            .select('id') \
            .eq('user_id', user_id) \
            .maybeSingle() \
            .execute()
        return jsonify({'connected': bool(creds_row.data)})
    except Exception as e:
        return jsonify({'connected': False, 'error': str(e)}), 200


@app.route('/list-google-drive-files', methods=['POST'])
def list_google_drive_files():
    """
    List folders + supported files inside a specific Drive folder.
    Expects JSON: { user_id, folder_id? }  (folder_id defaults to 'root')
    Returns: { success, folders: [{id, name}], files: [{id, name, mimeType, size, modifiedTime}] }
    """
    try:
        data = request.json or {}
        user_id = data.get('user_id')
        folder_id = data.get('folder_id', 'root')

        if not user_id:
            return jsonify({'error': 'user_id required'}), 400

        # Fetch refresh_token from Supabase using service_role (bypasses RLS)
        supabase = get_supabase_client()
        creds_row = supabase.table('user_google_credentials') \
            .select('refresh_token') \
            .eq('user_id', user_id) \
            .single() \
            .execute()
        refresh_token = creds_row.data.get('refresh_token') if creds_row.data else None
        if not refresh_token:
            return jsonify({'error': 'Google Drive not connected for this user'}), 400

        drive_service = build_drive_service(refresh_token)

        common_args = dict(
            spaces='drive',
            pageSize=100,
            orderBy='name',
            includeItemsFromAllDrives=True,
            supportsAllDrives=True
        )

        # Fetch subfolders in this location
        folder_results = drive_service.files().list(
            q=f"'{folder_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
            fields='files(id, name)',
            **common_args
        ).execute()

        # Fetch supported files in this location
        file_query = (
            f"'{folder_id}' in parents and "
            "(mimeType = 'application/pdf' or "
            "mimeType = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' or "
            "mimeType = 'application/vnd.google-apps.document' or "
            "mimeType = 'text/plain') and "
            "trashed = false"
        )
        file_results = drive_service.files().list(
            q=file_query,
            fields='files(id, name, mimeType, size, modifiedTime)',
            **common_args
        ).execute()

        return jsonify({
            'success': True,
            'folders': folder_results.get('files', []),
            'files': file_results.get('files', [])
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# --- Document upload and text extraction ---

@app.route('/process-documents', methods=['POST'])
def process_documents():
    """
    Accept uploaded files OR a Google Drive file ID, extract text, save to Supabase.
    Manual upload: multipart form data with 'files' field.
    Drive import:  JSON body with 'google_drive_file_id' and 'refresh_token'.
    Supports: PDF, DOCX, TXT (+ Google Docs exported as DOCX)
    """
    try:
        user_id = verify_jwt(request)
        if not user_id:
            return jsonify({'error': 'Unauthorized'}), 401

        client_id = user_id

        # ── Google Drive import path ──────────────────────────────────────────
        if request.is_json:
            data = request.json or {}
            google_drive_file_id = data.get('google_drive_file_id')

            if not google_drive_file_id:
                return jsonify({'error': 'google_drive_file_id required for Drive import'}), 400

            # Fetch refresh_token from Supabase (service_role bypasses RLS)
            supabase = get_supabase_client()
            creds_row = supabase.table('user_google_credentials') \
                .select('refresh_token') \
                .eq('user_id', user_id) \
                .single() \
                .execute()
            refresh_token = creds_row.data.get('refresh_token') if creds_row.data else None
            if not refresh_token:
                return jsonify({'error': 'Google Drive not connected for this user'}), 400

            drive_service = build_drive_service(refresh_token)

            # Fetch file metadata
            file_meta = drive_service.files().get(
                fileId=google_drive_file_id,
                fields='name, mimeType, size'
            ).execute()

            filename = file_meta.get('name', f'drive_{google_drive_file_id}')
            mime_type = file_meta.get('mimeType', '')
            file_size = int(file_meta.get('size', 0))

            # Determine file extension + download method
            from io import BytesIO
            if mime_type == 'application/vnd.google-apps.document':
                # Export Google Doc as DOCX
                file_content = drive_service.files().export_media(
                    fileId=google_drive_file_id,
                    mimeType='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                ).execute()
                file_ext = 'docx'
                if not filename.endswith('.docx'):
                    filename += '.docx'
            else:
                file_content = drive_service.files().get_media(fileId=google_drive_file_id).execute()
                file_ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''

            if file_ext not in {'pdf', 'docx', 'doc', 'txt'}:
                return jsonify({'error': f'Unsupported file type: .{file_ext}'}), 400

            # Write to temp file for text extraction
            temp_path = os.path.join('/tmp', f"drive_{google_drive_file_id}.{file_ext}")
            with open(temp_path, 'wb') as f:
                f.write(file_content)

            extracted_text = ''
            try:
                if file_ext == 'pdf':
                    from pypdf import PdfReader
                    reader = PdfReader(temp_path)
                    for page in reader.pages:
                        page_text = page.extract_text()
                        if page_text:
                            extracted_text += page_text + '\n'
                elif file_ext in ['docx', 'doc']:
                    from docx import Document
                    doc = Document(temp_path)
                    for para in doc.paragraphs:
                        if para.text.strip():
                            extracted_text += para.text + '\n'
                elif file_ext == 'txt':
                    with open(temp_path, 'r', encoding='utf-8', errors='ignore') as f:
                        extracted_text = f.read()
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)

            if not extracted_text.strip():
                return jsonify({'error': f'Could not extract text from {filename}. File may be empty or image-based.'}), 400

            supabase = get_supabase_client()

            # Upsert: remove existing Drive record for same file_id, then insert
            supabase.table('documents').delete().eq('client_id', client_id).eq('drive_file_id', google_drive_file_id).execute()
            insert_result = supabase.table('documents').insert({
                'client_id': client_id,
                'filename': filename,
                'file_type': file_ext,
                'drive_file_id': google_drive_file_id,
                'source': 'google_drive',
                'extracted_text': extracted_text,
                'file_size': file_size or len(file_content)
            }).execute()

            # Deduct 2 credits via RPC
            supabase.rpc('deduct_credits', {
                'p_user_id': client_id,
                'p_amount': 2,
                'p_operation': 'google_drive_import',
                'p_reference_id': None
            }).execute()

            # RAG chunking — run in background so HTTP response is not delayed
            doc_id = insert_result.data[0]['id']
            threading.Thread(
                target=_embed_in_background,
                args=(doc_id, client_id, filename, extracted_text),
                daemon=True
            ).start()

            return jsonify({
                'success': True,
                'documents_processed': 1,
                'documents': [{'filename': filename, 'file_type': file_ext, 'chars_extracted': len(extracted_text)}]
            })

        # ── Manual file upload path (existing code) ───────────────────────────
        files = request.files.getlist('files')

        if not files or all(f.filename == '' for f in files):
            return jsonify({'error': 'No files provided'}), 400

        if len(files) > 5:
            return jsonify({'error': 'Maximum 5 files allowed'}), 400

        MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
        ALLOWED_EXTENSIONS = {'pdf', 'docx', 'doc', 'txt'}

        supabase = get_supabase_client()
        processed = []

        for file in files:
            filename = file.filename
            if not filename:
                continue

            file_ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''

            if file_ext not in ALLOWED_EXTENSIONS:
                return jsonify({'error': f'Unsupported file type: .{file_ext}. Allowed: PDF, DOCX, TXT'}), 400

            # Save to temp location
            temp_path = os.path.join('/tmp', f"upload_{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}")
            file.save(temp_path)
            file_size = os.path.getsize(temp_path)

            if file_size > MAX_FILE_SIZE:
                os.remove(temp_path)
                return jsonify({'error': f'{filename} exceeds 10MB limit'}), 400

            # Extract text based on file type
            extracted_text = ''
            try:
                if file_ext == 'pdf':
                    from pypdf import PdfReader
                    reader = PdfReader(temp_path)
                    for page in reader.pages:
                        page_text = page.extract_text()
                        if page_text:
                            extracted_text += page_text + '\n'

                elif file_ext in ['docx', 'doc']:
                    from docx import Document
                    doc = Document(temp_path)
                    for para in doc.paragraphs:
                        if para.text.strip():
                            extracted_text += para.text + '\n'

                elif file_ext == 'txt':
                    with open(temp_path, 'r', encoding='utf-8', errors='ignore') as f:
                        extracted_text = f.read()

            finally:
                os.remove(temp_path)

            if not extracted_text.strip():
                return jsonify({'error': f'Could not extract text from {filename}. File may be empty or image-based.'}), 400

            # Delete existing record for same filename + client, then insert fresh
            drive_file_id = f"upload_{filename}"
            supabase.table('documents').delete().eq('client_id', client_id).eq('drive_file_id', drive_file_id).execute()
            insert_result = supabase.table('documents').insert({
                'client_id': client_id,
                'filename': filename,
                'file_type': file_ext,
                'drive_file_id': drive_file_id,
                'extracted_text': extracted_text,
                'file_size': file_size
            }).select('id').execute()

            # ── RAG: chunk + embed the document ──────────────────────────────
            try:
                from embeddings import embed_texts, chunk_text
                doc_id = insert_result.data[0]['id']

                # Remove any stale chunks for this document
                supabase.table('document_chunks').delete().eq('document_id', doc_id).execute()

                # Split into overlapping passages and generate embeddings
                chunks = chunk_text(extracted_text)
                embeddings = embed_texts(chunks)

                chunk_rows = [
                    {
                        'document_id': doc_id,
                        'client_id': client_id,
                        'chunk_index': i,
                        'chunk_text': c,
                        'embedding': e,
                    }
                    for i, (c, e) in enumerate(zip(chunks, embeddings))
                ]

                # Insert in batches of 100 to stay within request size limits
                for batch_start in range(0, len(chunk_rows), 100):
                    supabase.table('document_chunks').insert(
                        chunk_rows[batch_start:batch_start + 100]
                    ).execute()

                print(f"[RAG] {len(chunks)} chunks indexed for {filename}")
            except Exception as embed_err:
                # Non-fatal: Q&A falls back to full-text if chunks are missing
                print(f"[RAG] Warning: could not index chunks for {filename}: {embed_err}")

            processed.append({
                'filename': filename,
                'file_type': file_ext,
                'file_size': file_size,
                'chars_extracted': len(extracted_text),
                'preview': extracted_text.strip()[:200]
            })

        return jsonify({
            'success': True,
            'documents_processed': len(processed),
            'documents': processed
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# --- NotebookLM content generation (podcast / infographic) ---

def _notebooklm_job_worker(job_id, storyboard_id, content_type, options=None):
    """Background worker: generate NotebookLM content and update job status."""
    try:
        from generate_notebooklm import generate_podcast, generate_infographic, generate_video, cleanup_notebook
        supabase = get_supabase_client()

        # Update job → processing
        supabase.table('generation_jobs').update({
            'status': 'processing',
            'updated_at': datetime.now().isoformat()
        }).eq('id', job_id).execute()

        # Fetch storyboard + course data
        sb_result = supabase.table('storyboards').select('*, courses(*)').eq('id', storyboard_id).single().execute()
        storyboard = sb_result.data
        if not storyboard:
            raise Exception('Storyboard not found')

        course_data = storyboard['content_json']
        course_id = storyboard['course_id']
        client_id = storyboard['courses']['client_id']

        # Gather source document text
        docs_result = supabase.table('documents').select('extracted_text').eq('client_id', client_id).limit(10).execute()
        source_text = '\n\n'.join(
            doc['extracted_text'][:5000] for doc in (docs_result.data or []) if doc.get('extracted_text')
        )
        # Fall back to storyboard content if no docs
        if not source_text.strip():
            slides_text = '\n'.join(
                f"{s.get('title', '')}: {' '.join(s.get('bullets', []))}"
                for s in course_data.get('slides', [])
            )
            source_text = f"{course_data.get('title', '')}\n\n{slides_text}"

        # Generate the artifact
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        notebook_id = None

        if content_type == 'podcast':
            filename = f"podcast_{storyboard_id[:8]}_{timestamp}.mp3"
            filepath = os.path.join(OUTPUT_DIR, filename)
            notebook_id = generate_podcast(source_text, course_data, filepath, options=options)
            storage_path = f"podcasts/{filename}"
            content_type_header = 'audio/mpeg'
            file_type = 'podcast'
        elif content_type == 'video':
            filename = f"video_{storyboard_id[:8]}_{timestamp}.mp4"
            filepath = os.path.join(OUTPUT_DIR, filename)
            notebook_id = generate_video(source_text, course_data, filepath, options=options)
            storage_path = f"videos/{filename}"
            content_type_header = 'video/mp4'
            file_type = 'video'
        else:
            filename = f"infographic_{storyboard_id[:8]}_{timestamp}.png"
            filepath = os.path.join(OUTPUT_DIR, filename)
            print(f"[NotebookLM] Starting infographic generation with options={options}")
            notebook_id = generate_infographic(source_text, course_data, filepath, options=options)
            storage_path = f"infographics/{filename}"
            content_type_header = 'image/png'
            file_type = 'infographic'

        # Update notebook ID for tracking
        supabase.table('generation_jobs').update({
            'notebooklm_notebook_id': notebook_id,
            'updated_at': datetime.now().isoformat()
        }).eq('id', job_id).execute()

        # Upload to Supabase Storage
        with open(filepath, 'rb') as f:
            file_bytes = f.read()

        supabase.storage.from_('course-files').upload(
            path=storage_path,
            file=file_bytes,
            file_options={"content-type": content_type_header}
        )

        public_url = supabase.storage.from_('course-files').get_public_url(storage_path)

        # Save to generated_files table
        file_record = supabase.table('generated_files').insert({
            'course_id': course_id,
            'file_type': file_type,
            'file_url': public_url,
            'file_size': len(file_bytes)
        }).execute()

        # Update job → completed
        supabase.table('generation_jobs').update({
            'status': 'completed',
            'result_file_id': file_record.data[0]['id'],
            'updated_at': datetime.now().isoformat()
        }).eq('id', job_id).execute()

        # Cleanup local temp file
        os.remove(filepath)
        # NOTE: intentionally not deleting the NotebookLM notebook so the
        # notebook and its studio assets (audio, infographic, video) remain
        # visible in NotebookLM for inspection.
        # if notebook_id:
        #     try:
        #         cleanup_notebook(notebook_id)
        #     except Exception as e:
        #         print(f"[NotebookLM] Warning: cleanup failed for notebook {notebook_id}: {e}")

        print(f"[NotebookLM] {file_type} generated successfully: {filename}")

    except Exception as err:
        import traceback
        print(f"[NotebookLM] Job {job_id} failed: {err}")
        print(f"[NotebookLM] Traceback:\n{traceback.format_exc()}")
        try:
            supabase = get_supabase_client()
            supabase.table('generation_jobs').update({
                'status': 'failed',
                'error_message': str(err)[:500],
                'updated_at': datetime.now().isoformat()
            }).eq('id', job_id).execute()
        except Exception:
            pass


@app.route('/generate-notebooklm-content', methods=['POST'])
def generate_notebooklm_content():
    """
    Start async generation of a podcast or infographic via NotebookLM.
    Expected JSON: { "storyboard_id": "uuid", "content_type": "podcast"|"infographic" }
    Returns: { "job_id": "uuid" } immediately; poll /job-status/<job_id> for result.
    """
    try:
        data = request.json or {}
        storyboard_id = data.get('storyboard_id')
        content_type = data.get('content_type')
        options = data.get('options', {})

        if not storyboard_id:
            return jsonify({'error': 'storyboard_id is required'}), 400
        if content_type not in ('podcast', 'infographic', 'video'):
            return jsonify({'error': 'content_type must be "podcast", "infographic", or "video"'}), 400

        # Look up course_id from the storyboard
        supabase = get_supabase_client()
        sb = supabase.table('storyboards').select('course_id').eq('id', storyboard_id).single().execute()
        if not sb.data:
            return jsonify({'error': 'Storyboard not found'}), 404

        # Create job row
        job = supabase.table('generation_jobs').insert({
            'course_id': sb.data['course_id'],
            'job_type': content_type,
            'status': 'pending'
        }).execute()

        job_id = job.data[0]['id']

        # Spawn background worker
        threading.Thread(
            target=_notebooklm_job_worker,
            args=(job_id, storyboard_id, content_type, options),
            daemon=True
        ).start()

        return jsonify({'success': True, 'job_id': job_id})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/job-status/<job_id>', methods=['GET'])
def job_status(job_id):
    """
    Poll the status of a NotebookLM generation job.
    Returns: { "status": "pending"|"processing"|"completed"|"failed",
               "file_url": "...", "error_message": "..." }
    """
    try:
        supabase = get_supabase_client()
        result = supabase.table('generation_jobs') \
            .select('status, error_message, result_file_id') \
            .eq('id', job_id) \
            .single() \
            .execute()

        if not result.data:
            return jsonify({'error': 'Job not found'}), 404

        job = result.data
        response = {'status': job['status']}

        if job['status'] == 'completed' and job.get('result_file_id'):
            file_result = supabase.table('generated_files') \
                .select('file_url') \
                .eq('id', job['result_file_id']) \
                .single() \
                .execute()
            if file_result.data:
                response['file_url'] = file_result.data['file_url']

        if job.get('error_message'):
            response['error_message'] = job['error_message']

        return jsonify(response)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/notebooklm-status', methods=['GET'])
def notebooklm_status():
    """
    Health check for NotebookLM auth.
    Returns: { "available": true|false, "error": "..." }
    """
    auth_json = os.environ.get('NOTEBOOKLM_AUTH_JSON')
    if not auth_json:
        return jsonify({'available': False, 'error': 'NOTEBOOKLM_AUTH_JSON not configured'})

    try:
        from generate_notebooklm import check_auth
        check_auth()
        return jsonify({'available': True})
    except Exception as e:
        return jsonify({'available': False, 'error': str(e)})


# --- Direct file-return endpoints (kept for local/direct use) ---

@app.route('/generate-powerpoint', methods=['POST'])
def generate_ppt():
    """Generate PowerPoint directly and return file (no Supabase)"""
    try:
        data = request.json
        if not data or 'slides' not in data:
            return jsonify({'error': 'Missing course outline data'}), 400

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"course_{timestamp}.pptx"
        filepath = os.path.join(OUTPUT_DIR, filename)
        generate_powerpoint_file(data, filepath)

        return send_file(
            filepath,
            mimetype='application/vnd.openxmlformats-officedocument.presentationml.presentation',
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/generate-scorm', methods=['POST'])
def generate_scorm():
    """Generate SCORM package directly and return file (no Supabase)"""
    try:
        data = request.json
        if not data or 'slides' not in data:
            return jsonify({'error': 'Missing course outline data'}), 400

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"course_{timestamp}.zip"
        filepath = os.path.join(OUTPUT_DIR, filename)
        generate_scorm_package(data, filepath)

        return send_file(
            filepath,
            mimetype='application/zip',
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/search-chunks', methods=['POST'])
def search_chunks():
    """
    Embed a question and return the top-k most semantically similar document chunks.

    Expected JSON:
      {
        "question":     str,
        "client_id":    str (UUID),
        "document_ids": [str, ...],   # optional filter; empty/omitted = all docs
        "top_k":        int           # default 8
      }
    """
    try:
        data         = request.json or {}
        question     = data.get('question', '').strip()
        document_ids = data.get('document_ids') or []
        client_id    = data.get('client_id', '').strip()
        top_k        = int(data.get('top_k', 8))

        if not question:
            return jsonify({'error': 'question is required'}), 400
        if not client_id:
            return jsonify({'error': 'client_id is required'}), 400

        from embeddings import embed_texts
        query_embedding = embed_texts([question])[0]

        supabase = get_supabase_client()
        result = supabase.rpc('match_chunks', {
            'query_embedding': query_embedding,
            'match_document_ids': document_ids if document_ids else None,
            'match_client_id': client_id,
            'match_count': top_k,
        }).execute()

        return jsonify({'success': True, 'chunks': result.data or []})

    except Exception as e:
        print(f"[search-chunks] Error: {e}")
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
