import os
import threading
import smtplib
import json
from email.message import EmailMessage
from flask import Flask, render_template, jsonify, request, session
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "caredrop-super-secret-key-2026")

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

@app.route('/checkout')
def checkout_page():
    return render_template('checkout.html')

@app.route('/api/place-order', methods=['POST'])
def place_order():
    data = request.json
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        # 1. Register or find user
        cursor.execute("SELECT id FROM users WHERE email = %s", (data['email'],))
        user = cursor.fetchone()
        if user:
            user_id = user[0]
        else:
            cursor.execute("INSERT INTO users (name, phone, email) VALUES (%s, %s, %s) RETURNING id", 
                           (data['name'], data['phone'], data['email']))
            user_id = cursor.fetchone()[0]

        # 2. Create the Order
        cursor.execute("""
            INSERT INTO orders (user_id, patient_name, address, collection_date, time_slot, total_amount)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
        """, (user_id, data['patient_name'], data['address'], data['date'], data['time_slot'], data['total']))
        order_id = cursor.fetchone()[0]

        # 3. Add Tests to Order
        for item in data['cart']:
            cursor.execute("""
                INSERT INTO order_items (order_id, test_id, lab_id, price)
                VALUES (%s, %s, %s, %s)
            """, (order_id, item['id'], item['selectedLabId'], item['currentPrice']))
            
        conn.commit()
        
        # 4. Send Email
        msg = f"Your CareDrop Booking #{order_id} is confirmed!\n\nPatient: {data['patient_name']}\nDate: {data['date']} ({data['time_slot']})\nAmount to Pay on Collection: Rs. {data['total']}"
        send_email_async(data['email'], f"Booking Confirmed - #{order_id}", msg)
        
        return jsonify({"success": True, "order_id": order_id})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "message": str(e)})
    finally:
        release_db(conn)

if __name__ == '__main__':
    app.run(debug=True, port=5000)
