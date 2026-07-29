import os
import threading
import smtplib
import json
from email.message import EmailMessage
from flask import Flask, render_template, jsonify, request
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "caredrop-default-key")

# --- DATABASE POOLING ---
try:
    db_pool = psycopg2.pool.ThreadedConnectionPool(1, 20, dsn=os.environ.get("DATABASE_URL"))
except Exception as e:
    print(f"DB Error: {e}")

def get_db(): return db_pool.getconn()
def release_db(conn): db_pool.putconn(conn)

# --- BACKGROUND EMAIL ---
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

# --- ROUTES ---
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/tests')
def tests_catalog():
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    # 1. Fetch all tests
    cursor.execute("""
        SELECT t.id, t.name, t.fasting_requirement, c.name as category 
        FROM tests t JOIN test_categories c ON t.category_id = c.id
        WHERE t.is_active = TRUE ORDER BY c.name, t.name
    """)
    tests_list = cursor.fetchall()

    # 2. Fetch which labs offer which tests & their prices
    cursor.execute("""
        SELECT ltp.test_id, ltp.price, ltp.tat, l.id as lab_id, l.name as lab_name, l.badge_type
        FROM lab_test_pricing ltp JOIN labs l ON ltp.lab_id = l.id
        WHERE l.is_active = TRUE
    """)
    pricing_list = cursor.fetchall()
    
    release_db(conn)
    
    # Pass data to the frontend (pricing is converted to JSON so Javascript can use it)
    return render_template('tests.html', tests=tests_list, pricing=json.dumps(pricing_list))

if __name__ == '__main__':
    app.run(debug=True, port=5000)
