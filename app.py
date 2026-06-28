import os
import random
import re
import fitz  # PyMuPDF
import shutil
import hashlib
import uuid
import gc
import time
from datetime import datetime, timedelta
from fastapi import FastAPI, UploadFile, File, Form, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from supabase import create_client, Client

# --- Supabase Configuration ---
SUPABASE_URL = "https://fmoplhesmmxgwogextrp.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZtb3BsaGVzbW14Z3dvZ2V4dHJwIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MjY2NzA4OCwiZXhwIjoyMDk4MjQzMDg4fQ.6CH3bhsp5fn6WLaONl-hrHxQkUecVMmIbJdYZ1c28m8" 
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="toolscraft-hub-super-secret-key-xyz-2026")

UPLOAD_DIR = "uploads"
OUTPUT_DIR = "outputs"
TEMPLATE_FILE = "template.pdf"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# --- Helper Functions ---
def get_bdt_date():
    return (datetime.utcnow() + timedelta(hours=6)).strftime("%Y-%m-%d")

def get_bdt_time():
    return (datetime.utcnow() + timedelta(hours=6)).strftime("%Y-%m-%d %I:%M %p")

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

@app.on_event("startup")
async def setup_default_accounts():
    accounts = [
        {"username": "admin", "password": "admin786", "role": "admin", "credits": 999999},
        {"username": "user200", "password": "user123", "role": "user", "credits": 200}
    ]
    for acc in accounts:
        try:
            res = supabase.table("users").select("username").eq("username", acc["username"]).execute()
            if not res.data:
                supabase.table("users").insert({
                    "username": acc["username"], "password": hash_password(acc["password"]),
                    "role": acc["role"], "daily_credits": acc["credits"],
                    "credit_limit": acc["credits"], "used_credits": 0, "last_reset_date": get_bdt_date()
                }).execute()
            else:
                supabase.table("users").update({
                    "password": hash_password(acc["password"]), "role": acc["role"]
                }).eq("username", acc["username"]).execute()
        except Exception as e:
            print("Setup error:", e)

def check_active_session(request: Request):
    username = request.session.get("username")
    token = request.session.get("session_token")
    if not username or not token: return False
    try:
        res = supabase.table("users").select("session_token").eq("username", username).execute()
        if res.data and res.data[0].get("session_token") == token: return True
    except: pass
    return False

# পরিবর্তিত ক্রেডিট চেক — আর অটো রিসেট হবে না
def check_and_reset_credits(username):
    try:
        res = supabase.table("users").select("daily_credits, credit_limit, role").eq("username", username).execute()
        if res.data:
            row = res.data[0]
            credits = row['daily_credits']
            role = row['role']
            limit = row.get('credit_limit') or (999999 if role == 'admin' else 0)
            return credits
    except: pass
    return 0

# --- PDF Processing Logic (অপরিবর্তিত) ---
def process_master_pdf(user_pdf_path, output_path, original_filename, ai_percentage, shared_id):
    user_doc = fitz.open(user_pdf_path)
    if len(user_doc) > 0: user_doc.delete_page(0)
    actual_pages_count, actual_words, actual_chars = len(user_doc), 0, 0
    for p in user_doc:
        body_rect = fitz.Rect(0, 38, p.rect.width, p.rect.height - 38)
        text = p.get_text("text", clip=body_rect)
        actual_words += len(text.split())
        actual_chars += len(text)
    
    new_size = f"{os.path.getsize(user_pdf_path) / 1024:.1f} KB"
    new_id = shared_id
    base_name = re.sub(r'(?i)ai\s*report', '', os.path.splitext(original_filename)[0].replace("_", " ")).strip()
    new_title = " ".join(base_name.split()[:5]) if base_name.split() else "Document"

    now = datetime.utcnow() + timedelta(hours=6)
    sub_time = now - timedelta(minutes=2) 
    sub_date_str = sub_time.strftime(f"%b {sub_time.day}, %Y, {sub_time.strftime('%I').lstrip('0')}:%M %p BDT")
    down_date_str = now.strftime(f"%b {now.day}, %Y, {now.strftime('%I').lstrip('0')}:%M %p BDT")

    template_doc = fitz.open(TEMPLATE_FILE)
    page1_text = template_doc[0].get_text()
    
    old_id_match = re.search(r"trn:oid:::\d:\d+", page1_text)
    old_id = old_id_match.group(0) if old_id_match else None
    old_title_match = re.search(r"Aa Aa\s+(.*?)\s+Quick Submit", page1_text, re.DOTALL)
    old_title = old_title_match.group(1).strip() if old_title_match else "Fresh Template"
    old_fname_match = re.search(r"File Name\s+(.*?)\s+File Size", page1_text, re.DOTALL)
    old_fname_in_details = old_fname_match.group(1).strip() if old_fname_match else None
    old_pages_match = re.search(r"(\d+)\s+Pages", page1_text)
    old_pages_text = old_pages_match.group(0) if old_pages_match else None
    old_words_match = re.search(r"([\d,]+)\s+Words", page1_text)
    old_words_text = old_words_match.group(0) if old_words_match else None
    old_chars_match = re.search(r"([\d,]+)\s+Characters", page1_text)
    old_chars_text = old_chars_match.group(0) if old_chars_match else None
    old_sub_date_match = re.search(r"Submission Date\s+(.*?)\s+Download Date", page1_text, re.DOTALL)
    old_sub_date = old_sub_date_match.group(1).strip() if old_sub_date_match else None
    old_down_date_match = re.search(r"Download Date\s+(.*?)\s+File Name", page1_text, re.DOTALL)
    old_down_date = old_down_date_match.group(1).strip() if old_down_date_match else None

    replacements = {
        old_id: new_id, old_title: new_title, old_fname_in_details: original_filename, 
        old_pages_text: f"{actual_pages_count + 2} Pages", old_words_text: f"{actual_words:,} Words",
        old_chars_text: f"{actual_chars:,} Characters", "23.5 KB": new_size
    }
    if old_sub_date: replacements[old_sub_date] = sub_date_str
    if old_down_date: replacements[old_down_date] = down_date_str

    page1 = template_doc[0]
    for old_txt, new_txt in replacements.items():
        if not old_txt: continue
        for inst in page1.search_for(old_txt):
            rect_to_clear = fitz.Rect(inst.x0 - 40, inst.y0, inst.x1 + 10, inst.y1) if old_txt in [old_pages_text, old_words_text, old_chars_text] else inst
            page1.add_redact_annot(rect_to_clear, fill=(1, 1, 1))
            page1.apply_redactions()
            is_main_title = (old_txt == old_title)
            x_pos = inst.x0 - 7 if old_txt in [old_pages_text, old_words_text, old_chars_text] else inst.x0
            page1.insert_text((x_pos, inst.y1 - 2), str(new_txt), fontsize=18 if is_main_title else 9.5, fontname="hebo" if is_main_title else "helv", color=(0, 0, 0))

    aa_matches = page1.search_for("Aa Aa")
    if aa_matches:
        for aa_inst in aa_matches:
            page1.add_redact_annot(fitz.Rect(aa_inst.x0 - 2, aa_inst.y0 - 2, aa_inst.x1 + 10, aa_inst.y1 + 2), fill=(1, 1, 1))
            page1.apply_redactions()
            page1.insert_text((aa_inst.x0, aa_inst.y1), "Labib Hasan", fontsize=20, fontname="hebo", color=(0, 0, 0))

    if len(template_doc) > 1:
        page2 = template_doc[1]
        ai_headers = page2.search_for("58% detected as AI")
        if ai_headers:
            inst = ai_headers[0]
            page2.add_redact_annot(fitz.Rect(inst.x0, inst.y0 - 2, inst.x1 + 5, inst.y1 - 4), fill=(1, 1, 1))
            page2.apply_redactions()
            font_name_to_use, font_size_to_use = "hebo", 18
            font_path = os.path.join("static", "LexendDeca-Medium.ttf")
            if os.path.exists(font_path):
                try: page2.insert_font(fontname="lexend", fontfile=font_path); font_name_to_use, font_size_to_use = "lexend", 17
                except: pass
            page2.insert_text((inst.x0, inst.y1 - 4), f"{ai_percentage}% detected as AI", fontsize=font_size_to_use, fontname=font_name_to_use, color=(0, 0, 0))

        group_inst = page2.search_for("AI-generated only") or page2.search_for("Al-generated only")
        if group_inst:
            page2.add_redact_annot(fitz.Rect(group_inst[0].x1 + 2, group_inst[0].y0, group_inst[0].x1 + 60, group_inst[0].y1), fill=(1, 1, 1))
            page2.apply_redactions()
            page2.insert_text((group_inst[0].x1 + 3, group_inst[0].y1 - 2), f"{ai_percentage}%", fontsize=9.5, fontname="helv", color=(0, 0, 0))
            page2.add_redact_annot(fitz.Rect(group_inst[0].x0 - 12, group_inst[0].y0, group_inst[0].x0 - 2, group_inst[0].y1), fill=(1,1,1))
            page2.apply_redactions()
            try:
                ai_val = int(str(ai_percentage).replace('*', '').strip())
                if ai_val == 0: random_detection_num = 0
                elif ai_val <= 15: random_detection_num = random.randint(1, 4)
                elif ai_val <= 40: random_detection_num = random.randint(5, 15)
                elif ai_val <= 70: random_detection_num = random.randint(16, 35)
                else: random_detection_num = random.randint(36, 77)
            except: random_detection_num = random.randint(1, 77)
            x_pos = group_inst[0].x0 - 11 if random_detection_num > 9 else group_inst[0].x0 - 8
            page2.insert_text((x_pos, group_inst[0].y1 - 1.5), str(random_detection_num), fontsize=8.5, fontname="hebo", color=(0, 0, 0))

    template_doc.insert_pdf(user_doc)
    
    logo_path = "static/logo.png"
    if not os.path.exists(logo_path):
        logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "logo.png")

    for i, page in enumerate(template_doc):
        rect = page.rect
        header_height, footer_height = (50, 50) if i < 2 else (38, 38)
        header_title = "Cover Page" if i == 0 else "AI Writing Overview" if i == 1 else "AI Writing Submission"
        header_text = f"Page {i + 1} of {len(template_doc)} - {header_title}"
        
        header_rect = fitz.Rect(0, 0, rect.width, header_height)
        footer_rect = fitz.Rect(0, rect.height - footer_height, rect.width, rect.height)

        page.clean_contents() 
        page.draw_rect(header_rect, fill=(1, 1, 1), color=None, overlay=True)
        page.draw_rect(footer_rect, fill=(1, 1, 1), color=None, overlay=True)
        page.add_redact_annot(header_rect, fill=(1, 1, 1))
        page.add_redact_annot(footer_rect, fill=(1, 1, 1))
        page.apply_redactions()

        if os.path.exists(logo_path):
            page.insert_image(fitz.Rect(20, 15, 90, 35), filename=logo_path)
            page.insert_image(fitz.Rect(20, rect.height - 35, 90, rect.height - 15), filename=logo_path)
            
        page.insert_text(fitz.Point(110, 30), header_text, fontsize=7, color=(0, 0, 0))
        page.insert_text(fitz.Point(rect.width - 200, 30), f"Submission ID {new_id}", fontsize=7, color=(0, 0, 0))
        page.insert_text(fitz.Point(110, rect.height - 20), header_text, fontsize=7, color=(0, 0, 0))
        page.insert_text(fitz.Point(rect.width - 200, rect.height - 20), f"Submission ID {new_id}", fontsize=7, color=(0, 0, 0))

    template_doc.set_metadata({"producer": "pdf-lib[](https://github.com/Hopding/pdf-lib)"})
    template_doc.save(output_path, deflate=True, garbage=4)
    template_doc.close()
    user_doc.close()
    
    del template_doc
    del user_doc
    gc.collect() 

def apply_header_and_footer(input_pdf_path, output_path, shared_id):
    doc = fitz.open(input_pdf_path)
    logo_path = "static/logo.png"
    if not os.path.exists(logo_path):
        logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "logo.png")

    for i, page in enumerate(doc):
        rect = page.rect
        header_title = "Cover Page" if i == 0 else "Integrity Overview" if i == 1 else "Integrity Submission"
        header_text = f"Page {i + 1} of {len(doc)} - {header_title}"
        if os.path.exists(logo_path):
            page.insert_image(fitz.Rect(20, 15, 90, 35), filename=logo_path)
            page.insert_image(fitz.Rect(20, rect.height - 35, 90, rect.height - 15), filename=logo_path)
        page.insert_text(fitz.Point(110, 30), header_text, fontsize=7, color=(0, 0, 0))
        page.insert_text(fitz.Point(rect.width - 200, 30), f"Submission ID {shared_id}", fontsize=7, color=(0, 0, 0))
        page.insert_text(fitz.Point(110, rect.height - 20), header_text, fontsize=7, color=(0, 0, 0))
        page.insert_text(fitz.Point(rect.width - 200, rect.height - 20), f"Submission ID {shared_id}", fontsize=7, color=(0, 0, 0))
    doc.set_metadata({"producer": "pdf-lib[](https://github.com/Hopding/pdf-lib)"})
    doc.save(output_path)
    doc.close()
    
    del doc
    gc.collect()

# --- Routes (অপরিবর্তিত) ---
# ... (লগইন, আপলোড, ডাউনলোড, অ্যাডমিন সব একই রাখলাম, শুধু ক্রেডিট চেক চেঞ্জ হয়েছে)

@app.post("/upload")
async def upload_file(request: Request, file_ai: UploadFile = File(...), file_sim: UploadFile = File(...), ai_percentage: str = Form(...)):
    if not check_active_session(request):
        request.session.clear()
        return RedirectResponse(url="/login", status_code=303)

    username = request.session.get("username")
    credits = check_and_reset_credits(username)
    if credits <= 0:
        return HTMLResponse(content="<h3>আপনার ক্রেডিট শেষ! অ্যাডমিনের সাথে যোগাযোগ করুন।</h3><br><a href='/'>Back</a>", status_code=403)

    # বাকি আপলোড লজিক একই (পুরোটা আগের মতো রাখা হয়েছে)
    try:
        unique_id = str(uuid.uuid4())[:8]
        saved_ai_filename = f"{unique_id}_AI_{file_ai.filename}"
        input_ai_path = os.path.join(UPLOAD_DIR, saved_ai_filename)
        with open(input_ai_path, "wb") as buffer: shutil.copyfileobj(file_ai.file, buffer)
            
        saved_sim_filename = f"{unique_id}_SIM_{file_sim.filename}"
        input_sim_path = os.path.join(UPLOAD_DIR, saved_sim_filename)
        with open(input_sim_path, "wb") as buffer: shutil.copyfileobj(file_sim.file, buffer)

        output_report_path = os.path.join(OUTPUT_DIR, f"Report_{saved_ai_filename}")
        output_edited_path = os.path.join(OUTPUT_DIR, f"Edited_{saved_sim_filename}")
        shared_submission_id = f"trn:oid:::1:{random.randint(1000000000, 9999999999)}"

        process_master_pdf(input_ai_path, output_report_path, file_ai.filename, ai_percentage, shared_submission_id)
        apply_header_and_footer(input_sim_path, output_edited_path, shared_submission_id)

        res_u = supabase.table("users").select("used_credits").eq("username", username).execute()
        current_used = res_u.data[0].get("used_credits", 0) if res_u.data and res_u.data[0].get("used_credits") is not None else 0

        new_credits = credits - 1 if credits > 0 else 0
        supabase.table("users").update({"daily_credits": new_credits, "used_credits": current_used + 1}).eq("username", username).execute()

        current_time = get_bdt_time()
        supabase.table("file_history").insert([
            {"username": username, "filename": f"Report_{saved_ai_filename}", "processed_date": current_time},
            {"username": username, "filename": f"Edited_{saved_sim_filename}", "processed_date": current_time}
        ]).execute()
        
        supabase.table("credit_logs").insert({"username": username, "usage_date": get_bdt_date()}).execute()
        return RedirectResponse(url="/", status_code=303)
        
    except Exception as e:
        return HTMLResponse(content=f"<h3>Error: {str(e)}</h3>", status_code=500)

# বাকি সব রাউট (ডাউনলোড, অ্যাডমিন ইত্যাদি) আগের মতোই রাখো — শুধু উপরের অংশ রিপ্লেস করো