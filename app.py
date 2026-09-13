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
PATHOLOGIST_PASSWORD = os.environ.get("PATHOLOGIST_PASSWORD", "doc2026")

def get_db(): return psycopg2.connect(os.environ.get("DATABASE_URL"))

def safe_execute(query, params=None):
    conn = get_db()
    try: 
        cursor = conn.cursor()
        cursor.execute(query, params)
        conn.commit()
    except Exception as e: 
        conn.rollback(); print(e)
    finally: conn.close()

def log_audit(order_id, action, prev_state, new_state, notes=""):
    role = session.get('role', 'system')
    safe_execute("INSERT INTO audit_logs (order_id, user_role, action, previous_state, new_state, notes) VALUES (%s, %s, %s, %s, %s, %s)", (order_id, role, action, prev_state, new_state, notes))

@app.before_request
def ensure_db_schema():
    if not getattr(app, '_schema_checked', False):
        conn = get_db()
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Clinical & Financial Additions
            cursor.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS accession_id VARCHAR(50)")
            cursor.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS verified_by VARCHAR(255)")
            cursor.execute("CREATE TABLE IF NOT EXISTS invoices (id SERIAL PRIMARY KEY, order_id INT, invoice_ref VARCHAR(50), subtotal DECIMAL(10,2), discount DECIMAL(10,2) DEFAULT 0, total_amount DECIMAL(10,2), status VARCHAR(50) DEFAULT 'UNPAID', timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            cursor.execute("CREATE TABLE IF NOT EXISTS payments (id SERIAL PRIMARY KEY, invoice_id INT, amount DECIMAL(10,2), payment_method VARCHAR(50), transaction_ref VARCHAR(100), payment_status VARCHAR(50) DEFAULT 'SUCCESS', recorded_by VARCHAR(50), timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, notes TEXT)")
            cursor.execute("CREATE TABLE IF NOT EXISTS cash_reconciliation (id SERIAL PRIMARY KEY, date DATE DEFAULT CURRENT_DATE, expected_amount DECIMAL(10,2), actual_amount DECIMAL(10,2), difference DECIMAL(10,2), notes TEXT, recorded_by VARCHAR(50), timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            cursor.execute("CREATE TABLE IF NOT EXISTS audit_logs (id SERIAL PRIMARY KEY, order_id INT, user_role VARCHAR(50), action VARCHAR(255), previous_state VARCHAR(50), new_state VARCHAR(50), timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, notes TEXT)")
            cursor.execute("CREATE TABLE IF NOT EXISTS inventory (id SERIAL PRIMARY KEY, item_name VARCHAR(255) NOT NULL, category VARCHAR(100), current_stock INT DEFAULT 0, threshold INT DEFAULT 50, unit VARCHAR(50) DEFAULT 'units')")
            cursor.execute("CREATE TABLE IF NOT EXISTS partners (id SERIAL PRIMARY KEY, partner_name VARCHAR(255) NOT NULL, partner_type VARCHAR(50), contact_phone VARCHAR(20), commission_rate INT DEFAULT 0)")
            
            # AUTO-SEED DUMMY TESTS IF EMPTY (So the screen isn't blank)
            cursor.execute("CREATE TABLE IF NOT EXISTS tests (id SERIAL PRIMARY KEY, name VARCHAR(255), is_active BOOLEAN DEFAULT TRUE)")
            cursor.execute("CREATE TABLE IF NOT EXISTS labs (id SERIAL PRIMARY KEY, name VARCHAR(255), is_active BOOLEAN DEFAULT TRUE)")
            cursor.execute("CREATE TABLE IF NOT EXISTS lab_test_pricing (id SERIAL PRIMARY KEY, test_id INT, lab_id INT, price DECIMAL(10,2))")
            
            cursor.execute("SELECT COUNT(*) FROM tests")
            if cursor.fetchone()['count'] == 0:
                cursor.execute("INSERT INTO labs (name) VALUES ('Main CareDrop Lab') RETURNING id")
                lab_id = cursor.fetchone()['id']
                
                tests_to_add = [("Complete Blood Count (CBC)", 300), ("Liver Function Test (LFT)", 500), ("Lipid Profile", 400)]
                for t_name, price in tests_to_add:
                    cursor.execute("INSERT INTO tests (name) VALUES (%s) RETURNING id", (t_name,))
                    t_id = cursor.fetchone()['id']
                    cursor.execute("INSERT INTO lab_test_pricing (test_id, lab_id, price) VALUES (%s, %s, %s)", (t_id, lab_id, price))
            
            conn.commit()
        except Exception as e:
            conn.rollback(); print(f"Schema Error: {e}")
        finally:
            conn.close()
        app._schema_checked = True

def role_required(allowed_roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if session.get('role') == 'admin' or session.get('role') in allowed_roles:
                return f(*args, **kwargs)
            return redirect(url_for('unified_login'))
        return decorated_function
    return decorator

@app.route('/')
def home(): return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def unified_login():
    if request.method == 'POST':
        r, p = request.form.get('role'), request.form.get('password')
        if r == 'admin' and p == ADMIN_PASSWORD: session['role'] = 'admin'; return redirect(url_for('admin_dashboard'))
        elif r == 'receptionist' and p == RECEPTION_PASSWORD: session['role'] = 'receptionist'; return redirect(url_for('admin_dashboard'))
        elif r == 'technician' and p == TECH_PASSWORD: session['role'] = 'technician'; return redirect(url_for('admin_dashboard'))
        elif r == 'pathologist' and p == PATHOLOGIST_PASSWORD: session['role'] = 'pathologist'; return redirect(url_for('admin_dashboard'))
        return "Access Denied."
    return '''<html><body style="background:#F1F5F9; display:flex; justify-content:center; align-items:center; height:100vh; font-family:sans-serif;"><div style="background:white; padding:40px; border-radius:12px; box-shadow:0 4px 15px rgba(0,0,0,0.05); width:350px; text-align:center;"><h2 style="margin-top:0;">CareDrop Secure Portal</h2><form method="POST"><select name="role" style="width:100%; padding:12px; margin-bottom:15px; border-radius:6px; border:1px solid #CBD5E1; font-weight:bold;"><option value="admin">Admin</option><option value="receptionist">Receptionist</option><option value="technician">Technician</option><option value="pathologist">Pathologist</option></select><input type="password" name="password" placeholder="Password" required style="width:100%; padding:12px; margin-bottom:15px; border-radius:6px; border:1px solid #CBD5E1;"><button type="submit" style="width:100%; background:#0D9488; color:white; padding:12px; border:none; border-radius:6px; font-weight:bold; cursor:pointer;">Login</button></form></div></body></html>'''

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('unified_login'))

@app.route('/admin')
@role_required(['receptionist', 'technician', 'pathologist'])
def admin_dashboard():
    conn = get_db(); cursor = conn.cursor(cursor_factory=RealDictCursor)
    query = """
        SELECT o.*, u.patient_uid, i.invoice_ref, i.total_amount, i.status as financial_status, i.id as invoice_id,
        (SELECT COALESCE(SUM(amount), 0) FROM payments WHERE invoice_id = i.id AND payment_status = 'SUCCESS') as amount_paid
        FROM orders o JOIN users u ON o.user_id = u.id LEFT JOIN invoices i ON o.id = i.order_id
    """
    if session.get('role') == 'pathologist': query += " WHERE o.status = 'Pending Verification'"
    query += " ORDER BY o.id DESC"
    cursor.execute(query); orders = cursor.fetchall()
    
    cursor.execute("SELECT payment_method, SUM(amount) as total FROM payments WHERE DATE(timestamp) = CURRENT_DATE AND payment_status = 'SUCCESS' GROUP BY payment_method")
    today_collections = cursor.fetchall()
    
    cursor.execute("SELECT * FROM audit_logs ORDER BY timestamp DESC LIMIT 50"); audit_logs = cursor.fetchall()
    conn.close()
    
    return render_template('admin.html', orders=orders, audit_logs=audit_logs, today_collections=today_collections, user_role=session.get('role', 'admin'))

# --- NEW FULL-PAGE BOOKING ROUTE ---
@app.route('/admin/new-order')
@role_required(['receptionist', 'admin'])
def admin_new_order_page():
    conn = get_db(); cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM labs WHERE is_active = TRUE ORDER BY name"); labs = cursor.fetchall()
    cursor.execute("SELECT * FROM partners ORDER BY partner_name"); partners = cursor.fetchall()
    
    # Get tests and their default prices (from the first lab)
    cursor.execute("""
        SELECT t.id, t.name, COALESCE(ltp.price, 0) as price 
        FROM tests t LEFT JOIN lab_test_pricing ltp ON t.id = ltp.test_id 
        WHERE t.is_active = TRUE ORDER BY t.name
    """)
    all_tests = cursor.fetchall()
    conn.close()
    return render_template('admin_new_order.html', labs=labs, partners=partners, all_tests=all_tests, user_role=session.get('role', 'admin'))

@app.route('/admin/walk-in', methods=['POST'])
@role_required(['receptionist', 'admin'])
def admin_walk_in():
    p_name, phone, age, gender = request.form.get('patient_name'), request.form.get('phone'), request.form.get('age'), request.form.get('gender')
    address, ref_by = request.form.get('address', ''), request.form.get('referred_by', 'Self').strip()
    test_ids = request.form.getlist('test_ids')
    lab_id = request.form.get('lab_id', 1)
    discount = float(request.form.get('discount') or 0)
    advance = float(request.form.get('advance') or 0)
    pay_method = request.form.get('payment_method', 'Cash')
    
    if not test_ids:
        return "Error: No tests selected.", 400
        
    conn = get_db()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        cursor.execute("SELECT id FROM users WHERE phone = %s", (phone,))
        user = cursor.fetchone()
        if user: user_id = user['id']
        else:
            cursor.execute("INSERT INTO users (name, phone, email) VALUES (%s, %s, %s) RETURNING id", (p_name, phone, f"walkin_{phone}@caredrop.local"))
            user_id = cursor.fetchone()['id']
            cursor.execute("UPDATE users SET patient_uid = %s WHERE id = %s", (f"CD-PAT-{1000 + user_id}", user_id))
            
        cursor.execute("INSERT INTO orders (user_id, patient_name, age, gender, address, collection_date, time_slot, status, referred_by) VALUES (%s, %s, %s, %s, %s, %s, 'Immediate', 'Received in Lab', %s) RETURNING id", (user_id, p_name, age, gender, address, datetime.today().strftime('%Y-%m-%d'), ref_by))
        order_id = cursor.fetchone()['id']
        
        acc_id = f"CD-ACC-{datetime.today().strftime('%y%m')}-{order_id:04d}"
        ord_ref = f"ORD-{datetime.today().strftime('%y%m')}-{order_id:04d}"
        inv_ref = f"INV-{datetime.today().strftime('%y%m')}-{order_id:04d}"
        cursor.execute("UPDATE orders SET order_ref = %s, accession_id = %s WHERE id = %s", (ord_ref, acc_id, order_id))
        
        subtotal = 0
        for tid in test_ids:
            cursor.execute("SELECT price FROM lab_test_pricing WHERE test_id = %s AND lab_id = %s", (tid, lab_id))
            price_row = cursor.fetchone()
            price = float(price_row['price']) if price_row else 0
            subtotal += price
            cursor.execute("INSERT INTO order_items (order_id, test_id, lab_id, price, item_type) VALUES (%s, %s, %s, %s, 'test')", (order_id, tid, lab_id, price))
            
        final_total = max(0, subtotal - discount)
        cursor.execute("INSERT INTO invoices (order_id, invoice_ref, subtotal, discount, total_amount, status) VALUES (%s, %s, %s, %s, %s, 'UNPAID') RETURNING id", (order_id, inv_ref, subtotal, discount, final_total))
        invoice_id = cursor.fetchone()['id']
        
        if advance > 0:
            if advance > final_total: advance = final_total
            cursor.execute("INSERT INTO payments (invoice_id, amount, payment_method, recorded_by, notes) VALUES (%s, %s, %s, %s, 'Advance Payment')", (invoice_id, advance, pay_method, session.get('role')))
            new_status = 'PAID' if advance >= final_total else 'PARTIALLY_PAID'
            cursor.execute("UPDATE invoices SET status = %s WHERE id = %s", (new_status, invoice_id))
            
        conn.commit()
        log_audit(order_id, "Order Created", "None", "Received in Lab", f"Subtotal: ₹{subtotal}, Discount: ₹{discount}, Advance: ₹{advance}")
    except Exception as e: conn.rollback(); print(e)
    finally: conn.close()
    return redirect(url_for('admin_dashboard'))

# --- BALANCE & RECONCILIATION ---
@app.route('/admin/add-payment', methods=['POST'])
@role_required(['receptionist', 'admin'])
def add_payment():
    inv_id = request.form.get('invoice_id')
    amount = float(request.form.get('amount') or 0)
    method = request.form.get('payment_method')
    conn = get_db()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT total_amount, order_id FROM invoices WHERE id = %s", (inv_id,))
        invoice = cursor.fetchone()
        cursor.execute("SELECT COALESCE(SUM(amount), 0) as paid FROM payments WHERE invoice_id = %s AND payment_status = 'SUCCESS'", (inv_id,))
        already_paid = float(cursor.fetchone()['paid'])
        balance = float(invoice['total_amount']) - already_paid
        
        if amount > balance: amount = balance 
        if amount > 0:
            cursor.execute("INSERT INTO payments (invoice_id, amount, payment_method, recorded_by) VALUES (%s, %s, %s, %s)", (inv_id, amount, method, session.get('role')))
            new_status = 'PAID' if (already_paid + amount) >= float(invoice['total_amount']) else 'PARTIALLY_PAID'
            cursor.execute("UPDATE invoices SET status = %s WHERE id = %s", (new_status, inv_id))
            conn.commit()
            log_audit(invoice['order_id'], "Payment Received", "N/A", new_status, f"Collected ₹{amount} via {method}")
    except Exception as e: conn.rollback(); print(e)
    finally: conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/reconcile-cash', methods=['POST'])
@role_required(['admin'])
def reconcile_cash():
    actual = float(request.form.get('actual_cash') or 0)
    conn = get_db()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT COALESCE(SUM(amount), 0) as expected FROM payments WHERE DATE(timestamp) = CURRENT_DATE AND payment_method = 'Cash' AND payment_status = 'SUCCESS'")
        expected = float(cursor.fetchone()['expected'])
        cursor.execute("INSERT INTO cash_reconciliation (expected_amount, actual_amount, difference, recorded_by) VALUES (%s, %s, %s, %s)", (expected, actual, actual - expected, session.get('role')))
        conn.commit()
    except Exception as e: conn.rollback(); print(e)
    finally: conn.close()
    return redirect(url_for('admin_dashboard'))
    # --- MASTER SETTINGS & CSV IMPORT ---
@app.route('/admin/update-lab', methods=['POST'])
@role_required(['admin'])
def update_lab():
    lab_id = request.form.get('lab_id', 1)
    d1_name = request.form.get('doctor_1_name')
    d1_degree = request.form.get('doctor_1_degree')
    
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE labs SET doctor_1_name = %s, doctor_1_degree = %s WHERE id = %s", (d1_name, d1_degree, lab_id))
        conn.commit()
        log_audit(0, "System Settings", "N/A", "Updated", f"Updated Lab ID {lab_id} Pathologist info")
    except Exception as e: conn.rollback(); print(e)
    finally: conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/import-tests', methods=['POST'])
@role_required(['admin'])
def import_tests():
    if 'csv_file' not in request.files: return redirect(url_for('admin_dashboard'))
    file = request.files['csv_file']
    if file.filename == '': return redirect(url_for('admin_dashboard'))
    
    conn = get_db()
    try:
        # Read the CSV file
        stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
        reader = csv.DictReader(stream)
        cursor = conn.cursor()
        
        count = 0
        for row in reader:
            test_name = row.get('Test Name')
            price = row.get('Price', 0)
            
            if test_name:
                # Insert Test
                cursor.execute("INSERT INTO tests (name, is_active) VALUES (%s, TRUE) RETURNING id", (test_name,))
                t_id = cursor.fetchone()[0]
                # Insert Price for Lab 1 (Main Lab)
                cursor.execute("INSERT INTO lab_test_pricing (test_id, lab_id, price) VALUES (%s, 1, %s)", (t_id, price))
                count += 1
                
        conn.commit()
        log_audit(0, "Catalog Import", "N/A", "Success", f"Bulk imported {count} tests via CSV")
    except Exception as e: conn.rollback(); print(e)
    finally: conn.close()
    return redirect(url_for('admin_dashboard'))

if __name__ == '__main__': app.run(debug=True, port=5000)
