import os
import random
import base64
import io
import requests
import traceback
import smtplib
import threading
import json
import csv
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, send_file
from database import get_db_connection, init_db

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'caredrop_enterprise_secret_key_2026')

# ==========================================
# 0. INITIALIZATION & SETTINGS
# ==========================================
try:
    init_db()
except Exception as e:
    print(f"Startup DB Init Failed: {e}")

@app.context_processor
def inject_settings():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('SELECT * FROM site_settings WHERE id = 1')
        settings = cur.fetchone()
        cur.close()
        conn.close()
        if not settings:
            settings = {
                'phone': '+91 9485978790',
                'email': 'caredrop.ynr@gmail.com',
                'address': 'Shop No 434 L, Near Hospital, Sarojini Colony, Yamuna Nagar 135001'
            }
        return dict(site_settings=settings)
    except Exception:
        return dict(site_settings={
            'phone': '+91 9485978790',
            'email': 'caredrop.ynr@gmail.com',
            'address': 'Shop No 434 L, Near Hospital, Sarojini Colony, Yamuna Nagar 135001'
        })

# ==========================================
# 1. NOTIFICATION ENGINE
# ==========================================
def send_email(to_email, subject, body):
    brevo_key = os.environ.get('BREVO_API_KEY')
    sender_email = os.environ.get('MAIL_USERNAME', 'ihcdiagnostics.ynr@gmail.com')
    
    if brevo_key:
        try:
            url = "https://api.brevo.com/v3/smtp/email"
            payload = {
                "sender": {"name": "CareDrop Diagnostics", "email": sender_email},
                "to": [{"email": to_email}],
                "subject": subject,
                "htmlContent": body
            }
            headers = {
                "accept": "application/json", 
                "api-key": brevo_key.strip(), 
                "content-type": "application/json"
            }
            res = requests.post(url, json=payload, headers=headers, timeout=5)
            if res.status_code in [200, 201, 202]:
                return
        except Exception as e:
            print(f"Brevo API error: {e}")

    password = os.environ.get('MAIL_PASSWORD')
    if not password or not to_email:
        return
        
    try:
        msg = MIMEMultipart()
        msg['From'] = f"CareDrop Diagnostics <{sender_email}>"
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'html'))
        
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, password.strip())
        server.send_message(msg)
        server.quit()
    except Exception as e:
        print(f"SMTP failed: {e}")

def send_email_async(to_email, subject, body):
    thread = threading.Thread(target=send_email, args=(to_email, subject, body))
    thread.daemon = True
    thread.start()

# ==========================================
# 2. ACCESS CONTROL DECORATORS
# ==========================================
def admin_only(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('role') != 'admin': 
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

def reception_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('role') not in ['admin', 'reception']: 
            return redirect(url_for('reception_login'))
        return f(*args, **kwargs)
    return decorated_function

def partner_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('role') != 'partner': 
            return redirect(url_for('partner_login'))
        return f(*args, **kwargs)
    return decorated_function

# ==========================================
# 3. AUTHENTICATION ROUTES
# ==========================================
@app.route('/login', methods=['GET', 'POST'])
def patient_login(): 
    return render_template('auth_patient.html')

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        valid_user = os.environ.get('ADMIN_USER', 'admin')
        valid_pass = os.environ.get('ADMIN_PASSWORD', 'admin123')
        
        if username == valid_user and password == valid_pass:
            session['role'] = 'admin'
            return redirect(url_for('admin'))
        flash("Invalid Admin Credentials")
    return render_template('auth_admin.html') 

@app.route('/reception/login', methods=['GET', 'POST'])
def reception_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        valid_user = os.environ.get('RECEPTION_USER', 'reception')
        valid_pass = os.environ.get('RECEPTION_PASSWORD', 'reception123')
        
        if username == valid_user and password == valid_pass:
            session['role'] = 'reception'
            return redirect(url_for('admin'))
        flash("Invalid Reception Credentials")
    return render_template('auth_reception.html') 

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
    role = session.get('role')
    session.clear()
    if role == 'admin':
        return redirect(url_for('admin_login'))
    elif role == 'reception':
        return redirect(url_for('reception_login'))
    elif role == 'partner':
        return redirect(url_for('partner_login'))
    return redirect(url_for('index'))

@app.route('/api/send_login_otp', methods=['POST'])
def send_login_otp():
    email = request.form.get('email').lower().strip()
    otp = str(random.randint(1000, 9999))
    session['login_otp'] = otp
    session['login_email'] = email
    
    body = f"<div style='text-align: center;'><h2>Your login code is:</h2><h1>{otp}</h1></div>"
    send_email_async(email, "CareDrop Login Code", body)
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

# ==========================================
# 4. PUBLIC PATIENT BOOKING FLOW
# ==========================================
@app.route('/')
def index(): 
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('''
            SELECT t.*, COUNT(p.id) as param_count 
            FROM tests t 
            LEFT JOIN test_parameters p ON t.id = p.test_id 
            WHERE t.is_active = TRUE AND t.price > 0 
            GROUP BY t.id 
            ORDER BY t.id DESC LIMIT 4
        ''')
        tests = [dict(row) for row in cur.fetchall()]
        cur.close()
        conn.close()
        return render_template('index.html', tests=tests)
    except Exception:
        return render_template('index.html', tests=[])

@app.route('/tests')
def tests_catalogue():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        try:
            cur.execute("ALTER TABLE tests ADD COLUMN IF NOT EXISTS delivery_time VARCHAR(50) DEFAULT '24'")
            conn.commit()
        except:
            conn.rollback()

        cur.execute('''
            SELECT t.*, COUNT(p.id) as param_count 
            FROM tests t 
            LEFT JOIN test_parameters p ON t.id = p.test_id 
            WHERE t.is_active = TRUE 
            GROUP BY t.id 
            ORDER BY t.category, t.name ASC
        ''')
        tests = [dict(row) for row in cur.fetchall()]
        cur.close()
        conn.close()

        unique_categories = sorted(list(set(t.get('category', 'General Health / Blood') for t in tests if t.get('category'))))

        search_query = request.args.get('q', '').lower()
        if search_query:
            tests = [t for t in tests if search_query in str(t.get('name', '')).lower() or search_query in str(t.get('category', '')).lower()]

        return render_template('tests.html', tests=tests, categories=unique_categories)
    except Exception as e:
        return f"<p>System Error: {str(e)}</p>"

@app.route('/api/cart/add/<int:test_id>', methods=['POST'])
def add_to_cart(test_id):
    if 'cart' not in session:
        session['cart'] = []
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
    if not cart_ids: 
        return redirect(url_for('tests_catalogue'))
        
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM tests WHERE id = ANY(%s)', (cart_ids,))
    items = [dict(row) for row in cur.fetchall()]
    cur.close()
    conn.close()
    
    total = sum(t.get('price', 0) for t in items)
    return render_template('checkout.html', items=items, total=total)

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
        new_total = total - discount
        return jsonify({'valid': True, 'discount': discount, 'new_total': new_total, 'partner': partner['clinic_name']})
    return jsonify({'valid': False})

@app.route('/api/initiate_booking', methods=['POST'])
def initiate_booking():
    session['pending_order'] = {
        'full_name': request.form.get('full_name'),
        'age': request.form.get('age'),
        'gender': request.form.get('gender'),
        'phone': request.form.get('phone'),
        'email': request.form.get('email', '').lower().strip(),
        'address': request.form.get('address'),
        'booking_date': request.form.get('booking_date'),
        'time_slot': request.form.get('time_slot'),
        'referral_code': request.form.get('referral_code', '').upper()
    }
    otp = str(random.randint(1000, 9999))
    session['booking_otp'] = otp
    
    email_body = f"<h2>CareDrop Booking Verification</h2><h1>{otp}</h1>"
    send_email_async(session['pending_order']['email'], "Verify Your CareDrop Booking", email_body)
    return jsonify({'status': 'otp_sent'})

@app.route('/api/confirm_booking', methods=['POST'])
def confirm_booking():
    user_otp = request.form.get('otp')
    if user_otp != session.get('booking_otp'): 
        return jsonify({'status': 'error', 'msg': 'Invalid OTP code.'})
        
    o_data = session.get('pending_order')
    if not o_data:
        return jsonify({'status': 'error', 'msg': 'Booking session expired.'})
        
    conn = get_db_connection()
    cur = conn.cursor()
    
    cart_ids = session.get('cart', [])
    cur.execute('SELECT id, name, price, b2b_cost FROM tests WHERE id = ANY(%s)', (cart_ids,))
    items = cur.fetchall()
    
    test_ids = [i['id'] for i in items]
    tests_requested = ", ".join([i['name'] for i in items])
    gross_bill = sum(i['price'] for i in items)
    b2b_cost = sum(i['b2b_cost'] for i in items)
    
    discount_given = 0
    partner_commission = 0
    
    if o_data['referral_code']:
        cur.execute('SELECT * FROM partners WHERE referral_code = %s', (o_data['referral_code'],))
        partner = cur.fetchone()
        if partner:
            margin_pool = gross_bill * partner['margin_pool_pct']
            discount_given = round(gross_bill * 0.10) 
            partner_commission = margin_pool - discount_given

    total_bill = gross_bill - discount_given
    order_code = f"CD-{random.randint(1000, 9999)}"
    combined_time_slot = f"{o_data.get('booking_date', '')} | {o_data.get('time_slot', '')}"

    cur.execute('''
        INSERT INTO orders (
            order_code, full_name, age, gender, phone, email, address, 
            time_slot, tests_requested, gross_bill, discount_given, total_bill, 
            b2b_total_cost, referral_code, partner_commission
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id;
    ''', (
        order_code, o_data['full_name'], o_data['age'], o_data['gender'], 
        o_data['phone'], o_data['email'], o_data['address'], combined_time_slot, 
        tests_requested, gross_bill, discount_given, total_bill, b2b_cost, 
        o_data['referral_code'], partner_commission
    ))
    
    order_id = cur.fetchone()['id']
    
    try:
        if test_ids:
            cur.execute('SELECT param_name, unit, ref_range FROM test_parameters WHERE test_id = ANY(%s)', (test_ids,))
            for p in cur.fetchall():
                cur.execute(
                    "INSERT INTO test_results (order_id, parameter_name, units, ref_interval) VALUES (%s, %s, %s, %s)", 
                    (order_id, p['param_name'], p['unit'], p['ref_range'])
                )
    except Exception as e:
        print(f"LIMS Parameter staging note: {e}")
    
    conn.commit()
    cur.close()
    conn.close()
    
    session.pop('cart', None)
    session.pop('booking_otp', None)
    session.pop('pending_order', None)
    session['role'] = 'patient'
    session['patient_email'] = o_data['email']
    
    send_email_async(o_data['email'], "CareDrop Booking Confirmed", f"<p>Your test is booked. Order ID: {order_code}</p>")
    
    hq_alert = f"""
    <div style='font-family: sans-serif; padding: 20px;'>
        <h2 style='color: #00A8A8;'>New Booking Received</h2>
        <p><strong>Order ID:</strong> {order_code}</p>
        <p><strong>Patient:</strong> {o_data['full_name']} (Age: {o_data['age']}, {o_data['gender']})</p>
        <p><strong>Phone:</strong> {o_data['phone']}</p>
        <p><strong>Tests:</strong> {tests_requested}</p>
        <p><strong>Time Slot:</strong> {combined_time_slot}</p>
        <p><strong>Total Bill:</strong> ₹{total_bill}</p>
    </div>
    """
    send_email_async('caredrop.ynr@gmail.com', f"🚨 NEW BOOKING: {order_code}", hq_alert)
    
    return jsonify({'status': 'success', 'redirect': '/my_bookings'})

@app.route('/my_bookings')
def my_bookings():
    if session.get('role') != 'patient': 
        return redirect(url_for('patient_login'))
        
    email = session.get('patient_email')
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM orders WHERE email = %s ORDER BY id DESC', (email,))
    bookings = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('my_bookings.html', bookings=bookings)

# ==========================================
# 5. RECEPTION & PARTNER POS
# ==========================================
@app.route('/pos_book_test', methods=['POST'])
@reception_required
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
    
    try:
        if test_ids:
            cur.execute('SELECT param_name, unit, ref_range FROM test_parameters WHERE test_id = ANY(%s)', (test_ids,))
            for p in cur.fetchall():
                cur.execute("INSERT INTO test_results (order_id, parameter_name, units, ref_interval) VALUES (%s, %s, %s, %s)", (order_id, p['param_name'], p['unit'], p['ref_range']))
    except Exception as e:
        print(f"LIMS Error: {e}")
            
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('admin'))

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
    try:
        # 1. Safely extract data with fallbacks
        full_name = request.form.get('full_name')
        age = request.form.get('age')
        gender = request.form.get('gender')
        phone = request.form.get('phone')
        email = request.form.get('email', '').strip()
        address = request.form.get('address', 'Clinic Walk-in')
        time_slot = request.form.get('time_slot', 'Walk-in')
        tests_requested = request.form.get('tests_requested', '')
        
        # 2. Safe float conversions for the financial math
        try:
            discount_pct = float(request.form.get('discount_pct') or 0)
        except:
            discount_pct = 0.0
            
        try:
            gross_bill = float(request.form.get('total_bill') or 0)
        except:
            gross_bill = 0.0

        code = session.get('referral_code')
        conn = get_db_connection()
        cur = conn.cursor()
        
        # 3. Safely calculate clinic margin
        cur.execute('SELECT margin_pool_pct FROM partners WHERE referral_code = %s', (code,))
        partner = cur.fetchone()
        db_margin = float(partner['margin_pool_pct'] if partner and partner['margin_pool_pct'] else 0.20)
        margin_pool_pct = db_margin * 100.0
        
        if discount_pct > margin_pool_pct: 
            discount_pct = margin_pool_pct 
        
        discount_given = round(gross_bill * (discount_pct / 100.0))
        total_bill = gross_bill - discount_given
        partner_commission = round(gross_bill * ((margin_pool_pct - discount_pct) / 100.0))
        order_code = f"CD-{random.randint(1000, 9999)}"

        # 4. Insert the Order
        cur.execute('''
            INSERT INTO orders (order_code, full_name, age, gender, phone, email, address, time_slot, tests_requested, gross_bill, discount_given, total_bill, referral_code, partner_commission)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id;
        ''', (order_code, full_name, age, gender, phone, email, address, time_slot, tests_requested, gross_bill, discount_given, total_bill, code, partner_commission))
        
        order_id = cur.fetchone()['id']
        
        # 5. Add commission to the clinic's wallet
        cur.execute("UPDATE partners SET wallet_balance = wallet_balance + %s WHERE referral_code = %s", (partner_commission, code))
        
        # 6. LIMS INJECTION (Crucial: Generate the blank lab report parameters)
        if tests_requested:
            test_names = [n.strip() for n in tests_requested.split(',')]
            cur.execute('SELECT id FROM tests WHERE name = ANY(%s)', (test_names,))
            test_ids = [i['id'] for i in cur.fetchall()]
            
            if test_ids:
                cur.execute('SELECT param_name, unit, ref_range FROM test_parameters WHERE test_id = ANY(%s)', (test_ids,))
                for p in cur.fetchall():
                    cur.execute(
                        "INSERT INTO test_results (order_id, parameter_name, units, ref_interval) VALUES (%s, %s, %s, %s)", 
                        (order_id, p['param_name'], p['unit'], p['ref_range'])
                    )

        conn.commit()
        cur.close()
        conn.close()
        
        if email: 
            send_email_async(email, "CareDrop Booking Confirmed", f"<p>Your test is booked. Order ID: {order_code}. Total: ₹{total_bill}.</p>")
            
        return redirect(url_for('partner_dashboard'))
        
    except Exception as e:
        # THE INTERCEPTOR: If it fails again, tell us exactly why instead of 500 error.
        import traceback
        error_details = traceback.format_exc()
        return f"""
        <div style='padding: 40px; font-family: sans-serif; max-width: 800px; margin: auto;'>
            <h2 style='color: #E11D48; font-weight: 900;'>Partner POS Crash Intercepted!</h2>
            <p><strong>Primary Error:</strong> {str(e)}</p>
            <div style='background: #F1F5F9; padding: 20px; border-radius: 8px; border: 1px solid #CBD5E1; overflow-x: auto; margin-top: 20px;'>
                <pre style='font-size: 12px; color: #334155; margin: 0;'>{error_details}</pre>
            </div>
            <a href='/partner/dashboard' style='display: inline-block; margin-top: 20px; padding: 10px 20px; background: #0F172A; color: white; text-decoration: none; border-radius: 6px;'>Back to Dashboard</a>
        </div>
        """
# ==========================================
# 6. ADMIN & OPERATIONS MANAGEMENT
# ==========================================
@app.route('/admin')
@reception_required
def admin():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM orders ORDER BY id DESC')
    raw_orders = cur.fetchall()
    
    orders = []
    for o in raw_orders:
        order_dict = dict(o)
        if not order_dict.get('time_slot'):
            order_dict['time_slot'] = 'N/A | N/A'
        elif ' | ' not in order_dict['time_slot']:
            order_dict['time_slot'] = f"{order_dict['time_slot']} | N/A"
            
        order_dict['total_bill'] = float(order_dict.get('total_bill') or 0)
        order_dict['status'] = order_dict.get('status') or 'Pending'
        orders.append(order_dict)
    
    today_orders = len([o for o in orders if o['status'] != 'Completed'])
    revenue = sum([o['total_bill'] for o in orders if o['status'] == 'Completed'])
    pending_reports = len([o for o in orders if o['status'] == 'Sample Collected' and not o.get('uploaded_pdf')])
    
    cur.close()
    conn.close()
    return render_template('hq_dashboard.html', orders=orders, today_orders=today_orders, revenue=revenue, pending_reports=pending_reports, role=session.get('role'))

@app.route('/admin/mark_collected/<int:order_id>')
@reception_required
def hq_mark_collected(order_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE orders SET status = 'Sample Collected' WHERE id = %s", (order_id,))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('admin'))

@app.route('/admin/pos')
@reception_required
def admin_pos():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM tests WHERE is_active = TRUE ORDER BY name ASC')
    tests = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('admin_pos.html', tests=tests, role=session.get('role'))

@app.route('/admin/partners')
@admin_only
def admin_partners():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('SELECT * FROM partners ORDER BY id DESC')
        partners = cur.fetchall()
        cur.execute("SELECT partner_commission, referral_code FROM orders WHERE is_commission_paid = FALSE AND status = 'Completed'")
        unpaid_orders = cur.fetchall()
        
        ledgers = {p['referral_code']: {'details': p, 'unpaid': 0} for p in partners}
        for u in unpaid_orders:
            code = u['referral_code']
            comm = u['partner_commission'] or 0
            if code in ledgers and comm > 0:
                ledgers[code]['unpaid'] += comm
                
        cur.close()
        conn.close()
        return render_template('admin_partners.html', ledgers=ledgers.values())
        
    except Exception as e:
        # AUTO-HEALER
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute('''
                CREATE TABLE IF NOT EXISTS partners (
                    id SERIAL PRIMARY KEY,
                    clinic_name VARCHAR(255),
                    referral_code VARCHAR(50) UNIQUE,
                    password VARCHAR(255),
                    margin_pool_pct DECIMAL(4,2) DEFAULT 0.20,
                    wallet_balance DECIMAL(10,2) DEFAULT 0,
                    contact_person VARCHAR(255),
                    phone VARCHAR(50),
                    email VARCHAR(255),
                    address TEXT
                )
            ''')
            cur.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS referral_code VARCHAR(50)")
            cur.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS partner_commission DECIMAL(10,2) DEFAULT 0")
            cur.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS is_commission_paid BOOLEAN DEFAULT FALSE")
            conn.commit()
            cur.close()
            conn.close()
            return redirect(url_for('admin_partners'))
        except Exception as auto_heal_error:
            return f"<div style='padding:20px; font-family:sans-serif;'><h2>Critical DB Error</h2><p>Could not load or auto-heal partners. Error: {str(auto_heal_error)}</p></div>"

@app.route('/admin/partner/new', methods=['GET', 'POST'])
@admin_only
def admin_add_partner():
    try:
        if request.method == 'POST':
            # 1. Safely extract all form data with fallbacks
            clinic_name = request.form.get('clinic_name', 'New Clinic')
            contact_person = request.form.get('contact_person', '')
            phone = request.form.get('phone', '')
            email = request.form.get('email', '')
            address = request.form.get('address', '')
            password = request.form.get('password', '1234')
            
            # 2. Safely convert the percentage to a decimal (prevents ValueError crashes)
            try:
                raw_pct = request.form.get('margin_pool_pct')
                margin_pool_pct = float(raw_pct) / 100.0 if raw_pct else 0.20
            except:
                margin_pool_pct = 0.20
            
            referral_code = f"PT-{random.randint(1000, 9999)}"
            
            conn = get_db_connection()
            cur = conn.cursor()
            
            # 3. Auto-Heal: Add columns ONE BY ONE to ensure database stability
            columns_to_add = [
                "contact_person VARCHAR(255)",
                "phone VARCHAR(50)",
                "email VARCHAR(255)",
                "address TEXT"
            ]
            for col in columns_to_add:
                try:
                    cur.execute(f"ALTER TABLE partners ADD COLUMN IF NOT EXISTS {col}")
                    conn.commit()
                except Exception as db_alter_err:
                    conn.rollback() # Clear the error state and keep going

            # 4. Insert the new partner
            cur.execute('''
                INSERT INTO partners (clinic_name, referral_code, password, margin_pool_pct, contact_person, phone, email, address)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            ''', (clinic_name, referral_code, password, margin_pool_pct, contact_person, phone, email, address))
            
            partner_id = cur.fetchone()['id']
            conn.commit()
            cur.close()
            conn.close()
            
            return redirect(url_for('admin_partner_success', partner_id=partner_id))
            
        return render_template('admin_add_partner.html')
        
    except Exception as e:
        # THE INTERCEPTOR: If anything fails, print the exact error instead of a 500 page
        import traceback
        error_details = traceback.format_exc()
        return f"""
        <div style='padding: 40px; font-family: sans-serif; max-width: 800px; margin: auto;'>
            <h2 style='color: #E11D48; font-weight: 900;'>Backend Crash Intercepted!</h2>
            <p><strong>Primary Error:</strong> {str(e)}</p>
            <div style='background: #F1F5F9; padding: 20px; border-radius: 8px; border: 1px solid #CBD5E1; overflow-x: auto; margin-top: 20px;'>
                <pre style='font-size: 12px; color: #334155; margin: 0;'>{error_details}</pre>
            </div>
            <a href='/admin/partners' style='display: inline-block; margin-top: 20px; padding: 10px 20px; background: #0F172A; color: white; text-decoration: none; border-radius: 6px;'>Back to Dashboard</a>
        </div>
        """
@app.route('/admin/partner/<int:partner_id>/success')
@admin_only
def admin_partner_success(partner_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM partners WHERE id = %s', (partner_id,))
    partner = cur.fetchone()
    cur.close()
    conn.close()
    return render_template('admin_partner_card.html', partner=partner)

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

# ==========================================
# 7. ENTERPRISE CATALOG & CSV UPLOAD
# ==========================================
@app.route('/admin/catalog')
@admin_only
def admin_catalog():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM tests WHERE is_active = TRUE ORDER BY id DESC')
    tests = cur.fetchall()
    
    try:
        cur.execute('SELECT * FROM test_parameters')
        raw_params = cur.fetchall()
    except Exception as e:
        conn.rollback()
        raw_params = []
        flash("Please click 'Upgrade DB Schema' to enable the Enterprise LIMS features.", "error")
        
    test_params = []
    for p in raw_params:
        p_dict = dict(p)
        try:
            ranges = json.loads(p_dict['ref_range'])
            p_dict['display_range'] = f"Adult: {ranges.get('male','')} | Child: {ranges.get('child','')}"
        except:
            p_dict['display_range'] = p_dict.get('ref_range', '')
        test_params.append(p_dict)

    cur.close()
    conn.close()
    return render_template('admin_catalog.html', tests=tests, test_params=test_params)

@app.route('/admin/upgrade_schema')
@admin_only
def admin_upgrade_schema():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("ALTER TABLE test_parameters ADD COLUMN IF NOT EXISTS method VARCHAR(255) DEFAULT 'Automated'")
        cur.execute("ALTER TABLE test_parameters ADD COLUMN IF NOT EXISTS interpretation TEXT")
        cur.execute("ALTER TABLE test_parameters ADD COLUMN IF NOT EXISTS additional_info TEXT")
        cur.execute("ALTER TABLE test_parameters ALTER COLUMN ref_range TYPE TEXT")
        conn.commit()
        flash("Enterprise Schema Upgraded successfully! You can now use bulk uploads and complex logic.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Schema Upgrade Error (or already upgraded): {e}", "error")
    finally:
        cur.close()
        conn.close()
    return redirect(url_for('admin_catalog'))

@app.route('/admin/download_csv_template')
@admin_only
def download_csv_template():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Test_Name', 'Category', 'Price', 'B2B_Cost', 'Delivery_Time_Hours', 'Param_Name', 'Unit', 'Method', 'Adult_Male', 'Adult_Female', 'Child', 'Interpretation', 'Additional_Info'])
    writer.writerow(['Complete Blood Count', 'Hematology & Coagulation', '300', '150', '24', 'Hemoglobin (HB)', 'g/dl', 'Photometric', '13.0-17.0', '12.0-15.0', '11.0-14.0', 'Low levels indicate anemia...', 'Fasting not required.'])
    writer.writerow(['Lipid Profile', 'Heart / Lipid', '450', '200', '12', 'Total Cholesterol', 'mg/dl', 'Spectrophotometry', '0-200', '0-200', '0-170', 'High levels increase risk of stroke...', '12-hour strict fasting required.'])
    
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode('utf-8')), mimetype='text/csv', download_name='CareDrop_Bulk_Catalog_Template.csv')

@app.route('/admin/bulk_upload_catalog', methods=['POST'])
@admin_only
def bulk_upload_catalog():
    if 'csv_file' not in request.files:
        flash("No file uploaded", "error")
        return redirect(url_for('admin_catalog'))
        
    file = request.files['csv_file']
    if file.filename == '':
        flash("No file selected", "error")
        return redirect(url_for('admin_catalog'))

    try:
        stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
        csv_input = csv.DictReader(stream)
        
        conn = get_db_connection()
        cur = conn.cursor()
        
        try:
            cur.execute("ALTER TABLE tests ADD COLUMN IF NOT EXISTS delivery_time VARCHAR(50) DEFAULT '24'")
            conn.commit()
        except:
            conn.rollback()
        
        for row in csv_input:
            test_name = row.get('Test_Name', '').strip()
            if not test_name: 
                continue
                
            cur.execute("SELECT id FROM tests WHERE name = %s", (test_name,))
            test = cur.fetchone()
            
            if not test:
                cur.execute(
                    "INSERT INTO tests (name, category, price, b2b_cost, delivery_time) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                    (test_name, row.get('Category', 'General'), float(row.get('Price', 0) or 0), float(row.get('B2B_Cost', 0) or 0), row.get('Delivery_Time_Hours', '24'))
                )
                test_id = cur.fetchone()['id']
            else:
                test_id = test['id']

            param_name = row.get('Param_Name', '').strip()
            if param_name:
                smart_range = {
                    "male": row.get('Adult_Male', ''),
                    "female": row.get('Adult_Female', ''),
                    "child": row.get('Child', '')
                }
                
                cur.execute('''
                    INSERT INTO test_parameters (test_id, param_name, unit, ref_range, method, interpretation, additional_info) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                ''', (
                    test_id, param_name, row.get('Unit', ''), json.dumps(smart_range),
                    row.get('Method', 'Automated'), row.get('Interpretation', ''), row.get('Additional_Info', '')
                ))
                
        conn.commit()
        flash("Bulk upload successful! Catalog and LIMS updated.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"CSV Error: Ensure columns match exactly. Details: {str(e)}", "error")
    finally:
        cur.close()
        conn.close()
        
    return redirect(url_for('admin_catalog'))

@app.route('/admin/add_test', methods=['POST'])
@admin_only
def admin_add_test():
    name = request.form.get('name')
    category = request.form.get('category', 'General')
    price = float(request.form.get('retail_price') or 0)
    b2b_cost = float(request.form.get('b2b_cost') or 0)
    delivery_time = request.form.get('delivery_time', '24')
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("ALTER TABLE tests ADD COLUMN IF NOT EXISTS delivery_time VARCHAR(50) DEFAULT '24'")
        conn.commit()
    except:
        conn.rollback()
        
    cur.execute('INSERT INTO tests (name, category, price, b2b_cost, delivery_time) VALUES (%s, %s, %s, %s, %s)', (name, category, price, b2b_cost, delivery_time))
    conn.commit()
    cur.close()
    conn.close()
    flash(f"Test '{name}' added manually.", "success")
    return redirect(url_for('admin_catalog'))

@app.route('/admin/add_parameter', methods=['POST'])
@admin_only
def admin_add_parameter():
    test_id = request.form.get('test_id')
    if not test_id:
        flash("Error: You must select a test from the dropdown.", "error")
        return redirect(url_for('admin_catalog'))
        
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO test_parameters (test_id, param_name, unit, ref_range, method, interpretation, additional_info) VALUES (%s, %s, %s, %s, %s, %s, %s)", 
            (
                int(test_id), 
                request.form.get('param_name'), 
                request.form.get('unit'), 
                request.form.get('ref_range'),
                request.form.get('method', 'Automated'),
                request.form.get('interpretation', ''),
                request.form.get('additional_info', '')
            )
        )
        conn.commit()
        flash("Success! Smart Parameter added manually.", "success")
    except Exception as e:
        flash(f"Database Error: {str(e)}", "error")
    finally:
        cur.close()
        conn.close()
    return redirect(url_for('admin_catalog'))

@app.route('/admin/wipe_catalog')
@admin_only
def admin_wipe_catalog():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('TRUNCATE TABLE tests CASCADE')
    conn.commit()
    cur.close()
    conn.close()
    flash("Catalog completely wiped. You can now upload a fresh CSV.", "success")
    return redirect(url_for('admin_catalog'))

@app.route('/admin/wipe_orders')
@admin_only
def admin_wipe_orders():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('TRUNCATE TABLE orders CASCADE')
    try:
        cur.execute('TRUNCATE TABLE test_results CASCADE')
    except:
        pass
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('admin'))

# ==========================================
# 8. LIMS GENERATOR
# ==========================================
@app.route('/admin/upload_pdf/<int:order_id>', methods=['POST'])
@reception_required
def upload_pdf(order_id):
    file = request.files.get('pdf_file')
    if file and file.filename.endswith('.pdf'):
        encoded_pdf = base64.b64encode(file.read()).decode('utf-8')
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE orders SET uploaded_pdf = %s, status = 'Completed', completed_at = CURRENT_TIMESTAMP WHERE id = %s", (encoded_pdf, order_id))
        conn.commit()
        cur.close()
        conn.close()
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
        return send_file(io.BytesIO(base64.b64decode(order['uploaded_pdf'])), download_name=f"{order['order_code']}_Report.pdf", mimetype='application/pdf')
    return "PDF Not Found", 404

@app.route('/api/lab/submit_results/<int:order_id>', methods=['POST'])
@reception_required
def submit_lab_results(order_id):
    conn = get_db_connection()
    cur = conn.cursor()
    for key, value in request.form.items():
        if key.startswith('param_'):
            cur.execute("UPDATE test_results SET observed_value = %s WHERE id = %s", (value, key.split('_')[1]))
    
    cur.execute("UPDATE orders SET status = 'Completed', completed_at = CURRENT_TIMESTAMP WHERE id = %s", (order_id,))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(f'/lims_report/{order_id}')

@app.route('/admin/reopen_order/<int:order_id>')
@admin_only
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
    if order and order['uploaded_pdf']:
        cur.close()
        conn.close()
        return redirect(url_for('download_pdf', order_id=order_id))
        
    cur.execute('SELECT * FROM test_results WHERE order_id = %s', (order_id,))
    raw_results = cur.fetchall()
    
   # DEMOGRAPHIC ROUTING
    patient_gender = str(order['gender']).lower() if order['gender'] else 'male'
    try:
        age = float(order['age'])
    except:
        age = 30
    is_child = age <= 12 
    
    results = []
    for r in raw_results:
        r_dict = dict(r)
        
        try:
            cur.execute('SELECT method, additional_info FROM test_parameters WHERE param_name = %s LIMIT 1', (r_dict['parameter_name'],))
            extra = cur.fetchone()
            r_dict['method'] = extra['method'] if extra else 'Automated'
            r_dict['additional_info'] = extra['additional_info'] if extra else ''
        except:
            r_dict['method'] = 'Automated'
            r_dict['additional_info'] = ''
            
        try:
            ranges = json.loads(r_dict['ref_interval'])
            if is_child and ranges.get('child'):
                display = ranges['child'].get('display', ranges['child'])
            elif patient_gender == 'female' and ranges.get('female'):
                display = ranges['female'].get('display', ranges['female'])
            else:
                display = ranges.get('male', {}).get('display', ranges.get('male', ''))
                
            r_dict['display_range'] = display
            
            if isinstance(display, str) and '-' in display:
                parts = display.split('-')
                r_dict['min_val'] = parts[0].strip()
                r_dict['max_val'] = parts[1].strip()
            else:
                r_dict['min_val'], r_dict['max_val'] = '', ''
        except:
            r_dict['display_range'] = r_dict['ref_interval']
            r_dict['min_val'], r_dict['max_val'] = '', ''
            
        results.append(r_dict)
        
    cur.close()
    conn.close()
    return render_template('lims_report.html', order=order, results=results, role=session.get('role'))

# ==========================================
# 9. UTILITIES
# ==========================================
@app.route('/debug_email')
def debug_email():
    brevo_key = os.environ.get('BREVO_API_KEY')
    sender_email = os.environ.get('MAIL_USERNAME', 'ihcdiagnostics.ynr@gmail.com')
    pwd = os.environ.get('MAIL_PASSWORD')
    
    html = f"<div style='font-family: monospace; padding: 20px;'>"
    html += f"<h2>CareDrop Email Diagnostic System</h2><hr>"
    html += f"<p><b>Sender Email Used:</b> {sender_email}</p>"
    html += f"<p><b>Brevo API Key Configured:</b> {'Yes' if brevo_key else 'No (Missing in Render)'}</p>"
    html += f"<p><b>Gmail Password Configured:</b> {'Yes' if pwd else 'No (Missing in Render)'}</p>"
    
    if brevo_key:
        html += "<hr><h3>Testing Brevo API...</h3>"
        try:
            url = "https://api.brevo.com/v3/smtp/email"
            payload = {"sender": {"name": "CareDrop Diagnostics", "email": sender_email}, "to": [{"email": sender_email}], "subject": "CareDrop Test", "htmlContent": "<p>Brevo is working!</p>"}
            headers = {"accept": "application/json", "api-key": brevo_key.strip(), "content-type": "application/json"}
            res = requests.post(url, json=payload, headers=headers, timeout=5)
            
            if res.status_code in [200, 201, 202]:
                html += f"<p style='color: green;'><b>SUCCESS!</b> Check your inbox.</p>"
            else:
                html += f"<p style='color: red;'><b>BREVO REJECTED IT:</b> {res.text}</p>"
        except Exception as e:
            html += f"<p style='color: red;'><b>Error:</b> {str(e)}</p>"
            
    html += "</div>"
    return html
@app.route('/admin/fix_orders_schema')
@admin_only
def fix_orders_schema():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS test_results CASCADE")
    cur.execute("DROP TABLE IF EXISTS orders CASCADE")
    
    cur.execute('''
        CREATE TABLE orders (
            id SERIAL PRIMARY KEY,
            order_code VARCHAR(50) UNIQUE,
            full_name VARCHAR(255),
            age VARCHAR(20),
            gender VARCHAR(20),
            phone VARCHAR(50),
            email VARCHAR(255),
            address TEXT,
            time_slot VARCHAR(100),
            tests_requested TEXT,
            gross_bill DECIMAL(10,2) DEFAULT 0,
            discount_given DECIMAL(10,2) DEFAULT 0,
            total_bill DECIMAL(10,2) DEFAULT 0,
            b2b_total_cost DECIMAL(10,2) DEFAULT 0,
            referral_code VARCHAR(50),
            partner_commission DECIMAL(10,2) DEFAULT 0,
            is_commission_paid BOOLEAN DEFAULT FALSE,
            status VARCHAR(50) DEFAULT 'Pending',
            uploaded_pdf TEXT,
            completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cur.execute('''
        CREATE TABLE test_results (
            id SERIAL PRIMARY KEY,
            order_id INTEGER REFERENCES orders(id) ON DELETE CASCADE,
            parameter_name VARCHAR(255),
            observed_value VARCHAR(255),
            units VARCHAR(50),
            ref_interval TEXT
        )
    ''')
    
    conn.commit()
    cur.close()
    conn.close()
    flash("Orders Database completely rebuilt and ready!", "success")
    return redirect(url_for('admin'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
