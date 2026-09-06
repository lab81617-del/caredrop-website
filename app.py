import os
import threading
import json
import io
import csv
import random
import traceback
import urllib.request
import tempfile
from datetime import datetime
from flask import Flask, render_template, jsonify, request, session, redirect, url_for, send_file
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from fpdf import FPDF
import qrcode
import barcode
from barcode.writer import ImageWriter

load_dotenv()
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "caredrop-super-secret-key-2026")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "IHC2026!")

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
        safe_execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS referred_by VARCHAR(255) DEFAULT 'Self'")
        safe_execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        app._schema_checked = True

def send_email_api(recipient, subject, text_body):
    api_key = os.environ.get("BREVO_API_KEY")
    if not api_key: 
        return "Missing API Key"
    url = "https://api.brevo.com/v3/smtp/email"
    headers = {"accept": "application/json", "api-key": api_key, "content-type": "application/json"}
    data = {
        "sender": {"name": "CareDrop", "email": "ihcdiagnostics.ynr@gmail.com"}, 
        "to": [{"email": recipient}], 
        "subject": subject, 
        "textContent": text_body
    }
    try: 
        req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers=headers, method='POST')
        urllib.request.urlopen(req)
        return "Success"
    except Exception as e: 
        return str(e)

@app.route('/ping')
def ping(): 
    return "OK", 200

@app.route('/api/send-otp', methods=['POST'])
def send_otp():
    email = request.json.get('email', '').strip()
    if not email: 
        return jsonify({"success": False})
    otp = str(random.randint(1000, 9999))
    session[f'otp_{email}'] = otp
    if send_email_api(email, f"CareDrop OTP: {otp}", f"Code: {otp}") == "Success": 
        return jsonify({"success": True})
    return jsonify({"success": False})

@app.route('/api/verify-otp', methods=['POST'])
def verify_otp():
    email = request.json.get('email', '').strip()
    if session.get(f'otp_{email}') == request.json.get('otp', '').strip():
        session[f'verified_{email}'] = True
        return jsonify({"success": True})
    return jsonify({"success": False})

@app.route('/')
def home():
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT hp.id, hp.title, CAST(hp.price AS INTEGER) as original_price, l.id as lab_id, l.name as lab_name, l.rating,
        string_agg(t.name, ', ') as features, so.id as offer_id, CAST(so.discount_percent AS INTEGER) as discount_percent, so.badge, 
        CAST(ROUND(hp.price * (1 - (COALESCE(so.discount_percent, 0) / 100.0))) AS INTEGER) as discounted_price
        FROM health_packages hp JOIN labs l ON hp.lab_id = l.id LEFT JOIN package_tests pt ON hp.id = pt.package_id LEFT JOIN tests t ON pt.test_id = t.id
        LEFT JOIN special_offers so ON hp.id = so.package_id AND so.end_date >= CURRENT_DATE
        GROUP BY hp.id, l.id, l.name, l.rating, so.id, so.discount_percent, so.badge ORDER BY hp.id DESC
    """)
    packages = cursor.fetchall()
    conn.close()
    return render_template('index.html', packages=packages)

@app.route('/tests')
def tests_catalog():
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    grouped_tests = {}
    param_dict = {}
    
    cursor.execute("SELECT DISTINCT t.id, t.name, t.fasting_requirement, t.symptoms, c.name as category FROM tests t LEFT JOIN test_categories c ON t.category_id = c.id JOIN lab_test_pricing ltp ON t.id = ltp.test_id JOIN labs l ON ltp.lab_id = l.id WHERE t.is_active = TRUE AND l.is_active = TRUE ORDER BY c.name, t.name")
    for t in cursor.fetchall(): 
        grouped_tests.setdefault(t['category'] or 'Uncategorized', []).append(t)
        
    cursor.execute("SELECT ltp.test_id, CAST(ltp.price AS INTEGER) as price, l.id as lab_id, l.name as lab_name, CAST(l.rating AS FLOAT) as rating FROM lab_test_pricing ltp JOIN labs l ON ltp.lab_id = l.id WHERE l.is_active = TRUE")
    pricing = cursor.fetchall()
    
    cursor.execute("SELECT test_id, parameter_name FROM test_parameters")
    for p in cursor.fetchall(): 
        param_dict.setdefault(p['test_id'], []).append(p['parameter_name'])
    
    conn.close()
    return render_template('tests.html', grouped_tests=grouped_tests, pricing=json.dumps(pricing, default=str), packages="[]", raw_packages=[], param_dict=json.dumps(param_dict))

@app.route('/book')
def checkout_page(): 
    return render_template('checkout.html')

@app.route('/my-bookings')
def my_bookings():
    email = request.args.get('email', '').strip()
    orders = []
    if email and session.get(f'verified_{email}'):
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT id, order_ref, patient_name, status, CASE WHEN report_file IS NOT NULL THEN TRUE ELSE FALSE END as has_report FROM orders WHERE (SELECT email FROM users WHERE id = orders.user_id) = %s ORDER BY id DESC", (email,))
        orders = cursor.fetchall()
        conn.close()
    return render_template('my_bookings.html', orders=orders, searched_email=email)

@app.route('/download-report/<int:order_id>')
def download_report(order_id):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT report_file, report_filename FROM orders WHERE id = %s", (order_id,))
    record = cursor.fetchone()
    conn.close()
    if record and record['report_file']: 
        return send_file(io.BytesIO(record['report_file']), download_name=record['report_filename'], as_attachment=True)
    return "Not found", 404

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST' and request.form.get('password') == ADMIN_PASSWORD:
        session['admin_logged_in'] = True
        return redirect(url_for('admin_dashboard'))
    return '<html><body style="background:#F1F5F9; display: flex; justify-content:center; align-items:center; height: 100vh;"><form method="POST"><input type="password" name="password" required><button type="submit">Login</button></form></body></html>'

@app.route('/admin')
def admin_dashboard():
    if not session.get('admin_logged_in'): 
        return redirect(url_for('admin_login'))
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT o.id, o.order_ref, o.patient_name, o.age, o.gender, o.collection_date, o.time_slot, CAST(o.total_amount AS INTEGER) as total_amount, o.status, o.referred_by, u.patient_uid, CASE WHEN o.report_file IS NOT NULL THEN TRUE ELSE FALSE END as has_report FROM orders o JOIN users u ON o.user_id = u.id ORDER BY o.id DESC")
    orders = cursor.fetchall()
    
    cursor.execute("SELECT oi.order_id, t.name as test_name FROM order_items oi JOIN tests t ON oi.test_id = t.id WHERE oi.item_type = 'test'")
    items_map = {}
    for row in cursor.fetchall(): 
        items_map.setdefault(row['order_id'], []).append(row)
    for order in orders: 
        order['test_list'] = items_map.get(order['id'], [])
        
    cursor.execute("SELECT id, name, is_active, CAST(rating AS FLOAT) as rating FROM labs ORDER BY name")
    active_labs = [l for l in cursor.fetchall() if l['is_active']]
    
    cursor.execute("SELECT t.id as test_id, t.name as test_name, CAST(ltp.price AS INTEGER) as price FROM lab_test_pricing ltp JOIN tests t ON ltp.test_id = t.id")
    inventory = cursor.fetchall()
    conn.close()
    return render_template('admin.html', orders=orders, active_labs=active_labs, inventory=inventory)

@app.route('/admin/walk-in', methods=['POST'])
def admin_walk_in():
    if not session.get('admin_logged_in'): 
        return redirect(url_for('admin_login'))
    
    p_name = request.form.get('patient_name')
    phone = request.form.get('phone')
    age = request.form.get('age')
    gender = request.form.get('gender')
    total = request.form.get('total_amount', 0)
    test_id = request.form.get('test_id')
    lab_id = request.form.get('lab_id')
    ref_by = request.form.get('referred_by', 'Self').strip()
    
    if not ref_by: 
        ref_by = "Self"
    
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, patient_uid FROM users WHERE phone = %s", (phone,))
        user = cursor.fetchone()
        if user:
            user_id = user[0]
            if not user[1]: 
                cursor.execute("UPDATE users SET patient_uid = %s WHERE id = %s", (f"CD-PAT-{1000 + user_id}", user_id))
        else:
            cursor.execute("INSERT INTO users (name, phone, email) VALUES (%s, %s, %s) RETURNING id", (p_name, phone, f"walkin_{phone}@caredrop.local"))
            user_id = cursor.fetchone()[0]
            cursor.execute("UPDATE users SET patient_uid = %s WHERE id = %s", (f"CD-PAT-{1000 + user_id}", user_id))
            
        cursor.execute("INSERT INTO orders (user_id, patient_name, age, gender, collection_date, time_slot, total_amount, status, referred_by) VALUES (%s, %s, %s, %s, %s, 'Immediate', %s, 'Pending', %s) RETURNING id", (user_id, p_name, age, gender, datetime.today().strftime('%Y-%m-%d'), total, ref_by))
        order_id = cursor.fetchone()[0]
        
        cursor.execute("UPDATE orders SET order_ref = %s WHERE id = %s", (f"ORD-{datetime.today().strftime('%y%m')}-{order_id:04d}", order_id))
        cursor.execute("INSERT INTO order_items (order_id, test_id, lab_id, price, item_type) VALUES (%s, %s, %s, %s, 'test')", (order_id, test_id, lab_id, total))
        conn.commit()
    except Exception as e: 
        conn.rollback()
        print(e)
    finally: 
        conn.close()
    return redirect(url_for('admin_dashboard'))

# ==========================================
# MASSIVE LIMS AUTO-SEEDER
# ==========================================
@app.route('/admin/auto-seed-lims')
def auto_seed_lims():
    if not session.get('admin_logged_in'): 
        return redirect(url_for('admin_login'))
    conn = get_db()
    try:
        cursor = conn.cursor()
        master_params = {
            'Complete Blood Count': [
                ('Hemoglobin (HB)', 'g/dl', '12.0 - 16.0'), ('Total Leucocytes Count (WBC)', 'Cells/Cumm', '4000 - 10500'), 
                ('Neutrophils', '%', '40 - 80'), ('Lymphocytes', '%', '20 - 40'),
                ('Eosinophils', '%', '01 - 06'), ('Monocytes', '%', '02 - 10'), ('Basophils', '%', '00 - 01'),
                ('Absolute Neutrophil Count', 'Cells/uL', '2000 - 8000'), ('Absolute Lymphocyte Count', '/uL', '1000 - 3000'),
                ('Absolute Eosinophil Count (AEC)', 'Cells/cumm', '20 - 500'), ('Absolute Monocyte Count', 'Cells/uL', '200 - 1000'),
                ('Mean Cell Haemoglobin (MCH)', 'Pg', '27 - 32'), ('MCHC', 'g/dl', '31.5 - 34.5'),
                ('Erythrocyte count (RBC COUNT)', 'million/cmm', '3.8 - 4.8'), ('Packed Cell Volume (Hematocrit)', '%', '36 - 46'),
                ('Mean Cell Volume (MCV)', 'fL', '83 - 101'), ('Red Cell Distribution Width (RDW)-SD', 'fL', '35 - 56'),
                ('Red Cell Distribution Width (RDW)-CV', '%', '11.5 - 14.5'), ('Platelet Count', 'Lakh/cumm', '1.50 - 4.50'),
                ('Plateletcrit (PCT)', '%', '0.2 - 0.5'), ('Platelet-Large Cell Count (P-LCC)', 'lakh/cmm', '40 - 100'),
                ('Platelet large cell ratio (P-LCR)', '%', '11.9 - 66.9'), ('Platelet Distribution Width (PDW-CV)', '%', '9.00 - 17.00'),
                ('Platelet Distribution Width (PDW-SD)', '', '0 - 25'), ('Platelet-to-Lymphocyte Ratio (PLR)', '', '36.63 - 149.13'),
                ('Erythrocytes Sedimentation Rate (ESR)', 'mm/1st hr', '0 - 20')
            ],
            'Liver Function': [
                ('Bilirubin (Total)', 'mg/dL', '0.2 - 1.2'), ('Bilirubin (Direct)', 'mg/dL', '0.0 - 0.3'),
                ('Bilirubin (Indirect)', 'mg/dL', '0.2 - 0.9'), ('SGOT / AST', 'U/L', '5 - 40'), 
                ('SGPT / ALT', 'U/L', '7 - 56'), ('Alkaline Phosphatase (ALP)', 'U/L', '40 - 129'),
                ('Gamma Glutamyl Transferase (GGT)', 'U/L', 'Upto 60'), ('Total Protein', 'g/dL', '6.0 - 8.3'), 
                ('Albumin', 'g/dL', '3.5 - 5.2'), ('Globulin', 'g/dL', '2.5 - 3.5'), ('A/G Ratio', 'Ratio', '1.0 - 2.1')
            ],
            'Kidney Function': [
                ('Blood Urea', 'mg/dL', '14 - 40'), ('Blood Urea Nitrogen (BUN)', 'mg/dl', '5 - 25'), 
                ('Serum Creatinine', 'mg/dl', '0.5 - 1.1'), ('Bun/Creatinine Ratio', '', '6 - 23'), 
                ('Serum Uric Acid', 'mg/dL', '3.4 - 7.0'), ('Calcium', 'mg/dl', '8.6 - 10.2'), 
                ('Sodium', 'mmol/L', '135 - 155'), ('Potassium', 'mmol/L', '3.5 - 5.0'), ('Chloride', 'mmol/L', '95 - 108')
            ],
            'Lipid Profile': [
                ('Total Cholesterol', 'mg/dl', '< 200'), ('Triglycerides', 'mg/dl', '< 150'),
                ('Cholesterol-HDL', 'mg/dl', '40 - 60'), ('Cholesterol-LDL (Direct)', 'mg/dl', '< 100'),
                ('Cholesterol-VLDL', 'mg/dl', '7 - 40'), ('Total Cholesterol/HDL Ratio', 'Ratio', '< 6'),
                ('LDL/HDL Ratio', 'Ratio', '0.0 - 3.5'), ('Non-HDL Cholesterol', 'mg/dl', '0 - 160')
            ],
            'Thyroid Profile': [
                ('Total T3', 'ng/dL', '80 - 200'), ('Total T4', 'ug/dL', '4.5 - 12.0'), ('TSH', 'uIU/mL', '0.4 - 4.0')
            ],
            'HbA1c': [
                ('Glycosylated Hemoglobin (HbA1C)', '%', '< 5.6'), ('Estimated Average Glucose', 'mg/dl', '90 - 120')
            ],
            'Urine Routine': [
                ('Color', '', 'Pale Yellow'), ('Appearance', '', 'Clear'), ('Specific Gravity', '', '1.010 - 1.025'),
                ('pH', '', '5.0 - 8.0'), ('Protein / Albumin', '', 'Absent'), ('Glucose (Sugar)', '', 'Absent'),
                ('Ketones', '', 'Absent'), ('Blood', '', 'Absent'), ('Bilirubin', '', 'Absent'), ('Urobilinogen', '', 'Normal'),
                ('Pus Cells (Leukocytes)', '/HPF', '0 - 5'), ('Red Blood Cells (RBC)', '/HPF', '0 - 2'),
                ('Epithelial Cells', '/HPF', 'Few'), ('Casts', '', 'Absent'), ('Crystals', '', 'Absent')
            ],
            'Widal': [
                ('Salmonella Typhi O', 'Titer', '< 1:80'), ('Salmonella Typhi H', 'Titer', '< 1:80'),
                ('Salmonella Paratyphi AH', 'Titer', '< 1:80'), ('Salmonella Paratyphi BH', 'Titer', '< 1:80')
            ],
            'Dengue': [
                ('Dengue NS1 Antigen', 'Index', '< 0.9 (Negative)'), ('Dengue IgG Antibody', 'Index', '< 0.9 (Negative)'), ('Dengue IgM Antibody', 'Index', '< 0.9 (Negative)')
            ]
        }
        
        for search_name, params in master_params.items():
            cursor.execute("SELECT id FROM tests WHERE name ILIKE %s LIMIT 1", (f"%{search_name}%",))
            test = cursor.fetchone()
            if test:
                cursor.execute("DELETE FROM test_parameters WHERE test_id = %s", (test[0],))
                for p_name, unit, ref in params:
                    cursor.execute("INSERT INTO test_parameters (test_id, parameter_name, unit, reference_range) VALUES (%s, %s, %s, %s)", (test[0], p_name, unit, ref))
        conn.commit()
        return "<h2 style='color:green; padding:50px;'>SUCCESS! Over 100 parameters securely locked to your tests. Close this tab.</h2>"
    except Exception as e: 
        return f"<h2 style='color:red;'>Error: {str(e)}</h2>"
    finally: 
        conn.close()

# ==========================================
# ENTERPRISE MEDICAL PDF GENERATOR
# ==========================================
class CareDropPDF(FPDF):
    def __init__(self, qr_path, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.qr_path = qr_path

    def header(self):
        self.set_y(10)
        self.set_font("helvetica", "B", 20)
        self.set_text_color(11, 128, 100)
        self.cell(120, 8, "CAREDROP DIAGNOSTICS", ln=False)
        
        if self.qr_path:
            self.image(self.qr_path, x=175, y=8, w=22)
            
        self.ln(8)
        self.set_font("helvetica", "B", 9)
        self.set_text_color(100, 100, 100)
        self.cell(120, 5, "Precision & Care in Every Drop", ln=True)
        self.set_draw_color(11, 128, 100)
        self.set_line_width(0.5)
        self.line(10, 25, 200, 25)
        self.set_line_width(0.2)
        self.ln(5)
        
    def footer(self):
        self.set_y(-30)
        self.set_draw_color(200, 200, 200)
        self.line(10, 265, 200, 265)
        self.set_y(-25)
        self.set_font("helvetica", "B", 10)
        self.set_text_color(15, 23, 42)
        self.cell(100, 5, "CareDrop Digital Verification", ln=False, align="L")
        self.cell(90, 5, "Chief Laboratory Director", ln=True, align="R")
        self.set_font("helvetica", "", 8)
        self.set_text_color(100, 100, 100)
        self.cell(100, 4, "Electronically processed and verified. No physical signature required.", ln=False, align="L")
        self.cell(90, 4, "Verified by CareDrop LIMS", ln=True, align="R")
        self.set_y(-10)
        self.set_font("helvetica", "I", 8)
        self.cell(0, 5, f"Report Generated on {datetime.today().strftime('%Y-%m-%d %I:%M %p')} | Page {self.page_no()}", align="C")

@app.route('/admin/fill-report/<int:order_id>')
def admin_fill_report(order_id):
    if not session.get('admin_logged_in'): 
        return redirect(url_for('admin_login'))
    conn = get_db()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT o.*, u.patient_uid FROM orders o JOIN users u ON o.user_id = u.id WHERE o.id = %s", (order_id,))
        order = cursor.fetchone()
        
        cursor.execute("""
            SELECT oi.order_id, t.id as test_id, t.name as test_name, c.name as cat_name 
            FROM order_items oi JOIN tests t ON oi.test_id = t.id LEFT JOIN test_categories c ON t.category_id = c.id WHERE oi.item_type = 'test' AND oi.order_id = %s
            UNION
            SELECT oi.order_id, t.id as test_id, t.name as test_name, c.name as cat_name 
            FROM order_items oi JOIN package_tests pt ON oi.test_id = pt.package_id JOIN tests t ON pt.test_id = t.id LEFT JOIN test_categories c ON t.category_id = c.id WHERE oi.item_type = 'package' AND oi.order_id = %s
        """, (order_id, order_id))
        tests = cursor.fetchall()
        
        for t in tests:
            cursor.execute("SELECT id, parameter_name, unit, reference_range FROM test_parameters WHERE test_id = %s", (t['test_id'],))
            t['parameters'] = cursor.fetchall()
    except Exception as e: 
        return str(e)
    finally: 
        conn.close()
    return render_template('lims_report.html', order=order, tests=tests)

@app.route('/admin/save-results/<int:order_id>', methods=['POST'])
def save_results(order_id):
    if not session.get('admin_logged_in'): 
        return redirect(url_for('admin_login'))
    conn = get_db()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("DELETE FROM order_results WHERE order_id = %s", (order_id,)) 
        
        results_data = []
        for key, value in request.form.items():
            if key.startswith('param_') and value.strip() != '':
                param_id = key.split('_')[1]
                val = value.strip()
                should_print = request.form.get(f'print_{param_id}') == 'on'
                cursor.execute("INSERT INTO order_results (order_id, parameter_id, result_value) VALUES (%s, %s, %s)", (order_id, param_id, val))
                
                if should_print:
                    cursor.execute("SELECT tp.parameter_name, tp.unit, tp.reference_range, t.name as test_name, c.name as cat_name FROM test_parameters tp JOIN tests t ON tp.test_id = t.id LEFT JOIN test_categories c ON t.category_id = c.id WHERE tp.id = %s", (param_id,))
                    p_info = cursor.fetchone()
                    if p_info: 
                        results_data.append({
                            'cat': p_info['cat_name'] or 'PATHOLOGY', 
                            'test': p_info['test_name'], 
                            'param': p_info['parameter_name'], 
                            'val': val, 
                            'unit': p_info['unit'], 
                            'ref': p_info['reference_range']
                        })

        cursor.execute("SELECT o.*, u.patient_uid FROM orders o JOIN users u ON o.user_id = u.id WHERE o.id = %s", (order_id,))
        order = cursor.fetchone()

        # Generate Top-Header QR
        qr = qrcode.QRCode(box_size=4, border=0)
        qr.add_data(f"https://caredrop.in/download-report/{order_id}")
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="#0F172A", back_color="white")
        tf_qr = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
        qr_img.save(tf_qr, 'PNG')
        tf_qr.close()

        # Generate Barcode
        tf_bc = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
        tf_bc.close()
        bc_img = barcode.get('code128', order['patient_uid'], writer=ImageWriter())
        bc_path = bc_img.save(tf_bc.name.replace('.png', ''))

        pdf = CareDropPDF(qr_path=tf_qr.name)
        pdf.add_page()
        
        pdf.set_y(38)
        pdf.set_font("helvetica", "", 9)
        pdf.set_text_color(100, 100, 100)
        
        pdf.cell(32, 6, "Patient Name", 0, 0)
        pdf.set_text_color(15, 23, 42)
        pdf.set_font("helvetica", "B", 10)
        pdf.cell(68, 6, f": {order['patient_name']}", 0, 0)
        
        pdf.set_font("helvetica", "", 9)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(32, 6, "Registered On", 0, 0)
        pdf.set_text_color(15, 23, 42)
        pdf.set_font("helvetica", "B", 9)
        created_time = order.get('created_at', datetime.today()).strftime('%Y-%m-%d %I:%M %p')
        pdf.cell(58, 6, f": {created_time}", 0, 1)

        pdf.set_font("helvetica", "", 9)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(32, 6, "Age / Gender", 0, 0)
        pdf.set_text_color(15, 23, 42)
        pdf.set_font("helvetica", "B", 9)
        pdf.cell(68, 6, f": {order['age']} Yrs / {order['gender']}", 0, 0)
        
        pdf.set_font("helvetica", "", 9)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(32, 6, "Collected On", 0, 0)
        pdf.set_text_color(15, 23, 42)
        pdf.set_font("helvetica", "B", 9)
        pdf.cell(58, 6, f": {order['collection_date']} {order.get('time_slot', 'Immediate')}", 0, 1)

        pdf.set_font("helvetica", "", 9)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(32, 6, "Referred By", 0, 0)
        pdf.set_text_color(15, 23, 42)
        pdf.set_font("helvetica", "B", 9)
        pdf.cell(68, 6, f": {order.get('referred_by', 'Self')}", 0, 0)
        
        pdf.set_font("helvetica", "", 9)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(32, 6, "Reported On", 0, 0)
        pdf.set_text_color(15, 23, 42)
        pdf.set_font("helvetica", "B", 9)
        pdf.cell(58, 6, f": {datetime.today().strftime('%Y-%m-%d %I:%M %p')}", 0, 1)

        pdf.set_font("helvetica", "", 9)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(32, 8, "UID Barcode", 0, 0)
        pdf.image(bc_path, x=42, y=pdf.get_y()+1, h=6)
        pdf.ln(12)
        
        pdf.set_draw_color(220, 220, 220)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(4)
        
        pdf.set_font("helvetica", "B", 9)
        pdf.set_fill_color(248, 250, 252)
        pdf.set_text_color(100, 116, 139)
        pdf.cell(85, 8, ' INVESTIGATION', 0, 0, 'L', True)
        pdf.cell(25, 8, 'RESULT', 0, 0, 'C', True)
        pdf.cell(30, 8, 'UNIT', 0, 0, 'C', True)
        pdf.cell(50, 8, 'BIO. REF. INTERVAL', 0, 1, 'C', True)
        
        current_cat, current_test = "", ""
        for r in results_data:
            if r['cat'] != current_cat:
                pdf.ln(4)
                pdf.set_font("helvetica", "BU", 10)
                pdf.set_text_color(13, 148, 136)
                pdf.cell(190, 8, f"{r['cat'].upper()}", 0, 1, 'C')
                current_cat = r['cat']
                
            if r['test'] != current_test:
                pdf.set_font("helvetica", "B", 10)
                pdf.set_text_color(15, 23, 42)
                pdf.cell(190, 8, r['test'].title(), 0, 1, 'L')
                current_test = r['test']
            
            pdf.set_font("helvetica", "", 10)
            pdf.set_text_color(51, 65, 85)
            pdf.cell(85, 7, f"  {r['param']}", 0, 0, 'L')
            
            pdf.set_font("helvetica", "B", 10)
            pdf.set_text_color(15, 23, 42)
            pdf.cell(25, 7, r['val'], 0, 0, 'C')
            
            pdf.set_font("helvetica", "", 9)
            pdf.set_text_color(100, 100, 100)
            pdf.cell(30, 7, r['unit'], 0, 0, 'C')
            
            pdf.cell(50, 7, r['ref'], 0, 1, 'C')
            
            pdf.set_draw_color(241, 245, 249)
            pdf.line(12, pdf.get_y(), 198, pdf.get_y())

        os.remove(tf_qr.name)
        os.remove(bc_path)

        pdf_bytes = pdf.output()
        filename = f"CareDrop_Report_{order['patient_uid']}.pdf"
        cursor.execute("UPDATE orders SET report_file = %s, report_filename = %s, status = 'Completed', report_type = 'System' WHERE id = %s", (psycopg2.Binary(pdf_bytes), filename, order_id))
        conn.commit()
    except Exception as e: 
        conn.rollback()
        print(str(e))
    finally: 
        conn.close()
    return redirect(url_for('admin_dashboard'))

# --- OTHER ROUTES ---
@app.route('/admin/add-test', methods=['POST'])
def admin_add_test():
    if session.get('admin_logged_in'):
        name = request.form.get('test_name')
        cat_id = request.form.get('category_id')
        fasting = request.form.get('fasting')
        price = request.form.get('price')
        params = request.form.get('parameter_count', 1)
        lab_ids = request.form.getlist('lab_ids')
        conn = get_db()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM tests WHERE name ILIKE %s", (name,))
            existing = cursor.fetchone()
            if existing:
                test_id = existing[0]
            else:
                cursor.execute("INSERT INTO tests (name, category_id, fasting_requirement, is_active) VALUES (%s, %s, %s, TRUE) RETURNING id", (name, cat_id or None, fasting))
                test_id = cursor.fetchone()[0]
            for lid in lab_ids:
                cursor.execute("SELECT test_id FROM lab_test_pricing WHERE test_id=%s AND lab_id=%s", (test_id, lid))
                if cursor.fetchone():
                    cursor.execute("UPDATE lab_test_pricing SET price=%s, parameter_count=%s WHERE test_id=%s AND lab_id=%s", (price, params, test_id, lid))
                else: 
                    cursor.execute("INSERT INTO lab_test_pricing (test_id, lab_id, price, parameter_count) VALUES (%s, %s, %s, %s)", (test_id, lid, price, params))
            conn.commit()
        except: 
            conn.rollback()
        finally: 
            conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/add-category', methods=['POST'])
def add_category():
    if session.get('admin_logged_in'): 
        safe_execute("INSERT INTO test_categories (name) VALUES (%s) ON CONFLICT DO NOTHING", (request.form.get('category_name').strip(),))
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/add-lab', methods=['POST'])
def add_lab():
    if session.get('admin_logged_in'): 
        safe_execute("INSERT INTO labs (name, rating, cert_badge, is_active) VALUES (%s, %s, %s, TRUE) ON CONFLICT (name) DO UPDATE SET rating=%s, cert_badge=%s", (request.form.get('lab_name').strip(), request.form.get('rating'), request.form.get('cert_badge').strip(), request.form.get('rating'), request.form.get('cert_badge').strip()))
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/toggle-lab/<int:lab_id>', methods=['POST'])
def toggle_lab(lab_id):
    if session.get('admin_logged_in'): 
        safe_execute("UPDATE labs SET is_active = NOT is_active WHERE id = %s", (lab_id,))
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete-lab/<int:lab_id>', methods=['POST'])
def delete_lab(lab_id):
    if session.get('admin_logged_in'): 
        safe_execute("DELETE FROM lab_test_pricing WHERE lab_id=%s; DELETE FROM labs WHERE id=%s", (lab_id, lab_id))
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete-inventory/<int:test_id>/<int:lab_id>', methods=['POST'])
def delete_inventory(test_id, lab_id):
    if session.get('admin_logged_in'): 
        safe_execute("DELETE FROM lab_test_pricing WHERE test_id=%s AND lab_id=%s", (test_id, lab_id))
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete-master-test/<int:test_id>', methods=['POST'])
def delete_master_test(test_id):
    if session.get('admin_logged_in'): 
        safe_execute("DELETE FROM tests WHERE id=%s", (test_id,))
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/add-health-package', methods=['POST'])
def add_health_package():
    if session.get('admin_logged_in'):
        conn = get_db()
        try:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO health_packages (title, lab_id, price) VALUES (%s, %s, %s) RETURNING id", (request.form.get('title'), request.form.get('lab_id'), request.form.get('price')))
            pkg_id = cursor.fetchone()[0]
            for tid in request.form.getlist('test_ids'): 
                cursor.execute("INSERT INTO package_tests (package_id, test_id) VALUES (%s, %s)", (pkg_id, tid))
            conn.commit()
        except: 
            conn.rollback()
        finally: 
            conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete-health-package/<int:pkg_id>', methods=['POST'])
def delete_health_package(pkg_id):
    if session.get('admin_logged_in'): 
        safe_execute("DELETE FROM health_packages WHERE id=%s", (pkg_id,))
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/add-special-offer', methods=['POST'])
def add_special_offer():
    if session.get('admin_logged_in'): 
        safe_execute("DELETE FROM special_offers WHERE package_id=%s; INSERT INTO special_offers (package_id, discount_percent, badge, end_date) VALUES (%s, %s, %s, %s)", (request.form.get('package_id'), request.form.get('package_id'), request.form.get('discount_percent'), request.form.get('badge'), request.form.get('end_date')))
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete-offer/<int:offer_id>', methods=['POST'])
def delete_offer(offer_id):
    if session.get('admin_logged_in'): 
        safe_execute("DELETE FROM special_offers WHERE id=%s", (offer_id,))
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/add-phlebotomist', methods=['POST'])
def add_phlebotomist():
    if session.get('admin_logged_in'): 
        safe_execute("INSERT INTO phlebotomists (name, phone, vehicle_number) VALUES (%s, %s, %s)", (request.form.get('name'), request.form.get('phone'), request.form.get('vehicle_number')))
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/update-order', methods=['POST'])
def update_order():
    if session.get('admin_logged_in'): 
        safe_execute("UPDATE orders SET status=%s WHERE id=%s", (request.form.get('status'), request.form.get('order_id')))
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/assign-order', methods=['POST'])
def assign_order():
    if session.get('admin_logged_in'): 
        safe_execute("UPDATE orders SET phlebotomist_id=%s, payout_amount=%s WHERE id=%s", (request.form.get('phlebotomist_id') or None, request.form.get('payout_amount', 0), request.form.get('order_id')))
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/add-parameter', methods=['POST'])
def add_parameter():
    if session.get('admin_logged_in'):
        conn = get_db()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM tests WHERE name = %s", (request.form.get('test_name'),))
            test = cursor.fetchone()
            if test: 
                cursor.execute("INSERT INTO test_parameters (test_id, parameter_name, unit, reference_range) VALUES (%s, %s, %s, %s)", (test[0], request.form.get('parameter_name'), request.form.get('unit'), request.form.get('reference_range')))
            conn.commit()
        except: 
            conn.rollback()
        finally: 
            conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete-parameter/<int:param_id>', methods=['POST'])
def delete_parameter(param_id):
    if session.get('admin_logged_in'): 
        safe_execute("DELETE FROM test_parameters WHERE id=%s", (param_id,))
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/bulk-upload', methods=['POST'])
def bulk_upload():
    if session.get('admin_logged_in'):
        file = request.files.get('csv_file')
        if file and file.filename != '':
            conn = get_db()
            try:
                stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
                csv_input = csv.reader(stream)
                next(csv_input, None)
                cursor = conn.cursor()
                for row in csv_input:
                    if len(row) < 4: 
                        continue
                    name, cat_name, fasting, symptoms = [str(r).strip() for r in row[:4]]
                    if not name: 
                        continue
                    cursor.execute("INSERT INTO test_categories (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (cat_name,))
                    cursor.execute("SELECT id FROM test_categories WHERE name = %s", (cat_name,))
                    cursor.execute("INSERT INTO tests (name, category_id, fasting_requirement, is_active, symptoms) VALUES (%s, %s, %s, TRUE, %s) ON CONFLICT (name) DO NOTHING", (name, cursor.fetchone()[0], fasting, symptoms))
                conn.commit()
            except: 
                conn.rollback()
            finally: 
                conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/upload-report', methods=['POST'])
def upload_report():
    if session.get('admin_logged_in'):
        file = request.files.get('report_file')
        if file and file.filename: 
            safe_execute("UPDATE orders SET report_file=%s, report_filename=%s, status='Completed', report_type='Manual' WHERE id=%s", (psycopg2.Binary(file.read()), file.filename, request.form.get('order_id')))
    return redirect(url_for('admin_dashboard'))

@app.route('/api/place-order', methods=['POST'])
def place_order():
    name = request.form.get('name')
    phone = request.form.get('phone')
    email = request.form.get('email')
    patient_name = request.form.get('patient_name')
    age = request.form.get('age')
    gender = request.form.get('gender')
    address = request.form.get('address')
    date = request.form.get('date')
    cart_json = request.form.get('cart', '[]')
    
    if not session.get(f'verified_{email}'): 
        return jsonify({"success": False, "message": "Verify email."})
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
        if user:
            user_id = user[0]
        else:
            cursor.execute("INSERT INTO users (name, phone, email) VALUES (%s, %s, %s) RETURNING id", (name, phone, email))
            user_id = cursor.fetchone()[0]
        
        cursor.execute("INSERT INTO orders (user_id, patient_name, age, gender, address, collection_date, time_slot, total_amount, status) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'Pending') RETURNING id", (user_id, patient_name if patient_name else name, age, gender, address, date, request.form.get('time_slot', 'Morning'), request.form.get('total', 0)))
        order_id = cursor.fetchone()[0]
        cursor.execute("UPDATE orders SET order_ref = %s WHERE id = %s", (f"ORD-{datetime.today().strftime('%y%m')}-{order_id:04d}", order_id))
        
        for item in json.loads(cart_json):
            clean_id = str(item['id']).replace('PKG_','')
            is_pkg = 'package' if 'PKG_' in str(item['id']) else 'test'
            cursor.execute("INSERT INTO order_items (order_id, test_id, lab_id, price, item_type) VALUES (%s, %s, %s, %s, %s)", (order_id, clean_id, item['selectedLabId'], item['currentPrice'], is_pkg))
        conn.commit()
        return jsonify({"success": True, "order_id": order_id})
    except Exception as e: 
        conn.rollback()
        return jsonify({"success": False, "message": str(e)})
    finally: 
        conn.close()

if __name__ == '__main__': 
    app.run(debug=True, port=5000)
