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

# --- THE FLIGHT RECORDER (AUDIT LOG HELPER) ---
def log_audit(order_id, action, prev_state, new_state, notes=""):
    role = session.get('role', 'system')
    safe_execute("""
        INSERT INTO audit_logs (order_id, user_role, action, previous_state, new_state, notes) 
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (order_id, role, action, prev_state, new_state, notes))

@app.before_request
def ensure_db_schema():
    if not getattr(app, '_schema_checked', False):
        safe_execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS accession_id VARCHAR(50)")
        safe_execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS verified_by VARCHAR(255)")
        
        # New Audit & Exception Tables
        safe_execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id SERIAL PRIMARY KEY, order_id INT, user_role VARCHAR(50), action VARCHAR(255),
                previous_state VARCHAR(50), new_state VARCHAR(50), timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, notes TEXT
            )
        """)
        safe_execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                id SERIAL PRIMARY KEY, item_name VARCHAR(255) NOT NULL, category VARCHAR(100),
                current_stock INT DEFAULT 0, threshold INT DEFAULT 50, unit VARCHAR(50) DEFAULT 'units'
            )
        """)
        safe_execute("""
            CREATE TABLE IF NOT EXISTS partners (
                id SERIAL PRIMARY KEY, partner_name VARCHAR(255) NOT NULL, partner_type VARCHAR(50),
                contact_phone VARCHAR(20), commission_rate INT DEFAULT 0
            )
        """)
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

def dispatch_notifications_bg(patient_email, patient_phone, patient_name, order_ref, pdf_bytes, filename):
    try:
        if patient_email and '@' in patient_email and not patient_email.startswith('walkin_'):
            msg = EmailMessage()
            msg['Subject'] = f"Secure Medical Report - CareDrop Diagnostics ({order_ref})"
            msg['From'] = os.environ.get('MAIL_USERNAME', 'reports@caredrop.in')
            msg['To'] = patient_email
            msg.set_content(f"Dear {patient_name.title()},\n\nYour clinical investigations are complete. Please find your digitally verified laboratory report attached.\n\nThank you for choosing CareDrop.")
            msg.add_attachment(pdf_bytes, maintype='application', subtype='pdf', filename=filename)
            mail_pass = os.environ.get('MAIL_PASSWORD') or os.environ.get('GMAIL_APP_PASSWORD')
            if mail_pass and os.environ.get('MAIL_USERNAME'):
                with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
                    smtp.login(os.environ.get('MAIL_USERNAME'), mail_pass)
                    smtp.send_message(msg)
    except Exception as e: print(f"Background Email Failed: {e}")

@app.route('/')
def home(): return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def unified_login():
    if request.method == 'POST':
        role = request.form.get('role')
        password = request.form.get('password')
        if role == 'admin' and password == ADMIN_PASSWORD: session['role'] = 'admin'; return redirect(url_for('admin_dashboard'))
        elif role == 'receptionist' and password == RECEPTION_PASSWORD: session['role'] = 'receptionist'; return redirect(url_for('admin_dashboard'))
        elif role == 'technician' and password == TECH_PASSWORD: session['role'] = 'technician'; return redirect(url_for('admin_dashboard'))
        elif role == 'pathologist' and password == PATHOLOGIST_PASSWORD: session['role'] = 'pathologist'; return redirect(url_for('admin_dashboard'))
        return "Access Denied: Invalid Password."
    return '''<html><body style="background:#F1F5F9; display:flex; justify-content:center; align-items:center; height:100vh; font-family:sans-serif;">
    <div style="background:white; padding:40px; border-radius:12px; box-shadow:0 4px 15px rgba(0,0,0,0.05); width:350px; text-align:center;">
    <h2 style="color:#0F172A; margin-top:0;">CareDrop Secure Portal</h2>
    <form method="POST">
    <select name="role" style="width:100%; padding:12px; margin-bottom:15px; border-radius:6px; border:1px solid #CBD5E1; font-weight:bold;">
    <option value="admin">Master Administrator</option><option value="receptionist">Reception Desk</option>
    <option value="technician">Lab Technician</option><option value="pathologist">Chief Pathologist</option>
    </select>
    <input type="password" name="password" placeholder="Access Password" required style="width:100%; padding:12px; margin-bottom:15px; border-radius:6px; border:1px solid #CBD5E1;">
    <button type="submit" style="width:100%; background:#0D9488; color:white; padding:12px; border:none; border-radius:6px; font-weight:bold; cursor:pointer;">Authenticate</button>
    </form></div></body></html>'''

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('unified_login'))

@app.route('/admin')
@role_required(['receptionist', 'technician', 'pathologist'])
def admin_dashboard():
    conn = get_db(); cursor = conn.cursor(cursor_factory=RealDictCursor)
    if session.get('role') == 'pathologist':
        cursor.execute("SELECT o.*, u.patient_uid FROM orders o JOIN users u ON o.user_id = u.id WHERE o.status = 'Pending Verification' ORDER BY o.id DESC")
    else:
        cursor.execute("SELECT o.*, u.patient_uid FROM orders o JOIN users u ON o.user_id = u.id ORDER BY o.id DESC")
    
    orders = cursor.fetchall()
    
    # Fetch Audit Logs for the Modal
    cursor.execute("SELECT * FROM audit_logs ORDER BY timestamp DESC LIMIT 50")
    audit_logs = cursor.fetchall()
    
    cursor.execute("SELECT referred_by, SUM(total_amount) as total_revenue, SUM(balance_amount) as pending_balance, COUNT(id) as total_orders FROM orders GROUP BY referred_by")
    financials = cursor.fetchall()
    cursor.execute("SELECT * FROM labs ORDER BY name"); labs = cursor.fetchall()
    cursor.execute("SELECT * FROM inventory ORDER BY category, item_name"); warehouse = cursor.fetchall()
    cursor.execute("SELECT * FROM phlebotomists ORDER BY id DESC"); riders = cursor.fetchall()
    cursor.execute("SELECT * FROM partners ORDER BY partner_name"); partners = cursor.fetchall()
    cursor.execute("SELECT id, name FROM tests WHERE is_active = TRUE ORDER BY name"); all_tests = cursor.fetchall()
    conn.close()
    
    return render_template('admin.html', orders=orders, audit_logs=audit_logs, active_labs=[l for l in labs if l['is_active']], all_labs=labs, phlebotomists=riders, financials=financials, user_role=session.get('role', 'admin'), warehouse_stock=warehouse, partners=partners, all_tests=all_tests)

@app.route('/admin/walk-in', methods=['POST'])
@role_required(['receptionist'])
def admin_walk_in():
    p_name, phone, age, gender = request.form.get('patient_name'), request.form.get('phone'), request.form.get('age'), request.form.get('gender')
    address = request.form.get('address', '')
    total, test_id, lab_id = request.form.get('total_amount', 0), request.form.get('test_id'), request.form.get('lab_id')
    ref_by = request.form.get('referred_by', 'Self').strip() or "Self"
    
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE phone = %s", (phone,))
        user = cursor.fetchone()
        if user: user_id = user[0]
        else:
            cursor.execute("INSERT INTO users (name, phone, email) VALUES (%s, %s, %s) RETURNING id", (p_name, phone, f"walkin_{phone}@caredrop.local"))
            user_id = cursor.fetchone()[0]
            cursor.execute("UPDATE users SET patient_uid = %s WHERE id = %s", (f"CD-PAT-{1000 + user_id}", user_id))
            
        cursor.execute("""
            INSERT INTO orders (user_id, patient_name, age, gender, address, collection_date, time_slot, total_amount, balance_amount, status, referred_by) 
            VALUES (%s, %s, %s, %s, %s, %s, 'Immediate', %s, %s, 'Received in Lab', %s) RETURNING id
        """, (user_id, p_name, age, gender, address, datetime.today().strftime('%Y-%m-%d'), total, total, ref_by))
        order_id = cursor.fetchone()[0]
        
        acc_id = f"CD-ACC-{datetime.today().strftime('%y%m')}-{order_id:04d}"
        ord_ref = f"ORD-{datetime.today().strftime('%y%m')}-{order_id:04d}"
        cursor.execute("UPDATE orders SET order_ref = %s, accession_id = %s WHERE id = %s", (ord_ref, acc_id, order_id))
        cursor.execute("INSERT INTO order_items (order_id, test_id, lab_id, price, item_type) VALUES (%s, %s, %s, %s, 'test')", (order_id, test_id, lab_id, total))
        conn.commit()
        
        # Log the creation
        log_audit(order_id, "Order Created", "None", "Received in Lab", f"Walk-In/B2B Registration via {ref_by}")
        
    except Exception as e: conn.rollback(); print(e)
    finally: conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/assign-order', methods=['POST'])
@role_required(['receptionist'])
def assign_order():
    o_id = request.form.get('order_id')
    r_id = request.form.get('phlebotomist_id')
    safe_execute("UPDATE orders SET phlebotomist_id=%s, status='Dispatched' WHERE id=%s", (r_id, o_id))
    log_audit(o_id, "Rider Dispatched", "Pending", "Dispatched", f"Assigned to rider ID {r_id}")
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/print-barcode/<int:order_id>')
@role_required(['admin', 'receptionist', 'technician'])
def print_barcode(order_id):
    conn = get_db()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT patient_name, age, gender, accession_id, collection_date FROM orders WHERE id = %s", (order_id,))
        o = cursor.fetchone()
        log_audit(order_id, "Barcode Printed", "N/A", "N/A", "Tube label generated")
        html = f"""
        <html><body onload="window.print()" style="font-family:monospace; margin:0; padding:10px; width:200px; border:1px solid #000; text-align:center;">
        <h3 style="margin:0 0 5px 0;">CareDrop LIMS</h3>
        <p style="margin:0; font-size:12px; font-weight:bold;">{o['patient_name'][:15].upper()} ({o['age']}{o['gender'][0].upper()})</p>
        <p style="margin:5px 0; font-size:14px; font-weight:900; letter-spacing:1px;">*{o['accession_id']}*</p>
        <p style="margin:0; font-size:10px;">Acc: {o['accession_id']}</p>
        <p style="margin:0; font-size:10px;">Date: {o['collection_date']}</p>
        </body></html>
        """
        return html
    finally: conn.close()

# --- EXCEPTION WORKFLOW: SAMPLE REJECTION ---
@app.route('/admin/reject-sample', methods=['POST'])
@role_required(['technician', 'pathologist', 'admin'])
def reject_sample():
    order_id = request.form.get('order_id')
    reason = request.form.get('reason')
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

@app.route('/admin/request-recollection', methods=['POST'])
@role_required(['receptionist', 'admin'])
def request_recollection():
    order_id = request.form.get('order_id')
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE orders SET status = 'Pending', phlebotomist_id = NULL WHERE id = %s", (order_id,))
        conn.commit()
        log_audit(order_id, "Recollection Requested", "Sample Rejected", "Pending", "Reset for new rider dispatch")
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
        cursor.execute("UPDATE inventory SET current_stock = current_stock - 1 WHERE category IN ('Consumable', 'Reagent') AND current_stock > 0")
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
            
            cursor.execute("""
                SELECT r.result_value, tp.parameter_name, tp.unit, tp.reference_range, tp.methodology, tp.interpretation, t.name as test_name, c.name as cat_name 
                FROM order_results r JOIN test_parameters tp ON r.parameter_id = tp.id 
                JOIN tests t ON tp.test_id = t.id LEFT JOIN test_categories c ON t.category_id = c.id 
                WHERE r.order_id = %s
            """, (order_id,))
            results_db = cursor.fetchall()
            
            results_data = [{'cat': p['cat_name'] or 'PATHOLOGY', 'test': p['test_name'], 'param': p['parameter_name'], 'val': p['result_value'], 'unit': p['unit'], 'ref': p['reference_range'], 'method': p['methodology'], 'interpretation': p['interpretation']} for p in results_db]
                
            cursor.execute("SELECT l.* FROM order_items oi JOIN labs l ON oi.lab_id = l.id WHERE oi.order_id = %s LIMIT 1", (order_id,))
            lab_data = cursor.fetchone()
            
            pdf_bytes = generate_medical_report(order_id, order, results_data, lab_data)
            filename = f"CareDrop_Report_{order['patient_uid']}.pdf"
            
            cursor.execute("UPDATE orders SET report_file = %s, report_filename = %s, status = 'Completed', verified_by = 'Dr. Abdul Sameer Qureshi (Pathologist)' WHERE id = %s", (psycopg2.Binary(pdf_bytes), filename, order_id))
            conn.commit()
            
            log_audit(order_id, "Report Released", "Pending Verification", "Completed", "Digitally signed by Pathologist")
            threading.Thread(target=dispatch_notifications_bg, args=(order['email'], order['phone'], order['patient_name'], order['order_ref'], pdf_bytes, filename)).start()
            return redirect(url_for('admin_dashboard'))
        
        cursor.execute("SELECT o.*, u.patient_uid FROM orders o JOIN users u ON o.user_id = u.id WHERE o.id = %s", (order_id,))
        order = cursor.fetchone()
        cursor.execute("SELECT r.result_value, tp.parameter_name, tp.unit, tp.reference_range FROM order_results r JOIN test_parameters tp ON r.parameter_id = tp.id WHERE r.order_id = %s", (order_id,))
        results = cursor.fetchall()
        
        html = f"""
        <html><body style="font-family:'Plus Jakarta Sans', sans-serif; background:#F1F5F9; padding:40px;">
        <div style="max-width:800px; margin:auto; background:white; padding:30px; border-radius:12px; box-shadow:0 4px 15px rgba(0,0,0,0.05);">
        <h2 style="color:#0F172A;">Verify Clinical Results</h2>
        <p style="color:#64748B;">Patient: <b>{order['patient_name'].title()}</b> | Accession: <b>{order['accession_id']}</b></p>
        <table style="width:100%; border-collapse:collapse; margin-bottom:30px; border:1px solid #E2E8F0;">
        <tr style="background:#F8FAFC; text-align:left; color:#475569; font-size:12px; text-transform:uppercase;">
            <th style="padding:15px; border-bottom:1px solid #E2E8F0;">Parameter</th>
            <th style="padding:15px; border-bottom:1px solid #E2E8F0;">Result Entered</th>
            <th style="padding:15px; border-bottom:1px solid #E2E8F0;">Reference Range</th>
        </tr>
        """
        for r in results:
            html += f"<tr><td style='padding:15px; border-bottom:1px solid #F1F5F9; font-weight:600;'>{r['parameter_name']}</td><td style='padding:15px; border-bottom:1px solid #F1F5F9; font-weight:800; color:#0D9488;'>{r['result_value']} <span style='font-size:11px; color:#94A3B8;'>{r['unit']}</span></td><td style='padding:15px; border-bottom:1px solid #F1F5F9; color:#64748B;'>{r['reference_range']}</td></tr>"
        html += f"""
        </table>
        <form method="POST" style="display:flex; gap:15px; align-items:center;">
        <button type="submit" style="background:#16A34A; color:white; padding:12px 24px; border:none; border-radius:6px; font-weight:bold; cursor:pointer; font-size:14px;">Approve & Digitally Sign Report</button>
        </form>
        
        <form action="/admin/reject-sample" method="POST" style="margin-top:20px; border-top:1px solid #E2E8F0; padding-top:20px;">
            <input type="hidden" name="order_id" value="{order_id}">
            <p style="font-size:12px; color:#DC2626; font-weight:bold; margin-bottom:10px;">REJECT SAMPLE (Exception Workflow)</p>
            <select name="reason" style="padding:8px; border-radius:4px; border:1px solid #CBD5E1; margin-right:10px;" required>
                <option value="">Select Reason...</option>
                <option value="Values Not Correlating">Values Not Correlating (Require Re-run)</option>
                <option value="Sample Hemolyzed">Sample Hemolyzed</option>
            </select>
            <button type="submit" style="background:#DC2626; color:white; padding:8px 16px; border:none; border-radius:6px; font-weight:bold; cursor:pointer;">Reject & Send Back</button>
        </form>
        </div></body></html>
        """
        return html
    finally: conn.close()

if __name__ == '__main__': app.run(debug=True, port=5000)
