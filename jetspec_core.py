"""
JetSpec Pro — Headless Core Module
Extracts structured data from aircraft spec PDFs and generates branded Glintero dossiers.

Usage as library:
    from jetspec_core import extract_specs, extract_images, generate_pdf

Usage as CLI:
    python jetspec_core.py input.pdf --output output.pdf [--variant full|clean] [--json specs.json]
"""

import fitz  # PyMuPDF
from fpdf import FPDF
from PIL import Image
import io
import tempfile
import os
import json
import requests


# ---------------------------------------------------------------------------
# Font Management
# ---------------------------------------------------------------------------

FONT_URLS = {
    "PlayfairDisplay-Bold.ttf": "https://raw.githubusercontent.com/itext/itext-publications-examples-java/develop/src/main/resources/font/PlayfairDisplay-Bold.ttf",
    "PlayfairDisplay-Regular.ttf": "https://raw.githubusercontent.com/itext/itext-publications-examples-java/develop/src/main/resources/font/PlayfairDisplay-Regular.ttf",
    "Manrope-Bold.ttf": "https://github.com/terrapkg/pkg-manrope-fonts/raw/refs/heads/main/manrope-bold.ttf",
    "Manrope-Regular.ttf": "https://github.com/terrapkg/pkg-manrope-fonts/raw/refs/heads/main/manrope-regular.ttf",
}

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
FONTS_DIR = os.path.join(_THIS_DIR, "fonts")
LOGO_CANDIDATES = [
    os.path.join(_THIS_DIR, "Glintero Logo White.png"),
    os.path.join(_THIS_DIR, "logo.png"),
]


def _download_file(url, filepath):
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200 and len(r.content) > 5000:
            with open(filepath, "wb") as f:
                f.write(r.content)
            return True
        return False
    except Exception:
        return False


def ensure_fonts():
    os.makedirs(FONTS_DIR, exist_ok=True)
    status = {}
    for name, url in FONT_URLS.items():
        path = os.path.join(FONTS_DIR, name)
        if os.path.exists(path) and os.path.getsize(path) < 10000:
            os.remove(path)
        if not os.path.exists(path):
            status[name] = _download_file(url, path)
        else:
            status[name] = True
    return status


# ---------------------------------------------------------------------------
# PDF Text Helpers
# ---------------------------------------------------------------------------

def safe_text(text):
    if not text:
        return ""
    replacements = {
        "\u2022": "-", "\u2013": "-", "\u2014": "-",
        "\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'",
        "\u2026": "...", "\u202f": " ", "\u00a0": " ",
    }
    for char, rep in replacements.items():
        text = text.replace(char, rep)
    return text.encode("latin-1", "ignore").decode("latin-1")


# ---------------------------------------------------------------------------
# Image Extraction
# ---------------------------------------------------------------------------

def extract_images(pdf_bytes, min_dimension=400):
    """Extract large images from a PDF. Returns {page_num: [image_dict, ...]}."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    extracted = {}
    for page_num, page in enumerate(doc):
        page_idx = page_num + 1
        page_images = []
        for img_index, img in enumerate(page.get_images(full=True)):
            xref = img[0]
            try:
                pix = fitz.Pixmap(doc, xref)
                if pix.n >= 5:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                if pix.width > min_dimension and pix.height > min_dimension:
                    # Convert to JPEG for much smaller file sizes
                    png_data = pix.tobytes("png")
                    pil_img = Image.open(io.BytesIO(png_data))
                    if pil_img.mode in ("RGBA", "P", "LA"):
                        pil_img = pil_img.convert("RGB")
                    jpg_buf = io.BytesIO()
                    pil_img.save(jpg_buf, format="JPEG", quality=85, optimize=True)
                    jpg_data = jpg_buf.getvalue()
                    page_images.append({
                        "id": f"p{page_idx}_img{img_index}",
                        "bytes": jpg_data,
                        "pil": pil_img,
                        "ext": "jpg",
                        "page": page_idx,
                        "width": pix.width,
                        "height": pix.height,
                    })
                pix = None
            except Exception:
                continue
        if page_images:
            extracted[page_idx] = page_images
    return extracted


# ---------------------------------------------------------------------------
# Image Classification (filter non-aircraft photos)
# ---------------------------------------------------------------------------

def classify_images(images_flat: list, api_key: str, model_name: str = "gemini-2.5-flash") -> list:
    """Classify extracted images and keep only aircraft-related photos.
    
    Returns list of dicts with added 'category' field:
    - 'aircraft_exterior' — exterior shots of the aircraft
    - 'aircraft_interior' — cabin, cockpit, galley
    - 'aircraft_detail' — close-ups of engines, avionics panels, landing gear
    - 'rejected' — logos, headshots, marketing graphics, text pages
    """
    import google.generativeai as genai
    from PIL import Image as PILImage
    
    if not images_flat:
        return []
    
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name,
        generation_config={"temperature": 0.1},
    )
    
    # Build a contact sheet of thumbnails for batch classification
    # This is cheaper than classifying each image individually
    results = []
    
    for img in images_flat:
        pil = img.get("pil")
        if pil is None:
            pil = PILImage.open(io.BytesIO(img["bytes"]))
        
        # Resize for classification (don't waste tokens on full-res)
        thumb = pil.copy()
        thumb.thumbnail((512, 512))
        if thumb.mode in ("RGBA", "P", "LA"):
            thumb = thumb.convert("RGB")
        
        buf = io.BytesIO()
        thumb.save(buf, format="JPEG", quality=70)
        thumb_bytes = buf.getvalue()
        
        try:
            response = model.generate_content([
                {"mime_type": "image/jpeg", "data": thumb_bytes},
                """Classify this image from an aircraft specification PDF. Reply with EXACTLY one word:
- EXTERIOR (aircraft exterior photo)
- INTERIOR (cabin, cockpit, galley, lavatory)
- DETAIL (close-up of engine, avionics, landing gear, equipment)
- REJECT (person/headshot, company logo, marketing graphic, text/diagram, decorative element)

Reply with ONLY the single classification word, nothing else."""
            ])
            category = response.text.strip().upper()
            
            category_map = {
                "EXTERIOR": "aircraft_exterior",
                "INTERIOR": "aircraft_interior",
                "DETAIL": "aircraft_detail",
                "REJECT": "rejected",
            }
            img["category"] = category_map.get(category, "rejected")
        except Exception:
            # If classification fails, keep the image (conservative)
            img["category"] = "aircraft_exterior"
        
        results.append(img)
    
    return results


def filter_aircraft_images(classified_images: list) -> list:
    """Return only aircraft-related images, ordered: exterior first, then interior, then detail."""
    order = {"aircraft_exterior": 0, "aircraft_interior": 1, "aircraft_detail": 2}
    kept = [img for img in classified_images if img.get("category", "rejected") != "rejected"]
    kept.sort(key=lambda x: order.get(x.get("category", ""), 99))
    return kept


# ---------------------------------------------------------------------------
# Spec Extraction via Gemini
# ---------------------------------------------------------------------------

def extract_specs(pdf_bytes, api_key, model_name="gemini-2.5-flash"):
    """Send PDF to Gemini and get structured aircraft spec JSON back."""
    import time
    import google.generativeai as genai

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name,
        generation_config={"temperature": 0.1, "top_p": 0.95},
    )

    prompt = """
    Extract ALL aircraft specifications from this PDF into a JSON object.
    Strictly follow this structure:
    {
      "make": "Manufacturer", "model": "Model", "year": "Year",
      "serial": "Serial number if available",
      "registration": "Registration mark if available",
      "tagline": "Marketing tagline",
      "description": "A comprehensive 2-3 sentence summary highlighting key features.",
      "highlights": [{"point": "highlight1"}, {"point": "highlight2"}],
      "keySpecs": [max 4 items — ONLY the most important buyer-facing stats],
      "airframe": "Detailed text...", "engines": "Detailed text...", "apu": "Detailed text...",
      "avionics": "List items separated by newlines",
      "equipment": "List items separated by newlines",
      "maintenanceStatus": [ {"inspection": "12 Month", "lastPerformed": "date", "nextDue": "date"} ],
      "interior": "Detailed text...", "exterior": "Detailed text...",
      "imagePages": [ {"page": 1, "category": "hero"} ]
    }

    CRITICAL RULES FOR keySpecs:
    - Maximum 4 items. Pick ONLY what a buyer needs at a glance.
    - Priority order: Total Time (hours only, no cycles/landings), Passengers/Seating, Range (nm), Year of Manufacture
    - Do NOT put engine type, floats, registration, serial, or equipment in keySpecs — those go in their own sections
    - Values must be SHORT — e.g. "3,400 hrs" not "3,400 Hours / 2,100 Cycles (As of Jan 2024)"
    - Put cycles, landings, and detailed breakdowns in the airframe/engines sections instead
    - Registration and serial go in their own top-level fields, NOT in keySpecs

    Return ONLY valid JSON, no markdown fences.
    """

    max_retries = 3
    for attempt in range(max_retries):
        try:
            if attempt > 0:
                time.sleep(2 ** attempt)
            response = model.generate_content([
                {"mime_type": "application/pdf", "data": pdf_bytes},
                prompt,
            ])
            text = response.text.replace("```json", "").replace("```", "").strip()
            data = json.loads(text)
            # Normalize keySpecs format — Gemini sometimes returns {key: val} instead of {label, value}
            if data.get("keySpecs"):
                normalized = []
                for item in data["keySpecs"]:
                    if "label" in item and "value" in item:
                        normalized.append(item)
                    elif isinstance(item, dict) and len(item) == 1:
                        k, v = next(iter(item.items()))
                        normalized.append({"label": k, "value": str(v)})
                    elif isinstance(item, dict):
                        # Take first two keys as label/value
                        keys = list(item.keys())
                        normalized.append({"label": keys[0], "value": str(item[keys[0]])})
                data["keySpecs"] = normalized
            return data
        except Exception as e:
            error_msg = str(e)
            if any(kw in error_msg.lower() for kw in ("504", "deadline", "timeout")):
                if attempt < max_retries - 1:
                    continue
            raise RuntimeError(f"Gemini extraction failed: {error_msg}")

    raise RuntimeError("Gemini extraction failed after retries")


# ---------------------------------------------------------------------------
# PDF Generator
# ---------------------------------------------------------------------------

class PDFGenerator(FPDF):
    def __init__(self, font_status):
        super().__init__(orientation="L", unit="mm", format="A4")
        self.set_auto_page_break(auto=True, margin=15)
        self.set_compression(True)  # Compress images for sane file sizes

        if font_status.get("PlayfairDisplay-Regular.ttf") and font_status.get("PlayfairDisplay-Bold.ttf"):
            try:
                self.add_font("PlayfairDisplay", "", os.path.join(FONTS_DIR, "PlayfairDisplay-Regular.ttf"))
                self.add_font("PlayfairDisplay", "B", os.path.join(FONTS_DIR, "PlayfairDisplay-Bold.ttf"))
                self.serif_font = "PlayfairDisplay"
            except Exception:
                self.serif_font = "Times"
        else:
            self.serif_font = "Times"

        if font_status.get("Manrope-Regular.ttf") and font_status.get("Manrope-Bold.ttf"):
            try:
                self.add_font("Manrope", "", os.path.join(FONTS_DIR, "Manrope-Regular.ttf"))
                self.add_font("Manrope", "B", os.path.join(FONTS_DIR, "Manrope-Bold.ttf"))
                self.sans_font = "Manrope"
            except Exception:
                self.sans_font = "Helvetica"
        else:
            self.sans_font = "Helvetica"

    def set_background(self):
        self.set_fill_color(5, 5, 5)
        self.rect(0, 0, 297, 210, "F")

    def footer(self):
        self.set_y(-12)
        self.set_font(self.serif_font, "", 8)
        self.set_text_color(100, 100, 100)
        self.cell(0, 10, "Specification subject to verification", 0, 0, "C")

    def draw_logo(self):
        for p in LOGO_CANDIDATES:
            if os.path.exists(p):
                self.image(p, x=260, y=10, w=25)
                return

    def draw_gold_divider(self, x, y, w):
        self.set_fill_color(212, 175, 55)
        self.rect(x, y, w, 0.5, "F")


def generate_pdf(data, selected_images, variant="full", font_status=None):
    """Generate the branded PDF dossier. Returns PDF bytes."""
    if font_status is None:
        font_status = ensure_fonts()

    pdf = PDFGenerator(font_status)
    GOLD = (212, 175, 55)
    SERIF = pdf.serif_font
    SANS = pdf.sans_font
    show_branding = variant == "full"

    # ---- PAGE 1: COVER ----
    pdf.add_page()
    pdf.set_auto_page_break(False)
    pdf.set_background()

    if selected_images:
        hero_img = selected_images[0]
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{hero_img['ext']}") as tf:
            tf.write(hero_img["bytes"])
            tf.close()
            cw, ch = 297, 210
            iw = hero_img.get("width", 1920)
            ih = hero_img.get("height", 1080)
            aspect = iw / max(ih, 1)
            dw = cw
            dh = dw / aspect
            if dh > ch:
                dh = ch
                dw = dh * aspect
            px = (cw - dw) / 2
            py = (ch - dh) / 2
            pdf.image(tf.name, x=px, y=py, w=dw, h=dh)
            os.unlink(tf.name)
            pdf.set_y(150)
            pdf.set_fill_color(0, 0, 0)
            with pdf.local_context(fill_opacity=0.8):
                pdf.rect(0, 150, 297, 60, "F")

    pdf.set_y(160)
    pdf.set_x(20)
    pdf.set_font(SANS, "B", 10)
    pdf.set_text_color(*GOLD)
    pdf.cell(0, 8, safe_text(data.get("tagline", "AIRCRAFT DOSSIER")).upper(), 0, 1, "L")

    pdf.set_x(20)
    pdf.set_font(SERIF, "B", 42)
    pdf.set_text_color(255, 255, 255)
    cur_m = pdf.l_margin
    pdf.set_left_margin(20)
    pdf.multi_cell(180, 18, safe_text(data.get("model", "AIRCRAFT")), align="L")
    pdf.set_left_margin(cur_m)

    pdf.set_x(20)
    pdf.set_font(SERIF, "", 12)
    pdf.set_text_color(220, 220, 220)
    pdf.cell(0, 10, safe_text(f"{data.get('year', '')} | {data.get('make', '')}").upper(), 0, 1, "L")
    if show_branding:
        pdf.draw_logo()

    # ---- PAGE 2: KEY SPECS + ASSET + HIGHLIGHTS ----
    has_specs = bool(data.get("keySpecs"))
    has_desc = bool(data.get("description"))
    has_hl = bool(data.get("highlights"))

    if has_specs or has_desc or has_hl:
        pdf.add_page()
        pdf.set_auto_page_break(True, margin=15)
        pdf.set_background()

        if has_specs:
            pdf.set_y(20); pdf.set_x(15)
            pdf.set_font(SERIF, "B", 20)
            pdf.set_text_color(255, 255, 255)
            pdf.cell(0, 10, "Technical Specifications", 0, 1)
            pdf.draw_gold_divider(15, pdf.get_y() + 2, 120)
            pdf.ln(8)
            specs = data.get("keySpecs", [])
            y0 = pdf.get_y()

            # --- Stat cards: 2x2 grid, max 4 cards, bigger and bolder ---
            NUM_COLS = 2
            GRID_LEFT = 15
            GRID_WIDTH = 120
            GAP = 4
            card_w = (GRID_WIDTH - GAP * (NUM_COLS - 1)) / NUM_COLS
            card_h = 28
            specs = specs[:4]  # Hard cap at 4

            pdf.set_draw_color(50, 50, 50)

            for i, spec in enumerate(specs):
                col = i % NUM_COLS
                row = i // NUM_COLS
                xp = GRID_LEFT + col * (card_w + GAP)
                yp = y0 + row * (card_h + GAP)
                if yp + card_h > 185:
                    break

                # Card background
                pdf.set_fill_color(20, 20, 20)
                pdf.rect(xp, yp, card_w, card_h, "F")

                # Gold accent bar on top of card
                pdf.set_fill_color(212, 175, 55)
                pdf.rect(xp, yp, card_w, 0.8, "F")

                # Label
                pdf.set_xy(xp + 3, yp + 3)
                pdf.set_font(SERIF, "", 6)
                pdf.set_text_color(130, 130, 130)
                pdf.cell(card_w - 6, 3, safe_text(spec.get("label", "")).upper())

                # Value — clean up parenthetical dates, smart font sizing
                raw_val = str(spec.get("value", ""))
                # Strip "(As of ...)" type suffixes for cleaner display
                clean_val = raw_val
                paren_match = raw_val.find(" (")
                if paren_match > 0:
                    clean_val = raw_val[:paren_match].strip()

                val = safe_text(clean_val)
                pdf.set_xy(xp + 3, yp + 9)
                pdf.set_text_color(255, 255, 255)

                # Adaptive font size — bigger cards = bigger text
                if len(val) <= 10:
                    pdf.set_font(SERIF, "B", 18)
                elif len(val) <= 20:
                    pdf.set_font(SERIF, "B", 13)
                else:
                    pdf.set_font(SERIF, "B", 10)

                # Use multi_cell for long values that need wrapping
                avail_w = card_w - 6
                if pdf.get_string_width(val) > avail_w:
                    pdf.set_left_margin(xp + 3)
                    pdf.multi_cell(avail_w, 4.5, val)
                    pdf.set_left_margin(15)
                else:
                    pdf.cell(avail_w, 10, val)

        if has_desc or has_hl:
            pdf.set_xy(145, 20)
            pdf.set_font(SERIF, "B", 20)
            pdf.set_text_color(*GOLD)
            pdf.cell(0, 10, "The Asset", 0, 1)
            cy = 35
            if has_desc:
                pdf.set_xy(145, cy)
                pdf.set_font(SERIF, "", 10)
                pdf.set_text_color(220, 220, 220)
                cm = pdf.l_margin
                pdf.set_left_margin(145); pdf.set_right_margin(15)
                pdf.multi_cell(0, 6, safe_text(data.get("description", "")))
                pdf.set_left_margin(cm)
                cy = pdf.get_y() + 5
            if has_hl:
                pdf.set_y(cy); pdf.set_right_margin(10)
                for h in data.get("highlights", []):
                    val = h.get("point", "") if isinstance(h, dict) else str(h)
                    if val:
                        if pdf.get_y() > 185: break
                        pdf.set_left_margin(145); pdf.set_x(145)
                        pdf.set_fill_color(*GOLD)
                        pdf.rect(145, pdf.get_y() + 2, 2, 2, "F")
                        pdf.set_x(150)
                        pdf.set_text_color(200, 200, 200)
                        pdf.multi_cell(132, 6, safe_text(val))
        if show_branding:
            pdf.draw_logo()

    # ---- IMAGE GALLERY ----
    if len(selected_images) > 1:
        for i, img in enumerate(selected_images[1:]):
            pdf.add_page()
            pdf.set_auto_page_break(False)
            pdf.set_background()
            with tempfile.NamedTemporaryFile(delete=False, suffix=f".{img['ext']}") as tf:
                tf.write(img["bytes"]); tf.close()
                cw2, ch2 = 297, 210
                iw2, ih2 = img.get("width", 800), img.get("height", 600)
                a2 = iw2 / max(ih2, 1)
                dw2 = cw2; dh2 = dw2 / a2
                if dh2 > ch2: dh2 = ch2; dw2 = dh2 * a2
                pdf.image(tf.name, x=(cw2-dw2)/2, y=(ch2-dh2)/2, w=dw2, h=dh2)
                os.unlink(tf.name)
            pdf.set_y(190); pdf.set_x(10)
            pdf.set_fill_color(0, 0, 0)
            with pdf.local_context(fill_opacity=0.7):
                pdf.rect(10, 193, 80, 10, "F")
            pdf.set_xy(12, 195)
            pdf.set_font(SERIF, "", 8)
            pdf.set_text_color(255, 255, 255)
            pdf.cell(76, 6, safe_text(f"{data.get('model', '')} | VIEW {i+1}").upper(), align="L")
            if show_branding:
                pdf.draw_logo()

    # ---- DUAL COLUMN HELPER ----
    def print_dual(sections, title, uppercase_text=False):
        valid = [(n, c) for n, c in sections if c and str(c).strip()]
        if not valid: return
        pdf.add_page()
        pdf.set_auto_page_break(True, margin=15)
        pdf.set_background()
        pdf.set_y(20); pdf.set_x(15)
        pdf.set_font(SERIF, "B", 20)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(0, 10, title, 0, 1)
        pdf.draw_gold_divider(15, pdf.get_y() + 2, 267)
        pdf.ln(10)

        def block(sub_t, content, xp, sy):
            pdf.set_xy(xp, sy)
            if sub_t:
                pdf.set_font(SERIF, "B", 10); pdf.set_text_color(*GOLD)
                pdf.cell(130, 6, sub_t.upper(), 0, 1)
            pdf.set_x(xp)
            pdf.set_font(SERIF, "", 9); pdf.set_text_color(220, 220, 220)
            pdf.set_right_margin(297 - (xp + 130)); pdf.set_left_margin(xp)
            txt = safe_text(str(content))
            lines = txt.split("\n")
            proc = []
            if len(lines) > 1:
                for l in lines:
                    l = l.strip()
                    if l:
                        if not l.startswith(("-", "*")):
                            l = f"- {l}"
                        proc.append(l)
            else:
                proc = lines
            for l in proc:
                l = l.strip()
                if not l: continue
                if l.startswith(("-", "*")):
                    cl = l.lstrip("-* ").strip()
                    if uppercase_text: cl = cl.upper()
                    cx = pdf.get_x()
                    pdf.set_text_color(*GOLD); pdf.set_font(SERIF, "B", 14)
                    pdf.cell(5, 5, "-", 0, 0)
                    pdf.set_text_color(220, 220, 220); pdf.set_font(SERIF, "", 9)
                    pdf.set_x(cx + 6)
                    pdf.multi_cell(124, 5, cl)
                    pdf.set_x(xp)
                else:
                    if uppercase_text: l = l.upper()
                    pdf.multi_cell(130, 5, l); pdf.set_x(xp)
            pdf.set_left_margin(15); pdf.set_right_margin(10)
            return pdf.get_y() + 5

        mid = (len(valid) + 1) // 2
        yc = pdf.get_y(); iy = yc
        for n, c in valid[:mid]:
            if yc > 180:
                if show_branding: pdf.draw_logo()
                pdf.add_page(); pdf.set_background(); yc = 20; iy = 20
            yc = block(n, c, 15, yc)
        yr = iy
        for n, c in valid[mid:]:
            yr = block(n, c, 155, yr)
        if show_branding:
            pdf.draw_logo()

    # ---- TECH SPECS 2 ----
    print_dual([("Airframe", data.get("airframe")), ("Engines", data.get("engines")), ("APU", data.get("apu"))], "Technical Specifications (Cont.)")

    # ---- AVIONICS ----
    avi = data.get("avionics", "")
    al = [x.strip() for x in avi.split("\n") if x.strip()]
    h = (len(al) + 1) // 2
    print_dual([("", "\n".join(al[:h])), ("", "\n".join(al[h:]))], "Avionics", uppercase_text=True)

    # ---- EQUIPMENT ----
    eq = data.get("equipment", "")
    el = [x.strip() for x in eq.split("\n") if x.strip()]
    he = (len(el) + 1) // 2
    print_dual([("", "\n".join(el[:he])), ("", "\n".join(el[he:]))], "Equipment")

    # ---- MAINTENANCE STATUS ----
    maint = [m for m in data.get("maintenanceStatus", []) if m.get("inspection") or m.get("lastPerformed") or m.get("nextDue")]
    if maint:
        pdf.add_page(); pdf.set_auto_page_break(True, margin=15); pdf.set_background()

        # Title
        pdf.set_xy(15, 20); pdf.set_font(SERIF, "B", 20); pdf.set_text_color(255, 255, 255)
        pdf.cell(0, 10, "Maintenance Status", 0, 1)
        pdf.draw_gold_divider(15, pdf.get_y() + 2, 267); pdf.ln(8)

        # Full-width table: 3 columns spanning the page
        TABLE_LEFT = 15
        TABLE_WIDTH = 267  # 297 - 15*2
        COL_W1 = TABLE_WIDTH * 0.40  # Inspection name — widest
        COL_W2 = TABLE_WIDTH * 0.30  # Last performed
        COL_W3 = TABLE_WIDTH * 0.30  # Next due

        # Header row
        pdf.set_x(TABLE_LEFT)
        pdf.set_font(SERIF, "B", 9); pdf.set_text_color(*GOLD)
        pdf.set_fill_color(15, 15, 15)
        pdf.cell(COL_W1, 10, "INSPECTION", 0, 0, fill=True)
        pdf.cell(COL_W2, 10, "LAST PERFORMED", 0, 0, fill=True)
        pdf.cell(COL_W3, 10, "NEXT DUE", 0, 1, fill=True)

        # Data rows
        pdf.set_font(SERIF, "", 9); pdf.set_text_color(220, 220, 220)
        for i, item in enumerate(maint):
            pdf.set_x(TABLE_LEFT)
            # Alternating row background
            if i % 2 == 0:
                pdf.set_fill_color(18, 18, 18)
            else:
                pdf.set_fill_color(12, 12, 12)

            row_h = 9
            pdf.cell(COL_W1, row_h, safe_text(item.get("inspection", "")).upper(), 0, 0, fill=True)
            pdf.cell(COL_W2, row_h, safe_text(item.get("lastPerformed", "")).upper(), 0, 0, fill=True)
            pdf.cell(COL_W3, row_h, safe_text(item.get("nextDue", "")).upper(), 0, 1, fill=True)

            # Subtle divider between rows
            pdf.set_fill_color(40, 40, 40)
            pdf.rect(TABLE_LEFT, pdf.get_y(), TABLE_WIDTH, 0.2, "F")

        if show_branding: pdf.draw_logo()

    # ---- CONFIGURATION ----
    print_dual([("Interior", data.get("interior")), ("Exterior", data.get("exterior"))], "Configuration Details")

    # ---- CONTACT PAGE ----
    if show_branding:
        pdf.add_page(); pdf.set_background()
        lp = None
        for p in LOGO_CANDIDATES:
            if os.path.exists(p): lp = p; break
        if lp:
            pdf.image(lp, x=(297 - 80) / 2, y=60, w=80); pdf.set_y(85)
        else:
            pdf.set_y(80); pdf.set_font(SERIF, "B", 40); pdf.set_text_color(*GOLD)
            pdf.cell(0, 15, "CONTACT US", 0, 1, "C")
        pdf.ln(10); pdf.set_font(SERIF, "", 14); pdf.set_text_color(200, 200, 200)
        pdf.cell(0, 8, "sales@glintero.com", 0, 1, "C")
        pdf.cell(0, 8, "+971 4 330 1528", 0, 1, "C")
        pdf.cell(0, 8, "PO Box 453440, Dubai, UAE", 0, 1, "C")
        pdf.cell(0, 8, "www.glintero.com", 0, 1, "C")

    return bytes(pdf.output())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(description="JetSpec Pro - Headless Aircraft Spec PDF Generator")
    parser.add_argument("input", help="Input PDF spec sheet")
    parser.add_argument("--output", "-o", help="Output PDF path")
    parser.add_argument("--variant", choices=["full", "clean"], default="full")
    parser.add_argument("--json", dest="json_out", help="Save extracted specs JSON")
    parser.add_argument("--json-in", dest="json_in", help="Use pre-extracted specs JSON")
    parser.add_argument("--api-key", dest="api_key", help="Gemini API key (or GEMINI_API_KEY env)")
    parser.add_argument("--model", default="gemini-2.5-flash", help="Gemini model")
    parser.add_argument("--select-images", dest="select_images", default="all",
                        help="'all', 'hero', or comma-separated page numbers")
    parser.add_argument("--no-filter", dest="no_filter", action="store_true",
                        help="Skip AI image classification (include all images)")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: {args.input} not found"); return 1

    print(f"Reading {args.input}...")
    with open(args.input, "rb") as f:
        pdf_bytes = f.read()

    print("Checking fonts...")
    font_status = ensure_fonts()

    if args.json_in:
        print(f"Loading specs from {args.json_in}...")
        with open(args.json_in) as f:
            data = json.load(f)
    else:
        api_key = args.api_key or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            print("Error: No Gemini API key. Pass --api-key or set GEMINI_API_KEY"); return 1
        print(f"Extracting specs via {args.model}...")
        data = extract_specs(pdf_bytes, api_key, args.model)
        print(f"  -> {data.get('year', '?')} {data.get('make', '?')} {data.get('model', '?')}")

    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump(data, f, indent=2)
        print(f"Specs saved to {args.json_out}")

    print("Extracting images...")
    all_images = extract_images(pdf_bytes)
    flat = [img for imgs in all_images.values() for img in imgs]
    print(f"  -> Found {len(flat)} images across {len(all_images)} pages")

    if args.select_images == "hero":
        selected = flat[:1]
    elif args.select_images == "all":
        selected = flat
    else:
        pages = [int(p.strip()) for p in args.select_images.split(",")]
        selected = [img for img in flat if img["page"] in pages]

    # AI image classification — filter out non-aircraft images
    if not args.no_filter and selected:
        api_key = args.api_key or os.environ.get("GEMINI_API_KEY")
        if api_key:
            print("Classifying images (removing logos, headshots, graphics)...")
            classified = classify_images(selected, api_key, args.model)
            rejected = [img for img in classified if img.get("category") == "rejected"]
            selected = filter_aircraft_images(classified)
            print(f"  -> Kept {len(selected)} aircraft photos, rejected {len(rejected)}")
            if rejected:
                for r in rejected:
                    print(f"     Rejected: page {r['page']} ({r['width']}x{r['height']})")
        else:
            print("  Skipping image classification (no API key)")

    print(f"Generating {args.variant} PDF...")
    out = generate_pdf(data, selected, variant=args.variant, font_status=font_status)

    output_path = args.output
    if not output_path:
        base = os.path.splitext(os.path.basename(args.input))[0]
        output_path = f"{base}_glintero.pdf"

    with open(output_path, "wb") as f:
        f.write(out)
    print(f"Done! {output_path} ({len(out) / 1048576:.1f} MB)")
    return 0


if __name__ == "__main__":
    exit(main())
