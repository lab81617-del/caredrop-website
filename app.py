import os
from flask import Flask, render_template, session, redirect, url_for
from dotenv import load_dotenv
from database import get_db, init_db
from psycopg2.extras import RealDictCursor

load_dotenv()
app = Flask(__name__)

app.secret_key = os.environ.get("SECRET_KEY", "caredrop-v3-production-key-2026")

from blueprints.auth import auth_bp, role_required
from blueprints.pos import pos_bp

app.register_blueprint(auth_bp)
app.register_blueprint(pos_bp)

@app.before_request
def run_setup_once():
    if not getattr(app, '_db_ready', False):
        try:
            init_db()
            app._db_ready = True
        except Exception as e:
            print(f"Auto-setup error: {e}")

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/dashboard')
@role_required(['admin', 'receptionist', 'pathologist', 'technician'])
def dashboard():
    conn = get_db()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        cursor.execute("""
            SELECT t.id, t.name, t.code, COALESCE(lp.price, 0) as price 
            FROM tests t 
            LEFT JOIN lab_pricing lp ON t.id = lp.test_id 
            WHERE t.is_active = TRUE ORDER BY t.name
        """)
        all_tests = cursor.fetchall()
        
        cursor.execute("""
            SELECT o.id as order_id, o.order_ref, p.full_name as patient_name, 
                   o.clinical_status, o.created_at
            FROM orders o
            JOIN patients p ON o.patient_id = p.id
            WHERE o.clinical_status IN ('PENDING_VERIFICATION', 'PENDING_COLLECTION')
            ORDER BY o.created_at DESC
        """)
        lims_orders = cursor.fetchall()
        
        cursor.execute("""
            SELECT i.invoice_ref, p.full_name as patient_name, i.total_amount, i.status 
            FROM invoices i
            JOIN orders o ON i.order_id = o.id
            JOIN patients p ON o.patient_id = p.id
            ORDER BY i.issued_at DESC LIMIT 50
        """)
        ledger_invoices = cursor.fetchall()
        
        return render_template('dashboard.html', 
                               user_role=session.get('role', 'admin'),
                               all_tests=all_tests,
                               lims_orders=lims_orders,
                               ledger_invoices=ledger_invoices)
    except Exception as e:
        print(f"Dashboard Query Error: {e}")
        return f"Database initialization in progress. Please refresh in 5 seconds. (Error: {e})"
    finally:
        conn.close()

if __name__ == '__main__':
    app.run(debug=True, port=5000)
