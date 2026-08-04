import os
import threading
import smtplib
import json
from email.message import EmailMessage
from flask import Flask, render_template, jsonify, request, session, redirect, url_for
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "caredrop-super-secret-key-2026")

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "IHC2026!")

def get_db():
    return psycopg2.connect(os.environ.get("DATABASE_URL"))

def release_db(conn):
    conn.close()

def send_email_async(recipient, subject, body):
    gmail_pwd = os.environ.get("GMAIL_APP_PASSWORD")
    if not gmail_pwd: return
    def email_job():
        try:
            msg = EmailMessage()
            msg['Subject'], msg['From'], msg['To'] = subject, "ihcdiagnostics.ynr@gmail.com", recipient
            msg.set_content(body)
            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
                server.login("ihcdiagnostics.ynr@gmail.com", gmail_pwd)
                server.send_message(msg)
        except Exception as e: print(e)
    threading.Thread(target=email_job).start()

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/tests')
def tests_catalog():
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT t.id, t.name, t.fasting_requirement, c.name as category 
            FROM tests t JOIN test_categories c ON t.category_id = c.id
            WHERE t.is_active = TRUE ORDER BY c.name, t.name
        """)
        tests_list = cursor.fetchall()
        
        cursor.execute("""
            SELECT ltp.test_id, CAST(ltp.price AS FLOAT) as price, ltp.tat, l.id as lab_id, l.name as lab_name, l.badge_type
            FROM lab_test_pricing ltp JOIN labs l ON ltp.lab_id = l.id
            WHERE l.is_active = TRUE
        """)
        pricing_list = cursor.fetchall()
    finally:
        release_db(conn)
        
    return render_template('tests.html', tests=tests_list, pricing=json.dumps(pricing_list))

@app.route('/book')
def checkout_page():
    return render_template('checkout.html')

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    error = None
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            return redirect(url_for('admin_dashboard'))
        else:
            error = "Access Denied: Incorrect Password."
            
    return f'''
    <html>
        <head><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
        <body style="background:#F1F5F9; font-family:sans-serif; display:flex; justify-content:center; align-items:center; height:100vh; margin:0; padding:20px;">
            <div style="background:white; padding:40px; border-radius:12px; box-shadow:0 4px 15px rgba(0,0,0,0.1); text-align:center; width:100%; max-width:350px;">
                <h2 style="color:#0D9488; margin-bottom:5px;">CareDrop Admin</h2>
                <p style="color:#64748B; font-size:14px; margin-bottom:25px;">Authorized Personnel Only</p>
                <form method="POST">
                    <input type="password" name="password" placeholder="Enter Master Password" required style="width:100%; padding:14px; margin-bottom:15px; border:1px solid #CBD5E1; border-radius:8px; box-sizing:border-box; outline:none; font-size:16px;">
                    <button type="submit" style="width:100%; background:#0F172A; color:white; padding:14px; border:none; border-radius:8px; font-weight:bold; cursor:pointer; font-size:16px;">Login to Dashboard</button>
                </form>
                <div style="color:#DC2626; font-size:14px; margin-top:15px; font-weight:bold;">{error if error else ''}</div>
            </div>
        </body>
    </html>
    '''

@app.route('/admin')
def admin_dashboard():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))

    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        cursor.execute("""
            SELECT o.id, o.patient_name, o.address, o.collection_date, o.time_slot, o.total_amount, u.phone, u.name as booked_by 
            FROM orders o JOIN users u ON o.user_id = u.id 
            ORDER BY o.id DESC
        """)
        orders = cursor.fetchall()
        
        cursor.execute("""
            SELECT oi.order_id, t.name as test_name, l.name as lab_name, oi.price
            FROM order_items oi
            JOIN tests t ON oi.test_id = t.id
            JOIN labs l ON oi.lab_id = l.id
        """)
        db_items = cursor.fetchall()
        
        items_map = {}
        for row in db_items:
            if row['order_id'] not in items_map:
                items_map[row['order_id']] = []
            items_map[row['order_id']].append(row)
            
        for order in orders:
            # RENAMED to 'test_list' to avoid Python's dict.items() collision!
            order['test_list'] = items_map.get(order['id'], [])
            
        release_db(conn)
        return render_template('admin.html', orders=orders)
    
    except Exception as e:
        return f"<div style='padding:40px; font-family:sans-serif;'><h2>Database Error</h2><p style='color:red;'>{str(e)}</p></div>"

@app.route('/api/place-order', methods=['POST'])
def place_order():
    data = request.json
    if not data:
        return jsonify({"success": False, "message": "No data received by server."}), 400

    name = str(data.get('name', '')).strip()
    phone = str(data.get('phone', '')).strip()
    email = str(data.get('email', '')).strip()
    patient_name = str(data.get('patient_name', '')).strip()
    address = str(data.get('address', '')).strip()
    date = str(data.get('date', '')).strip()
    cart = data.get('cart', [])

    if not name or not phone or not email or not patient_name or not address or not date:
        return jsonify({"success": False, "message": "Rejected by server: Missing mandatory text fields."})

    if not cart or len(cart) == 0:
        return jsonify({"success": False, "message": "Rejected by server: Cart is empty."})

    conn = get_db()
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
        if user:
            user_id = user[0]
        else:
            cursor.execute("INSERT INTO users (name, phone, email) VALUES (%s, %s, %s) RETURNING id", 
                           (name, phone, email))
            user_id = cursor.fetchone()[0]

        cursor.execute("""
            INSERT INTO orders (user_id, patient_name, address, collection_date, time_slot, total_amount)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
        """, (user_id, patient_name, address, date, data.get('time_slot', 'Morning'), data.get('total', 0)))
        order_id = cursor.fetchone()[0]

        for item in cart:
            cursor.execute("""
                INSERT INTO order_items (order_id, test_id, lab_id, price)
                VALUES (%s, %s, %s, %s)
            """, (order_id, item['id'], item['selectedLabId'], item['currentPrice']))
            
        conn.commit()
        
        msg = f"Your CareDrop Booking #{order_id} is confirmed!\n\nPatient: {patient_name}\nDate: {date}\nAmount to Pay on Collection: Rs. {data.get('total', 0)}"
        send_email_async(email, f"Booking Confirmed - #{order_id}", msg)
        
        return jsonify({"success": True, "order_id": order_id})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        release_db(conn)

if __name__ == '__main__':
    app.run(debug=True, port=5000)
