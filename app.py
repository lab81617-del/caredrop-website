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
            
            # Clinical Additions
            cursor.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS accession_id VARCHAR(50)")
            cursor.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS verified_by VARCHAR(255)")
            
            # Financial Additions (Phase 2.1)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS invoices (
                    id SERIAL PRIMARY KEY, order_id INT, invoice_ref VARCHAR(50), 
                    subtotal DECIMAL(10,2), discount DECIMAL(10,2) DEFAULT 0, 
                    total_amount DECIMAL(10,2), status VARCHAR(50) DEFAULT 'UNPAID',
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS payments (
                    id SERIAL PRIMARY KEY, invoice_id INT, amount DECIMAL(10,2), 
                    payment_method VARCHAR(50), transaction_ref VARCHAR(100), 
                    payment_status VARCHAR(50) DEFAULT 'SUCCESS', 
                    recorded_by VARCHAR(50), timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, 
                    notes TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cash_reconciliation (
                    id SERIAL PRIMARY KEY, date DATE DEFAULT CURRENT_DATE, 
                    expected_amount DECIMAL(10,2), actual_amount DECIMAL(10,2), 
                    difference DECIMAL(10,2), notes TEXT, recorded_by VARCHAR(50), 
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Missing basic tables
            cursor.execute("CREATE TABLE IF NOT EXISTS audit_logs (id SERIAL PRIMARY KEY, order_id INT, user_role VARCHAR(50), action VARCHAR(255), previous_state VARCHAR(50), new_state VARCHAR(50), timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, notes TEXT)")
            cursor.execute("CREATE TABLE IF NOT EXISTS inventory (id SERIAL PRIMARY KEY, item_name VARCHAR(255) NOT NULL, category VARCHAR(100), current_stock INT DEFAULT 0, threshold INT DEFAULT 50, unit VARCHAR(50) DEFAULT 'units')")
            cursor.execute("CREATE TABLE IF NOT EXISTS partners (id SERIAL PRIMARY KEY, partner_name VARCHAR(255) NOT NULL, partner_type VARCHAR(50), contact_phone VARCHAR(20), commission_rate INT DEFAULT 0)")
            
            # LEGACY DATA MIGRATION: Convert old orders to the new Invoice model
            cursor.execute("SELECT id, total_amount, balance_amount, order_ref FROM orders WHERE id NOT IN (SELECT order_id FROM invoices)")
            legacy_orders = cursor.fetchall()
            for lo in legacy_orders:
                inv_ref = lo['order_ref'].replace('ORD-', 'INV-') if lo['order_ref'] else f"INV-LEGACY-{lo['id']}"
                t_amt = float(lo['total_amount'] or 0)
                b_amt = float(lo['balance_amount'] or 0)
                cursor.execute("INSERT INTO invoices (order_id, invoice_ref, subtotal, discount, total_amount, status) VALUES (%s, %s, %s, 0, %s, 'UNPAID') RETURNING id", (lo['id'], inv_ref, t_amt, t_amt))
                inv_id = cursor.fetchone()['id']
                
                paid_amount = t_amt - b_amt
                if paid_amount > 0:
                    cursor.execute("INSERT INTO payments (invoice_id, amount, payment_method, recorded_by, notes) VALUES (%s, %s, 'Cash', 'System Migration', 'Legacy payment migrated')", (inv_id, paid_amount))
                    inv_status = 'PAID' if paid_amount >= t_amt else 'PARTIALLY_PAID'
                    cursor.execute("UPDATE invoices SET status = %s WHERE id = %s", (inv_status, inv_id))
            
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

def dispatch_notifications_bg(patient_email, patient_name, order_ref, pdf_bytes, filename):
    try:
        if patient_email and '@' in patient_email and not patient_email.startswith('walkin_'):
            msg = EmailMessage()
            msg['Subject'] = f"CareDrop Report ({order_ref})"
            msg['From'] = os.environ.get('MAIL_USERNAME', 'reports@caredrop.in')
            msg['To'] = patient_email
            msg.set_content(f"Dear {patient_name.title()},\n\nYour verified report is attached.\n\nThank you, CareDrop.")
            msg.add_attachment(pdf_bytes, maintype='application', subtype='pdf', filename=filename)
            mail_pass = os.environ.get('MAIL_PASSWORD') or os.environ.get('GMAIL_APP_PASSWORD')
            if mail_pass and os.environ.get('MAIL_USERNAME'):
                with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
                    smtp.login(os.environ.get('MAIL_USERNAME'), mail_pass)
                    smtp.send_message(msg)
    except Exception as e: print(e)

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
    
    # Orders Query + Financial State Join
    query = """
        SELECT o.*, u.patient_uid, i.invoice_ref, i.total_amount, i.status as financial_status, i.id as invoice_id,
        (SELECT COALESCE(SUM(amount), 0) FROM payments WHERE invoice_id = i.id AND payment_status = 'SUCCESS') as amount_paid
        FROM orders o 
        JOIN users u ON o.user_id = u.id 
        LEFT JOIN invoices i ON o.id = i.order_id
    """
    if session.get('role') == 'pathologist': query += " WHERE o.status = 'Pending Verification'"
    query += " ORDER BY o.id DESC"
    
    cursor.execute(query)
    orders = cursor.fetchall()
    
    # Financial Analytics (Today's Collections)
    cursor.execute("SELECT payment_method, SUM(amount) as total FROM payments WHERE DATE(timestamp) = CURRENT_DATE AND payment_status = 'SUCCESS' GROUP BY payment_method")
    today_collections = cursor.fetchall()
    
    cursor.execute("SELECT * FROM audit_logs ORDER BY timestamp DESC LIMIT 50")
    audit_logs = cursor.fetchall()
    
    cursor.execute("SELECT * FROM labs ORDER BY name"); labs = cursor.fetchall()
    cursor.execute("SELECT * FROM phlebotomists ORDER BY id DESC"); riders = cursor.fetchall()
    cursor.execute("SELECT * FROM partners ORDER BY partner_name"); partners = cursor.fetchall()
    
    # Fetch tests with prices for the Multi-Select walk-in cart
    cursor.execute("SELECT t.id, t.name, ltp.price FROM tests t LEFT JOIN lab_test_pricing ltp ON t.id = ltp.test_id WHERE t.is_active = TRUE AND ltp.lab_id = 1 ORDER BY t.name")
    all_tests = cursor.fetchall()
    
    conn.close()
    
    return render_template('admin.html', orders=orders, audit_logs=audit_logs, active_labs=[l for l in labs if l['is_active']], all_labs=labs, phlebotomists=riders, today_collections=today_collections, user_role=session.get('role', 'admin'), partners=partners, all_tests=all_tests)

# --- REVENUE INTEGRITY: NEW WALK-IN FLOW ---
@app.route('/admin/walk-in', methods=['POST'])
@role_required(['receptionist'])
def admin_walk_in():
    p_name, phone, age, gender = request.form.get('patient_name'), request.form.get('phone'), request.form.get('age'), request.form.get('gender')
    address, ref_by = request.form.get('address', ''), request.form.get('referred_by', 'Self').strip()
    test_ids = request.form.getlist('test_ids')
    lab_id = request.form.get('lab_id', 1)
    discount = float(request.form.get('discount') or 0)
    advance = float(request.form.get('advance') or 0)
    pay_method = request.form.get('payment_method', 'Cash')
    
    conn = get_db()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # 1. Identity
        cursor.execute("SELECT id FROM users WHERE phone = %s", (phone,))
        user = cursor.fetchone()
        if user: user_id = user['id']
        else:
            cursor.execute("INSERT INTO users (name, phone, email) VALUES (%s, %s, %s) RETURNING id", (p_name, phone, f"walkin_{phone}@caredrop.local"))
            user_id = cursor.fetchone()['id']
            cursor.execute("UPDATE users SET patient_uid = %s WHERE id = %s", (f"CD-PAT-{1000 + user_id}", user_id))
            
        # 2. Clinical Order
        cursor.execute("INSERT INTO orders (user_id, patient_name, age, gender, address, collection_date, time_slot, status, referred_by) VALUES (%s, %s, %s, %s, %s, %s, 'Immediate', 'Received in Lab', %s) RETURNING id", (user_id, p_name, age, gender, address, datetime.today().strftime('%Y-%m-%d'), ref_by))
        order_id = cursor.fetchone()['id']
        
        acc_id = f"CD-ACC-{datetime.today().strftime('%y%m')}-{order_id:04d}"
        ord_ref = f"ORD-{datetime.today().strftime('%y%m')}-{order_id:04d}"
        inv_ref = f"INV-{datetime.today().strftime('%y%m')}-{order_id:04d}"
        cursor.execute("UPDATE orders SET order_ref = %s, accession_id = %s WHERE id = %s", (ord_ref, acc_id, order_id))
        
        # 3. Snapshot Prices & Calculate Subtotal
        subtotal = 0
        for tid in test_ids:
            cursor.execute("SELECT price FROM lab_test_pricing WHERE test_id = %s AND lab_id = %s", (tid, lab_id))
            price_row = cursor.fetchone()
            price = float(price_row['price']) if price_row else 0
            subtotal += price
            cursor.execute("INSERT INTO order_items (order_id, test_id, lab_id, price, item_type) VALUES (%s, %s, %s, %s, 'test')", (order_id, tid, lab_id, price))
            
        # 4. Financial Invoice Creation
        final_total = max(0, subtotal - discount)
        cursor.execute("INSERT INTO invoices (order_id, invoice_ref, subtotal, discount, total_amount, status) VALUES (%s, %s, %s, %s, %s, 'UNPAID') RETURNING id", (order_id, inv_ref, subtotal, discount, final_total))
        invoice_id = cursor.fetchone()['id']
        
        # 5. Advance Payment Application
        if advance > 0:
            if advance > final_total: advance = final_total # Overpayment protection on walk-in
            cursor.execute("INSERT INTO payments (invoice_id, amount, payment_method, recorded_by, notes) VALUES (%s, %s, %s, %s, 'Advance Payment')", (invoice_id, advance, pay_method, session.get('role')))
            new_status = 'PAID' if advance >= final_total else 'PARTIALLY_PAID'
            cursor.execute("UPDATE invoices SET status = %s WHERE id = %s", (new_status, invoice_id))
            
        conn.commit()
        log_audit(order_id, "Order Created", "None", "Received in Lab", f"Subtotal: ₹{subtotal}, Discount: ₹{discount}, Advance: ₹{advance}")
    except Exception as e: conn.rollback(); print(e)
    finally: conn.close()
    return redirect(url_for('admin_dashboard'))

# --- REVENUE INTEGRITY: COLLECT BALANCE PAYMENT ---
@app.route('/admin/add-payment', methods=['POST'])
@role_required(['receptionist', 'admin'])
def add_payment():
    inv_id = request.form.get('invoice_id')
    amount = float(request.form.get('amount') or 0)
    method = request.form.get('payment_method')
    ref = request.form.get('transaction_ref', '')
    notes = request.form.get('notes', '')
    
    conn = get_db()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT total_amount, order_id FROM invoices WHERE id = %s", (inv_id,))
        invoice = cursor.fetchone()
        
        cursor.execute("SELECT COALESCE(SUM(amount), 0) as paid FROM payments WHERE invoice_id = %s AND payment_status = 'SUCCESS'", (inv_id,))
        already_paid = float(cursor.fetchone()['paid'])
        balance = float(invoice['total_amount']) - already_paid
        
        # Overpayment Protection
        if amount > balance:
            amount = balance 
            
        if amount > 0:
            cursor.execute("INSERT INTO payments (invoice_id, amount, payment_method, transaction_ref, recorded_by, notes) VALUES (%s, %s, %s, %s, %s, %s)", 
                           (inv_id, amount, method, ref, session.get('role'), notes))
            
            new_status = 'PAID' if (already_paid + amount) >= float(invoice['total_amount']) else 'PARTIALLY_PAID'
            cursor.execute("UPDATE invoices SET status = %s WHERE id = %s", (new_status, inv_id))
            conn.commit()
            
            log_audit(invoice['order_id'], "Payment Received", "N/A", new_status, f"Collected ₹{amount} via {method}")
            
    except Exception as e: conn.rollback(); print(e)
    finally: conn.close()
    return redirect(url_for('admin_dashboard'))

# --- DAILY RECONCILIATION ---
@app.route('/admin/reconcile-cash', methods=['POST'])
@role_required(['admin'])
def reconcile_cash():
    actual = float(request.form.get('actual_cash') or 0)
    notes = request.form.get('notes', '')
    
    conn = get_db()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT COALESCE(SUM(amount), 0) as expected FROM payments WHERE DATE(timestamp) = CURRENT_DATE AND payment_method = 'Cash' AND payment_status = 'SUCCESS'")
        expected = float(cursor.fetchone()['expected'])
        diff = actual - expected
        
        cursor.execute("INSERT INTO cash_reconciliation (expected_amount, actual_amount, difference, notes, recorded_by) VALUES (%s, %s, %s, %s, %s)",
                       (expected, actual, diff, notes, session.get('role')))
        conn.commit()
    except Exception as e: conn.rollback(); print(e)
    finally: conn.close()
    return redirect(url_for('admin_dashboard'))

# ... [Retain standard Routing for Print Barcode, Reject Sample, Fill Report, Verify Results exactly as before] ...
@app.route('/admin/reject-sample', methods=['POST'])
@role_required(['technician', 'pathologist', 'admin'])
def reject_sample():
    order_id, reason = request.form.get('order_id'), request.form.get('reason')
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM orders WHERE id = %s", (order_id,))
        old_status = cursor.fetchone()[0]
        cursor.execute("UPDATE orders SET status = 'Sample Rejected' WHERE id = %s", (order_id,))
        conn.commit()
        log_audit(order_id, "Sample Rejected", old_status, "Sample Rejected", f"Reason: {reason}")
    except Exception as e: conn.rollback(); print(e)
    finally: conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/fill-report/<int:order_id>')
@role_required(['technician'])
def admin_fill_report(order_id):
    conn = get_db(); cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT o.*, u.patient_uid FROM orders o JOIN users u ON o.user_id = u.id WHERE o.id = %s", (order_id,))
    order = cursor.fetchone()
    cursor.execute("SELECT oi.order_id, t.id as test_id, t.name as test_name, c.name as cat_name FROM order_items oi JOIN tests t ON oi.test_id = t.id LEFT JOIN test_categories c ON t.category_id = c.id WHERE oi.item_type = 'test' AND oi.order_id = %s", (order_id,))
    tests = cursor.fetchall()
    for t in tests:
        cursor.execute("SELECT id, parameter_name, unit, reference_range FROM test_parameters WHERE test_id = %s", (t['test_id'],))
        t['parameters'] = cursor.fetchall()
    conn.close()
    return render_template('lims_report.html', order=order, tests=tests)

@app.route('/admin/save-results/<int:order_id>', methods=['POST'])
@role_required(['technician'])
def save_results(order_id):
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM orders WHERE id = %s", (order_id,))
        old_status = cursor.fetchone()[0]
        cursor.execute("DELETE FROM order_results WHERE order_id = %s", (order_id,)) 
        for key, value in request.form.items():
            if key.startswith('param_') and value.strip() != '':
                param_id = key.split('_')[1]
                cursor.execute("INSERT INTO order_results (order_id, parameter_id, result_value) VALUES (%s, %s, %s)", (order_id, param_id, value.strip()))
        cursor.execute("UPDATE orders SET status = 'Pending Verification' WHERE id = %s", (order_id,))
        conn.commit()
        log_audit(order_id, "Results Entered", old_status, "Pending Verification", "Clinical data saved by technician")
    except Exception as e: conn.rollback(); print(e)
    finally: conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/verify-results/<int:order_id>', methods=['GET', 'POST'])
@role_required(['pathologist'])
def verify_results(order_id):
    conn = get_db()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        if request.method == 'POST':
            cursor.execute("SELECT o.*, u.patient_uid, u.email FROM orders o JOIN users u ON o.user_id = u.id WHERE o.id = %s", (order_id,))
            order = cursor.fetchone()
            cursor.execute("SELECT r.result_value, tp.parameter_name, tp.unit, tp.reference_range, tp.methodology, tp.interpretation, t.name as test_name, c.name as cat_name FROM order_results r JOIN test_parameters tp ON r.parameter_id = tp.id JOIN tests t ON tp.test_id = t.id LEFT JOIN test_categories c ON t.category_id = c.id WHERE r.order_id = %s", (order_id,))
            results_db = cursor.fetchall()
            results_data = [{'cat': p['cat_name'] or 'PATHOLOGY', 'test': p['test_name'], 'param': p['parameter_name'], 'val': p['result_value'], 'unit': p['unit'], 'ref': p['reference_range'], 'method': p['methodology'], 'interpretation': p['interpretation']} for p in results_db]
            cursor.execute("SELECT l.* FROM order_items oi JOIN labs l ON oi.lab_id = l.id WHERE oi.order_id = %s LIMIT 1", (order_id,))
            lab_data = cursor.fetchone()
            
            pdf_bytes = generate_medical_report(order_id, order, results_data, lab_data)
            filename = f"CareDrop_Report_{order['patient_uid']}.pdf"
            
            cursor.execute("UPDATE orders SET report_file = %s, report_filename = %s, status = 'Completed', verified_by = 'Dr. Abdul Sameer Qureshi (Pathologist)' WHERE id = %s", (psycopg2.Binary(pdf_bytes), filename, order_id))
            conn.commit()
            log_audit(order_id, "Report Released", "Pending Verification", "Completed", "Digitally signed")
            threading.Thread(target=dispatch_notifications_bg, args=(order['email'], order['patient_name'], order['order_ref'], pdf_bytes, filename)).start()
            return redirect(url_for('admin_dashboard'))
        
        cursor.execute("SELECT o.*, u.patient_uid FROM orders o JOIN users u ON o.user_id = u.id WHERE o.id = %s", (order_id,))
        order = cursor.fetchone()
        cursor.execute("SELECT r.result_value, tp.parameter_name, tp.unit, tp.reference_range FROM order_results r JOIN test_parameters tp ON r.parameter_id = tp.id WHERE r.order_id = %s", (order_id,))
        results = cursor.fetchall()
        
        html = f"""<html><body style="font-family:'Plus Jakarta Sans', sans-serif; background:#F1F5F9; padding:40px;"><div style="max-width:800px; margin:auto; background:white; padding:30px; border-radius:12px;"><h2 style="color:#0F172A;">Verify Results</h2><p>Patient: <b>{order['patient_name'].title()}</b></p><table style="width:100%; border-collapse:collapse; margin-bottom:30px;"><tr style="background:#F8FAFC; text-align:left;"><th>Parameter</th><th>Result Entered</th><th>Reference Range</th></tr>"""
        for r in results: html += f"<tr><td style='padding:15px; border-bottom:1px solid #F1F5F9; font-weight:600;'>{r['parameter_name']}</td><td style='padding:15px; font-weight:800; color:#0D9488;'>{r['result_value']} {r['unit']}</td><td style='padding:15px; color:#64748B;'>{r['reference_range']}</td></tr>"
        html += f"""</table><form method="POST"><button type="submit" style="background:#16A34A; color:white; padding:12px 24px; border:none; border-radius:6px; font-weight:bold; cursor:pointer;">Approve & Sign</button></form></div></body></html>"""
        return html
    finally: conn.close()

if __name__ == '__main__': app.run(debug=True, port=5000)
