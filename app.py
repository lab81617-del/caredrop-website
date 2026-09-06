import os, threading, json, io, csv, random, traceback, urllib.request, tempfile
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

def get_db(): return psycopg2.connect(os.environ.get("DATABASE_URL"))

def safe_execute(query, params=None):
    conn = get_db()
    try: 
        cursor = conn.cursor(); cursor.execute(query, params); conn.commit()
    except Exception as e: conn.rollback(); print(e)
    finally: conn.close()

# Database upgrades to ensure timestamps and referral fields exist
@app.before_request
def ensure_db_schema():
    if not getattr(app, '_schema_checked', False):
        safe_execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS referred_by VARCHAR(255) DEFAULT 'Self'")
        safe_execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        app._schema_checked = True

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
    grouped_tests = {}; pricing = []; packages = []; param_dict = {}
    cursor.execute("SELECT DISTINCT t.id, t.name, t.fasting_requirement, t.symptoms, c.name as category FROM tests t LEFT JOIN test_categories c ON t.category_id = c.id JOIN lab_test_pricing ltp ON t.id = ltp.test_id JOIN labs l ON ltp.lab_id = l.id WHERE t.is_active = TRUE AND l.is_active = TRUE ORDER BY c.name, t.name")
    for t in cursor.fetchall(): grouped_tests.setdefault(t['category'] or 'Uncategorized', []).append(t)
    cursor.execute("SELECT ltp.test_id, CAST(ltp.price AS INTEGER) as price, l.id as lab_id, l.name as lab_name, CAST(l.rating AS FLOAT) as rating FROM lab_test_pricing ltp JOIN labs l ON ltp.lab_id = l.id WHERE l.is_active = TRUE")
    pricing = cursor.fetchall()
    cursor.execute("SELECT test_id, parameter_name FROM test_parameters")
    for p in cursor.fetchall(): param_dict.setdefault(p['test_id'], []).append(p['parameter_name'])
    conn.close()
    return render_template('tests.html', grouped_tests=grouped_tests, pricing=json.dumps(pricing, default=str), packages="[]", raw_packages=[], param_dict=json.dumps(param_dict))

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

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST' and request.form.get('password') == ADMIN_PASSWORD:
        session['admin_logged_in'] = True; return redirect(url_for('admin_dashboard'))
    return f'<html><body style="background:#F1F5F9; display: flex; justify-content:center; align-items:center; height: 100vh;"><form method="POST"><input type="password" name="password" required><button type="submit">Login</button></form></body></html>'

@app.route('/admin')
def admin_dashboard():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    conn = get_db(); cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT o.id, o.order_ref, o.patient_name, o.age, o.gender, o.collection_date, o.time_slot, CAST(o.total_amount AS INTEGER) as total_amount, o.status, o.referred_by, u.patient_uid, CASE WHEN o.report_file IS NOT NULL THEN TRUE ELSE FALSE END as has_report FROM orders o JOIN users u ON o.user_id = u.id ORDER BY o.id DESC")
    orders = cursor.fetchall()
    cursor.execute("SELECT oi.order_id, t.name as test_name FROM order_items oi JOIN tests t ON oi.test_id = t.id WHERE oi.item_type = 'test'")
    items_map = {}
    for row in cursor.fetchall(): items_map.setdefault(row['order_id'], []).append(row)
    for order in orders: order['test_list'] = items_map.get(order['id'], [])
    cursor.execute("SELECT id, name, is_active, CAST(rating AS FLOAT) as rating FROM labs ORDER BY name")
    active_labs = [l for l in cursor.fetchall() if l['is_active']]
    cursor.execute("SELECT t.id as test_id, t.name as test_name, CAST(ltp.price AS INTEGER) as price FROM lab_test_pricing ltp JOIN tests t ON ltp.test_id = t.id")
    inventory = cursor.fetchall()
    conn.close()
    return render_template('admin.html', orders=orders, active_labs=active_labs, inventory=inventory)

@app.route('/admin/walk-in', methods=['POST'])
def admin_walk_in():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    p_name, phone, age, gender = request.form.get('patient_name'), request.form.get('phone'), request.form.get('age'), request.form.get('gender')
    total, test_id, lab_id, ref_by = request.form.get('total_amount', 0), request.form.get('test_id'), request.form.get('lab_id'), request.form.get('referred_by', 'Self').strip()
    if not ref_by: ref_by = "Self"
    
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
            
        cursor.execute("INSERT INTO orders (user_id, patient_name, age, gender, collection_date, time_slot, total_amount, status, referred_by) VALUES (%s, %s, %s, %s, %s, 'Immediate', %s, 'Pending', %s) RETURNING id", (user_id, p_name, age, gender, datetime.today().strftime('%Y-%m-%d'), total, ref_by))
        order_id = cursor.fetchone()[0]
        cursor.execute("UPDATE orders SET order_ref = %s WHERE id = %s", (f"ORD-{datetime.today().strftime('%y%m')}-{order_id:04d}", order_id))
        cursor.execute("INSERT INTO order_items (order_id, test_id, lab_id, price, item_type) VALUES (%s, %s, %s, %s, 'test')", (order_id, test_id, lab_id, total))
        conn.commit()
    except Exception as e: conn.rollback(); print(e)
    finally: conn.close()
    return redirect(url_for('admin_dashboard'))

# ==========================================
# CUSTOM CAREDROP PDF ENGINE
# ==========================================
class CareDropPDF(FPDF):
    def __init__(self, qr_path, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.qr_path = qr_path

    def header(self):
        # Unique Modern Header
        self.set_font("helvetica", "B", 24)
        self.set_text_color(13, 148, 136) # Teal Branding
        self.cell(120, 10, "CAREDROP", ln=False)
        
        # QR Code ALWAYS at Top Right
        if self.qr_path:
            self.image(self.qr_path, x=175, y=8, w=22)
            
        self.ln(8)
        self.set_font("helvetica", "B", 10)
        self.set_text_color(100, 100, 100)
        self.cell(120, 5, "ADVANCED DIAGNOSTICS LABORATORY", ln=True)
        self.set_font("helvetica", "", 9)
        self.cell(120, 5, "Accurate. Transparent. Fast.", ln=True)
        self.ln(4)
        self.set_draw_color(13, 148, 136)
        self.set_line_width(0.5)
        self.line(10, 32, 200, 32)
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
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    conn = get_db()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT o.*, u.patient_uid FROM orders o JOIN users u ON o.user_id = u.id WHERE o.id = %s", (order_id,))
        order = cursor.fetchone()
        cursor.execute("SELECT oi.order_id, t.id as test_id, t.name as test_name FROM order_items oi JOIN tests t ON oi.test_id = t.id WHERE oi.item_type = 'test' AND oi.order_id = %s", (order_id,))
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
        cursor.execute("DELETE FROM order_results WHERE order_id = %s", (order_id,)) 
        
        results_data = []
        for key, value in request.form.items():
            if key.startswith('param_') and value.strip() != '':
                param_id = key.split('_')[1]
                val = value.strip()
                if request.form.get(f'print_{param_id}') == 'on':
                    cursor.execute("INSERT INTO order_results (order_id, parameter_id, result_value) VALUES (%s, %s, %s)", (order_id, param_id, val))
                    cursor.execute("SELECT tp.parameter_name, tp.unit, tp.reference_range, t.name as test_name, c.name as cat_name FROM test_parameters tp JOIN tests t ON tp.test_id = t.id LEFT JOIN test_categories c ON t.category_id = c.id WHERE tp.id = %s", (param_id,))
                    p_info = cursor.fetchone()
                    if p_info: results_data.append({'cat': p_info['cat_name'] or 'PATHOLOGY', 'test': p_info['test_name'], 'param': p_info['parameter_name'], 'val': val, 'unit': p_info['unit'], 'ref': p_info['reference_range']})

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

        # Generate Real Barcode Image
        tf_bc = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
        tf_bc.close()
        bc_img = barcode.get('code128', order['patient_uid'], writer=ImageWriter())
        bc_path = bc_img.save(tf_bc.name.replace('.png', ''))

        pdf = CareDropPDF(qr_path=tf_qr.name)
        pdf.add_page()
        
        # Unique Patient Demographics Grid
        pdf.set_y(38)
        pdf.set_font("helvetica", "", 9)
        pdf.set_text_color(100, 100, 100)
        
        # Row 1
        pdf.cell(32, 6, "Patient Name", 0, 0)
        pdf.set_text_color(15, 23, 42); pdf.set_font("helvetica", "B", 10)
        pdf.cell(68, 6, f": {order['patient_name']}", 0, 0)
        
        pdf.set_font("helvetica", "", 9); pdf.set_text_color(100, 100, 100)
        pdf.cell(32, 6, "Registered On", 0, 0)
        pdf.set_text_color(15, 23, 42); pdf.set_font("helvetica", "B", 9)
        created_time = order.get('created_at', datetime.today()).strftime('%Y-%m-%d %I:%M %p')
        pdf.cell(58, 6, f": {created_time}", 0, 1)

        # Row 2
        pdf.set_font("helvetica", "", 9); pdf.set_text_color(100, 100, 100)
        pdf.cell(32, 6, "Age / Gender", 0, 0)
        pdf.set_text_color(15, 23, 42); pdf.set_font("helvetica", "B", 9)
        pdf.cell(68, 6, f": {order['age']} Yrs / {order['gender']}", 0, 0)
        
        pdf.set_font("helvetica", "", 9); pdf.set_text_color(100, 100, 100)
        pdf.cell(32, 6, "Collected On", 0, 0)
        pdf.set_text_color(15, 23, 42); pdf.set_font("helvetica", "B", 9)
        pdf.cell(58, 6, f": {order['collection_date']} {order.get('time_slot', 'Immediate')}", 0, 1)

        # Row 3
        pdf.set_font("helvetica", "", 9); pdf.set_text_color(100, 100, 100)
        pdf.cell(32, 6, "Referred By", 0, 0)
        pdf.set_text_color(15, 23, 42); pdf.set_font("helvetica", "B", 9)
        pdf.cell(68, 6, f": {order.get('referred_by', 'Self')}", 0, 0)
        
        pdf.set_font("helvetica", "", 9); pdf.set_text_color(100, 100, 100)
        pdf.cell(32, 6, "Reported On", 0, 0)
        pdf.set_text_color(15, 23, 42); pdf.set_font("helvetica", "B", 9)
        pdf.cell(58, 6, f": {datetime.today().strftime('%Y-%m-%d %I:%M %p')}", 0, 1)

        # Row 4 (Barcode)
        pdf.set_font("helvetica", "", 9); pdf.set_text_color(100, 100, 100)
        pdf.cell(32, 8, "UID Barcode", 0, 0)
        pdf.image(bc_path, x=42, y=pdf.get_y()+1, h=6)
        pdf.ln(12)
        
        pdf.set_draw_color(220, 220, 220)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(4)
        
        # Clean Sleek Table Header
        pdf.set_font("helvetica", "B", 9)
        pdf.set_fill_color(248, 250, 252)
        pdf.set_text_color(100, 116, 139)
        pdf.cell(85, 8, ' INVESTIGATION', 0, 0, 'L', True)
        pdf.cell(25, 8, 'RESULT', 0, 0, 'C', True)
        pdf.cell(30, 8, 'UNIT', 0, 0, 'C', True)
        pdf.cell(50, 8, 'BIO. REF. INTERVAL', 0, 1, 'C', True)
        
       # Results Formatting
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
    except Exception as e: conn.rollback(); print(str(e))
    finally: conn.close()
    return redirect(url_for('admin_dashboard'))

if __name__ == '__main__': app.run(debug=True, port=5000)
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
    except Exception as e: conn.rollback(); print(str(e))
    finally: conn.close()
    return redirect(url_for('admin_dashboard'))

if __name__ == '__main__': app.run(debug=True, port=5000)
