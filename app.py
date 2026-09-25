import os
import random
import base64
import io
import requests
import traceback
import smtplib
import threading
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, send_file
from database import get_db_connection, init_db

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'caredrop_enterprise_secret_key_2026')

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
            settings = {'phone': '+91 9485978790', 'email': 'caredrop.ynr@gmail.com', 'address': 'Shop No 434 L, Near Hospital, Sarojini Colony, Yamuna Nagar 135001'}
        return dict(site_settings=settings)
    except Exception:
        return dict(site_settings={'phone': '+91 9485978790', 'email': 'caredrop.ynr@gmail.com', 'address': 'Shop No 434 L, Near Hospital, Sarojini Colony, Yamuna Nagar 135001'})

def send_email(to_email, subject, body):
    brevo_key = os.environ.get('BREVO_API_KEY')
    sender_email = os.environ.get('MAIL_USERNAME', 'ihcdiagnostics.ynr@gmail.com')
    if brevo_key:
        try:
            url = "https://api.brevo.com/v3/smtp/email"
            payload = {"sender": {"name": "CareDrop Diagnostics", "email": sender_email}, "to": [{"email": to_email}], "subject": subject, "htmlContent": body}
            headers = {"accept": "application/json", "api-key": brevo_key.strip(), "content-type": "application/json"}
            res = requests.post(url, json=payload, headers=headers, timeout=5)
            if res.status_code in [200, 201, 202]: return
        except Exception:
            pass
    password = os.environ.get('MAIL_PASSWORD')
    if not password or not to_email: return
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
    except Exception:
        pass

def send_email_async(to_email, subject, body):
    thread = threading.Thread(target=send_email, args=(to_email, subject, body))
    thread.daemon = True
    thread.start()

def admin_only(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('role') != 'admin': return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

def reception_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('role') not in ['admin', 'reception']: return redirect(url_for('reception_login'))
        return f(*args, **kwargs)
    return decorated_function

def partner_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('role') != 'partner': return redirect(url_for('partner_login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def patient_login(): return render_template('auth_patient.html')

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        if request.form.get('username') == os.environ.get('ADMIN_USER', 'admin') and request.form.get('password') == os.environ.get('ADMIN_PASSWORD', 'admin123'):
            session['role'] = 'admin'
            return redirect(url_for('admin'))
        flash("Invalid Admin Credentials")
    return render_template('auth_admin.html') 

@app.route('/reception/login', methods=['GET', 'POST'])
def reception_login():
    if request.method == 'POST':
        if request.form.get('username') == os.environ.get('RECEPTION_USER', 'reception') and request.form.get('password') == os.environ.get('RECEPTION_PASSWORD', 'reception123'):
            session['role'] = 'reception'
            return redirect(url_for('admin'))
        flash("Invalid Reception Credentials")
    return render_template('auth_reception.html') 

@app.route('/partner/login', methods=['GET', 'POST'])
def partner_login():
    if request.method == 'POST':
        code = request.form.get('referral_code', '').upper()
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('SELECT * FROM partners WHERE referral_code = %s AND password = %s', (code, request.form.get('password')))
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
    if role == 'admin': return redirect(url_for('admin_login'))
    elif role == 'reception': return redirect(url_for('reception_login'))
    elif role == 'partner': return redirect(url_for('partner_login'))
    return redirect(url_for('index'))

@app.route('/api/send_login_otp', methods=['POST'])
def send_login_otp():
    email = request.form.get('email').lower().strip()
    session['login_otp'] = str(random.randint(1000, 9999))
    session['login_email'] = email
    send_email_async(email, "CareDrop Login Code", f"<h2>Your login code is:</h2><h1>{session['login_otp']}</h1>")
    return jsonify({'status': 'success'})

@app.route('/api/verify_login_otp', methods=['POST'])
def verify_login_otp():
    if request.form.get('otp') == session.get('login_otp'):
        session['role'] = 'patient'
        session['patient_email'] = session.get('login_email')
        return redirect(url_for('my_bookings'))
    flash("Invalid OTP")
    return redirect(url_for('patient_login'))

@app.route('/')
def index(): 
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('SELECT * FROM tests WHERE is_active = TRUE AND price > 0 ORDER BY id DESC LIMIT 4')
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
        cur.execute('SELECT * FROM tests WHERE is_active = TRUE ORDER BY category, name ASC')
        tests = [dict(row) for row in cur.fetchall()]
        cur.close()
        conn.close()
        search_query = request.args.get('q', '').lower()
        if search_query:
            tests = [t for t in tests if search_query in str(t.get('name', '')).lower() or search_query in str(t.get('category', '')).lower()]
        return render_template('tests.html', tests=tests)
    except Exception as e:
        return str(e)

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
    items = [dict(row) for row in cur.fetchall()]
    cur.close()
    conn.close()
    return render_template('checkout.html', items=items, total=sum(t.get('price', 0) for t in items))

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

@app.route('/api/initiate_booking', methods=['POST'])
def initiate_booking():
    session['pending_order'] = {
        'full_name': request.form.get('full_name'), 'age': request.form.get('age'), 'gender': request.form.get('gender'),
        'phone': request.form.get('phone'), 'email': request.form.get('email', '').lower().strip(), 'address': request.form.get('address'),
        'booking_date': request.form.get('booking_date'), 'time_slot': request.form.get('time_slot'), 'referral_code': request.form.get('referral_code', '').upper()
    }
    session['booking_otp'] = str(random.randint(1000, 9999))
    send_email_async(session['pending_order']['email'], "CareDrop Booking", f"<h1>{session['booking_otp']}</h1>")
    return jsonify({'status': 'otp_sent'})

@app.route('/api/confirm_booking', methods=['POST'])
def confirm_booking():
    if request.form.get('otp') != session.get('booking_otp'): return jsonify({'status': 'error', 'msg': 'Invalid OTP.'})
    o_data = session.get('pending_order')
    if not o_data: return jsonify({'status': 'error', 'msg': 'Session expired.'})
        
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT id, name, price, b2b_cost FROM tests WHERE id = ANY(%s)', (session.get('cart', []),))
    items = cur.fetchall()
    gross_bill = sum(i['price'] for i in items)
    
    discount_given, partner_commission = 0, 0
    if o_data['referral_code']:
        cur.execute('SELECT * FROM partners WHERE referral_code = %s', (o_data['referral_code'],))
        partner = cur.fetchone()
        if partner:
            discount_given = round(gross_bill * 0.10) 
            partner_commission = (gross_bill * partner['margin_pool_pct']) - discount_given

    order_code = f"CD-{random.randint(1000, 9999)}"
    combined_time_slot = f"{o_data.get('booking_date', '')} | {o_data.get('time_slot', '')}"

    cur.execute('''
        INSERT INTO orders (order_code, full_name, age, gender, phone, email, address, time_slot, tests_requested, gross_bill, discount_given, total_bill, b2b_total_cost, referral_code, partner_commission)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id;
    ''', (order_code, o_data['full_name'], o_data['age'], o_data['gender'], o_data['phone'], o_data['email'], o_data['address'], combined_time_slot, ", ".join([i['name'] for i in items]), gross_bill, discount_given, gross_bill - discount_given, sum(i['b2b_cost'] for i in items), o_data['referral_code'], partner_commission))
    
    order_id = cur.fetchone()['id']
    test_ids = [i['id'] for i in items]
    try:
        if test_ids:
            cur.execute('SELECT param_name, unit, ref_range FROM test_parameters WHERE test_id = ANY(%s)', (test_ids,))
            for p in cur.fetchall():
                cur.execute("INSERT INTO test_results (order_id, parameter_name, units, ref_interval) VALUES (%s, %s, %s, %s)", (order_id, p['param_name'], p['unit'], p['ref_range']))
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
    return jsonify({'status': 'success', 'redirect': '/my_bookings'})

@app.route('/my_bookings')
def my_bookings():
    if session.get('role') != 'patient': return redirect(url_for('patient_login'))
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM orders WHERE email = %s ORDER BY id DESC', (session.get('patient_email'),))
    bookings = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('my_bookings.html', bookings=bookings)

@app.route('/pos_book_test', methods=['POST'])
@reception_required
def pos_book_test():
    full_name = request.form.get('full_name')
    gross_bill = float(request.form.get('total_bill', 0))
    order_code = f"CD-{random.randint(1000, 9999)}"
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO orders (order_code, full_name, age, gender, phone, email, address, time_slot, tests_requested, gross_bill, total_bill)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id;
    ''', (order_code, full_name, request.form.get('age'), request.form.get('gender'), request.form.get('phone'), request.form.get('email', ''), request.form.get('address'), request.form.get('time_slot'), request.form.get('tests_requested'), gross_bill, gross_bill))
    
    order_id = cur.fetchone()['id']
    test_names = [n.strip() for n in request.form.get('tests_requested').split(',')]
    cur.execute('SELECT id FROM tests WHERE name = ANY(%s)', (test_names,))
    test_ids = [i['id'] for i in cur.fetchall()]
    
    try:
        if test_ids:
            cur.execute('SELECT param_name, unit, ref_range FROM test_parameters WHERE test_id = ANY(%s)', (test_ids,))
            for p in cur.fetchall():
                cur.execute("INSERT INTO test_results (order_id, parameter_name, units, ref_interval) VALUES (%s, %s, %s, %s)", (order_id, p['param_name'], p['unit'], p['ref_range']))
    except Exception as e:
        pass
            
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
    gross_bill = float(request.form.get('total_bill', 0))
    discount_pct = float(request.form.get('discount_pct', 0))
    code = session.get('referral_code')
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT margin_pool_pct FROM partners WHERE referral_code = %s', (code,))
    partner = cur.fetchone()
    
    margin_pool_pct = partner['margin_pool_pct'] * 100
    if discount_pct > margin_pool_pct: discount_pct = margin_pool_pct 
    
    discount_given = round(gross_bill * (discount_pct / 100))
    total_bill = gross_bill - discount_given
    partner_commission = round(gross_bill * ((margin_pool_pct - discount_pct) / 100))
    order_code = f"CD-{random.randint(1000, 9999)}"

    cur.execute('''
        INSERT INTO orders (order_code, full_name, age, gender, phone, email, address, time_slot, tests_requested, gross_bill, discount_given, total_bill, referral_code, partner_commission)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id;
    ''', (order_code, request.form.get('full_name'), request.form.get('age'), request.form.get('gender'), request.form.get('phone'), request.form.get('email', ''), request.form.get('address'), request.form.get('time_slot'), request.form.get('tests_requested'), gross_bill, discount_given, total_bill, code, partner_commission))
    
    cur.execute("UPDATE partners SET wallet_balance = wallet_balance + %s WHERE referral_code = %s", (partner_commission, code))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('partner_dashboard'))

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
        if not order_dict.get('time_slot'): order_dict['time_slot'] = 'N/A | N/A'
        elif ' | ' not in order_dict['time_slot']: order_dict['time_slot'] = f"{order_dict['time_slot']} | N/A"
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
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM partners ORDER BY id DESC')
    partners = cur.fetchall()
    cur.execute('SELECT partner_commission, referral_code FROM orders WHERE is_commission_paid = FALSE AND partner_commission > 0 AND status = %s', ('Completed',))
    unpaid_orders = cur.fetchall()
    
    ledgers = {p['referral_code']: {'details': p, 'unpaid': 0} for p in partners}
    for u in unpaid_orders:
        if u['referral_code'] in ledgers: 
            ledgers[u['referral_code']]['unpaid'] += u['partner_commission']
        
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

@app.route('/admin/catalog')
@admin_only
def admin_catalog():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM tests WHERE is_active = TRUE ORDER BY id DESC')
    tests = cur.fetchall()
    cur.execute('SELECT * FROM test_parameters')
    raw_params = cur.fetchall()
    
    # Safely unpack JSON for UI Display
    test_params = []
    for p in raw_params:
        p_dict = dict(p)
        try:
            ranges = json.loads(p_dict['ref_range'])
            p_dict['display_range'] = f"M: {ranges.get('male',{}).get('min','')}-{ranges.get('male',{}).get('max','')} | F: {ranges.get('female',{}).get('min','')}-{ranges.get('female',{}).get('max','')}"
        except:
            p_dict['display_range'] = p_dict['ref_range']
        test_params.append(p_dict)

    cur.close()
    conn.close()
    return render_template('admin_catalog.html', tests=tests, test_params=test_params)

@app.route('/admin/add_test', methods=['POST'])
@admin_only
def admin_add_test():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('INSERT INTO tests (name, category, price, b2b_cost) VALUES (%s, %s, %s, %s)', (request.form.get('name'), request.form.get('category', 'General'), float(request.form.get('retail_price') or 0), float(request.form.get('b2b_cost') or 0)))
    conn.commit()
    cur.close()
    conn.close()
    flash(f"Test added to catalog.", "success")
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
            "INSERT INTO test_parameters (test_id, param_name, unit, ref_range) VALUES (%s, %s, %s, %s)", 
            (int(test_id), request.form.get('param_name'), request.form.get('unit'), request.form.get('ref_range'))
        )
        conn.commit()
        flash(f"Success! Smart Parameter added.", "success")
    except Exception as e:
        flash(f"Database Error: {str(e)}", "error")
    finally:
        cur.close()
        conn.close()
    return redirect(url_for('admin_catalog'))

@app.route('/admin/upgrade_schema')
@admin_only
def admin_upgrade_schema():
    # Widens the ref_range column to TEXT so it can securely hold complex JSON payloads
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("ALTER TABLE test_parameters ALTER COLUMN ref_range TYPE TEXT")
        conn.commit()
        flash("Enterprise Schema Upgraded: Database is now ready for Smart JSON Parameters.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Schema Upgrade Not Needed / Error: {e}", "error")
    finally:
        cur.close()
        conn.close()
    return redirect(url_for('admin_catalog'))

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
    
    # PHASE 2: SMART DEMOGRAPHIC ENGINE (Extracts gender-specific logic)
    results = []
    patient_gender = str(order['gender']).lower() if order['gender'] else 'male'
    
    for r in raw_results:
        r_dict = dict(r)
        try:
            ranges = json.loads(r_dict['ref_interval'])
            r_dict['min_val'] = ranges.get(patient_gender, {}).get('min', '')
            r_dict['max_val'] = ranges.get(patient_gender, {}).get('max', '')
            r_dict['display_range'] = ranges.get('text', '')
            
            # If no manual text was provided, build the string automatically
            if not r_dict['display_range'] and r_dict['min_val'] and r_dict['max_val']:
                r_dict['display_range'] = f"{r_dict['min_val']} - {r_dict['max_val']}"
        except:
            # Fallback for old plain-text references
            r_dict['display_range'] = r_dict['ref_interval']
            r_dict['min_val'] = ''
            r_dict['max_val'] = ''
            
        results.append(r_dict)
        
    cur.close()
    conn.close()
    return render_template('lims_report.html', order=order, results=results, role=session.get('role'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
