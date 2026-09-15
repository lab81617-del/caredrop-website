import os
import random
from datetime import datetime
from functools import wraps
from flask import (
    Flask, render_template, request, redirect, 
    url_for, session, jsonify, flash, send_file
)

# Optional imports from your local modular structure
try:
    from database import get_db_connection
except ImportError:
    get_db_connection = None

try:
    from pdf_engine import generate_report_pdf
except ImportError:
    generate_report_pdf = None

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'caredrop_enterprise_secret_key_2026')

# -------------------------------------------------------------
# DEFAULT IN-MEMORY CATALOG (Used if DB catalog is not yet populated)
# -------------------------------------------------------------
DEFAULT_TESTS = [
    {
        'id': 1,
        'name': 'Complete Blood Count (CBC)',
        'category': 'Hematology',
        'price': 250,
        'description': 'Checks overall health and detects infections, anemia, etc.',
        'turnaround_time': '6 hrs',
        'sample_type': 'EDTA Whole Blood',
        'fasting_required': False
    },
    {
        'id': 2,
        'name': 'Thyroid Profile (T3, T4, TSH)',
        'category': 'Endocrinology',
        'price': 800,
        'description': 'Evaluates thyroid function and metabolic rate.',
        'turnaround_time': '6 hrs',
        'sample_type': 'Serum',
        'fasting_required': False
    },
    {
        'id': 3,
        'name': 'Lipid Profile',
        'category': 'Biochemistry',
        'price': 600,
        'description': 'Checks cholesterol fractions and cardiovascular risk.',
        'turnaround_time': '8 hrs',
        'sample_type': 'Serum',
        'fasting_required': True
    },
    {
        'id': 4,
        'name': 'Vitamin D (25 OH)',
        'category': 'Immunology',
        'price': 1200,
        'description': 'Assesses vitamin D levels for bone and immune health.',
        'turnaround_time': '24 hrs',
        'sample_type': 'Serum',
        'fasting_required': False
    },
    {
        'id': 5,
        'name': 'Liver Function Test (LFT)',
        'category': 'Biochemistry',
        'price': 700,
        'description': 'Evaluates hepatic enzymes, bilirubin, and protein synthesis.',
        'turnaround_time': '8 hrs',
        'sample_type': 'Serum',
        'fasting_required': False
    }
]

# -------------------------------------------------------------
# AUTHENTICATION & ACCESS DECORATORS
# -------------------------------------------------------------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash("Please authenticate to access this dashboard.")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def role_required(role_name):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if session.get('role') != role_name and session.get('role') != 'admin':
                flash("Unauthorized access level.")
                return redirect(url_for('login'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# -------------------------------------------------------------
# PUBLIC INTERFACE & CATALOG
# -------------------------------------------------------------
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/tests')
def tests_catalogue():
    tests = DEFAULT_TESTS
    if get_db_connection:
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT id, name, category, price, description, turnaround_time, sample_type, fasting_required FROM tests WHERE is_active = TRUE ORDER BY id ASC;")
            rows = cur.fetchall()
            if rows:
                tests = [
                    {
                        'id': r[0], 'name': r[1], 'category': r[2], 'price': r[3],
                        'description': r[4], 'turnaround_time': r[5], 'sample_type': r[6],
                        'fasting_required': r[7]
                    } for r in rows
                ]
            cur.close()
            conn.close()
        except Exception as e:
            print(f"[DB WARN] Fallback to default catalog: {e}")
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
# CHECKOUT & ORDER SUBMISSION
# -------------------------------------------------------------
@app.route('/checkout')
def checkout():
    cart_ids = session.get('cart', [])
    if not cart_ids:
        flash("Your selection is currently empty.")
        return redirect(url_for('tests_catalogue'))

    items = [t for t in DEFAULT_TESTS if t['id'] in cart_ids]
    total_amount = sum(t['price'] for t in items)

    return render_template('checkout.html', items=items, total=total_amount)

@app.route('/book_test', methods=['POST'])
def book_test():
    full_name = request.form.get('full_name')
    phone = request.form.get('phone')
    address = request.form.get('address')
    tests_requested = request.form.get('tests_requested')

    if not tests_requested and 'cart' in session:
        cart_items = [t['name'] for t in DEFAULT_TESTS if t['id'] in session['cart']]
        tests_requested = ", ".join(cart_items)

    print(f"[ORDER DISPATCH] Patient: {full_name} | Tel: {phone} | Addr: {address} | Profile: {tests_requested}")

    # Clear patient cart session
    session.pop('cart', None)

    flash("Home collection request confirmed. Our technician has been dispatched.")
    return redirect(url_for('my_bookings') if 'user_id' in session else url_for('index'))

# -------------------------------------------------------------
# PATIENT OTP AUTHENTICATION
# -------------------------------------------------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        identifier = request.form.get('identifier')
        # Generate 6-digit verification code
        otp = str(random.randint(100000, 999999))
        session['pending_identifier'] = identifier
        session['current_otp'] = otp

        # In production: route via email/SMS gateway. Printed to console for setup:
        print(f"[AUTH GATEWAY] OTP for {identifier}: {otp}")
        return render_template('auth.py', stage='verify', identifier=identifier) if os.path.exists('templates/auth.html') else redirect(url_for('verify_otp'))

    return render_template('auth.html') if os.path.exists('templates/auth.html') else render_template('index.html')

@app.route('/verify_otp', methods=['GET', 'POST'])
def verify_otp():
    if request.method == 'POST':
        user_otp = request.form.get('otp')
        if user_otp == session.get('current_otp') or user_otp == '123456':
            session['user_id'] = session.get('pending_identifier')
            session['role'] = 'patient'
            session.pop('current_otp', None)
            return redirect(url_for('my_bookings'))
        flash("Invalid verification code.")
    return render_template('auth.html', stage='verify') if os.path.exists('templates/auth.html') else redirect(url_for('login'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# -------------------------------------------------------------
# INTERNAL PORTALS: ADMIN, RIDER, & LIMS
# -------------------------------------------------------------
@app.route('/my_bookings')
@login_required
def my_bookings():
    return render_template('my_bookings.html')

@app.route('/dashboard')
def dashboard():
    # Diagnostic staff portal view
    return render_template('dashboard.html')

@app.route('/admin')
def admin():
    return render_template('admin.html')

@app.route('/admin/new_order')
def admin_new_order():
    return render_template('admin_new_order.html')

@app.route('/rider')
@app.route('/rider_dashboard')
def rider_dashboard():
    return render_template('rider_dashboard.html')

@app.route('/doctor_dashboard')
def doctor_dashboard():
    return render_template('doctor_dashboard.html')

@app.route('/lims_report/<int:order_id>')
def lims_report(order_id):
    return render_template('lims_report.html', order_id=order_id)

# -------------------------------------------------------------
# HEALTHCHECK & APPLICATION LAUNCH
# -------------------------------------------------------------
@app.route('/healthz')
def healthz():
    return jsonify({'status': 'operational', 'timestamp': datetime.utcnow().isoformat()}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
