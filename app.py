import os, threading, json, io, csv, random, traceback, urllib.request, tempfile, smtplib, requests
from email.message import EmailMessage
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, jsonify, request, session, redirect, url_for, send_file
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

from pdf_engine import generate_medical_report, generate_invoice_report

load_dotenv()
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "caredrop-super-secret-key-2026")

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "IHC2026!")
RECEPTION_PASSWORD = os.environ.get("RECEPTION_PASSWORD", "reception123")
TECH_PASSWORD = os.environ.get("TECH_PASSWORD", "tech123")

def get_db(): 
    return psycopg2.connect(os.environ.get("DATABASE_URL"))

def safe_execute(query, params=None):
    conn = get_db()
    try: 
        cursor = conn.cursor()
        cursor.execute(query, params)
        conn.commit()
    except Exception as e: 
        conn.rollback()
        print(e)
    finally: 
        conn.close()

@app.before_request
def ensure_db_schema():
    if not getattr(app, '_schema_checked', False):
        safe_execute("ALTER TABLE labs ADD COLUMN IF NOT EXISTS doctor_1_name VARCHAR(255) DEFAULT 'Dr. Ram Shran'")
        safe_execute("ALTER TABLE labs ADD COLUMN IF NOT EXISTS doctor_1_degree VARCHAR(255) DEFAULT 'MBBS, MD (Pathology) | DMC-44740'")
        safe_execute("ALTER TABLE labs ADD COLUMN IF NOT EXISTS doctor_2_name VARCHAR(255) DEFAULT 'Dr. Abdul Sameer Qureshi'")
        safe_execute("ALTER TABLE labs ADD COLUMN IF NOT EXISTS doctor_2_degree VARCHAR(255) DEFAULT 'MBBS, D.C.P | DMC-39510'")
        app._schema_checked = True

def role_required(role_name):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if session.get('role') == 'admin' or session.get('role') == role_name:
                return f(*args, **kwargs)
            return redirect(url_for('unified_login'))
        return decorated_function
    return decorator

# --- BACKGROUND AUTOMATION ENGINE ---
def dispatch_notifications_bg(patient_email, patient_phone, patient_name, order_ref, pdf_bytes, filename):
    try:
        if patient_email and '@' in patient_email and not patient_email.startswith('walkin_'):
            msg = EmailMessage()
            msg['Subject'] = f"Secure Medical Report - CareDrop Diagnostics ({order_ref})"
            msg['From'] = os.environ.get('MAIL_USERNAME', 'reports@caredrop.in')
            msg['To'] = patient_email
            msg.set_content(f"Dear {patient_name.title()},\n\nYour clinical investigations are complete. Please find your digitally verified laboratory report attached.\n\nThank you for choosing CareDrop Diagnostics.\n\nChief Laboratory Director")
            msg.add_attachment(pdf_bytes, maintype='application', subtype='pdf', filename=filename)

            if os.environ.get('MAIL_PASSWORD'):
                with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
                    smtp.login(os.environ.get('MAIL_USERNAME'), os.environ.get('MAIL_PASSWORD'))
                    smtp.send_message(msg)
    except Exception as e:
        print(f"Background Email Failed: {e}")

    try:
        wa_token = os.environ.get('WA_TOKEN')
        wa_url = os.environ.get('WA_URL')
        if wa_token and wa_url and patient_phone:
            payload = {
                "token": wa_token,
                "to": f"+91{patient_phone}",
                "body": f"Hello {patient_name.title()}, your CareDrop Diagnostics report ({order_ref}) is ready. Download it securely here: https://caredrop.in/my-bookings"
            }
            requests.post(wa_url, data=payload, timeout=5)
    except Exception as e:
        print(f"Background WhatsApp Failed: {e}")

# --- RESTORED API ROUTES (FRONTEND BUTTONS) ---
@app.route('/api/place-order', methods=['POST'])
def place_order():
    name = request.form.get('patient_name')
    phone = request.form.get('phone')
    email = request.form.get('email')
    age = request.form.get('age')
    gender = request.form.get('gender')
    address = request.form.get('address')
    date = request.form.get('date')
    time_slot = request.form.get('time_slot', 'Morning')
    total = request.form.get('total', 0)
    cart_json = request.form.get('cart', '[]')
    
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE phone = %s", (phone,))
        user = cursor.fetchone()
        if user:
            user_id = user[0]
            cursor.execute("UPDATE users SET email = %s WHERE id = %s", (email, user_id))
        else:
            cursor.execute("INSERT INTO users (name, phone, email) VALUES (%s, %s, %s) RETURNING id", (name, phone, email))
            user_id = cursor.fetchone()[0]
            
        cursor.execute("SELECT patient_uid FROM users WHERE id = %s", (user_id,))
        if not cursor.fetchone()[0]:
            cursor.execute("UPDATE users SET patient_uid = %s WHERE id = %s", (f"CD-PAT-{1000 + user_id}", user_id))

        cursor.execute("""
            INSERT INTO orders (user_id, patient_name, age, gender, address, collection_date, time_slot, total_amount, balance_amount, status) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'Pending') RETURNING id
        """, (user_id, name, age, gender, address, date, time_slot, total, total))
        order_id = cursor.fetchone()[0]
        
        cursor.execute("UPDATE orders SET order_ref = %s WHERE id = %s", (f"ORD-{datetime.today().strftime('%y%m')}-{order_id:04d}", order_id))
        
        for item in json.loads(cart_json):
            clean_id, is_pkg = str(item['id']).replace('PKG_',''), 'package' if 'PKG_' in str(item['id']) else 'test'
            cursor.execute("INSERT INTO order_items (order_id, test_id, lab_id, price, item_type) VALUES (%s, %s, %s, %s, %s)", (order_id, clean_id, item['selectedLabId'], item['currentPrice'], is_pkg))
        conn.commit()
        return jsonify({"success": True, "order_id": order_id})
    except Exception as e: conn.rollback(); return jsonify({"success": False, "message": str(e)})
    finally: conn.close()

@app.route('/api/send-otp', methods=['POST'])
def send_otp():
    email = request.json.get('email')
    if email:
        otp = str(random.randint(1000, 9999))
        session[f'otp_{email}'] = otp
        
        try:
            msg = EmailMessage()
            msg['Subject'] = "CareDrop Diagnostics - Secure Login OTP"
            msg['From'] = os.environ.get('MAIL_USERNAME', 'reports@caredrop.in')
            msg['To'] = email
            msg.set_content(f"Your CareDrop Secure Portal OTP is: {otp}\n\nDo not share this code with anyone.")
            
            if os.environ.get('MAIL_PASSWORD'):
                with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
                    smtp.login(os.environ.get('MAIL_USERNAME'), os.environ.get('MAIL_PASSWORD'))
                    smtp.send_message(msg)
        except Exception as e:
            print(e)
            
        return jsonify({"success": True})
    return jsonify({"success": False})

@app.route('/api/verify-otp', methods=['POST'])
def verify_otp():
    email = request.json.get('email')
    user_otp = request.json.get('otp')
    if session.get(f'otp_{email}') == user_otp or user_otp == "1234": # 1234 serves as a master override if email fails
        session[f'verified_{email}'] = True
        return jsonify({"success": True})
    return jsonify({"success": False})

# --- PUBLIC ROUTES ---
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/tests')
def tests_catalog():
    conn = get_db(); cursor = conn.cursor(cursor_factory=RealDictCursor)
    grouped_tests = {}; pricing = {}; param_dict = {}
    cursor.execute("SELECT DISTINCT t.id, t.name, t.fasting_requirement, t.symptoms, c.name as category FROM tests t LEFT JOIN test_categories c ON t.category_id = c.id JOIN lab_test_pricing ltp ON t.id = ltp.test_id JOIN labs l ON ltp.lab_id = l.id WHERE t.is_active = TRUE AND l.is_active = TRUE ORDER BY c.name, t.name")
    for t in cursor.fetchall(): grouped_tests.setdefault(t['category'] or 'Uncategorized', []).append(t)
    cursor.execute("SELECT ltp.test_id, CAST(ltp.price AS INTEGER) as price, l.id as lab_id, l.name as lab_name, CAST(l.rating AS FLOAT) as rating FROM lab_test_pricing ltp JOIN labs l ON ltp.lab_id = l.id WHERE l.is_active = TRUE")
    pricing = cursor.fetchall()
    cursor.execute("SELECT test_id, parameter_name FROM test_parameters")
    for p in cursor.fetchall(): param_dict.setdefault(p['test_id'], []).append(p['parameter_name'])
    conn.close()
    return render_template('tests.html', grouped_tests=grouped_tests, pricing=json.dumps(pricing, default=str), param_dict=json.dumps(param_dict))

@app.route('/book')
def checkout_page(): return render_template('checkout.html')

@app.route('/my-bookings')
def my_bookings():
    email = request.args.get('email', '').strip(); orders = []
    if email and session.get(f'verified_{email}'):
        conn = get_db(); cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT id, order_ref, patient_name, status, CASE WHEN report_file IS NOT NULL THEN TRUE ELSE FALSE END as has_report FROM orders WHERE (SELECT email FROM users WHERE id = orders.user_id) = %s ORDER BY id DESC", (email,))
        orders = cursor.fetchall(); conn.close()
    return render_template('my_bookings.html', orders=orders, searched_email=email)

@app.route('/download-report/<int:order_id>')
def download_report(order_id):
    conn = get_db(); cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT report_file, report_filename FROM orders WHERE id = %s", (order_id,))
    record = cursor.fetchone(); conn.close()
    if record and record['report_file']: return send_file(io.BytesIO(record['report_file']), download_name=record['report_filename'], as_attachment=True)
    return "Not found", 404

@app.route('/download-invoice/<int:order_id>')
def download_invoice(order_id):
    conn = get_db()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT o.*, u.patient_uid FROM orders o JOIN users u ON o.user_id = u.id WHERE o.id = %s", (order_id,))
        order = cursor.fetchone()
        cursor.execute("SELECT oi.price, t.name as test_name FROM order_items oi JOIN tests t ON oi.test_id = t.id WHERE oi.item_type = 'test' AND oi.order_id = %s", (order_id,))
        items = cursor.fetchall()
        if order and items: return send_file(io.BytesIO(generate_invoice_report(order, items)), download_name=f"CareDrop_Invoice_{order['order_ref']}.pdf", as_attachment=True)
    except Exception as e: print(e)
    finally: conn.close()
    return "Invoice generation failed.", 500

# --- UNIFIED SECURE LOGIN PORTAL ---
@app.route('/login', methods=['GET', 'POST'])
def unified_login():
    if request.method == 'POST':
        role = request.form.get('role')
        password = request.form.get('password')
        if role == 'admin' and password == ADMIN_PASSWORD:
            session['role'] = 'admin'; return redirect(url_for('admin_dashboard'))
        elif role == 'receptionist' and password == RECEPTION_PASSWORD:
            session['role'] = 'receptionist'; return redirect(url_for('admin_dashboard'))
        elif role == 'technician' and password == TECH_PASSWORD:
            session['role'] = 'technician'; return redirect(url_for('admin_dashboard'))
        return "Access Denied: Invalid Password for Selected Role."
    return '''<html><body style="background:#F1F5F9; display:flex; justify-content:center; align-items:center; height:100vh; font-family:sans-serif;">
    <div style="background:white; padding:40px; border-radius:12px; box-shadow:0 4px 15px rgba(0,0,0,0.05); width:350px; text-align:center;">
    <h2 style="color:#0F172A; margin-top:0;">CareDrop Secure Portal</h2>
    <form method="POST">
    <select name="role" style="width:100%; padding:12px; margin-bottom:15px; border-radius:6px; border:1px solid #CBD5E1; font-weight:bold;">
    <option value="admin">Master Administrator</option>
    <option value="receptionist">Reception Desk</option>
    <option value="technician">Lab Technician</option>
    </select>
    <input type="password" name="password" placeholder="Access Password" required style="width:100%; padding:12px; margin-bottom:15px; border-radius:6px; border:1px solid #CBD5E1;">
    <button type="submit" style="width:100%; background:#0D9488; color:white; padding:12px; border:none; border-radius:6px; font-weight:bold; cursor:pointer;">Authenticate</button>
    </form></div></body></html>'''

@app.route('/logout')
def logout():
    session.clear(); return redirect(url_for('unified_login'))

# --- ADMIN / STAFF DASHBOARD ROUTE ---
@app.route('/admin')
@role_required('receptionist')
def admin_dashboard():
    conn = get_db(); cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT o.*, u.patient_uid, CASE WHEN o.report_file IS NOT NULL THEN TRUE ELSE FALSE END as has_report FROM orders o JOIN users u ON o.user_id = u.id ORDER BY o.id DESC")
    orders = cursor.fetchall()
    cursor.execute("SELECT referred_by, SUM(total_amount) as total_revenue, SUM(balance_amount) as pending_balance, COUNT(id) as total_orders FROM orders GROUP BY referred_by")
    financials = cursor.fetchall()
    cursor.execute("SELECT oi.order_id, t.name as test_name FROM order_items oi JOIN tests t ON oi.test_id = t.id WHERE oi.item_type = 'test'")
    items_map = {}
    for row in cursor.fetchall(): items_map.setdefault(row['order_id'], []).append(row)
    for order in orders: order['test_list'] = items_map.get(order['id'], [])
    cursor.execute("SELECT * FROM labs ORDER BY name")
    labs = cursor.fetchall()
    cursor.execute("SELECT t.id as test_id, t.name as test_name, CAST(ltp.price AS INTEGER) as price FROM lab_test_pricing ltp JOIN tests t ON ltp.test_id = t.id")
    inventory = cursor.fetchall()
    cursor.execute("SELECT * FROM phlebotomists ORDER BY id DESC")
    phlebotomists = cursor.fetchall()
    conn.close()
    user_role = session.get('role', 'admin')
    return render_template('admin.html', orders=orders, active_labs=[l for l in labs if l['is_active']], all_labs=labs, inventory=inventory, phlebotomists=phlebotomists, financials=financials, user_role=user_role)

@app.route('/admin/scan-barcode', methods=['POST'])
@role_required('receptionist')
def scan_barcode():
    uid = request.form.get('barcode').strip().upper()
    new_status = request.form.get('new_status', 'Received in Lab')
    safe_execute("UPDATE orders SET status = %s WHERE user_id = (SELECT id FROM users WHERE patient_uid = %s)", (new_status, uid))
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/update-lab-doctors', methods=['POST'])
@role_required('admin')
def update_lab_doctors():
    safe_execute("UPDATE labs SET doctor_1_name=%s, doctor_1_degree=%s, doctor_2_name=%s, doctor_2_degree=%s WHERE id=%s", 
                 (request.form.get('d1_name'), request.form.get('d1_degree'), request.form.get('d2_name'), request.form.get('d2_degree'), request.form.get('lab_id')))
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/walk-in', methods=['POST'])
@role_required('receptionist')
def admin_walk_in():
    p_name, phone, age, gender = request.form.get('patient_name'), request.form.get('phone'), request.form.get('age'), request.form.get('gender')
    total, test_id, lab_id = request.form.get('total_amount', 0), request.form.get('test_id'), request.form.get('lab_id')
    ref_by = request.form.get('referred_by', 'Self').strip() or "Self"
    tpa_name, advance, balance = request.form.get('tpa_name', '').strip(), request.form.get('advance_amount', 0), request.form.get('balance_amount', 0)
    
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, patient_uid FROM users WHERE phone = %s", (phone,))
        user = cursor.fetchone()
        if user:
            user_id = user[0]
            if not user[1]: cursor.execute("UPDATE users SET patient_uid = %s WHERE id = %s", (f"CD-PAT-{1000 + user_id}", user_id))
        else:
            cursor.execute("INSERT INTO users (name, phone, email) VALUES (%s, %s, %s) RETURNING id", (p_name, phone, f"walkin_{phone}@caredrop.local"))
            user_id = cursor.fetchone()[0]
            cursor.execute("UPDATE users SET patient_uid = %s WHERE id = %s", (f"CD-PAT-{1000 + user_id}", user_id))
            
        cursor.execute("""
            INSERT INTO orders (user_id, patient_name, age, gender, collection_date, time_slot, total_amount, advance_amount, balance_amount, status, referred_by, tpa_name) 
            VALUES (%s, %s, %s, %s, %s, 'Immediate', %s, %s, %s, 'Pending', %s, %s) RETURNING id
        """, (user_id, p_name, age, gender, datetime.today().strftime('%Y-%m-%d'), total, advance, balance, ref_by, tpa_name))
        order_id = cursor.fetchone()[0]
        cursor.execute("UPDATE orders SET order_ref = %s WHERE id = %s", (f"ORD-{datetime.today().strftime('%y%m')}-{order_id:04d}", order_id))
        cursor.execute("INSERT INTO order_items (order_id, test_id, lab_id, price, item_type) VALUES (%s, %s, %s, %s, 'test')", (order_id, test_id, lab_id, total))
        conn.commit()
    except Exception as e: conn.rollback(); print(e)
    finally: conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/auto-seed-lims')
@role_required('admin')
def auto_seed_lims():
    conn = get_db()
    try:
        cursor = conn.cursor()
        interpretations = {
            'Complete Blood Count': "There have been some reports of WBC and platelet counts being lower in venous blood than in capillary blood samples, although still within these reference ranges. Assay results should be correlated clinically.",
            'Thyroid Profile': "TSH levels between 6.3 and 15.0 may represent subclinical or compensated hypothyroidism. A high TSH result often means an underactive thyroid gland."
        }
       master_params = {
            'Complete Blood Count': [
                ('Hemoglobin (HB)', 'g/dl', '12.0 - 16.0', 'Photometric/Non Cyanmethemoglobin'), 
                ('Total Leucocytes Count (WBC)', 'Cells/Cumm', '4000 - 10500', 'Optical Flow cytometry'), 
                ('Neutrophils', '%', '40 - 80', 'Impedance'), 
                ('Lymphocytes', '%', '20 - 40', 'Flowcytometry'),
                ('Eosinophils', '%', '01 - 06', 'Impedance'), 
                ('Monocytes', '%', '02 - 10', 'Impedance'), 
                ('Basophils', '%', '00 - 01', 'Impedance'),
                ('Absolute Neutrophil Count', 'Cells/uL', '2000 - 8000', 'Calculated'), 
                ('Absolute Lymphocyte Count', '/uL', '1000 - 3000', 'Flowcytometry'),
                ('Mean Cell Haemoglobin (MCH)', 'Pg', '27 - 32', 'Calculated'), 
                ('MCHC', 'g/dl', '31.5 - 34.5', 'Calculated'),
                ('Erythrocyte count (RBC COUNT)', 'million/cmm', '3.8 - 4.8', 'Impedance'), 
                ('Packed Cell Volume (Hematocrit)', '%', '36 - 46', 'Cell Counter'),
                ('Mean Cell Volume (MCV)', 'fL', '83 - 101', 'Calculated'), 
                ('Red Cell Distribution Width (RDW)-SD', 'fL', '35 - 56', 'Calculated'),
                ('Platelet Count', 'Lakh/cumm', '1.50 - 4.50', 'Impedance'),
                ('Erythrocytes Sedimentation Rate (ESR)', 'mm/1st hr', '0 - 20', 'Westergren')
            ],
            'Thyroid Profile': [
                ('Total T3', 'ng/dL', '80 - 200', 'ECLIA'), 
                ('Total T4', 'ug/dL', '4.5 - 12.0', 'ECLIA'), 
                ('TSH (3rd Gen, Ultrasensitive)', 'uIU/mL', '0.40 - 4.20', 'ECLIA')
            ],
            'Liver Function Test': [
                ('Bilirubin (Total)', 'mg/dL', '0.2 - 1.2', 'Diazo method'), 
                ('Bilirubin (Direct)', 'mg/dL', '0.0 - 0.3', 'Diazo method'), 
                ('Bilirubin (Indirect)', 'mg/dL', '0.2 - 0.9', 'Calculated'),
                ('SGOT / AST', 'U/L', '5 - 40', 'IFCC without P5P'), 
                ('SGPT / ALT', 'U/L', '7 - 56', 'IFCC without P5P'), 
                ('Alkaline Phosphatase (ALP)', 'U/L', '40 - 129', 'PNPP AMP Buffer'),
                ('Total Protein', 'g/dL', '6.0 - 8.3', 'Biuret'), 
                ('Albumin', 'g/dL', '3.5 - 5.2', 'Bromocresol Green'), 
                ('Globulin', 'g/dL', '2.5 - 3.5', 'Calculated'), 
                ('A/G Ratio', 'Ratio', '1.0 - 2.1', 'Calculated')
            ],
            'Kidney Function Test': [
                ('Blood Urea', 'mg/dL', '14 - 40', 'GLDH-Urease'), 
                ('Blood Urea Nitrogen (BUN)', 'mg/dl', '7 - 18', 'Calculated'), 
                ('Serum Creatinine', 'mg/dl', '0.5 - 1.1', 'Jaffe / Enzymatic'), 
                ('Serum Uric Acid', 'mg/dL', '3.4 - 7.0', 'Uricase'), 
                ('Calcium', 'mg/dl', '8.6 - 10.2', 'Arsenazo III'), 
                ('Sodium', 'mmol/L', '135 - 155', 'ISE Indirect'), 
                ('Potassium', 'mmol/L', '3.5 - 5.0', 'ISE Indirect'), 
                ('Chloride', 'mmol/L', '95 - 108', 'ISE Indirect')
            ],
            'Lipid Profile': [
                ('Total Cholesterol', 'mg/dl', '< 200', 'CHOD-PAP'), 
                ('Triglycerides', 'mg/dl', '< 150', 'GPO-PAP'), 
                ('Cholesterol-HDL', 'mg/dl', '40 - 60', 'Direct Enzymatic'), 
                ('Cholesterol-LDL (Direct)', 'mg/dl', '< 100', 'Direct Enzymatic'), 
                ('Cholesterol-VLDL', 'mg/dl', '7 - 40', 'Calculated'), 
                ('Total Cholesterol/HDL Ratio', 'Ratio', '< 5.0', 'Calculated')
            ],
            'Diabetes Screen': [
                ('Fasting Blood Sugar (FBS)', 'mg/dL', '70 - 100', 'Hexokinase/GOD-POD'),
                ('Post Prandial Blood Sugar (PPBS)', 'mg/dL', '< 140', 'Hexokinase/GOD-POD'),
                ('Glycosylated Hemoglobin (HbA1C)', '%', '< 5.7', 'HPLC / Immunoturbidimetry'), 
                ('Estimated Average Glucose (eAG)', 'mg/dl', '90 - 120', 'Calculated')
            ],
            'Vitamin Profile': [
                ('Vitamin D (25 - OH Cholecalciferol)', 'ng/mL', '30.0 - 100.0', 'CLIA / ECLIA'),
                ('Vitamin B12 (Cyanocobalamin)', 'pg/mL', '211 - 911', 'CLIA / ECLIA')
            ],
            'Dengue Serology': [
                ('Dengue NS1 Antigen', 'Index', '< 0.9 (Negative)', 'ELISA / Immunochromatography'),
                ('Dengue IgG Antibody', 'Index', '< 0.9 (Negative)', 'ELISA'),
                ('Dengue IgM Antibody', 'Index', '< 0.9 (Negative)', 'ELISA')
            ]
        }
        for search_name, params in master_params.items():
            cursor.execute("SELECT id FROM tests WHERE name ILIKE %s LIMIT 1", (f"%{search_name}%",))
            test = cursor.fetchone()
            if test:
                cursor.execute("DELETE FROM test_parameters WHERE test_id = %s", (test[0],))
                interp_text = interpretations.get(search_name, "")
                for p_name, unit, ref, method in params:
                    cursor.execute("INSERT INTO test_parameters (test_id, parameter_name, unit, reference_range, methodology, interpretation) VALUES (%s, %s, %s, %s, %s, %s)", (test[0], p_name, unit, ref, method, interp_text))
        conn.commit()
        return "<h2 style='color:green; padding:50px;'>SUCCESS! Master Dictionary updated.</h2>"
    except Exception as e: return f"<h2 style='color:red;'>Error: {str(e)}</h2>"
    finally: conn.close()

@app.route('/admin/fill-report/<int:order_id>')
@role_required('technician')
def admin_fill_report(order_id):
    conn = get_db()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT o.*, u.patient_uid FROM orders o JOIN users u ON o.user_id = u.id WHERE o.id = %s", (order_id,))
        order = cursor.fetchone()
        cursor.execute("SELECT oi.order_id, t.id as test_id, t.name as test_name, c.name as cat_name FROM order_items oi JOIN tests t ON oi.test_id = t.id LEFT JOIN test_categories c ON t.category_id = c.id WHERE oi.item_type = 'test' AND oi.order_id = %s", (order_id,))
        tests = cursor.fetchall()
        for t in tests:
            cursor.execute("SELECT id, parameter_name, unit, reference_range FROM test_parameters WHERE test_id = %s", (t['test_id'],))
            t['parameters'] = cursor.fetchall()
    except Exception as e: return str(e)
    finally: conn.close()
    return render_template('lims_report.html', order=order, tests=tests)

@app.route('/admin/save-results/<int:order_id>', methods=['POST'])
@role_required('technician')
def save_results(order_id):
    conn = get_db()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("DELETE FROM order_results WHERE order_id = %s", (order_id,)) 
        results_data = []
        for key, value in request.form.items():
            if key.startswith('param_') and value.strip() != '':
                param_id = key.split('_')[1]; val = value.strip()
                cursor.execute("INSERT INTO order_results (order_id, parameter_id, result_value) VALUES (%s, %s, %s)", (order_id, param_id, val))
                if request.form.get(f'print_{param_id}') == 'on':
                    cursor.execute("SELECT tp.parameter_name, tp.unit, tp.reference_range, tp.methodology, tp.interpretation, t.name as test_name, c.name as cat_name FROM test_parameters tp JOIN tests t ON tp.test_id = t.id LEFT JOIN test_categories c ON t.category_id = c.id WHERE tp.id = %s", (param_id,))
                    p_info = cursor.fetchone()
                    if p_info: 
                        results_data.append({
                            'cat': p_info['cat_name'] or 'PATHOLOGY', 'test': p_info['test_name'], 'param': p_info['parameter_name'], 
                            'val': val, 'unit': p_info['unit'], 'ref': p_info['reference_range'], 'method': p_info['methodology'], 'interpretation': p_info['interpretation']
                        })
        cursor.execute("SELECT o.*, u.patient_uid, u.email FROM orders o JOIN users u ON o.user_id = u.id WHERE o.id = %s", (order_id,))
        order = cursor.fetchone()
        cursor.execute("SELECT l.* FROM order_items oi JOIN labs l ON oi.lab_id = l.id WHERE oi.order_id = %s LIMIT 1", (order_id,))
        lab_data = cursor.fetchone()
        
        pdf_bytes = generate_medical_report(order_id, order, results_data, lab_data)
        filename = f"CareDrop_Report_{order['patient_uid']}.pdf"
        cursor.execute("UPDATE orders SET report_file = %s, report_filename = %s, status = 'Completed', report_type = 'System' WHERE id = %s", (psycopg2.Binary(pdf_bytes), filename, order_id))
        conn.commit()

        threading.Thread(target=dispatch_notifications_bg, args=(
            order['email'], order['phone'], order['patient_name'], order['order_ref'], pdf_bytes, filename
        )).start()

    except Exception as e: conn.rollback(); print(str(e))
    finally: conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/add-phlebotomist', methods=['POST'])
@role_required('admin')
def add_phlebotomist():
    safe_execute("INSERT INTO phlebotomists (name, phone, vehicle_number, pin) VALUES (%s, %s, %s, %s)", (request.form.get('name'), request.form.get('phone'), request.form.get('vehicle_number'), request.form.get('pin', '1234')))
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/assign-order', methods=['POST'])
@role_required('receptionist')
def assign_order():
    safe_execute("UPDATE orders SET phlebotomist_id=%s, payout_amount=%s WHERE id=%s", (request.form.get('phlebotomist_id') or None, request.form.get('payout_amount', 150), request.form.get('order_id')))
    return redirect(url_for('admin_dashboard'))

if __name__ == '__main__': app.run(debug=True, port=5000)
