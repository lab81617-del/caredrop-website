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

def safe_migrate(query):
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(query)
        conn.commit()
    except:
        if conn: conn.rollback()
    finally:
        release_db(conn)

def auto_migrate_db():
    safe_migrate("CREATE TABLE IF NOT EXISTS test_categories (id SERIAL PRIMARY KEY, name VARCHAR(255) UNIQUE)")
    safe_migrate("ALTER TABLE labs ADD CONSTRAINT labs_name_key UNIQUE (name)")
    safe_migrate("ALTER TABLE labs ADD COLUMN IF NOT EXISTS rating NUMERIC DEFAULT 4.5")
    safe_migrate("ALTER TABLE labs ADD COLUMN IF NOT EXISTS cert_badge VARCHAR(100) DEFAULT 'Verified Partner'")
    safe_migrate("CREATE TABLE IF NOT EXISTS health_packages (id SERIAL PRIMARY KEY, title VARCHAR(255) NOT NULL, lab_id INTEGER REFERENCES labs(id) ON DELETE CASCADE, price NUMERIC NOT NULL)")
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
    result = send_email_api(email, f"CareDrop OTP: {otp}", msg)
    if result == "Success": return jsonify({"success": True, "message": "OTP sent to your email."})
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
        conn = get_db(); cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT hp.id, hp.title, CAST(hp.price AS INTEGER) as original_price, l.id as lab_id, l.name as lab_name,
                   string_agg(t.name, ', ') as features,
                   so.id as offer_id, CAST(so.discount_percent AS INTEGER) as discount_percent, so.badge, TO_CHAR(so.end_date, 'DD Mon YYYY') as end_date,
                   CAST(ROUND(hp.price * (1 - (COALESCE(so.discount_percent, 0) / 100.0))) AS INTEGER) as discounted_price
            FROM health_packages hp
            JOIN labs l ON hp.lab_id = l.id
            LEFT JOIN package_tests pt ON hp.id = pt.package_id
            LEFT JOIN tests t ON pt.test_id = t.id
            JOIN special_offers so ON hp.id = so.package_id AND so.end_date >= CURRENT_DATE
            GROUP BY hp.id, l.id, l.name, so.id, so.discount_percent, so.badge, so.end_date
            ORDER BY hp.id DESC
        """)
        packages = cursor.fetchall()
    except Exception as e: pass
    finally: release_db(conn)
    return render_template('index.html', packages=packages)

@app.route('/tests')
def tests_catalog():
    conn = None
    grouped_tests = {}
    pricing_list = []
    packages_list = []
    try:
        conn = get_db(); cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT DISTINCT t.id, t.name, t.fasting_requirement, c.name as category FROM tests t JOIN test_categories c ON t.category_id = c.id JOIN lab_test_pricing ltp ON t.id = ltp.test_id JOIN labs l ON ltp.lab_id = l.id WHERE t.is_active = TRUE AND l.is_active = TRUE ORDER BY c.name, t.name")
        raw_tests = cursor.fetchall()
        for t in raw_tests:
            cat = t['category']
            if cat not in grouped_tests: grouped_tests[cat] = []
            grouped_tests[cat].append(t)
            
        cursor.execute("SELECT ltp.test_id, CAST(ltp.price AS INTEGER) as price, l.id as lab_id, l.name as lab_name, CAST(l.rating AS FLOAT) as rating, l.cert_badge FROM lab_test_pricing ltp JOIN labs l ON ltp.lab_id = l.id WHERE l.is_active = TRUE")
        pricing_list = cursor.fetchall()
        
        cursor.execute("""
            SELECT hp.id, hp.title, CAST(hp.price AS INTEGER) as original_price, l.id as lab_id, l.name as lab_name, CAST(l.rating AS FLOAT) as rating, l.cert_badge,
                   COALESCE(array_remove(array_agg(t.id), NULL), '{}') as test_ids,
                   string_agg(t.name, ', ') as features,
                   so.id as offer_id, CAST(so.discount_percent AS INTEGER) as discount_percent, so.badge, TO_CHAR(so.end_date, 'DD Mon YYYY') as end_date,
                   CAST(ROUND(hp.price * (1 - (COALESCE(so.discount_percent, 0) / 100.0))) AS INTEGER) as discounted_price
            FROM health_packages hp
            JOIN labs l ON hp.lab_id = l.id
            LEFT JOIN package_tests pt ON hp.id = pt.package_id
            LEFT JOIN tests t ON pt.test_id = t.id
            LEFT JOIN special_offers so ON hp.id = so.package_id AND so.end_date >= CURRENT_DATE
            GROUP BY hp.id, l.id, l.name, l.rating, l.cert_badge, so.id, so.discount_percent, so.badge, so.end_date
            ORDER BY hp.id DESC
        """)
        packages_list = cursor.fetchall()
    except Exception as e: pass
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
            cursor.execute("SELECT o.id, o.patient_name, o.age, o.gender, o.address, o.collection_date, o.time_slot, CAST(o.total_amount AS INTEGER) as total_amount, o.status, u.phone, CASE WHEN o.report_file IS NOT NULL THEN TRUE ELSE FALSE END as has_report FROM orders o JOIN users u ON o.user_id = u.id WHERE u.email = %s ORDER BY o.id DESC", (email,))
            orders = cursor.fetchall()
            if orders:
                for order in orders:
                    cursor.execute("""
                        SELECT oi.order_id, CASE WHEN oi.item_type = 'package' THEN hp.title ELSE t.name END as test_name, l.name as lab_name, CAST(oi.price AS INTEGER) as price 
                        FROM order_items oi 
                        LEFT JOIN tests t ON oi.test_id = t.id AND oi.item_type = 'test'
                        LEFT JOIN health_packages hp ON oi.test_id = hp.id AND oi.item_type = 'package'
                        JOIN labs l ON oi.lab_id = l.id 
                        WHERE oi.order_id = %s
                    """, (order['id'],))
                    order['test_list'] = cursor.fetchall()
        except Exception as e: pass
        finally: release_db(conn)
    return render_template('my_bookings.html', orders=orders, searched_email=email)

@app.route('/api/submit-feedback', methods=['POST'])
def submit_feedback():
    email = request.form.get('email')
    order_id = request.form.get('order_id')
    message = request.form.get('message')
    if not session.get(f'verified_{email}'): return jsonify({"success": False, "message": "Unauthorized"})
    conn = None
    try:
        conn = get_db(); cursor = conn.cursor()
        cursor.execute("INSERT INTO patient_feedback (order_id, patient_email, message) VALUES (%s, %s, %s)", (order_id, email, message))
        conn.commit()
        return jsonify({"success": True})
    except: return jsonify({"success": False})
    finally: release_db(conn)

@app.route('/download-report/<int:order_id>')
def download_report(order_id):
    conn = None
    try:
        conn = get_db(); cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT report_file, report_filename FROM orders WHERE id = %s", (order_id,))
        record = cursor.fetchone()
        if record and record['report_file']: return send_file(io.BytesIO(record['report_file']), download_name=record['report_filename'], as_attachment=True)
    except Exception as e: pass
    finally: release_db(conn)
    return "Report not found.", 404
    
@app.route('/admin/download-prescription/<int:order_id>')
def download_prescription(order_id):
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    conn = None
    try:
        conn = get_db(); cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT prescription_file, prescription_filename FROM orders WHERE id = %s", (order_id,))
        record = cursor.fetchone()
        if record and record['prescription_file']: return send_file(io.BytesIO(record['prescription_file']), download_name=record['prescription_filename'], as_attachment=True)
    except Exception as e: pass
    finally: release_db(conn)
    return "Prescription not found.", 404

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    error = None
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD: session['admin_logged_in'] = True; return redirect(url_for('admin_dashboard'))
        else: error = "Access Denied."
    return f'<html><body style="background:#F1F5F9; display:flex; justify-content:center; align-items:center; height:100vh; font-family:sans-serif;"><div style="background:white; padding:40px; border-radius:12px; text-align:center;"><form method="POST"><input type="password" name="password" placeholder="Master Password" required style="padding:14px; margin-bottom:15px; width:100%; border:1px solid #ccc; border-radius:8px;"><button type="submit" style="width:100%; background:#0F172A; color:white; padding:14px; border:none; border-radius:8px; font-weight:bold; cursor:pointer;">Login</button></form><div style="color:red; margin-top:10px;">{error if error else ""}</div></div></body></html>'

@app.route('/admin')
def admin_dashboard():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    auto_migrate_db()
    conn = None
    try:
        conn = get_db(); cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT o.id, o.patient_name, o.age, o.gender, o.address, o.collection_date, o.time_slot, CAST(o.total_amount AS INTEGER) as total_amount, o.status, u.phone, CASE WHEN o.report_file IS NOT NULL THEN TRUE ELSE FALSE END as has_report, CASE WHEN o.prescription_file IS NOT NULL THEN TRUE ELSE FALSE END as has_prescription FROM orders o JOIN users u ON o.user_id = u.id ORDER BY o.id DESC")
        orders = cursor.fetchall()
        cursor.execute("SELECT oi.order_id, CASE WHEN oi.item_type = 'package' THEN hp.title ELSE t.name END as test_name, l.name as lab_name, CAST(oi.price AS INTEGER) as price FROM order_items oi LEFT JOIN tests t ON oi.test_id = t.id AND oi.item_type = 'test' LEFT JOIN health_packages hp ON oi.test_id = hp.id AND oi.item_type = 'package' JOIN labs l ON oi.lab_id = l.id")
        db_items = cursor.fetchall()
        items_map = {}
        for row in db_items:
            if row['order_id'] not in items_map: items_map[row['order_id']] = []
            items_map[row['order_id']].append(row)
        for order in orders: order['test_list'] = items_map.get(order['id'], [])
        
        cursor.execute("SELECT * FROM patient_feedback ORDER BY id DESC")
        feedbacks = cursor.fetchall()
        
        cursor.execute("SELECT id, name, CAST(rating AS FLOAT) as rating, cert_badge, is_active FROM labs ORDER BY name")
        all_labs = cursor.fetchall()
        cursor.execute("SELECT id, name FROM labs WHERE is_active = TRUE ORDER BY name")
        active_labs = cursor.fetchall()
        cursor.execute("SELECT id, name FROM test_categories ORDER BY name")
        categories = cursor.fetchall()
        cursor.execute("SELECT t.id as test_id, t.name as test_name, c.name as category_name, l.id as lab_id, l.name as lab_name, CAST(ltp.price AS INTEGER) as price FROM lab_test_pricing ltp JOIN tests t ON ltp.test_id = t.id JOIN labs l ON ltp.lab_id = l.id JOIN test_categories c ON t.category_id = c.id ORDER BY t.name ASC")
        inventory = cursor.fetchall()
        cursor.execute("SELECT id, name FROM tests WHERE is_active = TRUE ORDER BY name")
        all_tests = cursor.fetchall()
        
        # FETCH MASTER TESTS FOR DELETION
        cursor.execute("SELECT t.id, t.name, c.name as category_name, t.fasting_requirement FROM tests t JOIN test_categories c ON t.category_id = c.id ORDER BY t.name ASC")
        master_tests = cursor.fetchall()
        
        cursor.execute("""
            SELECT hp.id, hp.title, CAST(hp.price AS INTEGER) as original_price, l.name as lab_name,
                   string_agg(t.name, ', ') as features,
                   so.id as offer_id, CAST(so.discount_percent AS INTEGER) as discount_percent, so.badge, TO_CHAR(so.end_date, 'YYYY-MM-DD') as end_date,
                   CAST(ROUND(hp.price * (1 - (COALESCE(so.discount_percent, 0) / 100.0))) AS INTEGER) as discounted_price
            FROM health_packages hp
            JOIN labs l ON hp.lab_id = l.id
            LEFT JOIN package_tests pt ON hp.id = pt.package_id
            LEFT JOIN tests t ON pt.test_id = t.id
            LEFT JOIN special_offers so ON hp.id = so.package_id
            GROUP BY hp.id, l.name, so.id, so.discount_percent, so.badge, so.end_date
            ORDER BY hp.id DESC
        """)
        packages = cursor.fetchall()
    except Exception as e: raise e 
    finally: release_db(conn)
    return render_template('admin.html', orders=orders, feedbacks=feedbacks, active_labs=active_labs, all_labs=all_labs, categories=categories, inventory=inventory, packages=packages, all_tests=all_tests, master_tests=master_tests)

@app.route('/admin/upload-report', methods=['POST'])
def upload_report():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    order_id = request.form.get('order_id')
    file = request.files.get('report_file')
    conn = None
    if file and file.filename:
        file_data = file.read()
        try:
            conn = get_db(); cursor = conn.cursor()
            cursor.execute("UPDATE orders SET report_file = %s, report_filename = %s, status = 'Completed' WHERE id = %s", (psycopg2.Binary(file_data), file.filename, order_id))
            cursor.execute("SELECT u.email, o.patient_name FROM orders o JOIN users u ON o.user_id = u.id WHERE o.id = %s", (order_id,))
            user = cursor.fetchone()
            conn.commit()
            if user: send_email_async(user[0], f"Your Test Report is Ready - #{order_id}", f"Hello {user[1]},\n\nYour test report for CareDrop Order #{order_id} is now available!\nPlease visit our website, click 'My Bookings', verify your email, and download your PDF.")
        except Exception as e: pass
        finally: release_db(conn)
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/update-order', methods=['POST'])
def admin_update_order():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    conn = None
    try:
        conn = get_db(); cursor = conn.cursor()
        cursor.execute("UPDATE orders SET status = %s WHERE id = %s", (request.form.get('status'), request.form.get('order_id')))
        conn.commit()
    except Exception: pass
    finally: release_db(conn)
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/add-category', methods=['POST'])
def admin_add_category():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    conn = None
    try:
        conn = get_db(); cursor = conn.cursor()
        cursor.execute("INSERT INTO test_categories (name) VALUES (%s) ON CONFLICT DO NOTHING", (request.form.get('category_name').strip(),))
        conn.commit()
    except: pass
    finally: release_db(conn)
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/add-test', methods=['POST'])
def admin_add_test():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    test_name, category_id, fasting, price, lab_ids = request.form.get('test_name').strip(), request.form.get('category_id'), request.form.get('fasting').strip(), request.form.get('price'), request.form.getlist('lab_ids') 
    conn = None
    try:
        conn = get_db(); cursor = conn.cursor()
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

@app.route('/admin/delete-master-test/<int:test_id>', methods=['POST'])
def delete_master_test(test_id):
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    conn = None
    try:
        conn = get_db(); cursor = conn.cursor()
        cursor.execute("DELETE FROM tests WHERE id = %s", (test_id,))
        conn.commit()
    except: pass
    finally: release_db(conn)
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/add-lab', methods=['POST'])
def add_lab():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    conn = None
    try:
        conn = get_db(); cursor = conn.cursor()
        name = request.form.get('lab_name').strip()
        rating = request.form.get('rating')
        badge = request.form.get('cert_badge').strip()
        cursor.execute("INSERT INTO labs (name, rating, cert_badge, is_active) VALUES (%s, %s, %s, TRUE) ON CONFLICT (name) DO UPDATE SET rating = %s, cert_badge = %s", (name, rating, badge, rating, badge))
        conn.commit()
    except: pass
    finally: release_db(conn)
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/toggle-lab/<int:lab_id>', methods=['POST'])
def toggle_lab(lab_id):
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
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
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    conn = None
    try:
        conn = get_db(); cursor = conn.cursor()
        cursor.execute("DELETE FROM lab_test_pricing WHERE lab_id = %s", (lab_id,))
        cursor.execute("DELETE FROM labs WHERE id = %s", (lab_id,))
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

@app.route('/admin/add-health-package', methods=['POST'])
def add_health_package():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    conn = None
    try:
        conn = get_db(); cursor = conn.cursor()
        title = request.form.get('title').strip()
        lab_id = request.form.get('lab_id')
        price = request.form.get('price')
        test_ids = request.form.getlist('test_ids')
        cursor.execute("INSERT INTO health_packages (title, lab_id, price) VALUES (%s, %s, %s) RETURNING id", (title, lab_id, price))
        pkg_id = cursor.fetchone()[0]
        for tid in test_ids: cursor.execute("INSERT INTO package_tests (package_id, test_id) VALUES (%s, %s)", (pkg_id, tid))
        conn.commit()
    except Exception as e: pass
    finally: release_db(conn)
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete-health-package/<int:pkg_id>', methods=['POST'])
def delete_health_package(pkg_id):
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    conn = None
    try:
        conn = get_db(); cursor = conn.cursor()
        cursor.execute("DELETE FROM health_packages WHERE id = %s", (pkg_id,))
        conn.commit()
    except: pass
    finally: release_db(conn)
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/add-special-offer', methods=['POST'])
def add_special_offer():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    conn = None
    try:
        conn = get_db(); cursor = conn.cursor()
        pkg_id = request.form.get('package_id')
        discount = request.form.get('discount_percent')
        badge = request.form.get('badge').strip()
        end_date = request.form.get('end_date')
        cursor.execute("DELETE FROM special_offers WHERE package_id = %s", (pkg_id,))
        cursor.execute("INSERT INTO special_offers (package_id, discount_percent, badge, end_date) VALUES (%s, %s, %s, %s)", (pkg_id, discount, badge, end_date))
        conn.commit()
    except Exception as e: pass
    finally: release_db(conn)
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete-offer/<int:offer_id>', methods=['POST'])
def delete_offer(offer_id):
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    conn = None
    try:
        conn = get_db(); cursor = conn.cursor()
        cursor.execute("DELETE FROM special_offers WHERE id = %s", (offer_id,))
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
    age = request.form.get('age', '').strip()
    gender = request.form.get('gender', '').strip()
    address = request.form.get('address', '').strip()
    date = request.form.get('date', '').strip()
    cart_json = request.form.get('cart', '[]')
    cart = json.loads(cart_json)
    
    if not session.get(f'verified_{email}'): return jsonify({"success": False, "message": "Email not verified. Please complete OTP verification."})
    prescription = request.files.get('prescription')
    
    final_patient_name = patient_name if patient_name else name
    
    if not all([name, phone, final_patient_name, address, date, age, gender]): return jsonify({"success": False, "message": "Missing required patient fields."})
    if not cart and not (prescription and prescription.filename): return jsonify({"success": False, "message": "Please add tests to your cart or upload a prescription."})
    
    conn = None
    try:
        conn = get_db(); cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
        user_id = user[0] if user else (cursor.execute("INSERT INTO users (name, phone, email) VALUES (%s, %s, %s) RETURNING id", (name, phone, email)) or cursor.fetchone()[0])
        
        if prescription and prescription.filename:
            file_data = prescription.read()
            cursor.execute("INSERT INTO orders (user_id, patient_name, age, gender, address, collection_date, time_slot, total_amount, status, prescription_file, prescription_filename) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'Pending', %s, %s) RETURNING id", (user_id, final_patient_name, age, gender, address, date, request.form.get('time_slot', 'Morning'), request.form.get('total', 0), psycopg2.Binary(file_data), prescription.filename))
        else:
            cursor.execute("INSERT INTO orders (user_id, patient_name, age, gender, address, collection_date, time_slot, total_amount, status) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'Pending') RETURNING id", (user_id, final_patient_name, age, gender, address, date, request.form.get('time_slot', 'Morning'), request.form.get('total', 0)))
        
        order_id = cursor.fetchone()[0]
        for item in cart: 
            is_pkg = 'PKG_' in str(item['id'])
            clean_id = str(item['id']).replace('PKG_','')
            item_type = 'package' if is_pkg else 'test'
            cursor.execute("INSERT INTO order_items (order_id, test_id, lab_id, price, item_type) VALUES (%s, %s, %s, %s, %s)", (order_id, clean_id, item['selectedLabId'], item['currentPrice'], item_type))
        conn.commit()
        send_email_async(email, f"CareDrop Booking Confirmed - #{order_id}", f"Your Booking #{order_id} is confirmed!\nPatient: {final_patient_name} ({age} {gender})\nDate: {date}\nTotal: Rs. {request.form.get('total', 0)}")
        send_email_async("ihcdiagnostics.ynr@gmail.com", f"🚨 NEW ORDER #{order_id}", f"🚨 NEW BOOKING #{order_id}!\nPhone: {phone}\nPatient: {final_patient_name} ({age} {gender})\nAddress: {address}\nDate: {date}")
        return jsonify({"success": True, "order_id": order_id})
    except Exception as e: 
        if conn: conn.rollback()
        return jsonify({"success": False, "message": str(e)})
    finally: release_db(conn)

if __name__ == '__main__': app.run(debug=True, port=5000)
