import os
import threading
import json
import io
import csv
import random
import traceback
import urllib.request
from datetime import datetime
from flask import Flask, render_template, jsonify, request, session, redirect, url_for, send_file
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "caredrop-super-secret-key-2026")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "IHC2026!")

app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'pool_size': 5, 'max_overflow': 2, 'pool_recycle': 300, 'pool_pre_ping': True}

@app.errorhandler(Exception)
def handle_exception(e):
    return f"<h2>CareDrop System Diagnostics</h2><pre style='color:red; background: #F8FAFC; padding: 20px; border:1px solid #CBD5E1; border-radius: 8px;'>{traceback.format_exc()}</pre>", 500

def get_db(): return psycopg2.connect(os.environ.get("DATABASE_URL"))
def release_db(conn):
    if conn: conn.close()

def safe_migrate(query):
    conn = None
    try:
        conn = get_db(); cursor = conn.cursor(); cursor.execute(query); conn.commit()
    except:
        if conn: conn.rollback()
    finally: release_db(conn)

def auto_migrate_db():
    safe_migrate("CREATE TABLE IF NOT EXISTS test_categories (id SERIAL PRIMARY KEY, name VARCHAR(255) UNIQUE)")
    safe_migrate("ALTER TABLE labs ADD CONSTRAINT labs_name_key UNIQUE (name)")
    safe_migrate("ALTER TABLE labs ADD COLUMN IF NOT EXISTS rating NUMERIC DEFAULT 4.5")
    safe_migrate("ALTER TABLE labs ADD COLUMN IF NOT EXISTS cert_badge VARCHAR(100) DEFAULT 'Verified Partner'")
    safe_migrate("ALTER TABLE tests ADD COLUMN IF NOT EXISTS symptoms TEXT")
    safe_migrate("ALTER TABLE tests ADD CONSTRAINT tests_name_key UNIQUE (name)")
    safe_migrate("CREATE TABLE IF NOT EXISTS health_packages (id SERIAL PRIMARY KEY, title VARCHAR(255) NOT NULL, lab_id INTEGER REFERENCES labs(id) ON DELETE CASCADE, price NUMERIC NOT NULL, description TEXT)")
    safe_migrate("CREATE TABLE IF NOT EXISTS package_tests (package_id INTEGER REFERENCES health_packages(id) ON DELETE CASCADE, test_id INTEGER REFERENCES tests(id) ON DELETE CASCADE, PRIMARY KEY (package_id, test_id))")
    safe_migrate("CREATE TABLE IF NOT EXISTS special_offers (id SERIAL PRIMARY KEY, package_id INTEGER REFERENCES health_packages(id) ON DELETE CASCADE UNIQUE, discount_percent NUMERIC NOT NULL, badge VARCHAR(50), end_date DATE NOT NULL)")
    safe_migrate("ALTER TABLE orders ADD COLUMN IF NOT EXISTS report_file BYTEA")
    safe_migrate("ALTER TABLE orders ADD COLUMN IF NOT EXISTS report_filename VARCHAR(255)")
    safe_migrate("ALTER TABLE orders ADD COLUMN IF NOT EXISTS prescription_file BYTEA")
    safe_migrate("ALTER TABLE orders ADD COLUMN IF NOT EXISTS prescription_filename VARCHAR(255)")
    safe_migrate("ALTER TABLE orders ADD COLUMN IF NOT EXISTS age INTEGER")
    safe_migrate("ALTER TABLE orders ADD COLUMN IF NOT EXISTS gender VARCHAR(20)")
    safe_migrate("CREATE TABLE IF NOT EXISTS order_items (id SERIAL PRIMARY KEY, order_id INTEGER, test_id INTEGER, lab_id INTEGER, price NUMERIC)")
    safe_migrate("ALTER TABLE order_items ADD COLUMN IF NOT EXISTS item_type VARCHAR(20) DEFAULT 'test'")
    safe_migrate("CREATE TABLE IF NOT EXISTS patient_feedback (id SERIAL PRIMARY KEY, order_id INTEGER, patient_email VARCHAR(255), message TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    safe_migrate("ALTER TABLE lab_test_pricing ADD COLUMN IF NOT EXISTS parameter_count INTEGER DEFAULT 1")
    safe_migrate("CREATE TABLE IF NOT EXISTS phlebotomists (id SERIAL PRIMARY KEY, name VARCHAR(150), phone VARCHAR(50), vehicle_number VARCHAR(50), active_status BOOLEAN DEFAULT TRUE)")
    safe_migrate("ALTER TABLE orders ADD COLUMN IF NOT EXISTS phlebotomist_id INTEGER REFERENCES phlebotomists(id)")
    safe_migrate("ALTER TABLE orders ADD COLUMN IF NOT EXISTS payout_amount NUMERIC DEFAULT 0")
    # NEW: Patient UIDs & Order References
    safe_migrate("ALTER TABLE users ADD COLUMN IF NOT EXISTS patient_uid VARCHAR(50)")
    safe_migrate("ALTER TABLE orders ADD COLUMN IF NOT EXISTS order_ref VARCHAR(50)")

def send_email_api(recipient, subject, text_body):
    api_key = os.environ.get("BREVO_API_KEY")
    if not api_key: return "Missing BREVO_API_KEY"
    url = "https://api.brevo.com/v3/smtp/email"
    headers = {"accept": "application/json", "api-key": api_key, "content-type": "application/json"}
    data = {"sender": {"name": "CareDrop Diagnostics", "email": "ihcdiagnostics.ynr@gmail.com"}, "to": [{"email": recipient}], "subject": subject, "textContent": text_body}
    try:
        req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers=headers, method='POST')
        urllib.request.urlopen(req)
        return "Success"
    except Exception as e: return str(e)

def send_email_async(recipient, subject, body):
    threading.Thread(target=send_email_api, args=(recipient, subject, body)).start()

@app.route('/ping')
def ping(): return "OK", 200

@app.route('/api/send-otp', methods=['POST'])
def send_otp():
    email = request.json.get('email', '').strip()
    if not email: return jsonify({"success": False, "message": "Email is required."})
    otp = str(random.randint(1000, 9999))
    session[f'otp_{email}'] = otp
    msg = f"Your CareDrop Verification Code is: {otp}\n\nPlease use this 4-digit code to complete your request securely."
    if not os.environ.get("BREVO_API_KEY"): return jsonify({"success": False, "message": "CRITICAL ERROR: BREVO_API_KEY is missing."})
    if send_email_api(email, f"CareDrop OTP: {otp}", msg) == "Success": return jsonify({"success": True, "message": "OTP sent to your email."})
    return jsonify({"success": False, "message": "API CRASHED"})

@app.route('/api/verify-otp', methods=['POST'])
def verify_otp():
    email = request.json.get('email', '').strip()
    if session.get(f'otp_{email}') == request.json.get('otp', '').strip():
        session[f'verified_{email}'] = True
        return jsonify({"success": True, "message": "Verified."})
    return jsonify({"success": False, "message": "Invalid OTP."})

@app.route('/')
def home():
    auto_migrate_db()
    conn = None; packages = []
    try:
        conn = get_db(); cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT hp.id, hp.title, CAST(hp.price AS INTEGER) as original_price, l.id as lab_id, l.name as lab_name, l.rating,
            string_agg(t.name, ', ') as features, so.id as offer_id, CAST(so.discount_percent AS INTEGER) as discount_percent, so.badge, 
            CAST(ROUND(hp.price * (1 - (COALESCE(so.discount_percent, 0) / 100.0))) AS INTEGER) as discounted_price
            FROM health_packages hp JOIN labs l ON hp.lab_id = l.id LEFT JOIN package_tests pt ON hp.id = pt.package_id LEFT JOIN tests t ON pt.test_id = t.id
            JOIN special_offers so ON hp.id = so.package_id AND so.end_date >= CURRENT_DATE
            GROUP BY hp.id, l.id, l.name, l.rating, so.id, so.discount_percent, so.badge ORDER BY hp.id DESC
        """)
        packages = cursor.fetchall()
    except Exception: pass
    finally: release_db(conn)
    return render_template('index.html', packages=packages)

@app.route('/tests')
def tests_catalog():
    conn = None; grouped_tests = {}; pricing_list = []; packages_list = []
    try:
        conn = get_db(); cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT DISTINCT t.id, t.name, t.fasting_requirement, t.symptoms, c.name as category FROM tests t LEFT JOIN test_categories c ON t.category_id = c.id JOIN lab_test_pricing ltp ON t.id = ltp.test_id JOIN labs l ON ltp.lab_id = l.id WHERE t.is_active = TRUE AND l.is_active = TRUE ORDER BY c.name, t.name")
        for t in cursor.fetchall():
            cat = t['category'] or 'Uncategorized'
            if cat not in grouped_tests: grouped_tests[cat] = []
            grouped_tests[cat].append(t)
        cursor.execute("SELECT ltp.test_id, CAST(ltp.price AS INTEGER) as price, l.id as lab_id, l.name as lab_name, CAST(l.rating AS FLOAT) as rating FROM lab_test_pricing ltp JOIN labs l ON ltp.lab_id = l.id WHERE l.is_active = TRUE")
        pricing_list = cursor.fetchall()
        cursor.execute("""
            SELECT hp.id, hp.title, CAST(hp.price AS INTEGER) as original_price, l.id as lab_id, l.name as lab_name, CAST(l.rating AS FLOAT) as rating, string_agg(t.name, ', ') as features, so.id as offer_id, CAST(so.discount_percent AS INTEGER) as discount_percent, CAST(ROUND(hp.price * (1 - (COALESCE(so.discount_percent, 0) / 100.0))) AS INTEGER) as discounted_price
            FROM health_packages hp JOIN labs l ON hp.lab_id = l.id LEFT JOIN package_tests pt ON hp.id = pt.package_id LEFT JOIN tests t ON pt.test_id = t.id LEFT JOIN special_offers so ON hp.id = so.package_id AND so.end_date >= CURRENT_DATE
            GROUP BY hp.id, l.id, l.name, l.rating, so.id, so.discount_percent ORDER BY hp.id DESC
        """)
        packages_list = cursor.fetchall()
    except Exception: pass
    finally: release_db(conn)
    return render_template('tests.html', grouped_tests=grouped_tests, pricing=json.dumps(pricing_list, default=str), packages=json.dumps(packages_list, default=str), raw_packages=packages_list)

@app.route('/book')
def checkout_page(): return render_template('checkout.html')

@app.route('/my-bookings', methods=['GET', 'POST'])
def my_bookings():
    auto_migrate_db()
    email = request.args.get('email', '').strip()
    orders = []
    if email:
        if not session.get(f'verified_{email}'): return render_template('my_bookings.html', error="Please verify your email via OTP first.", searched_email=email)
        conn = None
        try:
            conn = get_db(); cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT o.id, o.order_ref, o.patient_name, o.age, o.gender, o.collection_date, o.time_slot, CAST(o.total_amount AS INTEGER) as total_amount, o.status, u.patient_uid, CASE WHEN o.report_file IS NOT NULL THEN TRUE ELSE FALSE END as has_report FROM orders o JOIN users u ON o.user_id = u.id WHERE u.email = %s ORDER BY o.id DESC", (email,))
            orders = cursor.fetchall()
            if orders:
                for order in orders:
                    cursor.execute("SELECT CASE WHEN oi.item_type = 'package' THEN hp.title ELSE t.name END as test_name, l.name as lab_name FROM order_items oi LEFT JOIN tests t ON oi.test_id = t.id AND oi.item_type = 'test' LEFT JOIN health_packages hp ON oi.test_id = hp.id AND oi.item_type = 'package' JOIN labs l ON oi.lab_id = l.id WHERE oi.order_id = %s", (order['id'],))
                    order['test_list'] = cursor.fetchall()
        except Exception: pass
        finally: release_db(conn)
    return render_template('my_bookings.html', orders=orders, searched_email=email)

@app.route('/download-report/<int:order_id>')
def download_report(order_id):
    conn = None
    try:
        conn = get_db(); cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT report_file, report_filename FROM orders WHERE id = %s", (order_id,))
        record = cursor.fetchone()
        if record and record['report_file']: return send_file(io.BytesIO(record['report_file']), download_name=record['report_filename'], as_attachment=True)
    except Exception: pass
    finally: release_db(conn)
    return "Report not found.", 404

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    error = None
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            return redirect(url_for('admin_dashboard'))
        else: error = "Access Denied."
    return f'<html><body style="background:#F1F5F9; display: flex; justify-content:center; align-items:center; height: 100vh; font-family: sans-serif;"><div style="background: white; padding:40px; border-radius: 12px; text-align:center;"><form method="POST"><input type="password" name="password" placeholder="Master Password" required style="padding: 14px; margin-bottom: 15px; width:100%; border:1px solid #ccc; border-radius:8px;"><button type="submit" style="width:100%; background: #0F172A; color: white; padding: 14px; border: none; border-radius: 8px; font-weight:bold; cursor:pointer;">Login</button></form><div style="color:red; margin-top:10px;">{error if error else ""}</div></div></body></html>'

@app.route('/admin')
def admin_dashboard():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    auto_migrate_db()
    conn = None
    try:
        conn = get_db(); cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT o.id, o.order_ref, o.patient_name, o.age, o.gender, o.address, o.collection_date, o.time_slot, CAST(o.total_amount AS INTEGER) as total_amount, o.status, u.phone, u.patient_uid, CASE WHEN o.report_file IS NOT NULL THEN TRUE ELSE FALSE END as has_report, o.phlebotomist_id, CAST(o.payout_amount AS INTEGER) as payout_amount FROM orders o JOIN users u ON o.user_id = u.id ORDER BY o.id DESC")
        orders = cursor.fetchall()
        
        cursor.execute("SELECT oi.order_id, CASE WHEN oi.item_type = 'package' THEN hp.title ELSE t.name END as test_name, l.name as lab_name, CAST(oi.price AS INTEGER) as price FROM order_items oi LEFT JOIN tests t ON oi.test_id = t.id AND oi.item_type = 'test' LEFT JOIN health_packages hp ON oi.test_id = hp.id AND oi.item_type = 'package' JOIN labs l ON oi.lab_id = l.id")
        db_items = cursor.fetchall()
        items_map = {}
        for row in db_items:
            if row['order_id'] not in items_map: items_map[row['order_id']] = []
            items_map[row['order_id']].append(row)
        for order in orders: order['test_list'] = items_map.get(order['id'], [])
        
        cursor.execute("SELECT id, name FROM labs WHERE is_active = TRUE ORDER BY name")
        active_labs = cursor.fetchall()
        
        cursor.execute("SELECT id, name FROM test_categories ORDER BY name")
        categories = cursor.fetchall()
        
        cursor.execute("SELECT t.id as test_id, t.name as test_name, c.name as category_name, l.id as lab_id, l.name as lab_name, CAST(ltp.price AS INTEGER) as price, COALESCE(ltp.parameter_count, 1) as parameter_count FROM lab_test_pricing ltp JOIN tests t ON ltp.test_id = t.id JOIN labs l ON ltp.lab_id = l.id LEFT JOIN test_categories c ON t.category_id = c.id ORDER BY t.name ASC")
        inventory = cursor.fetchall()
        
        cursor.execute("SELECT t.id, t.name, c.name as category_name, t.fasting_requirement, t.symptoms FROM tests t LEFT JOIN test_categories c ON t.category_id = c.id ORDER BY t.name ASC")
        master_tests = cursor.fetchall()
        
        cursor.execute("SELECT * FROM phlebotomists ORDER BY id DESC")
        phlebotomists = cursor.fetchall()

    except Exception as e: raise e
    finally: release_db(conn)
    return render_template('admin.html', orders=orders, active_labs=active_labs, categories=categories, inventory=inventory, master_tests=master_tests, phlebotomists=phlebotomists)

# NEW: The Walk-In Booking Engine
@app.route('/admin/walk-in', methods=['POST'])
def admin_walk_in():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    
    patient_name = request.form.get('patient_name', '').strip()
    phone = request.form.get('phone', '').strip()
    age = request.form.get('age', '').strip()
    gender = request.form.get('gender', '').strip()
    total = request.form.get('total_amount', 0)
    test_id = request.form.get('test_id')
    lab_id = request.form.get('lab_id')
    
    conn = None
    try:
        conn = get_db(); cursor = conn.cursor()
        
        # 1. Create or Find User & Generate Patient UID
        cursor.execute("SELECT id, patient_uid FROM users WHERE phone = %s", (phone,))
        user = cursor.fetchone()
        
        if user:
            user_id = user[0]
            if not user[1]: # Retroactively add UID if missing
                uid = f"CD-PAT-{1000 + user_id}"
                cursor.execute("UPDATE users SET patient_uid = %s WHERE id = %s", (uid, user_id))
        else:
            cursor.execute("INSERT INTO users (name, phone, email) VALUES (%s, %s, %s) RETURNING id", (patient_name, phone, f"walkin_{phone}@caredrop.local"))
            user_id = cursor.fetchone()[0]
            uid = f"CD-PAT-{1000 + user_id}"
            cursor.execute("UPDATE users SET patient_uid = %s WHERE id = %s", (uid, user_id))
            
        # 2. Create Order & Generate Order Reference Number
        today = datetime.today().strftime('%Y-%m-%d')
        cursor.execute("INSERT INTO orders (user_id, patient_name, age, gender, address, collection_date, time_slot, total_amount, status) VALUES (%s, %s, %s, %s, 'Walk-In Clinic', %s, 'Immediate', %s, 'Completed') RETURNING id", (user_id, patient_name, age, gender, today, total))
        order_id = cursor.fetchone()[0]
        
        order_ref = f"ORD-{datetime.today().strftime('%y%m')}-{order_id:04d}"
        cursor.execute("UPDATE orders SET order_ref = %s WHERE id = %s", (order_ref, order_id))
        
        # 3. Add Item
        cursor.execute("INSERT INTO order_items (order_id, test_id, lab_id, price, item_type) VALUES (%s, %s, %s, %s, 'test')", (order_id, test_id, lab_id, total))
        
        conn.commit()
    except Exception as e:
        if conn: conn.rollback()
    finally: release_db(conn)
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/bulk-upload', methods=['POST'])
def bulk_upload():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    file = request.files.get('csv_file')
    if not file or file.filename == '': return redirect(url_for('admin_dashboard'))
    conn = None
    try:
        stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
        csv_input = csv.reader(stream); next(csv_input, None)
        conn = get_db(); cursor = conn.cursor()
        for row in csv_input:
            if len(row) < 4: continue
            name, cat_name, fasting, symptoms = [str(r).strip() for r in row[:4]]
            if not name: continue
            cursor.execute("INSERT INTO test_categories (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (cat_name,))
            cursor.execute("SELECT id FROM test_categories WHERE name = %s", (cat_name,))
            cat_id = cursor.fetchone()[0]
            cursor.execute("INSERT INTO tests (name, category_id, fasting_requirement, is_active, symptoms) VALUES (%s, %s, %s, TRUE, %s) ON CONFLICT (name) DO NOTHING", (name, cat_id, fasting, symptoms))
        conn.commit()
    except Exception: pass
    finally: release_db(conn)
    return redirect(url_for('admin_dashboard'))

if __name__ == '__main__': app.run(debug=True, port=5000)
