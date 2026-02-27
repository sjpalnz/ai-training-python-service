from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.enum.text import PP_ALIGN
from pptx.dml.color import RGBColor

def generate_powerpoint_file(course_data, output_path):
    """
    Generate PowerPoint presentation from course outline
    
    Args:
        course_data: Dict with 'title' and 'slides' array
        output_path: Path where .pptx file will be saved
    """
    prs = Presentation()
    prs.slide_width = Inches(10)
    prs.slide_height = Inches(7.5)
    
    # Define colors
    TITLE_COLOR = RGBColor(44, 90, 160)  # Dark blue
    TEXT_COLOR = RGBColor(51, 51, 51)    # Dark gray
    
    for slide_data in course_data.get('slides', []):
        slide_type = slide_data.get('type', 'content')
        
        if slide_type == 'title':
            # Title slide
            slide = prs.slides.add_slide(prs.slide_layouts[6])  # Blank layout
            
            # Add title
            title_box = slide.shapes.add_textbox(
                Inches(0.5), Inches(2.5), Inches(9), Inches(1.5)
            )
            title_frame = title_box.text_frame
            title_frame.text = slide_data.get('content', course_data.get('title', 'Training Course'))
            
            # Format title
            for paragraph in title_frame.paragraphs:
                paragraph.alignment = PP_ALIGN.CENTER
                for run in paragraph.runs:
                    run.font.size = Pt(44)
                    run.font.bold = True
                    run.font.color.rgb = TITLE_COLOR
                    
        elif slide_type == 'objectives':
            # Objectives slide
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            
            # Title
            title_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.5), Inches(9), Inches(0.8))
            title_frame = title_box.text_frame
            title_frame.text = slide_data.get('title', 'Learning Objectives')
            for paragraph in title_frame.paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(36)
                    run.font.bold = True
                    run.font.color.rgb = TITLE_COLOR
            
            # Bullets
            if 'bullets' in slide_data:
                text_box = slide.shapes.add_textbox(Inches(1), Inches(1.5), Inches(8), Inches(5))
                text_frame = text_box.text_frame
                
                for bullet in slide_data['bullets']:
                    p = text_frame.add_paragraph()
                    p.text = bullet
                    p.level = 0
                    p.font.size = Pt(20)
                    p.font.color.rgb = TEXT_COLOR
                    
        elif slide_type == 'content':
            # Content slide
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            
            # Title
            title_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.5), Inches(9), Inches(0.8))
            title_frame = title_box.text_frame
            title_frame.text = slide_data.get('title', 'Content')
            for paragraph in title_frame.paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(32)
                    run.font.bold = True
                    run.font.color.rgb = TITLE_COLOR
            
            # Bullets
            if 'bullets' in slide_data:
                text_box = slide.shapes.add_textbox(Inches(1), Inches(1.5), Inches(8), Inches(5))
                text_frame = text_box.text_frame
                
                for bullet in slide_data['bullets']:
                    p = text_frame.add_paragraph()
                    p.text = bullet
                    p.level = 0
                    p.font.size = Pt(18)
                    p.font.color.rgb = TEXT_COLOR
                    
        elif slide_type == 'quiz':
            # Quiz slide
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            
            # Title
            title_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.5), Inches(9), Inches(0.6))
            title_frame = title_box.text_frame
            title_frame.text = slide_data.get('title', 'Knowledge Check')
            for paragraph in title_frame.paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(28)
                    run.font.bold = True
                    run.font.color.rgb = TITLE_COLOR
            
            # Question
            if 'question' in slide_data:
                q_box = slide.shapes.add_textbox(Inches(0.5), Inches(1.3), Inches(9), Inches(1))
                q_frame = q_box.text_frame
                q_frame.text = slide_data['question']
                for paragraph in q_frame.paragraphs:
                    for run in paragraph.runs:
                        run.font.size = Pt(20)
                        run.font.bold = True
                        run.font.color.rgb = TEXT_COLOR
            
            # Options
            if 'options' in slide_data:
                opts_box = slide.shapes.add_textbox(Inches(1), Inches(2.5), Inches(8), Inches(4))
                opts_frame = opts_box.text_frame
                
                for option in slide_data['options']:
                    p = opts_frame.add_paragraph()
                    p.text = option
                    p.level = 0
                    p.font.size = Pt(18)
                    p.font.color.rgb = TEXT_COLOR
                    
        elif slide_type == 'summary':
            # Summary slide
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            
            # Title
            title_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.5), Inches(9), Inches(0.8))
            title_frame = title_box.text_frame
            title_frame.text = slide_data.get('title', 'Key Takeaways')
            for paragraph in title_frame.paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(36)
                    run.font.bold = True
                    run.font.color.rgb = TITLE_COLOR
            
            # Bullets
            if 'bullets' in slide_data:
                text_box = slide.shapes.add_textbox(Inches(1), Inches(1.5), Inches(8), Inches(5))
                text_frame = text_box.text_frame
                
                for bullet in slide_data['bullets']:
                    p = text_frame.add_paragraph()
                    p.text = bullet
                    p.level = 0
                    p.font.size = Pt(20)
                    p.font.color.rgb = TEXT_COLOR
    
    # Save presentation
    prs.save(output_path)
    return output_path
