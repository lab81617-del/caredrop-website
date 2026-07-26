import os
import sqlite3
from flask import Flask, redirect, render_template, request, session, url_for

app = Flask(__name__)
app.secret_key = "caredrop_enterprise_secure_key"

def get_db_connection():
    conn = sqlite3.connect('caredrop.db')
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def index():
    conn = get_db_connection()
    search_query = request.args.get('q', '').strip()
    category = request.args.get('cat', '').strip()
    
    try:
        if search_query:
            tests = conn.execute('SELECT * FROM tests WHERE name LIKE ? OR description LIKE ?', 
                                 (f'%{search_query}%', f'%{search_query}%')).fetchall()
        elif category:
            tests = conn.execute('SELECT * FROM tests WHERE category = ?', (category,)).fetchall()
        else:
            tests = conn.execute('SELECT * FROM tests').fetchall()
            
        categories = conn.execute('SELECT DISTINCT category FROM tests').fetchall()
    except Exception:
        tests = []
        categories = []
        
    conn.close()
    user_phone = session.get('user_phone')
    cart_count = len(session.get('cart', []))
    return render_template('index.html', tests=tests, categories=[c['category'] for c in categories if c['category']], user=user_phone, cart_count=cart_count)

@app.route('/cart/add/<int:test_id>')
def add_to_cart(test_id):
    if 'cart' not in session:
        session['cart'] = []
    if test_id not in session['cart']:
        session['cart'].append(test_id)
        session.modified = True
    return redirect(url_for('index'))

@app.route('/cart/remove/<int:test_id>')
def remove_from_cart(test_id):
    if 'cart' in session:
        session['cart'] = [i for i in session['cart'] if i != test_id]
        session.modified = True
    return redirect(url_for('view_cart'))

@app.route('/cart')
def view_cart():
    cart_ids = session.get('cart', [])
    if not cart_ids:
        return render_template('cart.html', tests=[], total=0)
    
    conn = get_db_connection()
    placeholders = ','.join(['?'] * len(cart_ids))
    tests = conn.execute(f'SELECT * FROM tests WHERE id IN ({placeholder})', cart_ids).fetchall()
    conn.close()
    
    total = sum(t['price'] for t in tests)
    return render_template('cart.html', tests=tests, total=total)

@app.route('/book', methods=['GET', 'POST'])
def book():
    conn = get_db_connection()
    cart_ids = session.get('cart', [])
    
    if request.method == 'POST':
        patient_name = request.form.get('patient_name')
        phone = request.form.get('phone')
        address = request.form.get('address')
        date = request.form.get('date')
        
        try:
            for test_id in cart_ids:
                conn.execute('''INSERT INTO bookings (test_id, patient_name, phone, address, booking_date, status)
                              VALUES (?, ?, ?, ?, ?, 'Pending')''',
                           (test_id, patient_name, phone, address, date))
            conn.commit()
            session.pop('cart', None)
            session['user_phone'] = phone
        except Exception:
            pass
            
        conn.close()
        return redirect(url_for('booking_success'))
        
    tests = []
    total = 0
    if cart_ids:
        placeholders = ','.join(['?'] * len(cart_ids))
        tests = conn.execute(f'SELECT * FROM tests WHERE id IN ({placeholder})', cart_ids).fetchall()
        total = sum(t['price'] for t in tests)
        
    conn.close()
    return render_template('book.html', tests=tests, total=total)

@app.route('/booking-success')
def booking_success():
    return render_template('booking_success.html')

@app.route('/my-bookings')
def my_bookings():
    user_phone = session.get('user_phone')
    if not user_phone:
        return redirect(url_for('index'))
        
    conn = get_db_connection()
    bookings = conn.execute('''
        SELECT bookings.*, tests.name as test_name, tests.price 
        FROM bookings 
        JOIN tests ON bookings.test_id = tests.id 
        WHERE bookings.phone = ? 
        ORDER BY bookings.id DESC
    ''', (user_phone,)).fetchall()
    conn.close()
    return render_template('my_bookings.html', bookings=bookings, user=user_phone)

@app.route('/logout')
def logout():
    session.pop('user_phone', None)
    return redirect(url_for('index'))

# Secret Admin Panel
@app.route('/admin-login', methods=['GET', 'POST'])
def admin_login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == 'admin' and password == 'caredrop123':
            session['admin_logged_in'] = True
            return redirect(url_for('admin_dashboard'))
        else:
            error = 'Invalid administrator credentials.'
    return render_template('admin_login.html', error=error)

@app.route('/admin')
def admin_dashboard():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
        
    conn = get_db_connection()
    bookings = conn.execute('''
        SELECT bookings.*, tests.name as test_name, tests.price 
        FROM bookings 
        JOIN tests ON bookings.test_id = tests.id 
        ORDER BY bookings.id DESC
    ''').fetchall()
    tests = conn.execute('SELECT * FROM tests ORDER BY id DESC').fetchall()
    conn.close()
    return render_template('admin.html', bookings=bookings, tests=tests)

@app.route('/admin/add-test', methods=['POST'])
def add_test():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
        
    name = request.form.get('name')
    category = request.form.get('category')
    price = request.form.get('price')
    description = request.form.get('description')
    fasting = request.form.get('fasting', 'No')
    timing = request.form.get('timing', '24 Hours')
    
    conn = get_db_connection()
    conn.execute('INSERT INTO tests (name, category, price, description, fasting_required, report_timing) VALUES (?, ?, ?, ?, ?, ?)',
                 (name, category, price, description, fasting, timing))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete-test/<int:test_id>')
def delete_test(test_id):
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
        
    conn = get_db_connection()
    conn.execute('DELETE FROM tests WHERE id = ?', (test_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/update-status/<int:booking_id>/<status>')
def update_status(booking_id, status):
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
        
    conn = get_db_connection()
    conn.execute('UPDATE bookings SET status = ? WHERE id = ?', (status, booking_id))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin_login'))

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
