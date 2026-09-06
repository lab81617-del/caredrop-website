import os, threading, json, io, csv, random, traceback, urllib.request
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
    return f"<h2>CareDrop Diagnostics</h2><pre style='color:red; background: #F8FAFC; padding: 20px; border:1px solid #CBD5E1;'>{traceback.format_exc()}</pre>", 500

def get_db(): return psycopg2.connect(os.environ.get("DATABASE_URL"))
def release_db(conn):
    if conn: conn.close()

def send_email_api(recipient, subject, text_body):
    api_key = os.environ.get("BREVO_API_KEY")
    if not api_key: return "Missing API Key"
    url, headers = "https://api.brevo.com/v3/smtp/email", {"accept": "application/json", "api-key": api_key, "content-type": "application/json"}
    data = {"sender": {"name": "CareDrop", "email": "ihcdiagnostics.ynr@gmail.com"}, "to": [{"email": recipient}], "subject": subject, "textContent": text_body}
    try: urllib.request.urlopen(urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers=headers, method='POST')); return "Success"
    except Exception as e: return str(e)

def send_email_async(recipient, subject, body): threading.Thread(target=send_email_api, args=(recipient, subject, body)).start()

@app.route('/ping')
def ping(): return "OK", 200

@app.route('/api/send-otp', methods=['POST'])
def send_otp():
    email = request.json.get('email', '').strip()
    if not email: return jsonify({"success": False})
    otp = str(random.randint(1000, 9999)); session[f'otp_{email}'] = otp
    if send_email_api(email, f"CareDrop OTP: {otp}", f"Code: {otp}") == "Success": return jsonify({"success": True})
    return jsonify({"success": False})

@app.route('/api/verify-otp', methods=['POST'])
def verify_otp():
    email = request.json.get('email', '').strip()
    if session.get(f'otp_{email}') == request.json.get('otp', '').strip():
        session[f'verified_{email}'] = True
        return jsonify({"success": True})
    return jsonify({"success": False})

@app.route('/')
def home():
    conn = None; packages = []
    try:
        conn = get_db(); cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT hp.id, hp.title, CAST(hp.price AS INTEGER) as original_price, l.id as lab_id, l.name as lab_name, l.rating,
            string_agg(t.name, ', ') as features, so.id as offer_id, CAST(so.discount_percent AS INTEGER) as discount_percent, so.badge, 
            CAST(ROUND(hp.price * (1 - (COALESCE(so.discount_percent, 0) / 100.0))) AS INTEGER) as discounted_price
            FROM health_packages hp JOIN labs l ON hp.lab_id = l.id LEFT JOIN package_tests pt ON hp.id = pt.package_id LEFT JOIN tests t ON pt.test_id = t.id
            LEFT JOIN special_offers so ON hp.id = so.package_id AND so.end_date >= CURRENT_DATE
            GROUP BY hp.id, l.id, l.name, l.rating, so.id, so.discount_percent, so.badge ORDER BY hp.id DESC
        """)
        packages = cursor.fetchall()
    except Exception: pass
    finally: release_db(conn)
    return render_template('index.html', packages=packages)

@app.route('/tests')
def tests_catalog():
    conn = None; grouped_tests = {}; pricing = []; packages = []
    try:
        conn = get_db(); cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT DISTINCT t.id, t.name, t.fasting_requirement, t.symptoms, c.name as category FROM tests t LEFT JOIN test_categories c ON t.category_id = c.id JOIN lab_test_pricing ltp ON t.id = ltp.test_id JOIN labs l ON ltp.lab_id = l.id WHERE t.is_active = TRUE AND l.is_active = TRUE ORDER BY c.name, t.name")
        for t in cursor.fetchall():
            cat = t['category'] or 'Uncategorized'
            grouped_tests.setdefault(cat, []).append(t)
        cursor.execute("SELECT ltp.test_id, CAST(ltp.price AS INTEGER) as price, l.id as lab_id, l.name as lab_name, CAST(l.rating AS FLOAT) as rating FROM lab_test_pricing ltp JOIN labs l ON ltp.lab_id = l.id WHERE l.is_active = TRUE")
        pricing = cursor.fetchall()
        cursor.execute("""
            SELECT hp.id, hp.title, CAST(hp.price AS INTEGER) as original_price, l.id as lab_id, l.name as lab_name, CAST(l.rating AS FLOAT) as rating, string_agg(t.name, ', ') as features, so.id as offer_id, CAST(so.discount_percent AS INTEGER) as discount_percent, CAST(ROUND(hp.price * (1 - (COALESCE(so.discount_percent, 0) / 100.0))) AS INTEGER) as discounted_price
            FROM health_packages hp JOIN labs l ON hp.lab_id = l.id LEFT JOIN package_tests pt ON hp.id = pt.package_id LEFT JOIN tests t ON pt.test_id = t.id LEFT JOIN special_offers so ON hp.id = so.package_id AND so.end_date >= CURRENT_DATE
            GROUP BY hp.id, l.id, l.name, l.rating, so.id, so.discount_percent ORDER BY hp.id DESC
        """)
        packages = cursor.fetchall()
    except Exception: pass
    finally: release_db(conn)
    return render_template('tests.html', grouped_tests=grouped_tests, pricing=json.dumps(pricing, default=str), packages=json.dumps(packages, default=str), raw_packages=packages)

@app.route('/book')
def checkout_page(): return render_template('checkout.html')

@app.route('/my-bookings')
def my_bookings():
    email = request.args.get('email', '').strip()
    orders = []
    if email:
        if not session.get(f'verified_{email}'): return render_template('my_bookings.html', error="Verify email.", searched_email=email)
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
    return "Not found", 404

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    error = None
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            return redirect(url_for('admin_dashboard'))
        else: error = "Access Denied."
    return f'<html><body style="background:#F1F5F9; display: flex; justify-content:center; align-items:center; height: 100vh;"><div style="background: white; padding:40px; border-radius: 12px; text-align:center; font-family:sans-serif;"><form method="POST"><input type="password" name="password" placeholder="Master Password" required style="padding: 14px; margin-bottom: 15px; width:100%;"><button type="submit" style="width:100%; background: #0F172A; color: white; padding: 14px; border: none; border-radius: 8px;">Login</button></form></div></body></html>'

@app.route('/admin')
def admin_dashboard():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    conn = None
    try:
        conn = get_db(); cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        cursor.execute("SELECT o.id, o.order_ref, o.patient_name, o.age, o.gender, o.address, o.collection_date, o.time_slot, CAST(o.total_amount AS INTEGER) as total_amount, o.status, u.phone, u.patient_uid, CASE WHEN o.report_file IS NOT NULL THEN TRUE ELSE FALSE END as has_report, o.phlebotomist_id FROM orders o JOIN users u ON o.user_id = u.id ORDER BY o.id DESC")
        orders = cursor.fetchall()
        
        cursor.execute("SELECT oi.order_id, CASE WHEN oi.item_type = 'package' THEN hp.title ELSE t.name END as test_name, l.name as lab_name FROM order_items oi LEFT JOIN tests t ON oi.test_id = t.id AND oi.item_type = 'test' LEFT JOIN health_packages hp ON oi.test_id = hp.id AND oi.item_type = 'package' JOIN labs l ON oi.lab_id = l.id")
        items_map = {}
        for row in cursor.fetchall(): items_map.setdefault(row['order_id'], []).append(row)

        # LIMS ENGINE: Fetch all raw tests (unpacking packages) and parameters
        cursor.execute("""
            SELECT oi.order_id, t.id as test_id, t.name as test_name
            FROM order_items oi JOIN tests t ON oi.test_id = t.id WHERE oi.item_type = 'test'
            UNION
            SELECT oi.order_id, t.id as test_id, t.name as test_name
            FROM order_items oi JOIN package_tests pt ON oi.test_id = pt.package_id JOIN tests t ON pt.test_id = t.id WHERE oi.item_type = 'package'
        """)
        order_tests_raw = cursor.fetchall()
        
        cursor.execute("SELECT id, test_id, parameter_name, unit, reference_range FROM test_parameters")
        param_map = {}
        for p in cursor.fetchall(): param_map.setdefault(p['test_id'], []).append(p)
            
        order_test_map = {}
        for row in order_tests_raw:
            row['parameters'] = param_map.get(row['test_id'], [])
            order_test_map.setdefault(row['order_id'], []).append(row)
            
        for order in orders: 
            order['test_list'] = items_map.get(order['id'], [])
            order['lims_tests'] = order_test_map.get(order['id'], [])
            
        cursor.execute("SELECT tp.id, tp.parameter_name, tp.unit, tp.reference_range, t.name as test_name FROM test_parameters tp JOIN tests t ON tp.test_id = t.id ORDER BY t.name")
        test_parameters = cursor.fetchall()

        cursor.execute("SELECT id, name FROM labs WHERE is_active = TRUE ORDER BY name")
        active_labs = cursor.fetchall()
        
        cursor.execute("SELECT id, name FROM test_categories ORDER BY name")
        categories = cursor.fetchall()
        
        cursor.execute("SELECT t.id as test_id, t.name as test_name, c.name as category_name, l.id as lab_id, l.name as lab_name, CAST(ltp.price AS INTEGER) as price FROM lab_test_pricing ltp JOIN tests t ON ltp.test_id = t.id JOIN labs l ON ltp.lab_id = l.id LEFT JOIN test_categories c ON t.category_id = c.id ORDER BY t.name ASC")
        inventory = cursor.fetchall()
        
        cursor.execute("SELECT t.id, t.name, c.name as category_name, t.fasting_requirement, t.symptoms FROM tests t LEFT JOIN test_categories c ON t.category_id = c.id ORDER BY t.name ASC")
        master_tests = cursor.fetchall()
        
        cursor.execute("SELECT * FROM phlebotomists ORDER BY id DESC")
        phlebotomists = cursor.fetchall()

    except Exception as e: raise e
    finally: release_db(conn)
    return render_template('admin.html', orders=orders, active_labs=active_labs, categories=categories, inventory=inventory, master_tests=master_tests, phlebotomists=phlebotomists, test_parameters=test_parameters)

# LIMS ENGINE: Add Parameter
@app.route('/admin/add-parameter', methods=['POST'])
def add_parameter():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    test_name, param_name, unit, ref = request.form.get('test_name'), request.form.get('parameter_name'), request.form.get('unit'), request.form.get('reference_range')
    conn = None
    try:
        conn = get_db(); cursor = conn.cursor()
        cursor.execute("SELECT id FROM tests WHERE name = %s", (test_name,))
        test = cursor.fetchone()
        if test:
            cursor.execute("INSERT INTO test_parameters (test_id, parameter_name, unit, reference_range) VALUES (%s, %s, %s, %s)", (test[0], param_name, unit, ref))
            conn.commit()
    except Exception: pass
    finally: release_db(conn)
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete-parameter/<int:param_id>', methods=['POST'])
def delete_parameter(param_id):
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    conn = None
    try:
        conn = get_db(); cursor = conn.cursor()
        cursor.execute("DELETE FROM test_parameters WHERE id = %s", (param_id,))
        conn.commit()
    except: pass
    finally: release_db(conn)
    return redirect(url_for('admin_dashboard'))

# LIMS ENGINE: Save Results
@app.route('/admin/save-results/<int:order_id>', methods=['POST'])
def save_results(order_id):
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    conn = None
    try:
        conn = get_db(); cursor = conn.cursor()
        cursor.execute("DELETE FROM order_results WHERE order_id = %s", (order_id,)) # Clear old results if updating
        for key, value in request.form.items():
            if key.startswith('param_') and value.strip() != '':
                param_id = key.split('_')[1]
                cursor.execute("INSERT INTO order_results (order_id, parameter_id, result_value) VALUES (%s, %s, %s)", (order_id, param_id, value.strip()))
        
        # Temp save state (PDF generation added in next step)
        cursor.execute("UPDATE orders SET status = 'Completed', report_type = 'System' WHERE id = %s", (order_id,))
        conn.commit()
    except Exception: pass
    finally: release_db(conn)
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/walk-in', methods=['POST'])
def admin_walk_in():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    patient_name, phone, age, gender, total, test_id, lab_id = request.form.get('patient_name'), request.form.get('phone'), request.form.get('age'), request.form.get('gender'), request.form.get('total_amount', 0), request.form.get('test_id'), request.form.get('lab_id')
    conn = None
    try:
        conn = get_db(); cursor = conn.cursor()
        cursor.execute("SELECT id, patient_uid FROM users WHERE phone = %s", (phone,))
        user = cursor.fetchone()
        if user:
            user_id = user[0]
            if not user[1]: cursor.execute("UPDATE users SET patient_uid = %s WHERE id = %s", (f"CD-PAT-{1000 + user_id}", user_id))
        else:
            cursor.execute("INSERT INTO users (name, phone, email) VALUES (%s, %s, %s) RETURNING id", (patient_name, phone, f"walkin_{phone}@caredrop.local"))
            user_id = cursor.fetchone()[0]
            cursor.execute("UPDATE users SET patient_uid = %s WHERE id = %s", (f"CD-PAT-{1000 + user_id}", user_id))
            
        today = datetime.today().strftime('%Y-%m-%d')
        cursor.execute("INSERT INTO orders (user_id, patient_name, age, gender, address, collection_date, time_slot, total_amount, status) VALUES (%s, %s, %s, %s, 'Walk-In Clinic', %s, 'Immediate', %s, 'Completed') RETURNING id", (user_id, patient_name, age, gender, today, total))
        order_id = cursor.fetchone()[0]
        cursor.execute("UPDATE orders SET order_ref = %s WHERE id = %s", (f"ORD-{datetime.today().strftime('%y%m')}-{order_id:04d}", order_id))
        cursor.execute("INSERT INTO order_items (order_id, test_id, lab_id, price, item_type) VALUES (%s, %s, %s, %s, 'test')", (order_id, test_id, lab_id, total))
        conn.commit()
    except Exception: pass
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
            cursor.execute("INSERT INTO tests (name, category_id, fasting_requirement, is_active, symptoms) VALUES (%s, %s, %s, TRUE, %s) ON CONFLICT (name) DO NOTHING", (name, cursor.fetchone()[0], fasting, symptoms))
        conn.commit()
    except Exception: pass
    finally: release_db(conn)
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/upload-report', methods=['POST'])
def upload_report():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    order_id = request.form.get('order_id'); file = request.files.get('report_file')
    conn = None
    if file and file.filename:
        try:
            conn = get_db(); cursor = conn.cursor()
            cursor.execute("UPDATE orders SET report_file = %s, report_filename = %s, status = 'Completed', report_type = 'Manual' WHERE id = %s", (psycopg2.Binary(file.read()), file.filename, order_id))
            conn.commit()
        except Exception: pass
        finally: release_db(conn)
    return redirect(url_for('admin_dashboard'))

@app.route('/api/place-order', methods=['POST'])
def place_order():
    name, phone, email, patient_name, age, gender, address, date, cart_json = request.form.get('name'), request.form.get('phone'), request.form.get('email'), request.form.get('patient_name'), request.form.get('age'), request.form.get('gender'), request.form.get('address'), request.form.get('date'), request.form.get('cart', '[]')
    if not session.get(f'verified_{email}'): return jsonify({"success": False, "message": "Verify email."})
    conn = None
    try:
        conn = get_db(); cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
        user_id = user[0] if user else (cursor.execute("INSERT INTO users (name, phone, email) VALUES (%s, %s, %s) RETURNING id", (name, phone, email)) or cursor.fetchone()[0])
        final_name = patient_name if patient_name else name
        
        cursor.execute("INSERT INTO orders (user_id, patient_name, age, gender, address, collection_date, time_slot, total_amount, status) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'Pending') RETURNING id", (user_id, final_name, age, gender, address, date, request.form.get('time_slot', 'Morning'), request.form.get('total', 0)))
        order_id = cursor.fetchone()[0]
        cursor.execute("UPDATE orders SET order_ref = %s WHERE id = %s", (f"ORD-{datetime.today().strftime('%y%m')}-{order_id:04d}", order_id))
        
        for item in json.loads(cart_json):
            clean_id, is_pkg = str(item['id']).replace('PKG_',''), 'package' if 'PKG_' in str(item['id']) else 'test'
            cursor.execute("INSERT INTO order_items (order_id, test_id, lab_id, price, item_type) VALUES (%s, %s, %s, %s, %s)", (order_id, clean_id, item['selectedLabId'], item['currentPrice'], is_pkg))
        conn.commit()
        return jsonify({"success": True, "order_id": order_id})
    except Exception as e: return jsonify({"success": False, "message": str(e)})
    finally: release_db(conn)

if __name__ == '__main__': app.run(debug=True, port=5000)
