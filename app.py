import os, threading, json, io, csv, random, tempfile
from datetime import datetime
from flask import Flask, render_template, jsonify, request, session, redirect, url_for, send_file
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from fpdf import FPDF
import qrcode, barcode
from barcode.writer import ImageWriter
from pdf_engine import generate_medical_report, generate_invoice_report

load_dotenv()
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "caredrop-super-secret-key-2026")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "IHC2026!")

def get_db(): return psycopg2.connect(os.environ.get("DATABASE_URL"))

def safe_execute(query, params=None):
    conn = get_db()
    try: cursor = conn.cursor(); cursor.execute(query, params); conn.commit()
    except Exception as e: conn.rollback(); print(e)
    finally: conn.close()

@app.before_request
def ensure_db_schema():
    if not getattr(app, '_schema_checked', False):
        safe_execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS referred_by VARCHAR(255) DEFAULT 'Self'")
        safe_execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        safe_execute("ALTER TABLE phlebotomists ADD COLUMN IF NOT EXISTS pin VARCHAR(10) DEFAULT '1234'")
        app._schema_checked = True

# --- PUBLIC ROUTES ---
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
    grouped_tests = {}; pricing = []; param_dict = {}
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

@app.route('/api/place-order', methods=['POST'])
def place_order():
    name, phone, email, patient_name, age, gender, address, date, cart_json = request.form.get('name'), request.form.get('phone'), request.form.get('email'), request.form.get('patient_name'), request.form.get('age'), request.form.get('gender'), request.form.get('address'), request.form.get('date'), request.form.get('cart', '[]')
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

# --- RIDER PORTAL (FLEET APP) ---
@app.route('/rider/login', methods=['GET', 'POST'])
def rider_login():
    if request.method == 'POST':
        phone, pin = request.form.get('phone'), request.form.get('pin')
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT id, name FROM phlebotomists WHERE phone = %s AND pin = %s", (phone, pin))
        rider = cursor.fetchone()
        conn.close()
        if rider:
            session['rider_id'] = rider['id']
            return redirect(url_for('rider_dashboard'))
        return "Invalid Credentials. Ask Admin for your PIN."
    return '<html><body style="background:#F1F5F9; display: flex; justify-content:center; align-items:center; height: 100vh;"><div style="background:white; padding:40px; border-radius:12px; box-shadow:0 4px 6px rgba(0,0,0,0.05); text-align:center; font-family:sans-serif;"><h2 style="margin:0 0 20px 0; color:#0F172A;">CareDrop Rider Login</h2><form method="POST"><input type="tel" name="phone" placeholder="Registered Phone" required style="width:100%; padding:12px; margin-bottom:15px; border-radius:6px; border:1px solid #CBD5E1;"><input type="password" name="pin" placeholder="4-Digit PIN" required style="width:100%; padding:12px; margin-bottom:15px; border-radius:6px; border:1px solid #CBD5E1;"><button type="submit" style="width:100%; background:#0D9488; color:white; padding:12px; border:none; border-radius:6px; font-weight:bold; cursor:pointer;">Login to Fleet</button></form></div></body></html>'

@app.route('/rider')
def rider_dashboard():
    rider_id = session.get('rider_id')
    if not rider_id: return redirect(url_for('rider_login'))
    
    today = datetime.today().strftime('%Y-%m-%d')
    conn = get_db(); cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    cursor.execute("SELECT o.id, o.patient_name, o.age, o.gender, o.address, o.time_slot, o.status, CAST(o.payout_amount AS INTEGER) as payout_amount, u.phone FROM orders o JOIN users u ON o.user_id = u.id WHERE o.phlebotomist_id = %s AND o.collection_date = %s ORDER BY o.status DESC", (rider_id, today))
    orders = cursor.fetchall()
    
    pending_count = sum(1 for o in orders if o['status'] == 'Pending')
    total_earnings = sum(o['payout_amount'] for o in orders if o['status'] != 'Pending' and o['payout_amount'])
    conn.close()
    
    return render_template('rider.html', orders=orders, pending_count=pending_count, total_earnings=total_earnings)

@app.route('/rider/update-status', methods=['POST'])
def rider_update_status():
    if session.get('rider_id'): safe_execute("UPDATE orders SET status = 'Sample Collected' WHERE id = %s", (request.form.get('order_id'),))
    return redirect(url_for('rider_dashboard'))

@app.route('/rider/logout')
def rider_logout():
    session.pop('rider_id', None); return redirect(url_for('rider_login'))

# --- ADMIN ROUTES & LIMS ---
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST' and request.form.get('password') == ADMIN_PASSWORD:
        session['admin_logged_in'] = True; return redirect(url_for('admin_dashboard'))
    return f'<html><body style="background:#F1F5F9; display: flex; justify-content:center; align-items:center; height: 100vh;"><form method="POST"><input type="password" name="password" required><button type="submit">Login</button></form></body></html>'

@app.route('/admin')
def admin_dashboard():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    conn = get_db(); cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT o.id, o.order_ref, o.patient_name, o.age, o.gender, o.address, o.collection_date, o.time_slot, CAST(o.total_amount AS INTEGER) as total_amount, o.status, o.referred_by, u.phone, u.patient_uid, CASE WHEN o.report_file IS NOT NULL THEN TRUE ELSE FALSE END as has_report, o.phlebotomist_id, CAST(o.payout_amount AS INTEGER) as payout_amount FROM orders o JOIN users u ON o.user_id = u.id ORDER BY o.id DESC")
    orders = cursor.fetchall()
    cursor.execute("SELECT oi.order_id, t.name as test_name FROM order_items oi JOIN tests t ON oi.test_id = t.id WHERE oi.item_type = 'test'")
    items_map = {}
    for row in cursor.fetchall(): items_map.setdefault(row['order_id'], []).append(row)
    for order in orders: order['test_list'] = items_map.get(order['id'], [])
    cursor.execute("SELECT id, name, is_active, CAST(rating AS FLOAT) as rating FROM labs ORDER BY name")
    active_labs = [l for l in cursor.fetchall() if l['is_active']]
    cursor.execute("SELECT t.id as test_id, t.name as test_name, CAST(ltp.price AS INTEGER) as price FROM lab_test_pricing ltp JOIN tests t ON ltp.test_id = t.id")
    inventory = cursor.fetchall()
    cursor.execute("SELECT * FROM phlebotomists ORDER BY id DESC")
    phlebotomists = cursor.fetchall()
    conn.close()
    return render_template('admin.html', orders=orders, active_labs=active_labs, inventory=inventory, phlebotomists=phlebotomists)

@app.route('/admin/walk-in', methods=['POST'])
def admin_walk_in():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    p_name, phone, age, gender, total, test_id, lab_id, ref_by = request.form.get('patient_name'), request.form.get('phone'), request.form.get('age'), request.form.get('gender'), request.form.get('total_amount', 0), request.form.get('test_id'), request.form.get('lab_id'), request.form.get('referred_by', 'Self').strip()
    if not ref_by: ref_by = "Self"
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, patient_uid FROM users WHERE phone = %s", (phone,))
        user = cursor.fetchone()
        user_id = user[0] if user else (cursor.execute("INSERT INTO users (name, phone, email) VALUES (%s, %s, %s) RETURNING id", (p_name, phone, f"walkin_{phone}@caredrop.local")) or cursor.fetchone()[0])
        cursor.execute("UPDATE users SET patient_uid = %s WHERE id = %s AND patient_uid IS NULL", (f"CD-PAT-{1000 + user_id}", user_id))
        cursor.execute("INSERT INTO orders (user_id, patient_name, age, gender, collection_date, time_slot, total_amount, status, referred_by) VALUES (%s, %s, %s, %s, %s, 'Immediate', %s, 'Pending', %s) RETURNING id", (user_id, p_name, age, gender, datetime.today().strftime('%Y-%m-%d'), total, ref_by))
        order_id = cursor.fetchone()[0]
        cursor.execute("UPDATE orders SET order_ref = %s WHERE id = %s", (f"ORD-{datetime.today().strftime('%y%m')}-{order_id:04d}", order_id))
        cursor.execute("INSERT INTO order_items (order_id, test_id, lab_id, price, item_type) VALUES (%s, %s, %s, %s, 'test')", (order_id, test_id, lab_id, total))
        conn.commit()
    except Exception as e: conn.rollback(); print(e)
    finally: conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/assign-order', methods=['POST'])
def assign_order():
    if session.get('admin_logged_in'): safe_execute("UPDATE orders SET phlebotomist_id=%s, payout_amount=%s WHERE id=%s", (request.form.get('phlebotomist_id') or None, request.form.get('payout_amount', 150), request.form.get('order_id')))
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/add-phlebotomist', methods=['POST'])
def add_phlebotomist():
    if session.get('admin_logged_in'): safe_execute("INSERT INTO phlebotomists (name, phone, vehicle_number, pin) VALUES (%s, %s, %s, %s)", (request.form.get('name'), request.form.get('phone'), request.form.get('vehicle_number'), request.form.get('pin', '1234')))
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/bulk-upload', methods=['POST'])
def bulk_upload():
    if session.get('admin_logged_in') and request.files.get('csv_file'):
        conn = get_db()
        try:
            stream = io.StringIO(request.files['csv_file'].stream.read().decode("UTF8"), newline=None)
            csv_input = csv.reader(stream); next(csv_input, None)
            cursor = conn.cursor()
            for row in csv_input:
                if len(row) < 4 or not row[0].strip(): continue
                name, cat_name, fasting, symptoms = [str(r).strip() for r in row[:4]]
                cursor.execute("INSERT INTO test_categories (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (cat_name,))
                cursor.execute("SELECT id FROM test_categories WHERE name = %s", (cat_name,))
                cursor.execute("INSERT INTO tests (name, category_id, fasting_requirement, is_active, symptoms) VALUES (%s, %s, %s, TRUE, %s) ON CONFLICT (name) DO NOTHING", (name, cursor.fetchone()[0], fasting, symptoms))
            conn.commit()
        except: conn.rollback()
        finally: conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/fill-report/<int:order_id>')
def admin_fill_report(order_id):
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
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
def save_results(order_id):
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
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
                    cursor.execute("SELECT tp.parameter_name, tp.unit, tp.reference_range, t.name as test_name, c.name as cat_name FROM test_parameters tp JOIN tests t ON tp.test_id = t.id LEFT JOIN test_categories c ON t.category_id = c.id WHERE tp.id = %s", (param_id,))
                    p_info = cursor.fetchone()
                    if p_info: results_data.append({'cat': p_info['cat_name'] or 'PATHOLOGY', 'test': p_info['test_name'], 'param': p_info['parameter_name'], 'val': val, 'unit': p_info['unit'], 'ref': p_info['reference_range']})
        
        cursor.execute("SELECT o.*, u.patient_uid FROM orders o JOIN users u ON o.user_id = u.id WHERE o.id = %s", (order_id,))
        order = cursor.fetchone()
        pdf_bytes = generate_medical_report(order_id, order, results_data)
        filename = f"CareDrop_Report_{order['patient_uid']}.pdf"
        cursor.execute("UPDATE orders SET report_file = %s, report_filename = %s, status = 'Completed', report_type = 'System' WHERE id = %s", (psycopg2.Binary(pdf_bytes), filename, order_id))
        conn.commit()
    except Exception as e: conn.rollback(); print(str(e))
    finally: conn.close()
    return redirect(url_for('admin_dashboard'))

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

@app.route('/admin/auto-seed-lims')
def auto_seed_lims():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
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
                ('Bilirubin (Total)', 'mg/dL', '0.2 - 1.2'), ('Bilirubin (Direct)', 'mg/dL', '0.0 - 0.3'), ('Bilirubin (Indirect)', 'mg/dL', '0.2 - 0.9'),
                ('SGOT / AST', 'U/L', '5 - 40'), ('SGPT / ALT', 'U/L', '7 - 56'), ('Alkaline Phosphatase (ALP)', 'U/L', '40 - 129'),
                ('Gamma Glutamyl Transferase (GGT)', 'U/L', 'Upto 60'), ('Total Protein', 'g/dL', '6.0 - 8.3'), 
                ('Albumin', 'g/dL', '3.5 - 5.2'), ('Globulin', 'g/dL', '2.5 - 3.5'), ('A/G Ratio', 'Ratio', '1.0 - 2.1')
            ],
            'Kidney Function': [
                ('Blood Urea', 'mg/dL', '14 - 40'), ('Blood Urea Nitrogen (BUN)', 'mg/dl', '5 - 25'), ('Serum Creatinine', 'mg/dl', '0.5 - 1.1'), 
                ('Bun/Creatinine Ratio', '', '6 - 23'), ('Serum Uric Acid', 'mg/dL', '3.4 - 7.0'), ('Calcium', 'mg/dl', '8.6 - 10.2'), 
                ('Sodium', 'mmol/L', '135 - 155'), ('Potassium', 'mmol/L', '3.5 - 5.0'), ('Chloride', 'mmol/L', '95 - 108')
            ],
            'Lipid Profile': [
                ('Total Cholesterol', 'mg/dl', '< 200'), ('Triglycerides', 'mg/dl', '< 150'), ('Cholesterol-HDL', 'mg/dl', '40 - 60'), 
                ('Cholesterol-LDL (Direct)', 'mg/dl', '< 100'), ('Cholesterol-VLDL', 'mg/dl', '7 - 40'), ('Total Cholesterol/HDL Ratio', 'Ratio', '< 6'),
                ('LDL/HDL Ratio', 'Ratio', '0.0 - 3.5'), ('Non-HDL Cholesterol', 'mg/dl', '0 - 160')
            ],
            'Thyroid Profile': [('Total T3', 'ng/dL', '80 - 200'), ('Total T4', 'ug/dL', '4.5 - 12.0'), ('TSH', 'uIU/mL', '0.4 - 4.0')],
            'HbA1c': [('Glycosylated Hemoglobin (HbA1C)', '%', '< 5.6'), ('Estimated Average Glucose', 'mg/dl', '90 - 120')],
            'Urine Routine': [
                ('Color', '', 'Pale Yellow'), ('Appearance', '', 'Clear'), ('Specific Gravity', '', '1.010 - 1.025'),
                ('pH', '', '5.0 - 8.0'), ('Protein / Albumin', '', 'Absent'), ('Glucose (Sugar)', '', 'Absent'),
                ('Ketones', '', 'Absent'), ('Blood', '', 'Absent'), ('Bilirubin', '', 'Absent'), ('Urobilinogen', '', 'Normal'),
                ('Pus Cells (Leukocytes)', '/HPF', '0 - 5'), ('Red Blood Cells (RBC)', '/HPF', '0 - 2'), ('Epithelial Cells', '/HPF', 'Few')
            ]
        }
        for search_name, params in master_params.items():
            cursor.execute("SELECT id FROM tests WHERE name ILIKE %s LIMIT 1", (f"%{search_name}%",))
            test = cursor.fetchone()
            if test:
                cursor.execute("DELETE FROM test_parameters WHERE test_id = %s", (test[0],))
                for p_name, unit, ref in params: cursor.execute("INSERT INTO test_parameters (test_id, parameter_name, unit, reference_range) VALUES (%s, %s, %s, %s)", (test[0], p_name, unit, ref))
        conn.commit()
        return "<h2 style='color:green; padding:50px;'>SUCCESS! Over 100 parameters securely locked to your tests. Close this tab.</h2>"
    except Exception as e: return f"<h2 style='color:red;'>Error: {str(e)}</h2>"
    finally: conn.close()

if __name__ == '__main__': app.run(debug=True, port=5000)
