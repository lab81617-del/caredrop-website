import os, threading, json, io, csv, random, traceback, urllib.request, tempfile
from datetime import datetime
from flask import Flask, render_template, jsonify, request, session, redirect, url_for, send_file
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

from pdf_engine import generate_medical_report, generate_invoice_report

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
        safe_execute("ALTER TABLE phlebotomists ADD COLUMN IF NOT EXISTS pin VARCHAR(10) DEFAULT '1234'")
        safe_execute("ALTER TABLE test_parameters ADD COLUMN IF NOT EXISTS methodology VARCHAR(255)")
        safe_execute("ALTER TABLE test_parameters ADD COLUMN IF NOT EXISTS interpretation TEXT")
        safe_execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS tpa_name VARCHAR(255)")
        safe_execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS advance_amount NUMERIC DEFAULT 0")
        safe_execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS balance_amount NUMERIC DEFAULT 0")
        safe_execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS family_member VARCHAR(255)")
        app._schema_checked = True

# --- PUBLIC ROUTES ---
@app.route('/')
def home():
    return render_template('index.html')

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

# --- ADMIN ROUTES ---
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST' and request.form.get('password') == ADMIN_PASSWORD:
        session['admin_logged_in'] = True; return redirect(url_for('admin_dashboard'))
    return '<html><body style="background:#F1F5F9; display: flex; justify-content:center; align-items:center; height: 100vh;"><form method="POST"><input type="password" name="password" required><button type="submit">Login</button></form></body></html>'

@app.route('/admin')
def admin_dashboard():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    conn = get_db(); cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    cursor.execute("SELECT o.*, u.patient_uid, CASE WHEN o.report_file IS NOT NULL THEN TRUE ELSE FALSE END as has_report FROM orders o JOIN users u ON o.user_id = u.id ORDER BY o.id DESC")
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
    
    p_name, phone, age, gender = request.form.get('patient_name'), request.form.get('phone'), request.form.get('age'), request.form.get('gender')
    total, test_id, lab_id = request.form.get('total_amount', 0), request.form.get('test_id'), request.form.get('lab_id')
    ref_by = request.form.get('referred_by', 'Self').strip() or "Self"
    
    tpa_name = request.form.get('tpa_name', '').strip()
    advance = request.form.get('advance_amount', 0)
    balance = request.form.get('balance_amount', 0)
    
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

# --- MULTI-COLUMN SEEDER (WITH METHODOLOGIES) ---
@app.route('/admin/auto-seed-lims')
def auto_seed_lims():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    conn = get_db()
    try:
        cursor = conn.cursor()
        master_params = {
            'Complete Blood Count': [
                ('Hemoglobin (HB)', 'g/dl', '12.0 - 16.0', 'Photometric/Non Cyanmethemoglobin'), 
                ('Total Leucocytes Count (WBC)', 'Cells/Cumm', '4000 - 10500', 'Optical Flow cytometry/Manual'), 
                ('Neutrophils', '%', '40 - 80', 'Impedance/microscopy'), 
                ('Lymphocytes', '%', '20 - 40', 'Flowcytometry'),
                ('Eosinophils', '%', '01 - 06', 'Impedance/microscopy'), 
                ('Monocytes', '%', '02 - 10', 'Impedance/microscopy'), 
                ('Basophils', '%', '00 - 01', 'Impedance/microscopy'),
                ('Absolute Neutrophil Count', 'Cells/uL', '2000 - 8000', 'Automated Calculated'), 
                ('Absolute Lymphocyte Count', '/uL', '1000 - 3000', 'Flowcytometry'),
                ('Mean Cell Haemoglobin (MCH)', 'Pg', '27 - 32', 'Automated Calculated'), 
                ('MCHC', 'g/dl', '31.5 - 34.5', 'Automated Calculated'),
                ('Erythrocyte count (RBC COUNT)', 'million/cmm', '3.8 - 4.8', 'Impedance'), 
                ('Packed Cell Volume (Hematocrit)', '%', '36 - 46', 'Cell Counter'),
                ('Mean Cell Volume (MCV)', 'fL', '83 - 101', 'Automated Calculated'), 
                ('Red Cell Distribution Width (RDW)-SD', 'fL', '35 - 56', 'Automated Calculated'),
                ('Platelet Count', 'Lakh/cumm', '1.50 - 4.50', 'Impedance/microscopy'),
                ('Plateletcrit (PCT)', '%', '0.2 - 0.5', 'Automated Optical'), 
                ('Platelet large cell ratio (P-LCR)', '%', '11.9 - 66.9', 'Automated Calculated'),
                ('Erythrocytes Sedimentation Rate (ESR)', 'mm/1st hr', '0 - 20', 'Westergren')
            ],
            'Thyroid Profile': [
                ('Total T3', 'ng/dL', '80 - 200', 'ECLIA'), 
                ('Total T4', 'ug/dL', '4.5 - 12.0', 'ECLIA'), 
                ('TSH (3rd Gen, Ultrasensitive)', 'uIU/mL', '0.13 - 6.33', 'ECLIA')
            ]
        }
        
        for search_name, params in master_params.items():
            cursor.execute("SELECT id FROM tests WHERE name ILIKE %s LIMIT 1", (f"%{search_name}%",))
            test = cursor.fetchone()
            if test:
                cursor.execute("DELETE FROM test_parameters WHERE test_id = %s", (test[0],))
                for p_name, unit, ref, method in params:
                    cursor.execute("INSERT INTO test_parameters (test_id, parameter_name, unit, reference_range, methodology) VALUES (%s, %s, %s, %s, %s)", (test[0], p_name, unit, ref, method))
        conn.commit()
        return "<h2 style='color:green; padding:50px;'>SUCCESS! Master Dictionary updated with Clinical Methodologies.</h2>"
    except Exception as e: return f"<h2 style='color:red;'>Error: {str(e)}</h2>"
    finally: conn.close()

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
                    # CRITICAL: Now fetching the methodology column from the DB
                    cursor.execute("SELECT tp.parameter_name, tp.unit, tp.reference_range, tp.methodology, t.name as test_name, c.name as cat_name FROM test_parameters tp JOIN tests t ON tp.test_id = t.id LEFT JOIN test_categories c ON t.category_id = c.id WHERE tp.id = %s", (param_id,))
                    p_info = cursor.fetchone()
                    if p_info: 
                        results_data.append({
                            'cat': p_info['cat_name'] or 'PATHOLOGY', 
                            'test': p_info['test_name'], 
                            'param': p_info['parameter_name'], 
                            'val': val, 
                            'unit': p_info['unit'], 
                            'ref': p_info['reference_range'],
                            'method': p_info['methodology']  # Attaching methodology for the PDF
                        })
        
        cursor.execute("SELECT o.*, u.patient_uid FROM orders o JOIN users u ON o.user_id = u.id WHERE o.id = %s", (order_id,))
        order = cursor.fetchone()
        
        pdf_bytes = generate_medical_report(order_id, order, results_data)
        filename = f"CareDrop_Report_{order['patient_uid']}.pdf"
        cursor.execute("UPDATE orders SET report_file = %s, report_filename = %s, status = 'Completed', report_type = 'System' WHERE id = %s", (psycopg2.Binary(pdf_bytes), filename, order_id))
        conn.commit()
    except Exception as e: conn.rollback(); print(str(e))
    finally: conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/add-phlebotomist', methods=['POST'])
def add_phlebotomist():
    if session.get('admin_logged_in'): safe_execute("INSERT INTO phlebotomists (name, phone, vehicle_number, pin) VALUES (%s, %s, %s, %s)", (request.form.get('name'), request.form.get('phone'), request.form.get('vehicle_number'), request.form.get('pin', '1234')))
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/assign-order', methods=['POST'])
def assign_order():
    if session.get('admin_logged_in'): safe_execute("UPDATE orders SET phlebotomist_id=%s, payout_amount=%s WHERE id=%s", (request.form.get('phlebotomist_id') or None, request.form.get('payout_amount', 150), request.form.get('order_id')))
    return redirect(url_for('admin_dashboard'))

if __name__ == '__main__': app.run(debug=True, port=5000)
