from flask import Flask, render_template, request, session, redirect, url_for
import sqlite3
import json

app = Flask(__name__)
app.secret_key = 'care_drop_super_secret_key_123'

ADMIN_PASSWORD = "caredrop123"  # Secret admin lock password

def get_db_connection():
    conn = sqlite3.connect('caredrop.db')
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def home():
    conn = get_db_connection()
    tests = conn.execute('SELECT * FROM tests').fetchall()
    categories = [row['category'] for row in conn.execute('SELECT DISTINCT category FROM tests').fetchall()]
    
    user = None
    if 'user_id' in session:
        user = conn.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    
    conn.close()
    return render_template('index.html', tests=tests, categories=categories, user=user)

@app.route('/login', methods=['POST'])
def login():
    phone = request.form.get('phone')
    name = request.form.get('name', 'Patient')
    
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE phone = ?', (phone,)).fetchone()
    
    if not user:
        cursor = conn.cursor()
        cursor.execute('INSERT INTO users (phone, name) VALUES (?, ?)', (phone, name))
        conn.commit()
        user_id = cursor.lastrowid
    else:
        conn.execute('UPDATE users SET name = ? WHERE phone = ?', (name, phone))
        conn.commit()
        user_id = user['id']
        
    conn.close()
    session['user_id'] = user_id
    return redirect(url_for('home'))

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('home'))

@app.route('/checkout', methods=['POST'])
def checkout():
    if 'user_id' not in session:
        return redirect(url_for('home'))
        
    patient_name = request.form.get('patient_name')
    address = request.form.get('address')
    cart_data = request.form.get('cart_data')
    total_amount = request.form.get('total_amount')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO orders (user_id, patient_name, address, tests_ordered, total_amount) VALUES (?, ?, ?, ?, ?)',
                 (session['user_id'], patient_name, address, cart_data, total_amount))
    conn.commit()
    
    user = conn.execute('SELECT phone FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    conn.close()
    
    phone = user['phone'] if user else 'Customer'
    whatsapp_msg = f"New Booking! Name: {patient_name}, Phone: {phone}, Address: {address}, Total: ₹{total_amount} (Pay on Collection)."
    
    return f"""
    <div style='font-family:sans-serif; text-align:center; margin-top:80px; padding:20px; max-width:500px; margin-left:auto; margin-right:auto;'>
        <div style='background:#059669; color:white; width:60px; height:60px; border-radius:50%; display:flex; align-items:center; justify-content:center; margin:0 auto 20px auto; font-size:24px;'>✓</div>
        <h1 style='color:#0f172a; font-size:24px;'>Booking Confirmed!</h1>
        <p style='color:#64748b; font-size:14px; line-height:1.5;'>Thank you, {patient_name}. Our phlebotomist will collect samples from your address and payment will be collected securely at your doorstep.</p>
        <a href='https://wa.me/91903479760?text={whatsapp_msg}' target='_blank' style='display:block; background:#25D366; color:white; padding:14px; border-radius:14px; text-decoration:none; font-weight:bold; margin-top:20px;'>
            Notify Lab on WhatsApp
        </a>
        <a href='/' style='display:block; color:#64748b; font-weight:bold; margin-top:15px; font-size:13px;'>Back to Home</a>
    </div>
    """

@app.route('/upload-prescription', methods=['POST'])
def upload_prescription():
    if 'user_id' not in session:
        return redirect(url_for('home'))
        
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO prescriptions (user_id, image_path) VALUES (?, ?)',
                 (session['user_id'], 'prescription_slip.jpg'))
    conn.commit()
    
    user = conn.execute('SELECT phone FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    conn.close()
    
    phone = user['phone'] if user else 'Customer'
    whatsapp_msg = f"Prescription Uploaded by Patient Phone: {phone}. Please review."
    
    return f"""
    <div style='font-family:sans-serif; text-align:center; margin-top:80px; padding:20px; max-width:500px; margin-left:auto; margin-right:auto;'>
        <h1 style='color:#059669; font-size:24px;'>Prescription Received!</h1>
        <p style='color:#64748b; font-size:14px;'>Our technician will review your slip and call you to confirm test selection.</p>
        <a href='https://wa.me/91903479760?text={whatsapp_msg}' target='_blank' style='display:block; background:#25D366; color:white; padding:14px; border-radius:14px; text-decoration:none; font-weight:bold; margin-top:20px;'>
            Send to WhatsApp
        </a>
        <a href='/' style='display:block; color:#64748b; font-weight:bold; margin-top:15px; font-size:13px;'>Back to Home</a>
    </div>
    """

# HIDDEN ADMIN ROUTES (Access via: http://127.0.0.1:5000/caredrop-secure-admin)
@app.route('/caredrop-secure-admin-login', methods=['POST'])
def admin_login():
    password = request.form.get('password')
    if password == ADMIN_PASSWORD:
        session['is_admin'] = True
    return redirect(url_for('admin_dashboard'))

@app.route('/admin-logout')
def admin_logout():
    session.pop('is_admin', None)
    return redirect(url_for('admin_dashboard'))

@app.route('/caredrop-secure-admin', methods=['GET', 'POST'])
def admin_dashboard():
    if not session.get('is_admin'):
        return render_template('admin_login.html')
        
    conn = get_db_connection()
    
    if request.method == 'POST':
        test_name = request.form.get('test_name')
        lab_name = request.form.get('lab_name')
        b2b_price = int(request.form.get('b2b_price'))
        b2c_price = int(request.form.get('b2c_price'))
        category = request.form.get('category').strip().capitalize()
        
        conn.execute('INSERT INTO tests (test_name, lab_name, b2b_price, b2c_price, category) VALUES (?, ?, ?, ?, ?)',
                     (test_name, lab_name, b2b_price, b2c_price, category))
        conn.commit()
        return redirect(url_for('admin_dashboard'))
        
    tests = conn.execute('SELECT * FROM tests').fetchall()
    orders = conn.execute('''
        SELECT orders.id, orders.patient_name, orders.address, orders.tests_ordered, orders.total_amount, orders.payment_mode, orders.status, orders.order_date, users.phone 
        FROM orders JOIN users ON orders.user_id = users.id ORDER BY orders.id DESC
    ''').fetchall()
    
    prescriptions = conn.execute('''
        SELECT prescriptions.id, prescriptions.image_path, prescriptions.status, prescriptions.upload_date, users.phone 
        FROM prescriptions JOIN users ON prescriptions.user_id = users.id ORDER BY prescriptions.id DESC
    ''').fetchall()
    
    total_sales = sum([order['total_amount'] for order in orders]) if orders else 0
    total_orders_count = len(orders)
    
    total_profit = 0
    tests_dict = {t['test_name']: (t['b2c_price'] - t['b2b_price']) for t in tests}
    for order in orders:
        try:
            items = json.loads(order['tests_ordered'])
            for item in items:
                item_name = item.get('name')
                if item_name in tests_dict:
                    total_profit += tests_dict[item_name]
        except:
            pass

    conn.close()
    return render_template('admin.html', tests=tests, orders=orders, prescriptions=prescriptions, 
                           total_sales=total_sales, total_profit=total_profit, total_orders_count=total_orders_count)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)