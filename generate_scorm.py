import os
import zipfile
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom import minidom

def generate_scorm_package(course_data, output_path, podcast_url=None, infographic_url=None):
    """
    Generate SCORM 1.2 package from course outline

    Args:
        course_data:      Dict with 'title' and 'slides' array
        output_path:      Path where .zip file will be saved
        podcast_url:      Optional public URL to an MP3 podcast to embed as audio player
        infographic_url:  Optional public URL to a PNG infographic to embed as image
    """
    # Create temp directory for SCORM files
    temp_dir = '/tmp/scorm_temp'
    os.makedirs(temp_dir, exist_ok=True)

    course_title = course_data.get('title', 'Training Course')

    # Generate imsmanifest.xml
    manifest_content = generate_manifest(course_data)
    with open(os.path.join(temp_dir, 'imsmanifest.xml'), 'w') as f:
        f.write(manifest_content)

    # Generate index.html
    html_content = generate_html(course_data, podcast_url=podcast_url, infographic_url=infographic_url)
    with open(os.path.join(temp_dir, 'index.html'), 'w') as f:
        f.write(html_content)
    
    # Generate API wrapper
    api_content = generate_api_wrapper()
    with open(os.path.join(temp_dir, 'scorm_api.js'), 'w') as f:
        f.write(api_content)
    
    # Create ZIP file
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(temp_dir):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, temp_dir)
                zipf.write(file_path, arcname)
    
    # Clean up temp directory
    import shutil
    shutil.rmtree(temp_dir)
    
    return output_path

def generate_manifest(course_data):
    """Generate SCORM 1.2 imsmanifest.xml"""
    course_title = course_data.get('title', 'Training Course')
    
    manifest = Element('manifest', {
        'identifier': 'course_manifest',
        'version': '1.0',
        'xmlns': 'http://www.imsproject.org/xsd/imscp_rootv1p1p2',
        'xmlns:adlcp': 'http://www.adlnet.org/xsd/adlcp_rootv1p2',
        'xmlns:xsi': 'http://www.w3.org/2001/XMLSchema-instance',
        'xsi:schemaLocation': 'http://www.imsproject.org/xsd/imscp_rootv1p1p2 imscp_rootv1p1p2.xsd http://www.adlnet.org/xsd/adlcp_rootv1p2 adlcp_rootv1p2.xsd'
    })
    
    # Metadata
    metadata = SubElement(manifest, 'metadata')
    schema = SubElement(metadata, 'schema')
    schema.text = 'ADL SCORM'
    schemaversion = SubElement(metadata, 'schemaversion')
    schemaversion.text = '1.2'
    
    # Organizations
    organizations = SubElement(manifest, 'organizations', {'default': 'course_org'})
    organization = SubElement(organizations, 'organization', {'identifier': 'course_org'})
    title = SubElement(organization, 'title')
    title.text = course_title
    
    item = SubElement(organization, 'item', {
        'identifier': 'item_1',
        'identifierref': 'resource_1',
        'isvisible': 'true'
    })
    item_title = SubElement(item, 'title')
    item_title.text = course_title
    
    # Resources
    resources = SubElement(manifest, 'resources')
    resource = SubElement(resources, 'resource', {
        'identifier': 'resource_1',
        'type': 'webcontent',
        'adlcp:scormtype': 'sco',
        'href': 'index.html'
    })
    
    SubElement(resource, 'file', {'href': 'index.html'})
    SubElement(resource, 'file', {'href': 'scorm_api.js'})
    
    # Pretty print XML
    rough_string = tostring(manifest, 'utf-8')
    reparsed = minidom.parseString(rough_string)
    return reparsed.toprettyxml(indent='  ')

def generate_html(course_data, podcast_url=None, infographic_url=None):
    """Generate HTML content for SCORM package"""
    course_title = course_data.get('title', 'Training Course')
    slides = course_data.get('slides', [])
    
    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{course_title}</title>
    <script src="scorm_api.js"></script>
    <style>
        body {{
            font-family: Arial, sans-serif;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            background: linear-gradient(135deg, #1e3a8a 0%, #1e40af 100%);
            color: #333;
        }}
        .container {{
            background: white;
            border-radius: 10px;
            padding: 30px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }}
        h1 {{
            color: #2c5aa0;
            border-bottom: 3px solid #2c5aa0;
            padding-bottom: 10px;
        }}
        .slide {{
            margin-bottom: 30px;
            padding: 20px;
            background: #f8f9fa;
            border-radius: 8px;
        }}
        .slide-title {{
            color: #2c5aa0;
            font-size: 24px;
            font-weight: bold;
            margin-bottom: 15px;
        }}
        ul {{
            line-height: 1.8;
        }}
        .quiz {{
            background: #e3f2fd;
            padding: 20px;
            border-radius: 8px;
            margin-top: 15px;
        }}
        .quiz-question {{
            font-weight: bold;
            margin-bottom: 10px;
        }}
        button {{
            background: #2c5aa0;
            color: white;
            border: none;
            padding: 12px 24px;
            border-radius: 5px;
            cursor: pointer;
            font-size: 16px;
            margin-top: 20px;
        }}
        button:hover {{
            background: #1e3a8a;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>{course_title}</h1>
'''
    
    for slide in slides:
        slide_type = slide.get('type', 'content')
        
        if slide_type == 'title':
            continue  # Skip title slide in web view
            
        html += f'        <div class="slide">\n'
        
        if slide.get('title'):
            html += f'            <div class="slide-title">{slide["title"]}</div>\n'
        
        if slide.get('bullets'):
            html += '            <ul>\n'
            for bullet in slide['bullets']:
                html += f'                <li>{bullet}</li>\n'
            html += '            </ul>\n'
        
        if slide_type == 'quiz' and slide.get('question'):
            html += '            <div class="quiz">\n'
            html += f'                <div class="quiz-question">{slide["question"]}</div>\n'
            if slide.get('options'):
                html += '                <ul>\n'
                for option in slide['options']:
                    html += f'                    <li>{option}</li>\n'
                html += '                </ul>\n'
            html += '            </div>\n'
        
        html += '        </div>\n'
    
    if podcast_url:
        html += f'''        <div class="slide" style="background: #e8f5e9;">
            <div class="slide-title">Course Podcast</div>
            <p>Listen to an AI-generated discussion of the course content:</p>
            <audio controls style="width: 100%; margin-top: 15px;">
                <source src="{podcast_url}" type="audio/mpeg">
                Your browser does not support the audio element.
            </audio>
        </div>
'''

    if infographic_url:
        html += f'''        <div class="slide">
            <div class="slide-title">Course Infographic</div>
            <img src="{infographic_url}" alt="Course Infographic"
                 style="max-width: 100%; height: auto; border-radius: 8px; margin-top: 10px;" />
        </div>
'''

    html += '''        <button onclick="completeCourse()">Complete Course</button>
    </div>
    
    <script>
        function completeCourse() {
            if (window.API) {
                API.LMSSetValue("cmi.core.lesson_status", "completed");
                API.LMSSetValue("cmi.core.score.raw", "100");
                API.LMSCommit("");
                alert("Course completed successfully!");
            }
        }
        
        // Initialize SCORM
        if (window.API) {
            API.LMSInitialize("");
            API.LMSSetValue("cmi.core.lesson_status", "incomplete");
        }
    </script>
</body>
</html>'''
    
    return html

def generate_api_wrapper():
    """Generate SCORM API wrapper JavaScript"""
    return '''// SCORM 1.2 API Wrapper
var API = {
    LMSInitialize: function(param) {
        console.log("LMSInitialize called");
        return "true";
    },
    LMSFinish: function(param) {
        console.log("LMSFinish called");
        return "true";
    },
    LMSGetValue: function(element) {
        console.log("LMSGetValue: " + element);
        return "";
    },
    LMSSetValue: function(element, value) {
        console.log("LMSSetValue: " + element + " = " + value);
        return "true";
    },
    LMSCommit: function(param) {
        console.log("LMSCommit called");
        return "true";
    },
    LMSGetLastError: function() {
        return "0";
    },
    LMSGetErrorString: function(errorCode) {
        return "No error";
    },
    LMSGetDiagnostic: function(errorCode) {
        return "No error";
    }
};

window.API = API;
'''
