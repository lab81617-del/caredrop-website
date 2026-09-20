import os
import random
import csv
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
import sqlite3
from database import get_db_connection, init_db

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'caredrop_enterprise_secret_key_2026')

# Initialize DB and ensure email column exists
init_db()
try:
    conn = get_db_connection()
    conn.execute("ALTER TABLE orders ADD COLUMN email TEXT")
    conn.commit()
    conn.close()
except sqlite3.OperationalError:
    pass # Column already exists

# -------------------------------------------------------------
# EMAIL ENGINE
# -------------------------------------------------------------
def send_email(to_email, subject, body):
    sender = os.environ.get('MAIL_USERNAME', 'caredrop.ynr@gmail.com')
    password = os.environ.get('MAIL_PASSWORD', '') 
    
    if not password or not to_email:
        print(f"Skipping email to {to_email} (Credentials or Email missing)")
        return

    try:
        msg = MIMEMultipart()
        msg['From'] = f"CareDrop Diagnostics <{sender}>"
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'html'))
        
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender, password)
        server.send_message(msg)
        server.quit()
    except Exception as e:
        print(f"Email failed to send: {e}")

# -------------------------------------------------------------
# ACCESS CONTROL DECORATORS
# -------------------------------------------------------------
def hq_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('role') not in ['admin', 'rider']:
            return redirect(url_for('hq_login'))
        return f(*args, **kwargs)
    return decorated_function

def partner_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('role') != 'partner':
            return redirect(url_for('partner_login'))
        return f(*args, **kwargs)
    return decorated_function

# -------------------------------------------------------------
# AUTHENTICATION
# -------------------------------------------------------------
@app.route('/login', methods=['GET', 'POST'])
def patient_login():
    return render_template('auth_patient.html')

@app.route('/api/patient_login', methods=['POST'])
def api_patient_login():
    contact = request.form.get('contact')
    session['role'] = 'patient'
    session['patient_contact'] = contact
    return redirect(url_for('my_bookings'))

@app.route('/hq/login', methods=['GET', 'POST'])
def hq_login():
    if request.method == 'POST':
        password = request.form.get('password')
        if password == 'admin123':
            session['role'] = 'admin'
            return redirect(url_for('admin'))
        elif password == 'rider123':
            session['role'] = 'rider'
            return redirect(url_for('rider_dashboard'))
        flash("Invalid HQ Credentials")
    if os.path.exists('templates/auth_hq.html'):
        return render_template('auth_hq.html')
    return "HQ Login Missing"

@app.route('/partner/login', methods=['GET', 'POST'])
def partner_login():
    if request.method == 'POST':
        code = request.form.get('referral_code', '').upper()
        pwd = request.form.get('password')
        conn = get_db_connection()
        partner = conn.execute('SELECT * FROM partners WHERE referral_code = ? AND password = ?', (code, pwd)).fetchone()
        conn.close()
        if partner:
            session['role'] = 'partner'
            session['partner_id'] = partner['id']
            session['referral_code'] = partner['referral_code']
            return redirect(url_for('partner_dashboard'))
        flash("Invalid Clinic Credentials")
    if os.path.exists('templates/auth_partner.html'):
        return render_template('auth_partner.html')
    return "Partner Login Missing"

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# -------------------------------------------------------------
# PUBLIC SITE & CART
# -------------------------------------------------------------
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/tests')
def tests_catalogue():
    conn = get_db_connection()
    tests = conn.execute('SELECT * FROM tests WHERE is_active = 1 ORDER BY category, name ASC').fetchall()
    conn.close()
    return render_template('tests.html', tests=tests)

@app.route('/api/cart/add/<int:test_id>', methods=['POST'])
def add_to_cart(test_id):
    if 'cart' not in session: session['cart'] = []
    if test_id not in session['cart']:
        session['cart'].append(test_id)
        session.modified = True
    return jsonify({'status': 'success', 'total_items': len(session['cart'])})

@app.route('/api/cart/remove/<int:test_id>', methods=['POST'])
def remove_from_cart(test_id):
    if 'cart' in session and test_id in session['cart']:
        session['cart'].remove(test_id)
        session.modified = True
    return jsonify({'status': 'success', 'total_items': len(session.get('cart', []))})

# -------------------------------------------------------------
# CHECKOUT & BOOKING ENGINE
# -------------------------------------------------------------
@app.route('/checkout')
def checkout():
    cart_ids = session.get('cart', [])
    if not cart_ids: return redirect(url_for('tests_catalogue'))
    conn = get_db_connection()
    placeholders = ','.join('?' for _ in cart_ids)
    items = conn.execute(f'SELECT * FROM tests WHERE id IN ({placeholders})', cart_ids).fetchall()
    conn.close()
    total_amount = sum(t['price'] for t in items)
    return render_template('checkout.html', items=items, total=total_amount)

@app.route('/api/validate_promo', methods=['POST'])
def validate_promo():
    code = request.json.get('code', '').upper()
    total = float(request.json.get('total', 0))
    conn = get_db_connection()
    partner = conn.execute('SELECT * FROM partners WHERE referral_code = ?', (code,)).fetchone()
    conn.close()
    if partner:
        discount = round(total * 0.10)
        return jsonify({'valid': True, 'discount': discount, 'new_total': total - discount, 'partner': partner['clinic_name']})
    return jsonify({'valid': False})

@app.route('/book_test', methods=['POST'])
def book_test():
    full_name = request.form.get('full_name')
    phone = request.form.get('phone')
    email = request.form.get('email', '')
    address = request.form.get('address')
    referral_code = request.form.get('referral_code', '').upper()
    
    conn = get_db_connection()
    test_ids = []
    
    # Check if this is a web checkout (uses session cart) or POS checkout (uses form data)
    if 'cart' in session and session['cart'] and request.form.get('tests_requested') is None:
        cart_ids = session['cart']
        placeholders = ','.join('?' for _ in cart_ids)
        items = conn.execute(f'SELECT id, name, price, b2b_cost FROM tests WHERE id IN ({placeholders})', cart_ids).fetchall()
        test_ids = [i['id'] for i in items]
        tests_requested = ", ".join([i['name'] for i in items])
        gross_bill = sum(i['price'] for i in items)
        b2b_cost = sum(i['b2b_cost'] for i in items)
        session.pop('cart', None)
    else:
        # Admin POS Booking
        tests_requested = request.form.get('tests_requested')
        gross_bill = float(request.form.get('total_bill', 0))
        b2b_cost = gross_bill * 0.40 # Fallback margin calculation
        # Retrieve test IDs by matching names from the POS submission
        test_names = [name.strip() for name in tests_requested.split(',')]
        if test_names:
            placeholders = ','.join('?' for _ in test_names)
            items = conn.execute(f'SELECT id FROM tests WHERE name IN ({placeholders})', test_names).fetchall()
            test_ids = [i['id'] for i in items]
    
    discount_given = 0
    partner_commission = 0
    if referral_code:
        partner = conn.execute('SELECT * FROM partners WHERE referral_code = ?', (referral_code,)).fetchone()
        if partner:
            margin_pool = gross_bill * partner['margin_pool_pct'] 
            if session.get('role') not in ['partner', 'admin']:
                discount_given = round(gross_bill * 0.10)
            partner_commission = margin_pool - discount_given
            conn.execute('UPDATE partners SET wallet_balance = wallet_balance + ? WHERE referral_code = ?', (partner_commission, referral_code))

    total_bill = gross_bill - discount_given
    order_code = f"CD-{random.randint(1000, 9999)}"

    # Save Order
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO orders (order_code, full_name, phone, email, address, tests_requested, gross_bill, discount_given, total_bill, b2b_total_cost, referral_code, partner_commission)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    ''', (order_code, full_name, phone, email, address, tests_requested, gross_bill, discount_given, total_bill, b2b_cost, referral_code, partner_commission))
    
    order_id = cur.lastrowid
    
    # Generate empty LIMS parameter rows for this specific order
    if test_ids:
        placeholders = ','.join('?' for _ in test_ids)
        params = conn.execute(f'SELECT param_name, unit, ref_range FROM test_parameters WHERE test_id IN ({placeholders})', test_ids).fetchall()
        for p in params:
            cur.execute("INSERT INTO test_results (order_id, parameter_name, units, ref_interval) VALUES (?, ?, ?, ?)", (order_id, p['param_name'], p['unit'], p['ref_range']))
    
    conn.commit()
    conn.close()
    
    # Trigger Emails
    send_email(email, "CareDrop Booking Confirmed", f"<h3>Hello {full_name},</h3><p>Your test booking ({tests_requested}) is confirmed. Our rider will contact you shortly.</p>")
    send_email(os.environ.get('MAIL_USERNAME', ''), f"New Order: {order_code}", f"<p>Order {order_code} received from {full_name}. Value: Rs {total_bill}</p>")
    
    if session.get('role') == 'admin': return redirect(url_for('admin'))
    if session.get('role') == 'partner': return redirect(url_for('partner_dashboard'))
    
    session['role'] = 'patient'
    session['patient_contact'] = phone
    return redirect(url_for('my_bookings'))

# -------------------------------------------------------------
# ADMIN DASHBOARD (MULTI-PAGE ARCHITECTURE)
# -------------------------------------------------------------
@app.route('/admin')
@hq_required
def admin():
    conn = get_db_connection()
    orders = conn.execute('SELECT * FROM orders ORDER BY id DESC').fetchall()
    conn.close()
    return render_template('admin.html', orders=orders)

@app.route('/admin/pos')
@hq_required
def admin_pos():
    conn = get_db_connection()
    tests = conn.execute('SELECT * FROM tests WHERE is_active = 1 ORDER BY name ASC').fetchall()
    conn.close()
    return render_template('admin_pos.html', tests=tests)

@app.route('/admin/partners')
@hq_required
def admin_partners():
    conn = get_db_connection()
    partners = conn.execute('SELECT * FROM partners ORDER BY id DESC').fetchall()
    conn.close()
    return render_template('admin_partners.html', partners=partners)

@app.route('/admin/catalog')
@hq_required
def admin_catalog():
    conn = get_db_connection()
    tests = conn.execute('SELECT * FROM tests WHERE is_active = 1 ORDER BY id DESC').fetchall()
    test_params = conn.execute('SELECT * FROM test_parameters').fetchall()
    conn.close()
    return render_template('admin_catalog.html', tests=tests, test_params=test_params)

# --- ADMIN ACTIONS ---
@app.route('/admin/assign_rider/<int:order_id>', methods=['POST'])
@hq_required
def admin_assign_rider(order_id):
    rider_name = request.form.get('rider_name')
    conn = get_db_connection()
    conn.execute("UPDATE orders SET assigned_rider = ? WHERE id = ?", (rider_name, order_id))
    conn.commit()
    conn.close()
    return redirect(url_for('admin'))

@app.route('/admin/add_partner', methods=['POST'])
@hq_required
def admin_add_partner():
    name = request.form.get('name')
    clinic_name = request.form.get('clinic_name')
    referral_code = request.form.get('referral_code', '').upper()
    phone = request.form.get('phone')
    password = request.form.get('password')
    margin = float(request.form.get('margin_pool_pct', 30)) / 100.0
    conn = get_db_connection()
    try:
        conn.execute('INSERT INTO partners (name, clinic_name, referral_code, phone, password, margin_pool_pct) VALUES (?, ?, ?, ?, ?, ?)', (name, clinic_name, referral_code, phone, password, margin))
        conn.commit()
    except Exception: pass
    conn.close()
    return redirect(url_for('admin_partners'))

@app.route('/admin/delete_partner/<int:p_id>')
@hq_required
def admin_delete_partner(p_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM partners WHERE id = ?', (p_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_partners'))

@app.route('/admin/add_test', methods=['POST'])
@hq_required
def admin_add_test():
    name = request.form.get('name')
    b2b_cost = float(request.form.get('b2b_cost') or 0)
    price = float(request.form.get('retail_price') or 0)
    conn = get_db_connection()
    conn.execute('INSERT INTO tests (name, category, b2b_cost, price) VALUES (?, ?, ?, ?)', (name, 'General', b2b_cost, price))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_catalog'))

@app.route('/admin/delete_test/<int:t_id>')
@hq_required
def admin_delete_test(t_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM tests WHERE id = ?', (t_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_catalog'))

@app.route('/admin/add_parameter', methods=['POST'])
@hq_required
def admin_add_parameter():
    test_id = request.form.get('test_id')
    param_name = request.form.get('param_name')
    unit = request.form.get('unit')
    ref_range = request.form.get('ref_range')
    conn = get_db_connection()
    conn.execute("INSERT INTO test_parameters (test_id, param_name, unit, ref_range) VALUES (?, ?, ?, ?)", (test_id, param_name, unit, ref_range))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_catalog'))

@app.route('/admin/bulk_upload_tests', methods=['POST'])
@hq_required
def admin_bulk_upload():
    file = request.files.get('file')
    if file and file.filename.endswith('.csv'):
        conn = get_db_connection()
        try:
            stream = file.stream.read().decode("utf-8").splitlines()
            reader = csv.reader(stream)
            next(reader, None) # skip header
            for row in reader:
                if len(row) >= 4:
                    conn.execute('INSERT INTO tests (name, category, b2b_cost, price) VALUES (?, ?, ?, ?)', (row[0], row[1], float(row[2]), float(row[3])))
            conn.commit()
        except Exception as e:
            print(e)
        finally:
            conn.close()
    return redirect(url_for('admin_catalog'))

# -------------------------------------------------------------
# RIDER APP LOGISTICS
# -------------------------------------------------------------
@app.route('/rider')
@app.route('/rider_dashboard')
@hq_required
def rider_dashboard():
    conn = get_db_connection()
    orders = conn.execute("SELECT * FROM orders WHERE status != 'Completed' ORDER BY id DESC").fetchall()
    cash_collected = conn.execute("SELECT COALESCE(SUM(total_bill), 0) FROM orders WHERE is_paid = 1 AND payment_mode = 'Cash'").fetchone()[0]
    conn.close()
    return render_template('rider_dashboard.html', orders=orders, cash_collected=cash_collected)

@app.route('/rider/scan')
@hq_required
def rider_scan():
    conn = get_db_connection()
    pending = conn.execute("SELECT * FROM orders WHERE status = 'Pending' ORDER BY id ASC").fetchall()
    conn.close()
    if os.path.exists('templates/rider_scan.html'): return render_template('rider_scan.html', orders=pending)
    return redirect(url_for('rider_dashboard'))

@app.route('/api/rider/complete', methods=['POST'])
@hq_required
def rider_complete():
    order_id = request.form.get('order_id')
    barcode = request.form.get('barcode')
    temp_log = request.form.get('temperature')
    payment_mode = request.form.get('payment_mode')

    conn = get_db_connection()
    conn.execute("UPDATE orders SET barcode=?, temp_log=?, payment_mode=?, status='Sample Collected', is_paid=1 WHERE id=?", (barcode, temp_log, payment_mode, order_id))
    order = conn.execute("SELECT email, full_name, tests_requested FROM orders WHERE id=?", (order_id,)).fetchone()
    conn.commit()
    conn.close()
    
    send_email(order['email'], "Sample Collected", f"<h3>Hello {order['full_name']},</h3><p>Your sample for {order['tests_requested']} has been successfully collected and secured with barcode {barcode}. It is en route to our NABL partner lab.</p>")
    return redirect(url_for('rider_dashboard'))

# -------------------------------------------------------------
# LIMS REPORT ENGINE
# -------------------------------------------------------------
@app.route('/lims_report/<int:order_id>')
def lims_report(order_id):
    conn = get_db_connection()
    order = conn.execute('SELECT * FROM orders WHERE id = ?', (order_id,)).fetchone()
    results = conn.execute('SELECT * FROM test_results WHERE order_id = ?', (order_id,)).fetchall()
    conn.close()
    return render_template('lims_report.html', order=order, results=results)

@app.route('/api/lab/submit_results/<int:order_id>', methods=['POST'])
@hq_required
def submit_lab_results(order_id):
    conn = get_db_connection()
    for key, value in request.form.items():
        if key.startswith('param_'):
            result_id = key.split('_')[1]
            conn.execute("UPDATE test_results SET observed_value = ? WHERE id = ?", (value, result_id))
    
    conn.execute("UPDATE orders SET status = 'Completed' WHERE id = ?", (order_id,))
    order = conn.execute("SELECT email, full_name FROM orders WHERE id = ?", (order_id,)).fetchone()
    conn.commit()
    conn.close()
    
    report_link = url_for('patient_login', _external=True)
    send_email(order['email'], "Your Report is Ready", f"<h3>Hello {order['full_name']},</h3><p>Your diagnostic report is ready. Please log into your portal to download the securely signed PDF: <a href='{report_link}'>Download Report</a></p>")
    return redirect(f'/lims_report/{order_id}')

# -------------------------------------------------------------
# DASHBOARDS
# -------------------------------------------------------------
@app.route('/my_bookings')
def my_bookings():
    if session.get('role') != 'patient': return redirect(url_for('patient_login'))
    contact = session.get('patient_contact')
    conn = get_db_connection()
    bookings = conn.execute('SELECT * FROM orders WHERE phone = ? OR email = ? ORDER BY id DESC', (contact, contact)).fetchall()
    conn.close()
    return render_template('my_bookings.html', bookings=bookings)

@app.route('/partner/dashboard')
@partner_required
def partner_dashboard():
    code = session.get('referral_code')
    conn = get_db_connection()
    partner = conn.execute('SELECT * FROM partners WHERE referral_code = ?', (code,)).fetchone()
    orders = conn.execute('SELECT * FROM orders WHERE referral_code = ? ORDER BY id DESC', (code,)).fetchall()
    conn.close()
    return render_template('partner_dashboard.html', partner=partner, orders=orders)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
