import os
import random
import smtplib
import base64
import io
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, send_file
from database import get_db_connection, init_db

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'caredrop_enterprise_secret_key_2026')
init_db()

# --- EMAIL ENGINE ---
def send_email(to_email, subject, body):
    sender = os.environ.get('MAIL_USERNAME')
    password = os.environ.get('MAIL_PASSWORD')
    if not password or not to_email: return
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
        print(f"Email failed: {e}")

# --- ACCESS DECORATORS ---
def hq_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('role') not in ['admin', 'rider', 'reception']: return redirect(url_for('hq_login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_only(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('role') != 'admin': return redirect(url_for('hq_login'))
        return f(*args, **kwargs)
    return decorated_function

def partner_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('role') != 'partner': return redirect(url_for('partner_login'))
        return f(*args, **kwargs)
    return decorated_function

# --- EMAIL OTP LOGIN ---
@app.route('/login', methods=['GET', 'POST'])
def patient_login(): return render_template('auth_patient.html')

@app.route('/api/send_login_otp', methods=['POST'])
def send_login_otp():
    email = request.form.get('email').lower().strip()
    otp = str(random.randint(1000, 9999))
    session['login_otp'] = otp
    session['login_email'] = email
    send_email(email, "CareDrop Login Code", f"<h2>Your login code is: {otp}</h2>")
    return jsonify({'status': 'success'})

@app.route('/api/verify_login_otp', methods=['POST'])
def verify_login_otp():
    user_otp = request.form.get('otp')
    if user_otp == session.get('login_otp'):
        session['role'] = 'patient'
        session['patient_email'] = session.get('login_email')
        return redirect(url_for('my_bookings'))
    flash("Invalid OTP")
    return redirect(url_for('patient_login'))

# --- HQ / PARTNER LOGIN ---
@app.route('/hq/login', methods=['GET', 'POST'])
def hq_login():
    if request.method == 'POST':
        pwd = request.form.get('password')
        if pwd == os.environ.get('ADMIN_PASSWORD', 'admin123'): session['role'] = 'admin'
        elif pwd == os.environ.get('RECEPTION_PASSWORD', 'reception123'): session['role'] = 'reception'
        elif pwd == 'rider123': session['role'] = 'rider'
        else:
            flash("Invalid Clearance")
            return redirect(url_for('hq_login'))
        if session['role'] in ['admin', 'reception']: return redirect(url_for('admin'))
        if session['role'] == 'rider': return redirect(url_for('rider_dashboard'))
    return render_template('auth_hq.html')

@app.route('/partner/login', methods=['GET', 'POST'])
def partner_login():
    if request.method == 'POST':
        code = request.form.get('referral_code', '').upper()
        pwd = request.form.get('password')
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('SELECT * FROM partners WHERE referral_code = %s AND password = %s', (code, pwd))
        partner = cur.fetchone()
        cur.close()
        conn.close()
        if partner:
            session['role'] = 'partner'
            session['partner_id'] = partner['id']
            session['referral_code'] = partner['referral_code']
            return redirect(url_for('partner_dashboard'))
        flash("Invalid Clinic Credentials")
    return render_template('auth_partner.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# --- PUBLIC & CART ---
@app.route('/')
def index(): return render_template('index.html')

@app.route('/tests')
def tests_catalogue():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM tests WHERE is_active = TRUE ORDER BY category, name ASC')
    tests = cur.fetchall()
    cur.close()
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

@app.route('/checkout')
def checkout():
    cart_ids = session.get('cart', [])
    if not cart_ids: return redirect(url_for('tests_catalogue'))
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM tests WHERE id = ANY(%s)', (cart_ids,))
    items = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('checkout.html', items=items, total=sum(t['price'] for t in items))

@app.route('/api/validate_promo', methods=['POST'])
def validate_promo():
    code = request.json.get('code', '').upper()
    total = float(request.json.get('total', 0))
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM partners WHERE referral_code = %s', (code,))
    partner = cur.fetchone()
    cur.close()
    conn.close()
    if partner:
        discount = round(total * 0.10)
        return jsonify({'valid': True, 'discount': discount, 'new_total': total - discount, 'partner': partner['clinic_name']})
    return jsonify({'valid': False})

# --- BOOKING LOGIC ---
@app.route('/api/initiate_booking', methods=['POST'])
def initiate_booking():
    session['pending_order'] = {
        'full_name': request.form.get('full_name'),
        'age': request.form.get('age'),
        'gender': request.form.get('gender'),
        'phone': request.form.get('phone'),
        'email': request.form.get('email').lower().strip(),
        'address': request.form.get('address'),
        'time_slot': request.form.get('time_slot'),
        'referral_code': request.form.get('referral_code', '').upper()
    }
    otp = str(random.randint(1000, 9999))
    session['booking_otp'] = otp
    send_email(session['pending_order']['email'], "Verify Your CareDrop Booking", f"<h2>Your booking OTP is: {otp}</h2>")
    return jsonify({'status': 'otp_sent'})

@app.route('/api/confirm_booking', methods=['POST'])
def confirm_booking():
    user_otp = request.form.get('otp')
    if user_otp != session.get('booking_otp'): return jsonify({'status': 'error', 'msg': 'Invalid OTP'})
        
    o_data = session.get('pending_order')
    conn = get_db_connection()
    cur = conn.cursor()
    
    cart_ids = session.get('cart', [])
    cur.execute('SELECT id, name, price FROM tests WHERE id = ANY(%s)', (cart_ids,))
    items = cur.fetchall()
    test_ids = [i['id'] for i in items]
    tests_requested = ", ".join([i['name'] for i in items])
    gross_bill = sum(i['price'] for i in items)
    
    discount_given, partner_commission = 0, 0
    if o_data['referral_code']:
        cur.execute('SELECT * FROM partners WHERE referral_code = %s', (o_data['referral_code'],))
        partner = cur.fetchone()
        if partner:
            margin_pool = gross_bill * partner['margin_pool_pct']
            discount_given = round(gross_bill * 0.10) # Fixed 10% for patient web checkout
            partner_commission = margin_pool - discount_given

    total_bill = gross_bill - discount_given
    order_code = f"CD-{random.randint(1000, 9999)}"

    cur.execute('''
        INSERT INTO orders (order_code, full_name, age, gender, phone, email, address, time_slot, tests_requested, gross_bill, discount_given, total_bill, referral_code, partner_commission)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id;
    ''', (order_code, o_data['full_name'], o_data['age'], o_data['gender'], o_data['phone'], o_data['email'], o_data['address'], o_data['time_slot'], tests_requested, gross_bill, discount_given, total_bill, o_data['referral_code'], partner_commission))
    
    order_id = cur.fetchone()['id']
    
    if test_ids:
        cur.execute('SELECT param_name, unit, ref_range FROM test_parameters WHERE test_id = ANY(%s)', (test_ids,))
        for p in cur.fetchall():
            cur.execute("INSERT INTO test_results (order_id, parameter_name, units, ref_interval) VALUES (%s, %s, %s, %s)", (order_id, p['param_name'], p['unit'], p['ref_range']))
    
    conn.commit()
    cur.close()
    conn.close()
    
    session.pop('cart', None)
    session.pop('booking_otp', None)
    session.pop('pending_order', None)
    session['role'] = 'patient'
    session['patient_email'] = o_data['email']
    
    send_email(o_data['email'], "CareDrop Booking Confirmed", f"<p>Your test ({tests_requested}) is booked for {o_data['time_slot']}. Order ID: {order_code}</p>")
    send_email(os.environ.get('MAIL_USERNAME'), f"New Order: {order_code}", f"<p>Order {order_code} received from {o_data['full_name']} for {o_data['time_slot']}.</p>")
    return jsonify({'status': 'success', 'redirect': '/my_bookings'})

# --- POS MANUAL BOOKING (RECEPTION/ADMIN) ---
@app.route('/pos_book_test', methods=['POST'])
@hq_required
def pos_book_test():
    full_name = request.form.get('full_name')
    age = request.form.get('age')
    gender = request.form.get('gender')
    phone = request.form.get('phone')
    email = request.form.get('email', '')
    address = request.form.get('address')
    time_slot = request.form.get('time_slot')
    
    tests_requested = request.form.get('tests_requested')
    gross_bill = float(request.form.get('total_bill', 0))
    order_code = f"CD-{random.randint(1000, 9999)}"
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO orders (order_code, full_name, age, gender, phone, email, address, time_slot, tests_requested, gross_bill, total_bill)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id;
    ''', (order_code, full_name, age, gender, phone, email, address, time_slot, tests_requested, gross_bill, gross_bill))
    
    order_id = cur.fetchone()['id']
    test_names = [n.strip() for n in tests_requested.split(',')]
    cur.execute('SELECT id FROM tests WHERE name = ANY(%s)', (test_names,))
    test_ids = [i['id'] for i in cur.fetchall()]
    
    if test_ids:
        cur.execute('SELECT param_name, unit, ref_range FROM test_parameters WHERE test_id = ANY(%s)', (test_ids,))
        for p in cur.fetchall():
            cur.execute("INSERT INTO test_results (order_id, parameter_name, units, ref_interval) VALUES (%s, %s, %s, %s)", (order_id, p['param_name'], p['unit'], p['ref_range']))
            
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('admin_pos'))

# --- PARTNER PORTAL & CUSTOM DISCOUNT BOOKING ---
@app.route('/partner/dashboard')
@partner_required
def partner_dashboard():
    code = session.get('referral_code')
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM partners WHERE referral_code = %s', (code,))
    partner = cur.fetchone()
    cur.execute('SELECT * FROM orders WHERE referral_code = %s ORDER BY id DESC', (code,))
    orders = cur.fetchall()
    cur.execute('SELECT * FROM tests WHERE is_active = TRUE ORDER BY name ASC')
    tests = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('partner_dashboard.html', partner=partner, orders=orders, tests=tests)

@app.route('/partner/book_test', methods=['POST'])
@partner_required
def partner_book_test():
    full_name = request.form.get('full_name')
    age = request.form.get('age')
    gender = request.form.get('gender')
    phone = request.form.get('phone')
    email = request.form.get('email', '')
    address = request.form.get('address')
    time_slot = request.form.get('time_slot')
    discount_pct = float(request.form.get('discount_pct', 0)) # Discount given to patient
    tests_requested = request.form.get('tests_requested')
    gross_bill = float(request.form.get('total_bill', 0))
    
    code = session.get('referral_code')
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT margin_pool_pct FROM partners WHERE referral_code = %s', (code,))
    partner = cur.fetchone()
    
    margin_pool_pct = partner['margin_pool_pct'] * 100
    if discount_pct > margin_pool_pct: discount_pct = margin_pool_pct # Prevent giving more than they have
    
    discount_given = round(gross_bill * (discount_pct / 100))
    total_bill = gross_bill - discount_given
    partner_commission = round(gross_bill * ((margin_pool_pct - discount_pct) / 100))
    order_code = f"CD-{random.randint(1000, 9999)}"

    cur.execute('''
        INSERT INTO orders (order_code, full_name, age, gender, phone, email, address, time_slot, tests_requested, gross_bill, discount_given, total_bill, referral_code, partner_commission)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id;
    ''', (order_code, full_name, age, gender, phone, email, address, time_slot, tests_requested, gross_bill, discount_given, total_bill, code, partner_commission))
    
    # Add to partner wallet immediately
    cur.execute("UPDATE partners SET wallet_balance = wallet_balance + %s WHERE referral_code = %s", (partner_commission, code))
    conn.commit()
    cur.close()
    conn.close()
    
    if email:
        send_email(email, "CareDrop Booking Confirmed", f"<p>Your test is booked. Order ID: {order_code}. Total Payable: Rs {total_bill}.</p>")
    send_email(os.environ.get('MAIL_USERNAME'), f"Partner Order: {order_code}", f"<p>Partner {code} booked an order. Value: Rs {total_bill}</p>")
    return redirect(url_for('partner_dashboard'))

# --- RECEPTION / ADMIN PORTALS ---
@app.route('/admin')
@hq_required
def admin():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM orders ORDER BY id DESC')
    orders = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('admin.html', orders=orders, role=session.get('role'))

@app.route('/admin/pos')
@hq_required
def admin_pos():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM tests WHERE is_active = TRUE ORDER BY name ASC')
    tests = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('admin_pos.html', tests=tests)

@app.route('/admin/partners')
@admin_only
def admin_partners():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM partners ORDER BY id DESC')
    partners = cur.fetchall()
    cur.execute('SELECT partner_commission, referral_code FROM orders WHERE is_commission_paid = FALSE AND partner_commission > 0 AND status = %s', ('Completed',))
    unpaid_orders = cur.fetchall()
    
    ledgers = {}
    for p in partners: ledgers[p['referral_code']] = {'details': p, 'unpaid': 0}
    for u in unpaid_orders:
        if u['referral_code'] in ledgers: ledgers[u['referral_code']]['unpaid'] += u['partner_commission']
        
    cur.close()
    conn.close()
    return render_template('admin_partners.html', ledgers=ledgers.values())

@app.route('/admin/mark_commissions_paid/<referral_code>')
@admin_only
def mark_commissions_paid(referral_code):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE orders SET is_commission_paid = TRUE WHERE referral_code = %s AND status = 'Completed'", (referral_code,))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('admin_partners'))

# --- DIRECT PDF UPLOAD ---
@app.route('/admin/upload_pdf/<int:order_id>', methods=['POST'])
@hq_required
def upload_pdf(order_id):
    file = request.files.get('pdf_file')
    if file and file.filename.endswith('.pdf'):
        # Convert PDF to Base64 Text to survive Render resets
        encoded_pdf = base64.b64encode(file.read()).decode('utf-8')
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE orders SET uploaded_pdf = %s, status = 'Completed', completed_at = CURRENT_TIMESTAMP WHERE id = %s", (encoded_pdf, order_id))
        cur.execute("SELECT email, full_name FROM orders WHERE id = %s", (order_id,))
        order = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        
        report_link = url_for('patient_login', _external=True)
        send_email(order['email'], "Your Report is Ready", f"<h3>Hello {order['full_name']},</h3><p>Your diagnostic PDF report is ready for download: <a href='{report_link}'>Download Report</a></p>")
    return redirect(url_for('admin'))

@app.route('/download_pdf/<int:order_id>')
def download_pdf(order_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT uploaded_pdf, order_code FROM orders WHERE id = %s", (order_id,))
    order = cur.fetchone()
    cur.close()
    conn.close()
    
    if order and order['uploaded_pdf']:
        pdf_bytes = base64.b64decode(order['uploaded_pdf'])
        return send_file(io.BytesIO(pdf_bytes), download_name=f"{order['order_code']}_Report.pdf", mimetype='application/pdf')
    return "PDF Not Found", 404

# --- LIMS RESULTS & REOPEN ---
@app.route('/api/lab/submit_results/<int:order_id>', methods=['POST'])
@hq_required
def submit_lab_results(order_id):
    conn = get_db_connection()
    cur = conn.cursor()
    for key, value in request.form.items():
        if key.startswith('param_'):
            result_id = key.split('_')[1]
            cur.execute("UPDATE test_results SET observed_value = %s WHERE id = %s", (value, result_id))
    
    cur.execute("UPDATE orders SET status = 'Completed', completed_at = CURRENT_TIMESTAMP WHERE id = %s", (order_id,))
    cur.execute("SELECT email, full_name FROM orders WHERE id = %s", (order_id,))
    order = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    
    report_link = url_for('patient_login', _external=True)
    send_email(order['email'], "Your Report is Ready", f"<h3>Hello {order['full_name']},</h3><p>Your diagnostic PDF report is ready for download: <a href='{report_link}'>Download Report</a></p>")
    return redirect(f'/lims_report/{order_id}')

@app.route('/admin/reopen_order/<int:order_id>')
@hq_required
def reopen_order(order_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE orders SET status = 'Sample Collected', completed_at = NULL, uploaded_pdf = NULL WHERE id = %s", (order_id,))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('admin'))

@app.route('/lims_report/<int:order_id>')
def lims_report(order_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM orders WHERE id = %s', (order_id,))
    order = cur.fetchone()
    
    # If a direct PDF was uploaded, skip the LIMS renderer and download it
    if order['uploaded_pdf']:
        cur.close()
        conn.close()
        return redirect(url_for('download_pdf', order_id=order_id))
        
    cur.execute('SELECT * FROM test_results WHERE order_id = %s', (order_id,))
    results = cur.fetchall()
    cur.close()
    conn.close(
