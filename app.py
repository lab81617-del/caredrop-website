import os
import threading
import json
import io
import random
import traceback
import urllib.request
from flask import Flask, render_template, jsonify, request, session, redirect, url_for, send_file
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "caredrop-super-secret-key-2026")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "IHC2026!")

@app.errorhandler(Exception)
def handle_exception(e):
    error_trace = traceback.format_exc()
    return f"<h2>CareDrop System Diagnostics</h2><pre style='color:red; background:#F8FAFC; padding:20px; border:1px solid #CBD5E1; border-radius:8px;'>{error_trace}</pre>", 500

def get_db(): return psycopg2.connect(os.environ.get("DATABASE_URL"))
def release_db(conn): 
    if conn: conn.close()

def auto_migrate_db():
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS packages (id SERIAL PRIMARY KEY, title VARCHAR(255) NOT NULL, badge VARCHAR(50), discounted_price NUMERIC NOT NULL, original_price NUMERIC, features TEXT NOT NULL)")
        cursor.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS report_file BYTEA")
        cursor.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS report_filename VARCHAR(255)")
        cursor.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS prescription_file BYTEA")
        cursor.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS prescription_filename VARCHAR(255)")
        cursor.execute("ALTER TABLE packages ADD COLUMN IF NOT EXISTS lab_name VARCHAR(255) DEFAULT 'CareDrop Verified'")
        conn.commit()
    except Exception as e: print("Auto-Migrate Error:", e)
    finally: release_db(conn)

def send_email_api(recipient, subject, text_body):
    api_key = os.environ.get("BREVO_API_KEY")
    if not api_key: return "Missing BREVO_API_KEY"
    
    url = "https://api.brevo.com/v3/smtp/email"
    headers = {
        "accept": "application/json",
        "api-key": api_key,
        "content-type": "application/json"
    }
    data = {
        "sender": {"name": "CareDrop Diagnostics", "email": "ihcdiagnostics.ynr@gmail.com"},
        "to": [{"email": recipient}],
        "subject": subject,
        "textContent": text_body
    }
    try:
        req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers=headers, method='POST')
        urllib.request.urlopen(req)
        return "Success"
    except Exception as e:
        return str(e)

def send_email_async(recipient, subject, body):
    threading.Thread(target=send_email_api, args=(recipient, subject, body)).start()

@app.route('/api/send-otp', methods=['POST'])
def send_otp():
    email = request.json.get('email', '').strip()
    if not email: return jsonify({"success": False, "message": "Email is required."})
    
    otp = str(random.randint(1000, 9999))
    session[f'otp_{email}'] = otp
    msg = f"Your CareDrop Verification Code is: {otp}\n\nPlease use this 4-digit code to complete your request securely."
    
    if not os.environ.get("BREVO_API_KEY"):
        return jsonify({"success": False, "message": "CRITICAL ERROR: BREVO_API_KEY is missing in Render Environment."})
        
    result = send_email_api(email, f"CareDrop OTP: {otp}", msg)
    if result == "Success":
        return jsonify({"success": True, "message": "OTP sent to your email."})
    else:
        return jsonify({"success": False, "message": f"API CRASHED: {result}"})

@app.route('/api/verify-otp', methods=['POST'])
def verify_otp():
    email = request.json.get('email', '').strip()
    otp = request.json.get('otp', '').strip()
    if session.get(f'otp_{email}') == otp:
        session[f'verified_{email}'] = True
        return jsonify({"success": True, "message": "Email verified successfully."})
    return jsonify({"success": False, "message": "Invalid or expired OTP."})

@app.route('/')
def home():
    auto_migrate_db()
    conn = None
    packages = []
    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM packages ORDER BY id ASC")
        packages = cursor.fetchall()
    except Exception as e: pass
    finally: release_db(conn)
    return render_template('index.html', packages=packages)

@app.route('/tests')
def tests_catalog():
    conn = None
    tests_list = []
    pricing_list = []
    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT DISTINCT t.id, t.name, t.fasting_requirement, c.name as category 
            FROM tests t 
            JOIN test_categories c ON t.category_id = c.id 
            JOIN lab_test_pricing ltp ON t.id = ltp.test_id
            JOIN labs l ON ltp.lab_id = l.id
            WHERE t.is_active = TRUE AND l.is_active = TRUE 
            ORDER BY c.name, t.name
        """)
        tests_list = cursor.fetchall()
        cursor.execute("SELECT ltp.test_id, CAST(ltp.price AS FLOAT) as price, ltp.tat, l.id as lab_id, l.name as lab_name, l.badge_type FROM lab_test_pricing ltp JOIN labs l ON ltp.lab_id = l.id WHERE l.is_active = TRUE")
        pricing_list = cursor.fetchall()
    except Exception as e: pass
    finally: release_db(conn)
    return render_template('tests.html', tests=tests_list, pricing=json.dumps(pricing_list))

@app.route('/book')
def checkout_page(): return render_template('checkout.html')

@app.route('/my-bookings', methods=['GET', 'POST'])
def my_bookings():
    auto_migrate_db()
    email = request.args.get('email', '').strip()
    order_id_input = request.args.get('order_id', '').strip()
    orders = []
    
    if email and order_id_input:
        if not session.get(f'verified_{email}'):
            return render_template('my_bookings.html', error="Please verify your email via OTP first.", searched_email=email, searched_id=order_id_input)
        
        conn = None
        try:
            conn = get_db()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT o.id, o.patient_name, o.address, o.collection_date, o.time_slot, o.total_amount, o.status, 
                       u.phone, CASE WHEN o.report_file IS NOT NULL THEN TRUE ELSE FALSE END as has_report 
                FROM orders o JOIN users u ON o.user_id = u.id 
                WHERE u.email = %s AND o.id = %s
            """, (email, order_id_input))
            orders = cursor.fetchall()
            if orders:
                cursor.execute("""
                    SELECT oi.order_id, 
                           COALESCE(t.name, 'Special Health Package') as test_name, 
                           l.name as lab_name, oi.price 
                    FROM order_items oi 
                    LEFT JOIN tests t ON oi.test_id = t.id 
                    JOIN labs l ON oi.lab_id = l.id 
                    WHERE oi.order_id = %s
                """, (order_id_input,))
                db_items = cursor.fetchall()
                for order in orders: order['test_list'] = db_items
        except Exception as e: pass
        finally: release_db(conn)
        
    return render_template('my_bookings.html', orders=orders, searched_email=email, searched_id=order_id_input)

@app.route('/download-report/<int:order_id>')
def download_report(order_id):
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT report_file, report_filename FROM orders WHERE id = %s", (order_id,))
        record = cursor.fetchone()
        if record and record['report_file']:
            return send_file(io.BytesIO(record['report_file']), download_name=record['report_filename'], as_attachment=True)
    except Exception as e: pass
    finally: release_db(conn)
    return "Report not found.", 404
    
@app.route('/admin/download-prescription/<int:order_id>')
def download_prescription(order_id):
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT prescription_file, prescription_filename FROM orders WHERE id = %s", (order_id,))
        record = cursor.fetchone()
        if record and record['prescription_file']:
            return send_file(io.BytesIO(record['prescription_file']), download_name=record['prescription_filename'], as_attachment=True)
    except Exception as e: pass
    finally: release_db(conn)
    return "Prescription not found.", 404

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    error = None
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            return redirect(url_for('admin_dashboard'))
        else: error = "Access Denied."
    return f'<html><body style="background:#F1F5F9; display:flex; justify-content:center; align-items:center; height:100vh;"><div style="background:white; padding:40px; border-radius:12px; text-align:center;"><form method="POST"><input type="password" name="password" placeholder="Master Password" required style="padding:14px; margin-bottom:15px; width:100%;"><button type="submit" style="width:100%; background:#0F172A; color:white; padding:14px;">Login</button></form><div style="color:red;">{error if error else ""}</div></div></body></html>'

@app.route('/admin')
def admin_dashboard():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    auto_migrate_db()
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT o.id, o.patient_name, o.address, o.collection_date, o.time_slot, o.total_amount, o.status, u.phone, CASE WHEN o.report_file IS NOT NULL THEN TRUE ELSE FALSE END as has_report, CASE WHEN o.prescription_file IS NOT NULL THEN TRUE ELSE FALSE END as has_prescription FROM orders o JOIN users u ON o.user_id = u.id ORDER BY o.id DESC")
        orders = cursor.fetchall()
        cursor.execute("""
            SELECT oi.order_id, 
                   COALESCE(t.name, 'Special Health Package') as test_name, 
                   l.name as lab_name, oi.price 
            FROM order_items oi 
            LEFT JOIN tests t ON oi.test_id = t.id 
            JOIN labs l ON oi.lab_id = l.id
        """)
        db_items = cursor.fetchall()
        items_map = {}
        for row in db_items:
            if row['order_id'] not in items_map: items_map[row['order_id']] = []
            items_map[row['order_id']].append(row)
        for order in orders: order['test_list'] = items_map.get(order['id'], [])
        
        cursor.execute("SELECT id, name, is_active FROM labs ORDER BY name")
        all_labs = cursor.fetchall()
        cursor.execute("SELECT DISTINCT ON (LOWER(name)) id, name FROM labs WHERE is_active = TRUE ORDER BY LOWER(name), id")
        active_labs = cursor.fetchall()
        cursor.execute("SELECT id, name FROM test_categories ORDER BY name")
        categories = cursor.fetchall()
        cursor.execute("SELECT t.id as test_id, t.name as test_name, c.name as category_name, l.id as lab_id, l.name as lab_name, ltp.price FROM lab_test_pricing ltp JOIN tests t ON ltp.test_id = t.id JOIN labs l ON ltp.lab_id = l.id JOIN test_categories c ON t.category_id = c.id ORDER BY t.id DESC LIMIT 200")
        inventory = cursor.fetchall()
        cursor.execute("SELECT * FROM packages ORDER BY id DESC")
        packages = cursor.fetchall()
    except Exception as e: raise e 
    finally: release_db(conn)
    return render_template('admin.html', orders=orders, active_labs=active_labs, all_labs=all_labs, categories=categories, inventory=inventory, packages=packages)

@app.route('/admin/upload-report', methods=['POST'])
def upload_report():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    order_id = request.form.get('order_id')
    file = request.files.get('report_file')
    conn = None
    if file and file.filename:
        file_data = file.read()
        try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("UPDATE orders SET report_file = %s, report_filename = %s, status = 'Completed' WHERE id = %s", (psycopg2.Binary(file_data), file.filename, order_id))
            cursor.execute("SELECT u.email, o.patient_name FROM orders o JOIN users u ON o.user_id = u.id WHERE o.id = %s", (order_id,))
            user = cursor.fetchone()
            conn.commit()
            if user:
                msg = f"Hello {user[1]},\n\nYour test report for CareDrop Order #{order_id} is now available!\nPlease visit our website, click 'My Bookings', verify your email, and download your PDF."
                send_email_async(user[0], f"Your Test Report is Ready - #{order_id}", msg)
        except Exception as e: print(e)
        finally: release_db(conn)
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/update-order', methods=['POST'])
def admin_update_order():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("UPDATE orders SET status = %s WHERE id = %s", (request.form.get('status'), request.form.get('order_id')))
        conn.commit()
    except Exception: pass
    finally: release_db(conn)
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/add-test', methods=['POST'])
def admin_add_test():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    test_name, category_id, fasting, price, lab_ids = request.form.get('test_name').strip(), request.form.get('category_id'), request.form.get('fasting'), request.form.get('price'), request.form.getlist('lab_ids') 
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM tests WHERE name ILIKE %s", (test_name,))
        existing = cursor.fetchone()
        test_id = existing[0] if existing else cursor.execute("INSERT INTO tests (name, category_id, fasting_requirement, is_active) VALUES (%s, %s, %s, TRUE) RETURNING id", (test_name, category_id, fasting)) or cursor.fetchone()[0]
        for lab_id in lab_ids:
            cursor.execute("SELECT id FROM lab_test_pricing WHERE test_id = %s AND lab_id = %s", (test_id, lab_id))
            if cursor.fetchone(): cursor.execute("UPDATE lab_test_pricing SET price = %s WHERE test_id = %s AND lab_id = %s", (price, test_id, lab_id))
            else: cursor.execute("INSERT INTO lab_test_pricing (test_id, lab_id, price, tat) VALUES (%s, %s, %s, '24 Hours')", (test_id, lab_id, price))
        conn.commit()
    except: pass
    finally: release_db(conn)
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete-inventory/<int:test_id>/<int:lab_id>', methods=['POST'])
def delete_inventory(test_id, lab_id):
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    conn = None
    try:
        conn = get_db(); cursor = conn.cursor()
        cursor.execute("DELETE FROM lab_test_pricing WHERE test_id = %s AND lab_id = %s", (test_id, lab_id))
        conn.commit()
    except: pass
    finally: release_db(conn)
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/add-lab', methods=['POST'])
def add_lab():
    if session.get('admin_logged_in') and request.form.get('lab_name').strip():
        conn = None
        try:
            conn = get_db(); cursor = conn.cursor()
            cursor.execute("INSERT INTO labs (name, is_active) VALUES (%s, TRUE)", (request.form.get('lab_name').strip(),))
            conn.commit()
        except: pass
        finally: release_db(conn)
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/toggle-lab/<int:lab_id>', methods=['POST'])
def toggle_lab(lab_id):
    if session.get('admin_logged_in'):
        conn = None
        try:
            conn = get_db(); cursor = conn.cursor()
            cursor.execute("UPDATE labs SET is_active = NOT is_active WHERE id = %s", (lab_id,))
            conn.commit()
        except: pass
        finally: release_db(conn)
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete-lab/<int:lab_id>', methods=['POST'])
def delete_lab(lab_id):
    if session.get('admin_logged_in'):
        conn = None
        try:
            conn = get_db(); cursor = conn.cursor()
            cursor.execute("DELETE FROM lab_test_pricing WHERE lab_id = %s", (lab_id,))
            cursor.execute("DELETE FROM labs WHERE id = %s", (lab_id,))
            conn.commit()
        except: pass
        finally: release_db(conn)
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/add-package', methods=['POST'])
def add_package():
    if session.get('admin_logged_in'):
        conn = None
        try:
            conn = get_db(); cursor = conn.cursor()
            # NOW SAVES THE SPECIFIC LAB NAME WITH THE PACKAGE
            cursor.execute("INSERT INTO packages (title, badge, discounted_price, original_price, features, lab_name) VALUES (%s, %s, %s, %s, %s, %s)", (request.form.get('title').strip(), request.form.get('badge').strip(), request.form.get('discounted_price'), request.form.get('original_price'), request.form.get('features').strip(), request.form.get('lab_name', 'CareDrop Verified')))
            conn.commit()
        except: pass
        finally: release_db(conn)
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete-package/<int:pkg_id>', methods=['POST'])
def delete_package(pkg_id):
    if session.get('admin_logged_in'):
        conn = None
        try:
            conn = get_db(); cursor = conn.cursor()
            cursor.execute("DELETE FROM packages WHERE id = %s", (pkg_id,))
            conn.commit()
        except: pass
        finally: release_db(conn)
    return redirect(url_for('admin_dashboard'))

@app.route('/api/place-order', methods=['POST'])
def place_order():
    name = request.form.get('name', '').strip()
    phone = request.form.get('phone', '').strip()
    email = request.form.get('email', '').strip()
    patient_name = request.form.get('patient_name', '').strip()
    address = request.form.get('address', '').strip()
    date = request.form.get('date', '').strip()
    cart_json = request.form.get('cart', '[]')
    cart = json.loads(cart_json)
    
    if not session.get(f'verified_{email}'):
        return jsonify({"success": False, "message": "Email not verified. Please complete OTP verification."})
        
    prescription = request.files.get('prescription')
    
    if not all([name, phone, patient_name, address, date]): 
        return jsonify({"success": False, "message": "Missing required patient fields."})
        
    if not cart and not (prescription and prescription.filename):
        return jsonify({"success": False, "message": "Please add tests to your cart or upload a prescription."})
    
    conn = None
    try:
        conn = get_db(); cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
        user_id = user[0] if user else (cursor.execute("INSERT INTO users (name, phone, email) VALUES (%s, %s, %s) RETURNING id", (name, phone, email)) or cursor.fetchone()[0])
        
        if prescription and prescription.filename:
            file_data = prescription.read()
            cursor.execute("INSERT INTO orders (user_id, patient_name, address, collection_date, time_slot, total_amount, status, prescription_file, prescription_filename) VALUES (%s, %s, %s, %s, %s, %s, 'Pending', %s, %s) RETURNING id", (user_id, patient_name, address, date, request.form.get('time_slot', 'Morning'), request.form.get('total', 0), psycopg2.Binary(file_data), prescription.filename))
        else:
            cursor.execute("INSERT INTO orders (user_id, patient_name, address, collection_date, time_slot, total_amount, status) VALUES (%s, %s, %s, %s, %s, %s, 'Pending') RETURNING id", (user_id, patient_name, address, date, request.form.get('time_slot', 'Morning'), request.form.get('total', 0)))
            
        order_id = cursor.fetchone()[0]
        
        for item in cart: 
            clean_id = str(item['id']).replace('PKG_','')
            cursor.execute("INSERT INTO order_items (order_id, test_id, lab_id, price) VALUES (%s, %s, %s, %s)", (order_id, clean_id, item['selectedLabId'], item['currentPrice']))
        conn.commit()
        
        send_email_async(email, f"CareDrop Booking Confirmed - #{order_id}", f"Your Booking #{order_id} is confirmed!\nPatient: {patient_name}\nDate: {date}\nTotal: Rs. {request.form.get('total', 0)}")
        send_email_async("ihcdiagnostics.ynr@gmail.com", f"🚨 NEW ORDER #{order_id}", f"🚨 NEW BOOKING #{order_id}!\nPhone: {phone}\nPatient: {patient_name}\nAddress: {address}\nDate: {date}")
        
        return jsonify({"success": True, "order_id": order_id})
    except Exception as e: 
        if conn: conn.rollback()
        return jsonify({"success": False, "message": str(e)})
    finally: release_db(conn)

if __name__ == '__main__': app.run(debug=True, port=5000)
