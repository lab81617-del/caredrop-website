import os
import threading
import smtplib
from email.message import EmailMessage
from flask import Flask, render_template, jsonify, request
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "caredrop-default-key")

# ==========================================
# 1. ENTERPRISE DATABASE POOLING
# ==========================================
try:
    db_pool = psycopg2.pool.ThreadedConnectionPool(
        minconn=1,
        maxconn=20,
        dsn=os.environ.get("DATABASE_URL")
    )
    print("✅ Database pool initialized successfully.")
except Exception as e:
    print(f"❌ Error initializing DB Pool: {e}")

def get_db():
    return db_pool.getconn()

def release_db(conn):
    db_pool.putconn(conn)

# ==========================================
# 2. ASYNCHRONOUS EMAIL WORKER
# ==========================================
def send_email_async(recipient, subject, body):
    gmail_pwd = os.environ.get("GMAIL_APP_PASSWORD")
    if not gmail_pwd:
        print("⚠️ No Gmail App Password set. Skipping email.")
        return

    def email_job():
        try:
            msg = EmailMessage()
            msg['Subject'] = subject
            msg['From'] = "ihcdiagnostics.ynr@gmail.com"
            msg['To'] = recipient
            msg.set_content(body)

            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
                server.login("ihcdiagnostics.ynr@gmail.com", gmail_pwd)
                server.send_message(msg)
            print(f"📧 Email sent successfully to {recipient}")
        except Exception as e:
            print(f"❌ Background email failed: {e}")

    # Fire the email in the background instantly
    threading.Thread(target=email_job).start()

# ==========================================
# 3. ROUTES
# ==========================================

@app.route('/')
def home():
    # This will load our new Corporate Homepage
    return render_template('index.html')

if __name__ == '__main__':
    app.run(debug=True, port=5000)
