from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import json
import os
from datetime import datetime
from generate_powerpoint import generate_powerpoint_file
from generate_scorm import generate_scorm_package

app = Flask(__name__)
CORS(app)  # Allow requests from Supabase

# Directory for generated files
OUTPUT_DIR = '/tmp/generated_files'
os.makedirs(OUTPUT_DIR, exist_ok=True)

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

        # Generate PowerPoint file
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"course_{storyboard_id[:8]}_{timestamp}.pptx"
        filepath = os.path.join(OUTPUT_DIR, filename)
        generate_powerpoint_file(course_data, filepath)

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

        # Generate SCORM package
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"scorm_{storyboard_id[:8]}_{timestamp}.zip"
        filepath = os.path.join(OUTPUT_DIR, filename)
        generate_scorm_package(course_data, filepath)

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


# --- Document upload and text extraction ---

@app.route('/process-documents', methods=['POST'])
def process_documents():
    """
    Accept up to 5 uploaded files, extract text, save to Supabase documents table.
    Accepts multipart form data with 'files' field and optional 'client_id'.
    Supports: PDF, DOCX, TXT
    """
    try:
        user_id = verify_jwt(request)
        if not user_id:
            return jsonify({'error': 'Unauthorized'}), 401

        client_id = user_id
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
            supabase.table('documents').insert({
                'client_id': client_id,
                'filename': filename,
                'file_type': file_ext,
                'drive_file_id': drive_file_id,
                'extracted_text': extracted_text,
                'file_size': file_size
            }).execute()

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


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
