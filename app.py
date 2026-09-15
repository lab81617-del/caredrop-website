import os
import random
from datetime import datetime
from functools import wraps
from flask import (
    Flask, render_template, request, redirect, 
    url_for, session, jsonify, flash
)
from database import get_db_connection, init_db

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'caredrop_enterprise_secret_key_2026')

# Initialize DB tables on startup
init_db()

# -------------------------------------------------------------
# AUTHENTICATION
# -------------------------------------------------------------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash("Please log in to continue.")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# -------------------------------------------------------------
# PUBLIC CATALOG & HOMEPAGE
# -------------------------------------------------------------
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/tests')
def tests_catalogue():
    conn = get_db_connection()
    tests = conn.execute('SELECT * FROM tests WHERE is_active = 1 ORDER BY id ASC').fetchall()
    conn.close()
    return render_template('tests.html', tests=tests)

# -------------------------------------------------------------
# CART API
# -------------------------------------------------------------
@app.route('/api/cart/add/<int:test_id>', methods=['POST'])
def add_to_cart(test_id):
    if 'cart' not in session:
        session['cart'] = []
    
    cart = session['cart']
    if test_id not in cart:
        cart.append(test_id)
        session['cart'] = cart
        session.modified = True
        
    return jsonify({
        'status': 'success',
        'total_items': len(session['cart']),
        'cart': session['cart']
    })

@app.route('/api/cart/remove/<int:test_id>', methods=['POST'])
def remove_from_cart(test_id):
    if 'cart' in session and test_id in session['cart']:
        session['cart'].remove(test_id)
        session.modified = True
    return jsonify({
        'status': 'success',
        'total_items': len(session.get('cart', [])),
        'cart': session.get('cart', [])
    })

# -------------------------------------------------------------
# CHECKOUT & BOOKINGS (Real DB Inserts)
# -------------------------------------------------------------
@app.route('/checkout')
def checkout():
    cart_ids = session.get('cart', [])
    if not cart_ids:
        flash("Your cart is empty.")
        return redirect(url_for('tests_catalogue'))

    conn = get_db_connection()
    placeholders = ','.join('?' for _ in cart_ids)
    items = conn.execute(f'SELECT * FROM tests WHERE id IN ({placeholders})', cart_ids).fetchall()
    conn.close()

    total_amount = sum(t['price'] for t in items)
    return render_template('checkout.html', items=items, total=total_amount)

@app.route('/book_test', methods=['POST'])
def book_test():
    full_name = request.form.get('full_name')
    phone = request.form.get('phone')
    address = request.form.get('address')
    tests_requested = request.form.get('tests_requested')
    total_bill = request.form.get('total_bill')

    conn = get_db_connection()
    
    # Calculate costs if submitted via cart
    if not tests_requested and 'cart' in session:
        cart_ids = session['cart']
        placeholders = ','.join('?' for _ in cart_ids)
        items = conn.execute(f'SELECT name, price, b2b_cost FROM tests WHERE id IN ({placeholders})', cart_ids).fetchall()
        tests_requested = ", ".join([i['name'] for i in items])
        total_bill = sum(i['price'] for i in items)
        b2b_cost = sum(i['b2b_cost'] for i in items)
    else:
        total_bill = float(total_bill or 0.0)
        b2b_cost = total_bill * 0.40 # Fallback 40% B2B margin approximation

    order_code = f"CD-{random.randint(1000, 9999)}"

    conn.execute('''
        INSERT INTO orders (order_code, full_name, phone, address, tests_requested, total_bill, b2b_total_cost, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'Pending');
    ''', (order_code, full_name, phone, address, tests_requested, total_bill, b2b_cost))
    
    conn.commit()
    conn.close()

    session.pop('cart', None)
    flash(f"Booking {order_code} confirmed successfully.")
    return redirect(url_for('my_bookings') if 'user_id' in session else url_for('index'))

# -------------------------------------------------------------
# ADMIN OPERATIONS
# -------------------------------------------------------------
@app.route('/admin')
def admin():
    conn = get_db_connection()
    orders = conn.execute('SELECT * FROM orders ORDER BY id DESC').fetchall()
    tests = conn.execute('SELECT * FROM tests WHERE is_active = 1 ORDER BY id DESC').fetchall()
    
    # Metrics
    metrics = conn.execute('''
        SELECT 
            COALESCE(SUM(total_bill), 0) as total_rev,
            COALESCE(SUM(b2b_total_cost), 0) as total_b2b
        FROM orders;
    ''').fetchone()
    
    conn.close()
    return render_template('admin.html', orders=orders, tests=tests, metrics=metrics)

@app.route('/admin/add_test', methods=['POST'])
def admin_add_test():
    name = request.form.get('name')
    category = request.form.get('category')
    sample_type = request.form.get('sample_type')
    b2b_cost = float(request.form.get('b2b_cost') or 0.0)
    retail_price = float(request.form.get('retail_price') or 0.0)
    turnaround_time = request.form.get('turnaround_time') or 'Same Day'
    partner_lab = request.form.get('partner_lab') or 'Accu Probe'

    conn = get_db_connection()
    conn.execute('''
        INSERT INTO tests (name, category, sample_type, b2b_cost, price, turnaround_time, partner_lab)
        VALUES (?, ?, ?, ?, ?, ?, ?);
    ''', (name, category, sample_type, b2b_cost, retail_price, turnaround_time, partner_lab))
    conn.commit()
    conn.close()

    flash(f"Test '{name}' successfully added to catalog.")
    return redirect(url_for('admin'))

# -------------------------------------------------------------
# RIDER FIELD APP
# -------------------------------------------------------------
@app.route('/rider')
@app.route('/rider_dashboard')
def rider_dashboard():
    conn = get_db_connection()
    orders = conn.execute("SELECT * FROM orders WHERE status != 'Completed' ORDER BY id DESC").fetchall()
    cash_collected = conn.execute("SELECT COALESCE(SUM(total_bill), 0) FROM orders WHERE is_paid = 1 AND payment_mode = 'Cash'").fetchone()[0]
    conn.close()
    return render_template('rider_dashboard.html', orders=orders, cash_collected=cash_collected)

@app.route('/api/rider/complete', methods=['POST'])
def rider_complete():
    order_id = request.form.get('order_id')
    barcode = request.form.get('barcode')
    temp_log = request.form.get('temperature')
    payment_mode = request.form.get('payment_mode')

    conn = get_db_connection()
    conn.execute('''
        UPDATE orders 
        SET barcode = ?, temp_log = ?, payment_mode = ?, status = 'Sample Collected', is_paid = 1
        WHERE id = ? OR order_code = ?;
    ''', (barcode, temp_log, payment_mode, order_id, order_id))
    conn.commit()
    conn.close()

    flash(f"Sample registered under Barcode {barcode}.")
    return redirect(url_for('rider_dashboard'))

# -------------------------------------------------------------
# DYNAMIC LIMS LAB REPORT
# -------------------------------------------------------------
@app.route('/lims_report/<int:order_id>')
def lims_report(order_id):
    conn = get_db_connection()
    order = conn.execute('SELECT * FROM orders WHERE id = ?', (order_id,)).fetchone()
    
    if not order:
        # Fallback to demo object if ID not found
        order = {
            'id': order_id,
            'order_code': f'CD-{order_id}',
            'full_name': 'Walk-in Patient',
            'tests_requested': 'Routine Investigation',
            'barcode': f'BC-{random.randint(100000, 999999)}',
            'temp_log': '4.0°C',
            'created_at': datetime.now().strftime('%d-%b-%Y %I:%M %p')
        }
    
    conn.close()
    return render_template('lims_report.html', order=order)

# -------------------------------------------------------------
# PATIENT PORTAL
# -------------------------------------------------------------
@app.route('/my_bookings')
def my_bookings():
    conn = get_db_connection()
    bookings = conn.execute('SELECT * FROM orders ORDER BY id DESC').fetchall()
    conn.close()
    return render_template('my_bookings.html', bookings=bookings)

@app.route('/login')
def login():
    return render_template('auth.html') if os.path.exists('templates/auth.html') else render_template('index.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
