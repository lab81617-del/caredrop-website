import os, threading, json, io, csv, random, traceback, urllib.request, tempfile
from datetime import datetime
from flask import Flask, render_template, jsonify, request, session, redirect, url_for, send_file
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from fpdf import FPDF
import qrcode

load_dotenv()
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "caredrop-super-secret-key-2026")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "IHC2026!")
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'pool_size': 5, 'max_overflow': 2, 'pool_recycle': 300, 'pool_pre_ping': True}

def get_db(): return psycopg2.connect(os.environ.get("DATABASE_URL"))

def safe_execute(query, params=None):
    conn = get_db()
    try: 
        cursor = conn.cursor(); cursor.execute(query, params); conn.commit()
    except Exception as e: conn.rollback(); print(e)
    finally: conn.close()

def send_email_api(recipient, subject, text_body):
    api_key = os.environ.get("BREVO_API_KEY")
    if not api_key: return "Missing API Key"
    url, headers = "https://api.brevo.com/v3/smtp/email", {"accept": "application/json", "api-key": api_key, "content-type": "application/json"}
    data = {"sender": {"name": "CareDrop", "email": "ihcdiagnostics.ynr@gmail.com"}, "to": [{"email": recipient}], "subject": subject, "textContent": text_body}
    try: urllib.request.urlopen(urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers=headers, method='POST')); return "Success"
    except Exception as e: return str(e)

def send_email_async(recipient, subject, body): threading.Thread(target=send_email_api, args=(recipient, subject, body)).start()

@app.route('/ping')
def ping(): return "OK", 200

@app.route('/api/send-otp', methods=['POST'])
def send_otp():
    email = request.json.get('email', '').strip()
    if not email: return jsonify({"success": False})
    otp = str(random.randint(1000, 9999)); session[f'otp_{email}'] = otp
    if send_email_api(email, f"CareDrop OTP: {otp}", f"Code: {otp}") == "Success": return jsonify({"success": True})
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
    conn = get_db(); cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT hp.id, hp.title, CAST(hp.price AS INTEGER) as original_price, l.id as lab_id, l.name as lab_name, l.rating,
        string_agg(t.name, ', ') as features, so.id as offer_id, CAST(so.discount_percent AS INTEGER) as discount_percent, so.badge, 
        CAST(ROUND(hp.price * (1 - (COALESCE(so.discount_percent, 0) / 100.0))) AS INTEGER) as discounted_price
        FROM health_packages hp JOIN labs l ON hp.lab_id = l.id LEFT JOIN package_tests pt ON hp.id = pt.package_id LEFT JOIN tests t ON pt.test_id = t.id
        LEFT JOIN special_offers so ON hp.id = so.package_id AND so.end_date >= CURRENT_DATE
        GROUP BY hp.id, l.id, l.name, l.rating, so.id, so.discount_percent, so.badge ORDER BY hp.id DESC
    """)
    packages = cursor.fetchall(); conn.close()
    return render_template('index.html', packages=packages)

@app.route('/tests')
def tests_catalog():
    conn = get_db(); cursor = conn.cursor(cursor_factory=RealDictCursor)
    grouped_tests = {}; pricing = []; packages = []
    
    cursor.execute("SELECT DISTINCT t.id, t.name, t.fasting_requirement, t.symptoms, c.name as category FROM tests t LEFT JOIN test_categories c ON t.category_id = c.id JOIN lab_test_pricing ltp ON t.id = ltp.test_id JOIN labs l ON ltp.lab_id = l.id WHERE t.is_active = TRUE AND l.is_active = TRUE ORDER BY c.name, t.name")
    for t in cursor.fetchall(): grouped_tests.setdefault(t['category'] or 'Uncategorized', []).append(t)
        
    cursor.execute("SELECT ltp.test_id, CAST(ltp.price AS INTEGER) as price, l.id as lab_id, l.name as lab_name, CAST(l.rating AS FLOAT) as rating FROM lab_test_pricing ltp JOIN labs l ON ltp.lab_id = l.id WHERE l.is_active = TRUE")
    pricing = cursor.fetchall()
    
    cursor.execute("""
        SELECT hp.id, hp.title, CAST(hp.price AS INTEGER) as original_price, l.id as lab_id, l.name as lab_name, CAST(l.rating AS FLOAT) as rating, string_agg(t.name, ', ') as features, so.id as offer_id, CAST(so.discount_percent AS INTEGER) as discount_percent, CAST(ROUND(hp.price * (1 - (COALESCE(so.discount_percent, 0) / 100.0))) AS INTEGER) as discounted_price
        FROM health_packages hp JOIN labs l ON hp.lab_id = l.id LEFT JOIN package_tests pt ON hp.id = pt.package_id LEFT JOIN tests t ON pt.test_id = t.id LEFT JOIN special_offers so ON hp.id = so.package_id AND so.end_date >= CURRENT_DATE
        GROUP BY hp.id, l.id, l.name, l.rating, so.id, so.discount_percent ORDER BY hp.id DESC
    """)
    packages = cursor.fetchall(); conn.close()
    return render_template('tests.html', grouped_tests=grouped_tests, pricing=json.dumps(pricing, default=str), packages=json.dumps(packages, default=str), raw_packages=packages)

@app.route('/book')
def checkout_page(): return render_template('checkout.html')

@app.route('/my-bookings')
def my_bookings():
    email = request.args.get('email', '').strip()
    orders = []
    if email and session.get(f'verified_{email}'):
        conn = get_db(); cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT o.id, o.order_ref, o.patient_name, o.age, o.gender, o.collection_date, o.time_slot, CAST(o.total_amount AS INTEGER) as total_amount, o.status, u.patient_uid, CASE WHEN o.report_file IS NOT NULL THEN TRUE ELSE FALSE END as has_report FROM orders o JOIN users u ON o.user_id = u.id WHERE u.email = %s ORDER BY o.id DESC", (email,))
        orders = cursor.fetchall()
        for order in orders:
            cursor.execute("SELECT CASE WHEN oi.item_type = 'package' THEN hp.title ELSE t.name END as test_name, l.name as lab_name FROM order_items oi LEFT JOIN tests t ON oi.test_id = t.id AND oi.item_type = 'test' LEFT JOIN health_packages hp ON oi.test_id = hp.id AND oi.item_type = 'package' JOIN labs l ON oi.lab_id = l.id WHERE oi.order_id = %s", (order['id'],))
            order['test_list'] = cursor.fetchall()
        conn.close()
    elif email: return render_template('my_bookings.html', error="Verify email.", searched_email=email)
    return render_template('my_bookings.html', orders=orders, searched_email=email)

@app.route('/download-report/<int:order_id>')
def download_report(order_id):
    conn = get_db(); cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT report_file, report_filename FROM orders WHERE id = %s", (order_id,))
    record = cursor.fetchone(); conn.close()
    if record and record['report_file']: return send_file(io.BytesIO(record['report_file']), download_name=record['report_filename'], as_attachment=True)
    return "Not found", 404

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST' and request.form.get('password') == ADMIN_PASSWORD:
        session['admin_logged_in'] = True; return redirect(url_for('admin_dashboard'))
    return f'<html><body style="background:#F1F5F9; display: flex; justify-content:center; align-items:center; height: 100vh;"><div style="background: white; padding:40px; border-radius: 12px; text-align:center; font-family:sans-serif;"><form method="POST"><input type="password" name="password" placeholder="Master Password" required style="padding: 14px; margin-bottom: 15px; width:100%;"><button type="submit" style="width:100%; background: #0F172A; color: white; padding: 14px; border: none; border-radius: 8px;">Login</button></form></div></body></html>'

@app.route('/admin')
def admin_dashboard():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    conn = get_db(); cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    cursor.execute("SELECT o.id, o.order_ref, o.patient_name, o.age, o.gender, o.address, o.collection_date, o.time_slot, CAST(o.total_amount AS INTEGER) as total_amount, o.status, u.phone, u.patient_uid, CASE WHEN o.report_file IS NOT NULL THEN TRUE ELSE FALSE END as has_report, o.phlebotomist_id, CAST(o.payout_amount AS INTEGER) as payout_amount FROM orders o JOIN users u ON o.user_id = u.id ORDER BY o.id DESC")
    orders = cursor.fetchall()
    
    cursor.execute("SELECT oi.order_id, CASE WHEN oi.item_type = 'package' THEN hp.title ELSE t.name END as test_name, l.name as lab_name FROM order_items oi LEFT JOIN tests t ON oi.test_id = t.id AND oi.item_type = 'test' LEFT JOIN health_packages hp ON oi.test_id = hp.id AND oi.item_type = 'package' JOIN labs l ON oi.lab_id = l.id")
    items_map = {}
    for row in cursor.fetchall(): items_map.setdefault(row['order_id'], []).append(row)

    for order in orders: order['test_list'] = items_map.get(order['id'], [])
        
    cursor.execute("SELECT tp.id, tp.parameter_name, tp.unit, tp.reference_range, t.name as test_name FROM test_parameters tp JOIN tests t ON tp.test_id = t.id ORDER BY t.name")
    test_parameters = cursor.fetchall()

    cursor.execute("SELECT id, name, is_active, CAST(rating AS FLOAT) as rating, cert_badge FROM labs ORDER BY name")
    all_labs = cursor.fetchall(); active_labs = [l for l in all_labs if l['is_active']]
    
    cursor.execute("SELECT id, name FROM test_categories ORDER BY name")
    categories = cursor.fetchall()
    
    cursor.execute("SELECT t.id as test_id, t.name as test_name, c.name as category_name, l.id as lab_id, l.name as lab_name, CAST(ltp.price AS INTEGER) as price FROM lab_test_pricing ltp JOIN tests t ON ltp.test_id = t.id JOIN labs l ON ltp.lab_id = l.id LEFT JOIN test_categories c ON t.category_id = c.id ORDER BY t.name ASC")
    inventory = cursor.fetchall()
    
    cursor.execute("SELECT t.id, t.name, c.name as category_name, t.fasting_requirement, t.symptoms FROM tests t LEFT JOIN test_categories c ON t.category_id = c.id ORDER BY t.name ASC")
    master_tests = cursor.fetchall()
    
    cursor.execute("SELECT * FROM phlebotomists ORDER BY id DESC")
    phlebotomists = cursor.fetchall()

    cursor.execute("""
        SELECT hp.id, hp.title, CAST(hp.price AS INTEGER) as original_price, l.name as lab_name,
        string_agg(t.name, ', ') as features, so.id as offer_id, CAST(so.discount_percent AS INTEGER) as discount_percent, so.badge, TO_CHAR(so.end_date, 'YYYY-MM-DD') as end_date,
        CAST(ROUND(hp.price * (1 - (COALESCE(so.discount_percent, 0) / 100.0))) AS INTEGER) as discounted_price
        FROM health_packages hp JOIN labs l ON hp.lab_id = l.id LEFT JOIN package_tests pt ON hp.id = pt.package_id LEFT JOIN tests t ON pt.test_id = t.id LEFT JOIN special_offers so ON hp.id = so.package_id
        GROUP BY hp.id, l.name, so.id, so.discount_percent, so.badge, so.end_date ORDER BY hp.id DESC
    """)
    packages = cursor.fetchall(); conn.close()
    
    return render_template('admin.html', orders=orders, all_labs=all_labs, active_labs=active_labs, categories=categories, inventory=inventory, packages=packages, master_tests=master_tests, phlebotomists=phlebotomists, test_parameters=test_parameters)

# ==========================================
# THE PYTHON LIMS AUTO-SEEDER (ONE-CLICK FIX)
# ==========================================
@app.route('/admin/auto-seed-lims')
def auto_seed_lims():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    conn = get_db()
    try:
        cursor = conn.cursor()
        master_params = {
            'Complete Blood Count': [
                ('Hemoglobin', 'g/dL', '13.0 - 17.0'), ('RBC Count', 'mill/cumm', '4.5 - 5.5'), 
                ('Total WBC Count (TLC)', 'cells/cumm', '4000 - 11000'), ('Platelet Count', 'lakhs/cumm', '1.5 - 4.5'),
                ('Neutrophils', '%', '40 - 80'), ('Lymphocytes', '%', '20 - 40'),
                ('Eosinophils', '%', '1 - 6'), ('Monocytes', '%', '2 - 10'), ('PCV / Hematocrit', '%', '40 - 50')
            ],
            'Liver Function': [
                ('Bilirubin (Total)', 'mg/dL', '0.2 - 1.2'), ('Bilirubin (Direct)', 'mg/dL', '0.0 - 0.3'),
                ('SGOT / AST', 'U/L', '5 - 40'), ('SGPT / ALT', 'U/L', '7 - 56'), ('Alkaline Phosphatase (ALP)', 'U/L', '40 - 129'),
                ('Total Protein', 'g/dL', '6.0 - 8.3'), ('Albumin', 'g/dL', '3.5 - 5.2')
            ],
            'Kidney Function': [
                ('Urea', 'mg/dL', '17 - 43'), ('Creatinine', 'mg/dL', '0.6 - 1.2'), ('Uric Acid', 'mg/dL', '3.5 - 7.2'),
                ('Sodium', 'mEq/L', '135 - 145'), ('Potassium', 'mEq/L', '3.5 - 5.1')
            ],
            'Lipid Profile': [
                ('Total Cholesterol', 'mg/dL', '< 200'), ('Triglycerides', 'mg/dL', '< 150'),
                ('HDL Cholesterol', 'mg/dL', '40 - 60'), ('LDL Cholesterol', 'mg/dL', '< 100')
            ],
            'Thyroid Profile': [
                ('Total T3', 'ng/dL', '80 - 200'), ('Total T4', 'ug/dL', '4.5 - 12.0'), ('TSH', 'uIU/mL', '0.4 - 4.0')
            ]
        }
        
        for search_name, params in master_params.items():
            cursor.execute("SELECT id FROM tests WHERE name ILIKE %s LIMIT 1", (f"%{search_name}%",))
            test = cursor.fetchone()
            if test:
                for p_name, unit, ref in params:
                    cursor.execute("INSERT INTO test_parameters (test_id, parameter_name, unit, reference_range) VALUES (%s, %s, %s, %s)", (test[0], p_name, unit, ref))
        conn.commit()
        return "<h2 style='color:green; padding:50px;'>SUCCESS! All LIMS Parameters have been injected and locked to your tests. You can now close this tab and fill Aman's report!</h2>"
    except Exception as e: return f"<h2 style='color:red;'>Error: {str(e)}</h2>"
    finally: conn.close()

# ==========================================
# LIMS REPORT BUILDER & A4 PDF GENERATOR
# ==========================================
class LIMS_PDF(FPDF):
    def header(self):
        self.set_font("helvetica", "B", 24)
        self.set_text_color(13, 148, 136)
        self.cell(0, 10, "CAREDROP DIAGNOSTICS", ln=True, align="C")
        self.set_font("helvetica", "I", 11)
        self.set_text_color(100, 100, 100)
        self.cell(0, 6, "Precision & Care in Every Drop", ln=True, align="C")
        self.ln(5)
        self.line(10, 32, 200, 32)
        self.ln(8)
        
    def footer(self):
        self.set_y(-15)
        self.set_font("helvetica", "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, "This is a computer-generated medical report. Authorized via CareDrop LIMS.", align="C")

@app.route('/admin/fill-report/<int:order_id>')
def admin_fill_report(order_id):
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    conn = get_db()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT o.*, u.patient_uid FROM orders o JOIN users u ON o.user_id = u.id WHERE o.id = %s", (order_id,))
        order = cursor.fetchone()
        
        cursor.execute("""
            SELECT oi.order_id, t.id as test_id, t.name as test_name 
            FROM order_items oi JOIN tests t ON oi.test_id = t.id WHERE oi.item_type = 'test' AND oi.order_id = %s
            UNION
            SELECT oi.order_id, t.id as test_id, t.name as test_name 
            FROM order_items oi JOIN package_tests pt ON oi.test_id = pt.package_id JOIN tests t ON pt.test_id = t.id WHERE oi.item_type = 'package' AND oi.order_id = %s
        """, (order_id, order_id))
        tests = cursor.fetchall()
        
        for t in tests:
            cursor.execute("SELECT id, parameter_name, unit, reference_range FROM test_parameters WHERE test_id = %s", (t['test_id'],))
            t['parameters'] = cursor.fetchall()
    except Exception as e: return str(e)
    finally: conn.close()
    return render_template('lims_report.html', order=order, tests=tests)

@app.route('/admin/save-results/<int:order_id>', methods=['POST'])
def save_results(order_id):
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    conn = get_db()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # 1. Save results to Database
        cursor.execute("DELETE FROM order_results WHERE order_id = %s", (order_id,)) 
        results_data = []
        
        for key, value in request.form.items():
            if key.startswith('param_') and value.strip() != '':
                param_id = key.split('_')[1]
                val = value.strip()
                cursor.execute("INSERT INTO order_results (order_id, parameter_id, result_value) VALUES (%s, %s, %s)", (order_id, param_id, val))
                
                cursor.execute("SELECT tp.parameter_name, tp.unit, tp.reference_range, t.name as test_name FROM test_parameters tp JOIN tests t ON tp.test_id = t.id WHERE tp.id = %s", (param_id,))
                p_info = cursor.fetchone()
                if p_info: results_data.append({'test': p_info['test_name'], 'param': p_info['parameter_name'], 'val': val, 'unit': p_info['unit'], 'ref': p_info['reference_range']})

        # 2. Get Order Info for PDF
        cursor.execute("SELECT o.*, u.patient_uid FROM orders o JOIN users u ON o.user_id = u.id WHERE o.id = %s", (order_id,))
        order = cursor.fetchone()

        # 3. Generate A4 PDF
        pdf = LIMS_PDF()
        pdf.add_page()
        
        # Patient Details Box
        pdf.set_fill_color(248, 250, 252)
        pdf.set_font("helvetica", "B", 10)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(95, 8, f" Patient Name: {order['patient_name']} ({order['age']} {order['gender']})", border=1, fill=True)
        pdf.cell(95, 8, f" Patient UID: {order['patient_uid']}", border=1, ln=True, fill=True)
        pdf.cell(95, 8, f" Order Ref: {order['order_ref']}", border=1, fill=True)
        pdf.cell(95, 8, f" Date: {order['collection_date']}", border=1, ln=True, fill=True)
        pdf.ln(10)
        
        # Results Table Header
        pdf.set_font("helvetica", "B", 11)
        pdf.set_fill_color(13, 148, 136)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(80, 10, 'Test Parameter', 1, 0, 'L', fill=True)
        pdf.cell(30, 10, 'Result', 1, 0, 'C', fill=True)
        pdf.cell(30, 10, 'Unit', 1, 0, 'C', fill=True)
        pdf.cell(50, 10, 'Reference Range', 1, 1, 'C', fill=True)
        
        # Print Results
        current_test = ""
        pdf.set_text_color(15, 23, 42)
        for r in results_data:
            if r['test'] != current_test:
                pdf.set_font("helvetica", "B", 10)
                pdf.set_fill_color(241, 245, 249)
                pdf.cell(190, 8, r['test'].upper(), 1, 1, 'L', fill=True)
                current_test = r['test']
            
            pdf.set_font("helvetica", "", 10)
            pdf.cell(80, 8, f" {r['param']}", 1)
            pdf.set_font("helvetica", "B", 10)
            pdf.cell(30, 8, r['val'], 1, 0, 'C')
            pdf.set_font("helvetica", "", 10)
            pdf.cell(30, 8, r['unit'], 1, 0, 'C')
            pdf.cell(50, 8, r['ref'], 1, 1, 'C')

       # 4. Generate QR Code Image
        qr = qrcode.QRCode(box_size=3, border=1)
        qr.add_data(f"https://caredrop.in/download-report/{order_id}")
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tf:
            img.save(tf, 'PNG')
            tf_path = tf.name
            
        pdf.ln(15)
        pdf.set_font("helvetica", "B", 10)
        pdf.cell(0, 5, 'Scan to Verify & Download Document:', ln=True)
        pdf.image(tf_path, x=10, w=25)
        os.remove(tf_path)

        # 5. Save PDF to Database
        pdf_bytes = pdf.output()
        filename = f"CareDrop_Report_{order['patient_uid']}.pdf"
        cursor.execute("UPDATE orders SET report_file = %s, report_filename = %s, status = 'Completed', report_type = 'System' WHERE id = %s", (psycopg2.Binary(pdf_bytes), filename, order_id))
        conn.commit()
    except Exception as e: conn.rollback(); print(str(e))
    finally: conn.close()
    return redirect(url_for('admin_dashboard'))

# --- OTHER ROUTES ---
@app.route('/admin/add-test', methods=['POST'])
def admin_add_test():
    if session.get('admin_logged_in'):
        name, cat_id, fasting, price, params = request.form.get('test_name'), request.form.get('category_id'), request.form.get('fasting'), request.form.get('price'), request.form.get('parameter_count', 1)
        lab_ids = request.form.getlist('lab_ids')
        conn = get_db()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM tests WHERE name ILIKE %s", (name,))
            existing = cursor.fetchone()
            test_id = existing[0] if existing else cursor.execute("INSERT INTO tests (name, category_id, fasting_requirement, is_active) VALUES (%s, %s, %s, TRUE) RETURNING id", (name, cat_id or None, fasting)) or cursor.fetchone()[0]
            for lid in lab_ids:
                if cursor.execute("SELECT test_id FROM lab_test_pricing WHERE test_id=%s AND lab_id=%s", (test_id, lid)) or cursor.fetchone():
                    cursor.execute("UPDATE lab_test_pricing SET price=%s, parameter_count=%s WHERE test_id=%s AND lab_id=%s", (price, params, test_id, lid))
                else: cursor.execute("INSERT INTO lab_test_pricing (test_id, lab_id, price, parameter_count) VALUES (%s, %s, %s, %s)", (test_id, lid, price, params))
            conn.commit()
        except: conn.rollback()
        finally: conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/add-category', methods=['POST'])
def add_category():
    if session.get('admin_logged_in'): safe_execute("INSERT INTO test_categories (name) VALUES (%s) ON CONFLICT DO NOTHING", (request.form.get('category_name').strip(),))
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/add-lab', methods=['POST'])
def add_lab():
    if session.get('admin_logged_in'): safe_execute("INSERT INTO labs (name, rating, cert_badge, is_active) VALUES (%s, %s, %s, TRUE) ON CONFLICT (name) DO UPDATE SET rating=%s, cert_badge=%s", (request.form.get('lab_name').strip(), request.form.get('rating'), request.form.get('cert_badge').strip(), request.form.get('rating'), request.form.get('cert_badge').strip()))
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/toggle-lab/<int:lab_id>', methods=['POST'])
def toggle_lab(lab_id):
    if session.get('admin_logged_in'): safe_execute("UPDATE labs SET is_active = NOT is_active WHERE id = %s", (lab_id,))
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete-lab/<int:lab_id>', methods=['POST'])
def delete_lab(lab_id):
    if session.get('admin_logged_in'): safe_execute("DELETE FROM lab_test_pricing WHERE lab_id=%s; DELETE FROM labs WHERE id=%s", (lab_id, lab_id))
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete-inventory/<int:test_id>/<int:lab_id>', methods=['POST'])
def delete_inventory(test_id, lab_id):
    if session.get('admin_logged_in'): safe_execute("DELETE FROM lab_test_pricing WHERE test_id=%s AND lab_id=%s", (test_id, lab_id))
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete-master-test/<int:test_id>', methods=['POST'])
def delete_master_test(test_id):
    if session.get('admin_logged_in'): safe_execute("DELETE FROM tests WHERE id=%s", (test_id,))
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/add-health-package', methods=['POST'])
def add_health_package():
    if session.get('admin_logged_in'):
        conn = get_db()
        try:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO health_packages (title, lab_id, price) VALUES (%s, %s, %s) RETURNING id", (request.form.get('title'), request.form.get('lab_id'), request.form.get('price')))
            pkg_id = cursor.fetchone()[0]
            for tid in request.form.getlist('test_ids'): cursor.execute("INSERT INTO package_tests (package_id, test_id) VALUES (%s, %s)", (pkg_id, tid))
            conn.commit()
        except: conn.rollback()
        finally: conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete-health-package/<int:pkg_id>', methods=['POST'])
def delete_health_package(pkg_id):
    if session.get('admin_logged_in'): safe_execute("DELETE FROM health_packages WHERE id=%s", (pkg_id,))
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/add-special-offer', methods=['POST'])
def add_special_offer():
    if session.get('admin_logged_in'): safe_execute("DELETE FROM special_offers WHERE package_id=%s; INSERT INTO special_offers (package_id, discount_percent, badge, end_date) VALUES (%s, %s, %s, %s)", (request.form.get('package_id'), request.form.get('package_id'), request.form.get('discount_percent'), request.form.get('badge'), request.form.get('end_date')))
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete-offer/<int:offer_id>', methods=['POST'])
def delete_offer(offer_id):
    if session.get('admin_logged_in'): safe_execute("DELETE FROM special_offers WHERE id=%s", (offer_id,))
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/add-phlebotomist', methods=['POST'])
def add_phlebotomist():
    if session.get('admin_logged_in'): safe_execute("INSERT INTO phlebotomists (name, phone, vehicle_number) VALUES (%s, %s, %s)", (request.form.get('name'), request.form.get('phone'), request.form.get('vehicle_number')))
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/update-order', methods=['POST'])
def update_order():
    if session.get('admin_logged_in'): safe_execute("UPDATE orders SET status=%s WHERE id=%s", (request.form.get('status'), request.form.get('order_id')))
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/assign-order', methods=['POST'])
def assign_order():
    if session.get('admin_logged_in'): safe_execute("UPDATE orders SET phlebotomist_id=%s, payout_amount=%s WHERE id=%s", (request.form.get('phlebotomist_id') or None, request.form.get('payout_amount', 0), request.form.get('order_id')))
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/add-parameter', methods=['POST'])
def add_parameter():
    if session.get('admin_logged_in'):
        conn = get_db()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM tests WHERE name = %s", (request.form.get('test_name'),))
            test = cursor.fetchone()
            if test: cursor.execute("INSERT INTO test_parameters (test_id, parameter_name, unit, reference_range) VALUES (%s, %s, %s, %s)", (test[0], request.form.get('parameter_name'), request.form.get('unit'), request.form.get('reference_range')))
            conn.commit()
        except: conn.rollback()
        finally: conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete-parameter/<int:param_id>', methods=['POST'])
def delete_parameter(param_id):
    if session.get('admin_logged_in'): safe_execute("DELETE FROM test_parameters WHERE id=%s", (param_id,))
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/walk-in', methods=['POST'])
def admin_walk_in():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    patient_name, phone, age, gender, total, test_id, lab_id = request.form.get('patient_name'), request.form.get('phone'), request.form.get('age'), request.form.get('gender'), request.form.get('total_amount', 0), request.form.get('test_id'), request.form.get('lab_id')
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, patient_uid FROM users WHERE phone = %s", (phone,))
        user = cursor.fetchone()
        if user:
            user_id = user[0]
            if not user[1]: cursor.execute("UPDATE users SET patient_uid = %s WHERE id = %s", (f"CD-PAT-{1000 + user_id}", user_id))
        else:
            cursor.execute("INSERT INTO users (name, phone, email) VALUES (%s, %s, %s) RETURNING id", (patient_name, phone, f"walkin_{phone}@caredrop.local"))
            user_id = cursor.fetchone()[0]
            cursor.execute("UPDATE users SET patient_uid = %s WHERE id = %s", (f"CD-PAT-{1000 + user_id}", user_id))
            
        cursor.execute("INSERT INTO orders (user_id, patient_name, age, gender, address, collection_date, time_slot, total_amount, status) VALUES (%s, %s, %s, %s, 'Walk-In Clinic', %s, 'Immediate', %s, 'Pending') RETURNING id", (user_id, patient_name, age, gender, datetime.today().strftime('%Y-%m-%d'), total))
        order_id = cursor.fetchone()[0]
        cursor.execute("UPDATE orders SET order_ref = %s WHERE id = %s", (f"ORD-{datetime.today().strftime('%y%m')}-{order_id:04d}", order_id))
        cursor.execute("INSERT INTO order_items (order_id, test_id, lab_id, price, item_type) VALUES (%s, %s, %s, %s, 'test')", (order_id, test_id, lab_id, total))
        conn.commit()
    except Exception as e: conn.rollback(); print(e)
    finally: conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/bulk-upload', methods=['POST'])
def bulk_upload():
    if session.get('admin_logged_in'):
        file = request.files.get('csv_file')
        if file and file.filename != '':
            conn = get_db()
            try:
                stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
                csv_input = csv.reader(stream); next(csv_input, None)
                cursor = conn.cursor()
                for row in csv_input:
                    if len(row) < 4: continue
                    name, cat_name, fasting, symptoms = [str(r).strip() for r in row[:4]]
                    if not name: continue
                    cursor.execute("INSERT INTO test_categories (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (cat_name,))
                    cursor.execute("SELECT id FROM test_categories WHERE name = %s", (cat_name,))
                    cursor.execute("INSERT INTO tests (name, category_id, fasting_requirement, is_active, symptoms) VALUES (%s, %s, %s, TRUE, %s) ON CONFLICT (name) DO NOTHING", (name, cursor.fetchone()[0], fasting, symptoms))
                conn.commit()
            except: conn.rollback()
            finally: conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/upload-report', methods=['POST'])
def upload_report():
    if session.get('admin_logged_in'):
        file = request.files.get('report_file')
        if file and file.filename: safe_execute("UPDATE orders SET report_file=%s, report_filename=%s, status='Completed', report_type='Manual' WHERE id=%s", (psycopg2.Binary(file.read()), file.filename, request.form.get('order_id')))
    return redirect(url_for('admin_dashboard'))

@app.route('/api/place-order', methods=['POST'])
def place_order():
    name, phone, email, patient_name, age, gender, address, date, cart_json = request.form.get('name'), request.form.get('phone'), request.form.get('email'), request.form.get('patient_name'), request.form.get('age'), request.form.get('gender'), request.form.get('address'), request.form.get('date'), request.form.get('cart', '[]')
    if not session.get(f'verified_{email}'): return jsonify({"success": False, "message": "Verify email."})
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
        user_id = user[0] if user else (cursor.execute("INSERT INTO users (name, phone, email) VALUES (%s, %s, %s) RETURNING id", (name, phone, email)) or cursor.fetchone()[0])
        
        cursor.execute("INSERT INTO orders (user_id, patient_name, age, gender, address, collection_date, time_slot, total_amount, status) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'Pending') RETURNING id", (user_id, patient_name if patient_name else name, age, gender, address, date, request.form.get('time_slot', 'Morning'), request.form.get('total', 0)))
        order_id = cursor.fetchone()[0]
        cursor.execute("UPDATE orders SET order_ref = %s WHERE id = %s", (f"ORD-{datetime.today().strftime('%y%m')}-{order_id:04d}", order_id))
        
        for item in json.loads(cart_json):
            clean_id, is_pkg = str(item['id']).replace('PKG_',''), 'package' if 'PKG_' in str(item['id']) else 'test'
            cursor.execute("INSERT INTO order_items (order_id, test_id, lab_id, price, item_type) VALUES (%s, %s, %s, %s, %s)", (order_id, clean_id, item['selectedLabId'], item['currentPrice'], is_pkg))
        conn.commit()
        return jsonify({"success": True, "order_id": order_id})
    except Exception as e: conn.rollback(); return jsonify({"success": False, "message": str(e)})
    finally: conn.close()

if __name__ == '__main__': app.run(debug=True, port=5000)
