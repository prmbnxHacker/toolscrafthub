import os
import random
import re
import fitz  # PyMuPDF
import shutil
import hashlib
import uuid
import gc     # RAM ফ্রি করার জন্য যুক্ত করা হয়েছে
import time   # 10 সেকেন্ড অপেক্ষা করার জন্য যুক্ত করা হয়েছে
from datetime import datetime, timedelta
from fastapi import FastAPI, UploadFile, File, Form, Request, Depends, BackgroundTasks # BackgroundTasks যুক্ত করা হয়েছে
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from supabase import create_client, Client

# --- Supabase Configuration ---
SUPABASE_URL = "https://xfjacmylnjchugdcwfna.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InhmamFjbXlsbmpjaHVnZGN3Zm5hIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NjQ0MjkzMywiZXhwIjoyMDkyMDE4OTMzfQ.FmWKadLJ51mXFJb9YTd-jEqXdi02NPtuarxT9mvJrPE" 
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

def check_and_reset_credits(username):
    try:
        res = supabase.table("users").select("daily_credits, credit_limit, last_reset_date, role").eq("username", username).execute()
        today = get_bdt_date()
        if res.data:
            row = res.data[0]
            credits = row['daily_credits']
            last_date = row['last_reset_date']
            role = row['role']
            limit = row.get('credit_limit') or (999999 if role == 'admin' else 5)
            if last_date != today:
                supabase.table("users").update({"daily_credits": limit, "last_reset_date": today}).eq("username", username).execute()
                credits = limit
            return credits
    except: pass
    return 0

# --- PDF Processing Logic ---
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
    for i, page in enumerate(template_doc):
        rect = page.rect
        header_height, footer_height = (50, 50) if i < 2 else (38, 38)
        header_title = "Cover Page" if i == 0 else "AI Writing Overview" if i == 1 else "AI Writing Submission"
        header_text = f"Page {i + 1} of {len(template_doc)} - {header_title}"
        
        # Header ও Footer এর rect তৈরি
        header_rect = fitz.Rect(0, 0, rect.width, header_height)
        footer_rect = fitz.Rect(0, rect.height - footer_height, rect.width, rect.height)

        # ধাপ ১: আগে পুরনো header/footer area redact করো (নিচের text মুছে ফেলো)
        page.draw_rect(header_rect, fill=(1, 1, 1), color=None, overlay=True)
        page.draw_rect(footer_rect, fill=(1, 1, 1), color=None, overlay=True)
        page.add_redact_annot(header_rect, fill=(1, 1, 1))
        page.add_redact_annot(footer_rect, fill=(1, 1, 1))
        page.apply_redactions()

        # ধাপ ২: পরিষ্কার সাদা background-এ নতুন করে logo ও text বসাও
        if os.path.exists("static/logo.png"):
            page.insert_image(fitz.Rect(20, 15, 90, 35), filename="static/logo.png")
            page.insert_image(fitz.Rect(20, rect.height - 35, 90, rect.height - 15), filename="static/logo.png")
        page.insert_text(fitz.Point(110, 30), header_text, fontsize=7, color=(0, 0, 0))
        page.insert_text(fitz.Point(rect.width - 200, 30), f"Submission ID {new_id}", fontsize=7, color=(0, 0, 0))
        page.insert_text(fitz.Point(110, rect.height - 20), header_text, fontsize=7, color=(0, 0, 0))
        page.insert_text(fitz.Point(rect.width - 200, rect.height - 20), f"Submission ID {new_id}", fontsize=7, color=(0, 0, 0))

        # ধাপ ৩: এখন high-res (3x) pixmap নাও — logo ও text সহ সবকিছু ধরা পড়বে
        mat = fitz.Matrix(3.0, 3.0)
        header_pix = page.get_pixmap(matrix=mat, clip=header_rect, colorspace=fitz.csRGB)
        footer_pix = page.get_pixmap(matrix=mat, clip=footer_rect, colorspace=fitz.csRGB)

        header_jpeg = header_pix.tobytes("jpeg", jpg_quality=95)
        footer_jpeg = footer_pix.tobytes("jpeg", jpg_quality=95)

        # ধাপ ৪: আবার redact করো — এবার text+logo সব মিলিয়ে image হিসেবে flatten করো
        # (এতে header/footer এর কোনো text কপি করা যাবে না)
        page.add_redact_annot(header_rect, fill=(1, 1, 1))
        page.add_redact_annot(footer_rect, fill=(1, 1, 1))
        page.apply_redactions()

        # ধাপ ৫: high-res image হিসেবে বসাও
        page.insert_image(header_rect, stream=header_jpeg)
        page.insert_image(footer_rect, stream=footer_jpeg)
        
    template_doc.set_metadata({"producer": "pdf-lib (https://github.com/Hopding/pdf-lib)"})
    template_doc.save(output_path, deflate=True, garbage=4)
    template_doc.close()
    user_doc.close()
    
    # RAM থেকে অপ্রয়োজনীয় ক্যাশ মুছে ফেলা
    del template_doc
    del user_doc
    gc.collect() 

def apply_header_and_footer(input_pdf_path, output_path, shared_id):
    doc = fitz.open(input_pdf_path)
    for i, page in enumerate(doc):
        rect = page.rect
        header_title = "Cover Page" if i == 0 else "Integrity Overview" if i == 1 else "Integrity Submission"
        header_text = f"Page {i + 1} of {len(doc)} - {header_title}"
        if os.path.exists("static/logo.png"):
            page.insert_image(fitz.Rect(20, 15, 90, 35), filename="static/logo.png")
            page.insert_image(fitz.Rect(20, rect.height - 35, 90, rect.height - 15), filename="static/logo.png")
        page.insert_text(fitz.Point(110, 30), header_text, fontsize=7, color=(0, 0, 0))
        page.insert_text(fitz.Point(rect.width - 200, 30), f"Submission ID {shared_id}", fontsize=7, color=(0, 0, 0))
        page.insert_text(fitz.Point(110, rect.height - 20), header_text, fontsize=7, color=(0, 0, 0))
        page.insert_text(fitz.Point(rect.width - 200, rect.height - 20), f"Submission ID {shared_id}", fontsize=7, color=(0, 0, 0))
    doc.set_metadata({"producer": "pdf-lib (https://github.com/Hopding/pdf-lib)"})
    doc.save(output_path)
    doc.close()
    
    # মেমরি ক্লিয়ার করা
    del doc
    gc.collect()

# --- Routes ---
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html", context={"request": request, "error": None})

@app.post("/login")
async def login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    try:
        res = supabase.table("users").select("role").eq("username", username).eq("password", hash_password(password)).execute()
        if res.data:
            role = res.data[0]['role']
            token = str(uuid.uuid4())
            supabase.table("users").update({"session_token": token}).eq("username", username).execute()
            request.session.update({"username": username, "role": role, "session_token": token})
            return RedirectResponse(url="/", status_code=303)
        return templates.TemplateResponse(request=request, name="login.html", context={"request": request, "error": "ভুল ইউজারনেম বা পাসওয়ার্ড!"})
    except Exception as e: 
        return templates.TemplateResponse(request=request, name="login.html", context={"request": request, "error": f"সিস্টেম এরর: {str(e)}"})

@app.get("/logout")
async def logout(request: Request):
    username = request.session.get("username")
    if username:
        try: supabase.table("users").update({"session_token": None}).eq("username", username).execute()
        except: pass
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    if not check_active_session(request):
        request.session.clear()
        return RedirectResponse(url="/login", status_code=303)
    username = request.session.get("username")
    role = request.session.get("role")
    credits = check_and_reset_credits(username)
    user_files = []
    try:
        res = supabase.table("file_history").select("id, filename, processed_date").eq("username", username).order("id", desc=True).limit(10).execute()
        if res.data: user_files = [(f['id'], f['filename'], f['processed_date']) for f in res.data]
    except: pass
    return templates.TemplateResponse(request=request, name="index.html", context={"request": request, "username": username, "role": role, "credits": credits, "user_files": user_files})

@app.post("/upload")
async def upload_file(request: Request, file_ai: UploadFile = File(...), file_sim: UploadFile = File(...), ai_percentage: str = Form(...)):
    if not check_active_session(request):
        request.session.clear()
        return RedirectResponse(url="/login", status_code=303)

    username = request.session.get("username")
    credits = check_and_reset_credits(username)
    if credits <= 0:
        return HTMLResponse(content="<h3>আপনার আজকের ক্রেডিট শেষ!</h3><br><a href='/'>Back</a>", status_code=403)

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

        if credits < 900000:
            supabase.table("users").update({"daily_credits": credits - 1, "used_credits": current_used + 1}).eq("username", username).execute()
        else:
            supabase.table("users").update({"used_credits": current_used + 1}).eq("username", username).execute()

        current_time = get_bdt_time()
        supabase.table("file_history").insert([
            {"username": username, "filename": f"Report_{saved_ai_filename}", "processed_date": current_time},
            {"username": username, "filename": f"Edited_{saved_sim_filename}", "processed_date": current_time}
        ]).execute()
        
        supabase.table("credit_logs").insert({"username": username, "usage_date": get_bdt_date()}).execute()
        return RedirectResponse(url="/", status_code=303)
        
    except Exception as e:
        return HTMLResponse(content=f"<h3>Error: {str(e)}</h3>", status_code=500)

# ফাইল ডিলিট করার ব্যাকগ্রাউন্ড ফাংশন
def delete_file_and_history(file_id: int, output_path: str, upload_filename: str):
    time.sleep(30)  # 30 সেকেন্ড অপেক্ষা করবে
    try:
        # লোকাল স্টোরেজ থেকে ডিলিট
        if os.path.exists(output_path): os.remove(output_path)
        
        in_path = os.path.join(UPLOAD_DIR, upload_filename)
        if os.path.exists(in_path): os.remove(in_path)
        
        # ডাটাবেজ (হিস্ট্রি) থেকে ডিলিট
        supabase.table("file_history").delete().eq("id", file_id).execute()
    except Exception as e:
        print("Delete error:", e)

@app.get("/download_past_file/{file_id}")
async def download_past_file(request: Request, file_id: int, background_tasks: BackgroundTasks):
    if not check_active_session(request):
        request.session.clear()
        return RedirectResponse(url="/login", status_code=303)
    
    username = request.session.get("username")
    try:
        res = supabase.table("file_history").select("filename").eq("id", file_id).eq("username", username).execute()
        if res.data:
            saved_filename = res.data[0]['filename']
            output_path = os.path.join(OUTPUT_DIR, saved_filename)
            
            # অরিজিনাল আপলোড করা ফাইলের নাম বের করা
            upload_filename = saved_filename.replace("Report_", "", 1) if saved_filename.startswith("Report_") else saved_filename.replace("Edited_", "", 1)
            
            if os.path.exists(output_path):
                # ডাউনলোড রেসপন্স রিটার্ন করার পাশাপাশি ব্যাকগ্রাউন্ডে ১০ সেকেন্ডের ডিলিট টাস্ক রান করবে
                background_tasks.add_task(delete_file_and_history, file_id, output_path, upload_filename)
                
                return FileResponse(output_path, media_type="application/pdf", filename=saved_filename[9:])
    except Exception as e: 
        print(e)
        
    return HTMLResponse("<h3>ফাইলটি সার্ভারে পাওয়া যায়নি বা ইতিমধ্যে ডিলিট হয়ে গেছে!</h3><br><a href='/'>হোমে ফিরে যান</a>", status_code=404)

@app.post("/delete_my_file")
async def delete_my_file(request: Request, file_id: int = Form(...)):
    if not check_active_session(request): return RedirectResponse(url="/login", status_code=303)
    username = request.session.get("username")
    try:
        res = supabase.table("file_history").select("filename").eq("id", file_id).eq("username", username).execute()
        if res.data:
            saved_filename = res.data[0]['filename']
            out_path = os.path.join(OUTPUT_DIR, saved_filename)
            upload_filename = saved_filename.replace("Report_", "", 1) if saved_filename.startswith("Report_") else saved_filename.replace("Edited_", "", 1)
            in_path = os.path.join(UPLOAD_DIR, upload_filename)
            if os.path.exists(in_path): os.remove(in_path)
            if os.path.exists(out_path): os.remove(out_path)
            supabase.table("file_history").delete().eq("id", file_id).execute()
    except: pass
    return RedirectResponse(url="/", status_code=303)

@app.post("/delete_all_files")
async def delete_all_files(request: Request):
    if not check_active_session(request): return RedirectResponse(url="/login", status_code=303)
    try: supabase.table("file_history").delete().eq("username", request.session.get("username")).execute()
    except: pass
    return RedirectResponse(url="/", status_code=303)

@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    if not check_active_session(request) or request.session.get("role") != "admin": return HTMLResponse("Access Denied", status_code=403)
    users, history, daily_usage_list = [], [], []
    today = get_bdt_date()
    try:
        users_res = supabase.table("users").select("username, role, daily_credits, used_credits").execute()
            
        if users_res.data:
            for u in users_res.data:
                uname = u['username']
                logs_today = supabase.table("credit_logs").select("id").eq("username", uname).eq("usage_date", today).execute()
                used_today = len(logs_today.data) if logs_today.data else 0
                total_used = u.get('used_credits', 0) if u.get('used_credits') is not None else 0
                users.append((uname, u['role'], u['daily_credits'], used_today, total_used))

        hist_res = supabase.table("file_history").select("username, filename, processed_date").order("id", desc=True).limit(50).execute()
        if hist_res.data: history = [(h['username'], h['filename'], h['processed_date']) for h in hist_res.data]
                
        five_days_ago = (datetime.utcnow() + timedelta(hours=6) - timedelta(days=5)).strftime("%Y-%m-%d")
        supabase.table("credit_logs").delete().lt("usage_date", five_days_ago).execute()
        
        usage_res = supabase.table("credit_logs").select("username, usage_date").execute()
        if usage_res.data:
            usage_dict = {}
            for row in usage_res.data:
                key = (row['username'], row['usage_date'])
                usage_dict[key] = usage_dict.get(key, 0) + 1
            daily_usage_list = sorted([{"username": u, "date": d, "used": count} for (u, d), count in usage_dict.items()], key=lambda x: x['date'], reverse=True)
            
    except: pass
    total_files = len(os.listdir(UPLOAD_DIR)) + len(os.listdir(OUTPUT_DIR))
    return templates.TemplateResponse(request=request, name="admin.html", context={"request": request, "users": users, "history": history, "total_files": total_files, "daily_usage": daily_usage_list})

@app.post("/admin/create_user")
async def create_user(request: Request, new_username: str = Form(...), new_password: str = Form(...), initial_credits: int = Form(5)):
    if not check_active_session(request) or request.session.get("role") != "admin": return HTMLResponse("Access Denied", status_code=403)
    try:
        supabase.table("users").insert({"username": new_username, "password": hash_password(new_password), "role": "user", "daily_credits": initial_credits, "credit_limit": initial_credits, "used_credits": 0, "last_reset_date": get_bdt_date()}).execute()
    except: pass
    return RedirectResponse(url="/admin", status_code=303)

@app.post("/admin/update_credits")
async def update_credits(request: Request, up_username: str = Form(...), new_credits: int = Form(...)):
    if not check_active_session(request) or request.session.get("role") != "admin": return HTMLResponse("Access Denied", status_code=403)
    try:
        supabase.table("users").update({"daily_credits": int(new_credits), "credit_limit": int(new_credits)}).eq("username", up_username).execute()
    except: pass
    return RedirectResponse(url="/admin", status_code=303)

@app.post("/admin/reset_used_credits")
async def reset_used_credits(request: Request, rst_username: str = Form(...)):
    if not check_active_session(request) or request.session.get("role") != "admin": return HTMLResponse("Access Denied", status_code=403)
    try: supabase.table("users").update({"used_credits": 0}).eq("username", rst_username).execute()
    except: pass
    return RedirectResponse(url="/admin", status_code=303)

@app.post("/admin/delete_user")
async def delete_user(request: Request, del_username: str = Form(...)):
    if not check_active_session(request) or request.session.get("role") != "admin": return HTMLResponse("Access Denied", status_code=403)
    if del_username == "admin": return HTMLResponse("Admin account cannot be deleted!", status_code=400)
    try: supabase.table("users").delete().eq("username", del_username).execute()
    except: pass
    return RedirectResponse(url="/admin", status_code=303)

@app.post("/admin/clear_all_files")
async def clear_all_files(request: Request):
    if not check_active_session(request) or request.session.get("role") != "admin": return HTMLResponse("Access Denied", status_code=403)
    for folder in [UPLOAD_DIR, OUTPUT_DIR]:
        for f in os.listdir(folder):
            if os.path.isfile(os.path.join(folder, f)): os.remove(os.path.join(folder, f))
    try: supabase.table("file_history").delete().neq("id", 0).execute()
    except: pass
    return RedirectResponse(url="/admin", status_code=303)

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
