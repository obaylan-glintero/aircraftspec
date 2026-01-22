import streamlit as st
import fitz  # PyMuPDF
from fpdf import FPDF
from PIL import Image
import io
import tempfile
import os
import datetime
import json
import requests
import google.generativeai as genai

# --- CONFIGURATION ---
st.set_page_config(page_title="JetSpec Pro", page_icon="✈️", layout="wide")

# UI CSS - Responsive to light/dark mode
st.markdown("""
    <style>
    /* Gold button styling that works in both modes */
    .stButton>button {
        background-color: #D4AF37;
        color: #000000;
        border: none;
        font-weight: bold;
        transition: all 0.3s;
    }
    .stButton>button:hover {
        background-color: #B8941F;
        box-shadow: 0 4px 8px rgba(212, 175, 55, 0.3);
    }

    /* Download button styling */
    .stDownloadButton>button {
        background-color: #D4AF37;
        color: #000000;
        border: none;
        font-weight: bold;
    }
    .stDownloadButton>button:hover {
        background-color: #B8941F;
    }

    /* Expander styling - responsive to theme */
    div[data-testid="stExpander"] {
        border: 1px solid rgba(128, 128, 128, 0.3);
        border-radius: 4px;
    }

    /* Radio button labels */
    .stRadio > label {
        font-weight: 500;
    }

    /* Subheaders */
    .stApp h3 {
        color: #D4AF37;
        font-weight: 600;
    }
    </style>
    """, unsafe_allow_html=True)

# --- ROBUST FONT MANAGEMENT ---
def get_font_path(font_name):
    return os.path.join("fonts", font_name)

def download_file(url, filepath):
    try:
        r = requests.get(url)
        # Verify it's a real font file (> 5KB) and not a 404 HTML page
        if r.status_code == 200 and len(r.content) > 5000:
            with open(filepath, 'wb') as f:
                f.write(r.content)
            return True
        return False
    except:
        return False

def ensure_fonts_exist():
    fonts_dir = "fonts"
    if not os.path.exists(fonts_dir):
        os.makedirs(fonts_dir)
        
    # Correct Static URLs for Google Fonts (Using user provided and researched raw links)
    fonts = {
        "PlayfairDisplay-Bold.ttf": "https://raw.githubusercontent.com/itext/itext-publications-examples-java/develop/src/main/resources/font/PlayfairDisplay-Bold.ttf",
        "PlayfairDisplay-Regular.ttf": "https://raw.githubusercontent.com/itext/itext-publications-examples-java/develop/src/main/resources/font/PlayfairDisplay-Regular.ttf",
        "Manrope-Bold.ttf": "https://github.com/terrapkg/pkg-manrope-fonts/raw/refs/heads/main/manrope-bold.ttf",
        "Manrope-Regular.ttf": "https://github.com/terrapkg/pkg-manrope-fonts/raw/refs/heads/main/manrope-regular.ttf"
    }

    status = {}
    for name, url in fonts.items():
        path = os.path.join(fonts_dir, name)
        # Force redownload if file is small (likely broken)
        if os.path.exists(path) and os.path.getsize(path) < 10000:
             os.remove(path)
            
        if not os.path.exists(path):
            status[name] = download_file(url, path)
        else:
            status[name] = True
    return status

font_status = ensure_fonts_exist()

# --- PDF GENERATOR CLASS ---

class PDFGenerator(FPDF):
    def __init__(self):
        super().__init__(orientation='L', unit='mm', format='A4')
        self.set_auto_page_break(auto=True, margin=15)
        self.set_compression(False) # Disable compression for better image quality
        
        # Font Fallback Logic
        if font_status.get("PlayfairDisplay-Regular.ttf") and font_status.get("PlayfairDisplay-Bold.ttf"):
            try:
                self.add_font('PlayfairDisplay', '', 'fonts/PlayfairDisplay-Regular.ttf')
                self.add_font('PlayfairDisplay', 'B', 'fonts/PlayfairDisplay-Bold.ttf')
                self.serif_font = 'PlayfairDisplay'
            except: self.serif_font = 'Times'
        else: self.serif_font = 'Times'

        if font_status.get("Manrope-Regular.ttf") and font_status.get("Manrope-Bold.ttf"):
            try:
                self.add_font('Manrope', '', 'fonts/Manrope-Regular.ttf')
                self.add_font('Manrope', 'B', 'fonts/Manrope-Bold.ttf')
                self.sans_font = 'Manrope'
            except: self.sans_font = 'Helvetica'
        else: self.sans_font = 'Helvetica'

    def set_background(self):
        # Draw background color
        self.set_fill_color(5, 5, 5)
        # Ensure we cover the whole page
        self.rect(0, 0, 297, 210, 'F')

    # Removed header() to avoid logo occlusion. Logo will be drawn manually at the end of each page.

    def footer(self):
        self.set_y(-12)
        # Use SERIF for footer as requested ("Serif everywhere except tagline")
        # Or keep small sans for legibility? "Serif font everywhere except the tagline" -> implying strict.
        self.set_font(self.serif_font, '', 8)
        self.set_text_color(100, 100, 100)
        self.cell(0, 10, 'Specification subject to verification', 0, 0, 'C')
    
    def draw_logo(self):
        # Explicitly draw logo on top of everything
        possible_logos = ["Glintero Logo White.png", "logo.png", "fonts/logo.png"]
        logo_path = None
        for p in possible_logos:
            if os.path.exists(p):
                logo_path = p
                break
            
        if logo_path:
             # 297mm width. Top right.
             self.image(logo_path, x=260, y=10, w=25)

    def draw_gold_divider(self, x, y, w):
        self.set_fill_color(212, 175, 55)
        self.rect(x, y, w, 0.5, 'F')

# --- HELPER FUNCTIONS ---

def safe_text(text):
    if not text: return ""
    replacements = { "•": "-", "–": "-", "—": "-", "“": '"', "”": '"', "‘": "'", "’": "'", "…": "...", "\u202f": " ", "\u00a0": " " }
    for char, rep in replacements.items(): text = text.replace(char, rep)
    return text.encode('latin-1', 'ignore').decode('latin-1')

def extract_images_from_pdf(pdf_bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    extracted_images = {} 
    for page_num, page in enumerate(doc):
        page_idx = page_num + 1 
        image_list = page.get_images(full=True)
        page_images = []
        for img_index, img in enumerate(image_list):
            xref = img[0]
            try:
                # Use Pixmap for better quality and handling of masks/CMYK
                pix = fitz.Pixmap(doc, xref)
                
                # Convert CMYK or odd color spaces to RGB
                # if pix.n - pix.alpha > 3: # Greater than 3 means CMYK (4) usually
                # Actually, simpler check: if not RGB/GRAY
                if pix.n >= 5: # CMYK + Alpha or similar
                     pix = fitz.Pixmap(fitz.csRGB, pix)
                
                # Check for image size to filter icons
                # Increased threshold to avoid low-res thumbnails being blown up
                if pix.width > 400 and pix.height > 400:
                    image_data = pix.tobytes("png")
                    image = Image.open(io.BytesIO(image_data))
                    
                    page_images.append({
                        "id": f"p{page_idx}_img{img_index}",
                        "bytes": image_data, 
                        "pil": image,
                        "ext": "png", # Always PNG from pixmap
                        "page": page_idx,
                        "width": pix.width,
                        "height": pix.height
                    })
                
                pix = None # Release memory
            except Exception as e:
                # print(f"Image extraction error: {e}")
                continue
        if page_images: extracted_images[page_idx] = page_images
    return extracted_images

def parse_pdf_with_gemini(pdf_bytes):
    api_key = st.secrets.get("GEMINI_API_KEY")
    if not api_key: st.error("❌ Missing GEMINI_API_KEY"); return None

    genai.configure(api_key=api_key)
    
    # STRICT MODEL
    model_name = "gemini-3-pro-preview"
    try: model = genai.GenerativeModel(model_name)
    except: 
        try: model = genai.GenerativeModel(f"models/{model_name}")
        except Exception as e: st.error(f"Error loading {model_name}: {e}"); return None

    prompt = """
    Extract ALL aircraft specifications from this PDF into a JSON object.
    Strictly follow this structure:
    {
      "make": "Manufacturer", "model": "Model", "year": "Year",
      "tagline": "Marketing tagline", "description": "Summary paragraph",
      "highlights": [{"point": "highlight1"}, {"point": "highlight2"}],
      "description": "A comprehensive 2-3 sentence summary highlighting key features.",
      "keySpecs": [ {"label": "Total Time", "value": "3400 hrs"}, {"label": "Passengers", "value": "12"} ],
      "airframe": "Detailed text...", "engines": "Detailed text...", "apu": "Detailed text...",
      "avionics": "List items separated by newlines",
      "equipment": "List items separated by newlines",
      "maintenanceStatus": [ {"inspection": "12 Month", "lastPerformed": "date", "nextDue": "date"} ],
      "interior": "Detailed text...", "exterior": "Detailed text...",
      "imagePages": [ {"page": 1, "category": "hero"} ]
    }
    """
    try:
        response = model.generate_content([{'mime_type': 'application/pdf', 'data': pdf_bytes}, prompt])
        return json.loads(response.text.replace("```json", "").replace("```", "").strip())
    except Exception as e:
        st.error(f"AI Error: {e}"); return None

def generate_brochure_pdf(data, selected_images, variant="full"):
    pdf = PDFGenerator()
    GOLD = (212, 175, 55)
    SERIF = pdf.serif_font
    SANS = pdf.sans_font # Only for Tagline

    # Determine whether to show branding based on variant
    show_branding = (variant == "full")
    
    # -------------------------------------------------------------------------
    # 1. PAGE 1: COVER
    # -------------------------------------------------------------------------
    pdf.add_page()
    pdf.set_auto_page_break(False)
    pdf.set_background()

    if selected_images:
        hero_img = selected_images[0]
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{hero_img['ext']}") as tf:
            tf.write(hero_img['bytes'])
            tf.close()
            pdf.image(tf.name, x=0, y=0, w=297, h=210) 
            os.unlink(tf.name)
            
            pdf.set_y(150)
            pdf.set_fill_color(0, 0, 0)
            with pdf.local_context(fill_opacity=0.8):
                pdf.rect(0, 150, 297, 60, 'F')

    pdf.set_y(160)
    pdf.set_x(20)
    
    # Tagline is the ONLY place for Sans
    pdf.set_font(SANS, 'B', 10)
    pdf.set_text_color(*GOLD)
    pdf.cell(0, 8, safe_text(data.get('tagline', 'AIRCRAFT DOSSIER')).upper(), 0, 1, 'L')
    
    pdf.set_x(20)
    pdf.set_font(SERIF, 'B', 42)
    pdf.set_text_color(255, 255, 255)
    
    cur_m = pdf.l_margin
    pdf.set_left_margin(20)
    pdf.multi_cell(180, 18, safe_text(data.get('model', 'AIRCRAFT')), align='L')
    pdf.set_left_margin(cur_m)
    
    pdf.set_x(20)
    pdf.set_font(SERIF, '', 12)
    pdf.set_text_color(220, 220, 220)
    sub = f"{data.get('year', '')} | {data.get('make', '')}"
    pdf.cell(0, 10, safe_text(sub).upper(), 0, 1, 'L')

    if show_branding:
        pdf.draw_logo()

    # -------------------------------------------------------------------------
    # 2. PAGE 2: KEY SPECS + ASSET + HIGHLIGHTS
    # -------------------------------------------------------------------------
    
    # Check if we have enough data to justify this page
    has_specs = bool(data.get('keySpecs'))
    has_desc = bool(data.get('description'))
    has_highlights = bool(data.get('highlights'))
    
    if has_specs or has_desc or has_highlights:
        pdf.add_page()
        pdf.set_auto_page_break(True, margin=15)
        pdf.set_background()
        
        # Left Col: Specs
        if has_specs:
            pdf.set_y(20)
            pdf.set_x(15)
            pdf.set_font(SERIF, 'B', 20)
            pdf.set_text_color(255, 255, 255)
            pdf.cell(0, 10, "Technical Specifications", 0, 1)
            pdf.draw_gold_divider(15, pdf.get_y()+2, 120)
            pdf.ln(8)
    
            specs = data.get('keySpecs', [])
            y_start = pdf.get_y()
            
            if specs:
                row_height = 22
                col_width = 60
                pdf.set_draw_color(50, 50, 50)
                pdf.set_fill_color(20, 20, 20)
                
                for i, spec in enumerate(specs):
                    x_pos = 15 if i % 2 == 0 else 75
                    y_pos = y_start + (int(i/2) * row_height)
                    
                    if y_pos > 180: break 
    
                    pdf.set_xy(x_pos, y_pos)
                    pdf.rect(x_pos, y_pos, col_width, row_height, 'F')
                    
                    pdf.set_xy(x_pos + 4, y_pos + 4)
                    pdf.set_font(SERIF, '', 7) # Changed to Serif
                    pdf.set_text_color(150, 150, 150)
                    pdf.cell(col_width, 4, safe_text(spec.get('label', '')).upper())
                    
                    pdf.set_xy(x_pos + 4, y_pos + 10)
                    pdf.set_font(SERIF, '', 14)
                    pdf.set_text_color(255, 255, 255)
                    val = safe_text(str(spec.get('value', '')))
                    if pdf.get_string_width(val) > col_width - 8:
                        pdf.set_font(SERIF, '', 11)
                    pdf.cell(col_width, 8, val)
    
        # Right Col: The Asset & Highlights
        if has_desc or has_highlights:
            pdf.set_xy(145, 20)
            pdf.set_font(SERIF, 'B', 20)
            pdf.set_text_color(*GOLD)
            pdf.cell(0, 10, "The Asset", 0, 1)
            
            current_y = 35
            if has_desc:
                pdf.set_xy(145, current_y)
                pdf.set_font(SERIF, '', 10) # Changed to Serif
                pdf.set_text_color(220, 220, 220)
                
                cur_m = pdf.l_margin
                pdf.set_left_margin(145)
                pdf.set_right_margin(15)
                pdf.multi_cell(0, 6, safe_text(data.get('description', '')))
                pdf.set_left_margin(cur_m)
                
                current_y = pdf.get_y() + 5
            
            if has_highlights:
                pdf.set_y(current_y)
                pdf.set_right_margin(10)
                
                highlights = data.get('highlights', [])
                if highlights:
                    SAFE_WIDTH = 132
                    for h in highlights:
                        val = h.get('point', '') if isinstance(h, dict) else str(h)
                        if val:
                            if pdf.get_y() > 185: break
    
                            pdf.set_left_margin(145)
                            pdf.set_x(145) 
                            
                            pdf.set_fill_color(*GOLD)
                            pdf.rect(145, pdf.get_y()+2, 2, 2, 'F')
                            
                            pdf.set_x(150) 
                            pdf.set_text_color(200, 200, 200)
                            pdf.multi_cell(SAFE_WIDTH, 6, safe_text(val))

        if show_branding:
            pdf.draw_logo()

    # -------------------------------------------------------------------------
    # 3. PAGE 3: IMAGE PAGES (GALLERY)
    # -------------------------------------------------------------------------
    if len(selected_images) > 1:
        for i, img in enumerate(selected_images[1:]):
            pdf.add_page()
            pdf.set_auto_page_break(False)
            pdf.set_background()
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=f".{img['ext']}") as tf:
                tf.write(img['bytes'])
                tf.close()
                
                # Smart Scaling Logic - Preserve Aspect Ratio Always
                # A4 Landscape: 297mm x 210mm
                container_w = 297
                container_h = 210
                
                img_w_px = img.get('width', 800)
                img_h_px = img.get('height', 600)
                
                # Calculate aspect ratio
                if img_h_px == 0: aspect = 1.0 # Safety
                else: aspect = img_w_px / img_h_px
                
                # Calculate dimensions to fit within container (Contain)
                # First try fitting to width
                disp_w = container_w
                disp_h = disp_w / aspect
                
                # If height exceeds container, fit to height instead
                if disp_h > container_h:
                    disp_h = container_h
                    disp_w = disp_h * aspect
                
                # Center the image
                pos_x = (container_w - disp_w) / 2
                pos_y = (container_h - disp_h) / 2
                
                pdf.image(tf.name, x=pos_x, y=pos_y, w=disp_w, h=disp_h)

                os.unlink(tf.name)
            
            # --- Text Overlay ---
            pdf.set_y(190)
            pdf.set_x(10)
            
            # Ensure text overlay is on top of image (already is, since drawn after)
            pdf.set_fill_color(0, 0, 0)
            with pdf.local_context(fill_opacity=0.7):
                pdf.rect(10, 193, 80, 10, 'F')
            
            pdf.set_xy(12, 195)
            pdf.set_font(SERIF, '', 8) # Changed to Serif
            pdf.set_text_color(255, 255, 255)
            pdf.cell(76, 6, safe_text(f"{data.get('model', '')} | VIEW {i+1}").upper(), align='L')

            if show_branding:
                pdf.draw_logo()

    # -------------------------------------------------------------------------
    # Helper for Two-Column Text Blocks
    # -------------------------------------------------------------------------
    def print_dual_column_blocks(sections, title, uppercase_text=False):
        # Filter out empty sections
        valid_sections = [(name, content) for name, content in sections if content and str(content).strip()]
        
        if not valid_sections:
            return

        pdf.add_page()
        pdf.set_auto_page_break(True, margin=15)
        pdf.set_background()
        
        pdf.set_y(20)
        pdf.set_x(15)
        pdf.set_font(SERIF, 'B', 20)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(0, 10, title, 0, 1)
        pdf.draw_gold_divider(15, pdf.get_y()+2, 267)
        pdf.ln(10)

        LEFT_COL_X = 15
        RIGHT_COL_X = 155
        COL_WIDTH = 130
        
        def print_block(sub_title, content, x_pos, start_y):
            pdf.set_xy(x_pos, start_y)
            pdf.set_font(SERIF, 'B', 10) # Serif
            pdf.set_text_color(*GOLD)
            # sub_title might be None if we just flow text, but here we have sections
            if sub_title:
                pdf.cell(COL_WIDTH, 6, sub_title.upper(), 0, 1)
            
            pdf.set_x(x_pos)
            pdf.set_font(SERIF, '', 9) # Serif
            pdf.set_text_color(220, 220, 220)
            
            pdf.set_right_margin(297 - (x_pos + COL_WIDTH))
            pdf.set_left_margin(x_pos)
            
            txt = safe_text(str(content))
            
            # Bullet point logic
            # If text has multiple lines, ensure they look like bullets
            lines = txt.split('\n')
            
            # Helper to check if line is a bullet
            def is_bullet(l):
                 return l.strip().startswith('-') or l.strip().startswith('•') or l.strip().startswith('*')

            # Pre-process: ensure bullets exist if lines > 1
            processed_lines = []
            if len(lines) > 1:
                for l in lines:
                    l = l.strip()
                    if l:
                        if not is_bullet(l):
                             l = f"• {l}"
                        processed_lines.append(l)
            else:
                processed_lines = lines

            if not processed_lines: processed_lines = [txt]

            for l in processed_lines:
                l = l.strip()
                if not l: continue
                
                if is_bullet(l):
                    # Clean the bullet char
                    clean_text = l.lstrip('-•* ').strip()
                    if uppercase_text: clean_text = clean_text.upper()
                    
                    # Draw Gold Bullet
                    current_x = pdf.get_x()
                    current_y = pdf.get_y()
                    
                    pdf.set_text_color(*GOLD)
                    pdf.set_font(SERIF, 'B', 14) # Larger bullet
                    pdf.cell(5, 5, "•", 0, 0)
                    
                    # Draw Text
                    pdf.set_text_color(220, 220, 220)
                    pdf.set_font(SERIF, '', 9)
                    
                    # Calculate available width
                    avail_width = COL_WIDTH - 6
                    
                    # Store X for multiline indent
                    pdf.set_x(current_x + 6)
                    
                    # Use multi_cell for wrapping, but we need to manage X manually after
                    # FPDF multi_cell resets X to left margin usually.
                    # We can use a trick: save margins?
                    
                    # Simpler: Just print it.
                    pdf.multi_cell(avail_width, 5, clean_text)
                    
                    # Restore X ? No, multi_cell moves Y down. 
                    # Just ensure next 'l' starts at correct X
                    pdf.set_x(x_pos)
                else:
                    if uppercase_text: l = l.upper()
                    pdf.set_text_color(220, 220, 220)
                    pdf.set_font(SERIF, '', 9)
                    pdf.multi_cell(COL_WIDTH, 5, l)
                    pdf.set_x(x_pos)
            
            pdf.set_left_margin(15) # Default left
            pdf.set_right_margin(10) # Default right
            
            return pdf.get_y() + 5
            
            pdf.set_left_margin(15) # Default left
            pdf.set_right_margin(10) # Default right
            
            return pdf.get_y() + 5

        # Distribute sections to left/right
        mid = (len(valid_sections) + 1) // 2
        left_sections = valid_sections[:mid]
        right_sections = valid_sections[mid:]
        
        y_cursor = pdf.get_y()
        initial_y = y_cursor
        
        for name, content in left_sections:
            if content:
                if y_cursor > 180:
                    if show_branding:
                        pdf.draw_logo()
                    pdf.add_page()
                    pdf.set_background()
                    y_cursor = 20
                    initial_y = 20
                y_cursor = print_block(name, content, LEFT_COL_X, y_cursor)
        
        y_cursor_right = initial_y
        for name, content in right_sections:
            if content:
                 # Check right column overflow?
                 # For simplicity assuming it fits or flows.
                 y_cursor_right = print_block(name, content, RIGHT_COL_X, y_cursor_right)

        if show_branding:
            pdf.draw_logo()

    # -------------------------------------------------------------------------
    # 4. PAGE 4: TECH SPECS 2 (Airframe, Engine, APU)
    # -------------------------------------------------------------------------
    tech_specs_2 = [
        ("Airframe", data.get('airframe')),
        ("Engines", data.get('engines')),
        ("APU", data.get('apu'))
    ]
    # We can use the dual col helper
    print_dual_column_blocks(tech_specs_2, "Technical Specifications (Cont.)")

    # -------------------------------------------------------------------------
    # 5. PAGE 5: AVIONICS (2-Col, UPPERCASE)
    # -------------------------------------------------------------------------
    avi_text = data.get('avionics', "")
    # Treat Avionics as a single big block or split it? 
    # Usually it's a list. Let's try to split it into 2 columns if it's a list.
    # The helper expects [(Title, Content)].
    # If avi_text is a string, we can split it in half?
    # Or just print it in 2 columns flowing?
    # Let's simple split the text by lines.
    
    avi_lines = [x.strip() for x in avi_text.split('\n') if x.strip()]
    half = (len(avi_lines) + 1) // 2
    avi_left = "\n".join(avi_lines[:half])
    avi_right = "\n".join(avi_lines[half:])
    
    # We pass empty titles to just flow text
    avi_sections = [("", avi_left), ("", avi_right)]
    print_dual_column_blocks(avi_sections, "Avionics", uppercase_text=True)

    # -------------------------------------------------------------------------
    # 6. PAGE 6: EQUIPMENT (2-Col)
    # -------------------------------------------------------------------------
    eq_text = data.get('equipment', "")
    eq_lines = [x.strip() for x in eq_text.split('\n') if x.strip()]
    half_eq = (len(eq_lines) + 1) // 2
    eq_left = "\n".join(eq_lines[:half_eq])
    eq_right = "\n".join(eq_lines[half_eq:])
    
    eq_sections = [("", eq_left), ("", eq_right)]
    print_dual_column_blocks(eq_sections, "Equipment", uppercase_text=False) # Not strictly requested UPPERCASE for Equipment, but 2-col requested.

    # -------------------------------------------------------------------------
    # 7. PAGE 7: MAINTENANCE STATUS
    # -------------------------------------------------------------------------
    maint = data.get('maintenanceStatus', [])
    # Filter empty maintenance items if any
    valid_maint = [m for m in maint if m.get('inspection') or m.get('lastPerformed') or m.get('nextDue')]
    if valid_maint:
        pdf.add_page()
        pdf.set_auto_page_break(True, margin=15)
        pdf.set_background()
        
        pdf.set_xy(15, 20)
        pdf.set_font(SERIF, 'B', 18)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(125, 10, "Maintenance Status", 0, 1)
        pdf.draw_gold_divider(15, pdf.get_y()+2, 125)
        pdf.ln(8)
        
        pdf.set_font(SERIF, 'B', 8) # Serif
        pdf.set_text_color(*GOLD)
        pdf.set_x(15)
        pdf.cell(45, 8, "INSPECTION", 0, 0)
        pdf.cell(40, 8, "LAST", 0, 0)
        pdf.cell(40, 8, "NEXT", 0, 1)
        pdf.set_font(SERIF, '', 8) # Serif
        pdf.set_text_color(220, 220, 220)
        for item in valid_maint:
            pdf.set_x(15)
            ins = safe_text(item.get('inspection', ''))
            last = safe_text(item.get('lastPerformed', ''))
            nxt = safe_text(item.get('nextDue', ''))
            pdf.cell(45, 7, ins.upper(), "B")
            pdf.cell(40, 7, last.upper(), "B")
            pdf.cell(40, 7, nxt.upper(), "B", 1)

        if show_branding:
            pdf.draw_logo()
    
    # -------------------------------------------------------------------------
    # 8. PAGE 8: CONFIGURATION (Exterior, Interior)
    # -------------------------------------------------------------------------
    config_sections = [
        ("Interior", data.get('interior')),
        ("Exterior", data.get('exterior'))
    ]
    print_dual_column_blocks(config_sections, "Configuration Details")

    # -------------------------------------------------------------------------
    # 9. PAGE 9: CONTACT PAGE (Only for full variant)
    # -------------------------------------------------------------------------
    if show_branding:
        pdf.add_page()
        pdf.set_background()

        # Centered Contact Info
        pdf.set_y(80)

        # Replace "CONTACT US" text with Glintero Logo centered
        possible_logos = ["Glintero Logo White.png", "logo.png", "fonts/logo.png"]
        logo_path = None
        for p in possible_logos:
            if os.path.exists(p):
                logo_path = p
                break

        if logo_path:
            # Calculate center position for logo
            # Page width 297. Let's make logo reasonable size, e.g., width 80mm
            logo_w = 80
            # Aspect ratio of logo? We let FPDF handle it or just set W
            x_pos = (297 - logo_w) / 2

            # Draw logo centered
            pdf.image(logo_path, x=x_pos, y=60, w=logo_w)

            # Adjust Y for text below logo
            pdf.set_y(60 + 25) # Approx height of logo + spacing
        else:
            # Fallback if no logo
            pdf.set_font(SERIF, 'B', 40)
            pdf.set_text_color(*GOLD)
            pdf.cell(0, 15, "CONTACT US", 0, 1, 'C')

        pdf.ln(10)

        # Removed "GLINTERO AVIATION CONSULTANCY" text as requested

        # Use SANS for contact details for readability, or SERIF if strictly requested.
        # User said: "font used is not playfair display" implies they WANT playfair.
        # "Use a serif font everywhere except the tagline".
        # So we MUST use SERIF here too.
        pdf.set_font(SERIF, '', 14)
        pdf.set_text_color(200, 200, 200)
        pdf.cell(0, 8, "glintero@glintero.com", 0, 1, 'C')
        pdf.cell(0, 8, "+971 4 330 1528", 0, 1, 'C')
        pdf.cell(0, 8, "PO Box 453440, Dubai, UAE", 0, 1, 'C')
        pdf.cell(0, 8, "www.glintero.com", 0, 1, 'C')

        # Removed pdf.draw_logo() from this page as requested

    return bytes(pdf.output())

# --- MAIN APP ---

def main():
    st.title("✈️ JetSpec Pro")
    st.caption("Landscape Edition • Playfair & Manrope Fonts")

    if 'parsed_data' not in st.session_state: st.session_state['parsed_data'] = None
    if 'pdf_bytes' not in st.session_state: st.session_state['pdf_bytes'] = None
    if 'images' not in st.session_state: st.session_state['images'] = {}

    uploaded_file = st.file_uploader("Upload PDF Spec Sheet", type="pdf")

    if uploaded_file and st.session_state['pdf_bytes'] != uploaded_file.getvalue():
        st.session_state['pdf_bytes'] = uploaded_file.getvalue()
        st.session_state['parsed_data'] = None
        st.session_state['images'] = {}
        st.rerun()

    if st.session_state['pdf_bytes'] and not st.session_state['parsed_data']:
        if st.button("Analyze & Extract"):
            with st.spinner("Extracting with Gemini 3 Pro Preview..."):
                data = parse_pdf_with_gemini(st.session_state['pdf_bytes'])
                imgs = extract_images_from_pdf(st.session_state['pdf_bytes'])
                if data:
                    st.session_state['parsed_data'] = data
                    st.session_state['images'] = imgs
                    st.success("Extraction Complete")
                else: st.error("Extraction Failed")

    if st.session_state['parsed_data']:
        data = st.session_state['parsed_data']
        
        st.divider()
        st.subheader("1. Comprehensive Data Review")
        
        with st.form("data_form"):
            c1, c2, c3 = st.columns(3)
            data['make'] = c1.text_input("Make", data.get('make'))
            data['model'] = c2.text_input("Model", data.get('model'))
            data['year'] = c3.text_input("Year", data.get('year'))
            data['tagline'] = st.text_input("Tagline", data.get('tagline'))
            
            st.markdown("##### Key Specifications")
            if not data.get('keySpecs'): data['keySpecs'] = [{"label": "", "value": ""}]
            data['keySpecs'] = st.data_editor(data['keySpecs'], num_rows="dynamic", use_container_width=True)

            c_left, c_right = st.columns(2)
            with c_left:
                data['description'] = st.text_area("Description", data.get('description'), height=150)
                data['engines'] = st.text_area("Engines", data.get('engines'), height=100)
                data['airframe'] = st.text_area("Airframe", data.get('airframe'), height=100)
            with c_right:
                data['interior'] = st.text_area("Interior", data.get('interior'), height=100)
                data['exterior'] = st.text_area("Exterior", data.get('exterior'), height=100)
                data['apu'] = st.text_area("APU", data.get('apu'), height=100)

            st.markdown("##### Avionics & Equipment")
            c_a, c_e = st.columns(2)
            data['avionics'] = c_a.text_area("Avionics", data.get('avionics'), height=200)
            data['equipment'] = c_e.text_area("Equipment", data.get('equipment'), height=200)

            st.markdown("##### Maintenance Status")
            if not data.get('maintenanceStatus'): data['maintenanceStatus'] = [{"inspection": "", "lastPerformed": "", "nextDue": ""}]
            data['maintenanceStatus'] = st.data_editor(data['maintenanceStatus'], num_rows="dynamic", use_container_width=True)
            
            st.markdown("##### Highlights")
            hl = data.get('highlights', [])
            if hl and isinstance(hl[0], str): hl = [{"point": x} for x in hl]
            if not hl: hl = [{"point": ""}]
            data['highlights'] = st.data_editor(hl, num_rows="dynamic", use_container_width=True)

            if st.form_submit_button("Update Data"):
                st.session_state['parsed_data'] = data
                st.success("Data Updated")

        st.divider()
        st.subheader("2. Select Images")
        
        # New: Allow uploading additional photos
        uploaded_photos = st.file_uploader("Upload Additional Photos", type=["png", "jpg", "jpeg"], accept_multiple_files=True)
        additional_images = []
        if uploaded_photos:
            for i, photo in enumerate(uploaded_photos):
                try:
                    p_img = Image.open(photo)
                    # Convert to RGB if needed
                    if p_img.mode in ("RGBA", "P"): p_img = p_img.convert("RGB")
                    
                    b_io = io.BytesIO()
                    p_img.save(b_io, format="PNG")
                    img_bytes = b_io.getvalue()
                    
                    additional_images.append({
                        "id": f"uploaded_{i}",
                        "bytes": img_bytes,
                        "pil": p_img,
                        "ext": "png",
                        "page": 0, # 0 indicates uploaded
                        "width": p_img.width,
                        "height": p_img.height
                    })
                except Exception as e:
                    st.error(f"Error loading {photo.name}: {e}")

        # Merge extracted and uploaded
        extracted_flat = [img for page in st.session_state['images'].values() for img in page]
        all_images = extracted_flat + additional_images

        if not all_images:
            st.info("No images found.")
        else:
            selected = []
            cols = st.columns(4)
            for i, img in enumerate(all_images):
                with cols[i % 4]:
                    st.image(img['pil'], use_container_width=True)
                    suggested = any(p['page'] == img['page'] for p in data.get('imagePages', []))
                    label = "HERO" if i == 0 else "Select"
                    if st.checkbox(label, key=f"img_{i}", value=suggested):
                        selected.append(img)

        st.divider()
        st.subheader("3. Select Spec Sheet Variant")
        variant = st.radio(
            "Choose which version to generate:",
            options=["full", "clean"],
            format_func=lambda x: "Full Spec Sheet (with Glintero branding & contact page)" if x == "full" else "Clean Spec Sheet (no logo, no contact page)",
            index=0
        )

        st.divider()
        if st.button("GENERATE LANDSCAPE DOSSIER", type="primary"):
            with st.spinner("Compiling PDF..."):
                pdf_data = generate_brochure_pdf(data, selected, variant=variant)
                st.download_button(
                    label="Download Dossier",
                    data=pdf_data,
                    file_name=f"Dossier_{data.get('model', 'Aircraft')}.pdf",
                    mime="application/pdf"
                )

if __name__ == "__main__":
    main()