import os
import random
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from database import get_db_connection, init_db

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'caredrop_enterprise_secret_key_2026')

# Initialize DB
init_db()

# --- ACCESS CONTROL DECORATORS ---
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

# --- SECURE LOGIN ROUTING ---
@app.route('/login', methods=['GET', 'POST'])
def patient_login():
    # Email/Phone OTP flow for Patients
    return render_template('auth_patient.html') if os.path.exists('templates/auth_patient.html') else "Patient OTP Portal"

@app.route('/hq/login', methods=['GET', 'POST'])
def hq_login():
    # Staff/Admin Login (Hidden from main site)
    if request.method == 'POST':
        # Hardcoded for setup; move to DB later
        if request.form.get('password') == 'admin123':
            session['role'] = 'admin'
            return redirect(url_for('admin'))
        elif request.form.get('password') == 'rider123':
            session['role'] = 'rider'
            return redirect(url_for('rider_dashboard'))
    return render_template('auth_hq.html') if os.path.exists('templates/auth_hq.html') else "HQ Password Required"

@app.route('/partner/login', methods=['GET', 'POST'])
def partner_login():
    # Clinic/Pharmacy Login (Hidden from main site)
    if request.method == 'POST':
        code = request.form.get('referral_code')
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
    return render_template('auth_partner.html') if os.path.exists('templates/auth_partner.html') else "Partner Login Required"

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# --- PUBLIC SITE ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/tests')
def tests_catalogue():
    conn = get_db_connection()
    tests = conn.execute('SELECT * FROM tests WHERE is_active = 1 ORDER BY category, name ASC').fetchall()
    conn.close()
    return render_template('tests.html', tests=tests)

# --- CART API ---
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

# --- CHECKOUT & PARTNER REFERRAL ENGINE ---
@app.route('/checkout')
def checkout():
    cart_ids = session.get('cart', [])
    if not cart_ids:
        return redirect(url_for('tests_catalogue'))
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
    partner = conn.execute('SELECT * FROM partners WHERE referral_code = ? AND is_active = 1', (code,)).fetchone()
    conn.close()
    
    if partner:
        # Give patient a 10% discount from the partner's pool
        discount = round(total * 0.10)
        new_total = total - discount
        return jsonify({'valid': True, 'discount': discount, 'new_total': new_total, 'partner': partner['clinic_name']})
    return jsonify({'valid': False})

@app.route('/book_test', methods=['POST'])
def book_test():
    full_name = request.form.get('full_name')
    phone = request.form.get('phone')
    address = request.form.get('address')
    referral_code = request.form.get('referral_code', '').upper()
    
    conn = get_db_connection()
    
    # Calculate costs from session cart
    cart_ids = session.get('cart', [])
    placeholders = ','.join('?' for _ in cart_ids)
    items = conn.execute(f'SELECT name, price, b2b_cost FROM tests WHERE id IN ({placeholders})', cart_ids).fetchall()
    
    tests_requested = ", ".join([i['name'] for i in items])
    gross_bill = sum(i['price'] for i in items)
    b2b_cost = sum(i['b2b_cost'] for i in items)
    
    # Process Partner Commission & Discount
    discount_given = 0
    partner_commission = 0
    
    if referral_code:
        partner = conn.execute('SELECT * FROM partners WHERE referral_code = ?', (referral_code,)).fetchone()
        if partner:
            margin_pool = gross_bill * partner['margin_pool_pct'] # Total 30% available
            
            # Scenario A: Patient booked on public site using code (gets 10% discount)
            if session.get('role') != 'partner':
                discount_given = round(gross_bill * 0.10)
                
            # Partner gets whatever is left from the pool
            partner_commission = margin_pool - discount_given
            
            # Credit Partner Wallet
            conn.execute('UPDATE partners SET wallet_balance = wallet_balance + ? WHERE referral_code = ?', (partner_commission, referral_code))

    total_bill = gross_bill - discount_given
    order_code = f"CD-{random.randint(1000, 9999)}"

    # Save Order
    conn.execute('''
        INSERT INTO orders (order_code, full_name, phone, address, tests_requested, gross_bill, discount_given, total_bill, b2b_total_cost, referral_code, partner_commission)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    ''', (order_code, full_name, phone, address, tests_requested, gross_bill, discount_given, total_bill, b2b_cost, referral_code, partner_commission))
    
    conn.commit()
    conn.close()
    session.pop('cart', None)
    
    # Redirect logic based on who booked it
    if session.get('role') == 'partner':
        return redirect(url_for('partner_dashboard'))
    return redirect(url_for('index'))

# --- PROTECTED ADMIN & RIDER ROUTES ---
@app.route('/admin')
@hq_required
def admin():
    conn = get_db_connection()
    orders = conn.execute('SELECT * FROM orders ORDER BY id DESC').fetchall()
    tests = conn.execute('SELECT * FROM tests WHERE is_active = 1 ORDER BY id DESC').fetchall()
    metrics = conn.execute('SELECT COALESCE(SUM(total_bill), 0) as total_rev, COALESCE(SUM(b2b_total_cost), 0) as total_b2b FROM orders;').fetchone()
    conn.close()
    return render_template('admin.html', orders=orders, tests=tests, metrics=metrics)

@app.route('/rider')
@hq_required
def rider_dashboard():
    conn = get_db_connection()
    orders = conn.execute("SELECT * FROM orders WHERE status != 'Completed' ORDER BY id DESC").fetchall()
    cash_collected = conn.execute("SELECT COALESCE(SUM(total_bill), 0) FROM orders WHERE is_paid = 1 AND payment_mode = 'Cash'").fetchone()[0]
    conn.close()
    return render_template('rider_dashboard.html', orders=orders, cash_collected=cash_collected)

# --- PARTNER PORTAL ---
@app.route('/partner/dashboard')
@partner_required
def partner_dashboard():
    code = session.get('referral_code')
    conn = get_db_connection()
    partner = conn.execute('SELECT * FROM partners WHERE referral_code = ?', (code,)).fetchone()
    orders = conn.execute('SELECT * FROM orders WHERE referral_code = ? ORDER BY id DESC', (code,)).fetchall()
    conn.close()
    return render_template('partner_dashboard.html', partner=partner, orders=orders)

@app.route('/lims_report/<int:order_id>')
def lims_report(order_id):
    conn = get_db_connection()
    order = conn.execute('SELECT * FROM orders WHERE id = ?', (order_id,)).fetchone()
    conn.close()
    return render_template('lims_report.html', order=order)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
