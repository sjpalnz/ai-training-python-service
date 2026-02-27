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

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'AI Training Platform - Python Service',
        'timestamp': datetime.now().isoformat()
    })

@app.route('/generate-powerpoint', methods=['POST'])
def generate_ppt():
    """
    Generate PowerPoint from course outline
    
    Expected JSON:
    {
        "title": "Course Title",
        "slides": [
            {"number": 1, "type": "title", "content": "..."},
            {"number": 2, "type": "objectives", "title": "...", "bullets": [...]},
            ...
        ]
    }
    """
    try:
        data = request.json
        
        if not data or 'slides' not in data:
            return jsonify({'error': 'Missing course outline data'}), 400
        
        # Generate unique filename
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"course_{timestamp}.pptx"
        filepath = os.path.join(OUTPUT_DIR, filename)
        
        # Call your PowerPoint generation function
        generate_powerpoint_file(data, filepath)
        
        # Return file
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
    """
    Generate SCORM package from course outline
    
    Expected JSON: Same as PowerPoint
    """
    try:
        data = request.json
        
        if not data or 'slides' not in data:
            return jsonify({'error': 'Missing course outline data'}), 400
        
        # Generate unique filename
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"course_{timestamp}.zip"
        filepath = os.path.join(OUTPUT_DIR, filename)
        
        # Call your SCORM generation function
        generate_scorm_package(data, filepath)
        
        # Return file
        return send_file(
            filepath,
            mimetype='application/zip',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/generate-pdf-storyboard', methods=['POST'])
def generate_pdf():
    """Generate PDF storyboard"""
    try:
        data = request.json
        
        if not data or 'slides' not in data:
            return jsonify({'error': 'Missing course outline data'}), 400
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"storyboard_{timestamp}.pdf"
        filepath = os.path.join(OUTPUT_DIR, filename)
        
        # Import and call PDF generation
        from generate_storyboard import generate_pdf_storyboard
        generate_pdf_storyboard(data, filepath)
        
        return send_file(
            filepath,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # For Railway, read PORT from environment
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
