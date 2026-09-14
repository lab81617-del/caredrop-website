import os
from flask import Flask, render_template, session, redirect, url_for
from dotenv import load_dotenv
from database import get_db, init_db
from psycopg2.extras import RealDictCursor

# Load environment variables
load_dotenv()

# Initialize Flask App
app = Flask(__name__)

# STRICT SECURITY: Enforce secret key requirement in production
if os.environ.get("FLASK_ENV") == "production":
    app.secret_key = os.environ["SECRET_KEY"] # Will intentionally crash if missing
else:
    app.secret_key = os.environ.get("SECRET_KEY", "caredrop-v3-dev-key")

# Import Blueprints
from blueprints.auth import auth_bp, role_required
from blueprints.pos import pos_bp

# Register Blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(pos_bp)

# ---------------------------------------------------------
# DATABASE INITIALIZATION BACKDOOR (For Render Free Tier)
# ---------------------------------------------------------
@app.route('/setup-db')
def setup_db():
    """Temporary route to build PostgreSQL tables since Render Free lacks shell access."""
    init_db()
    return "Database tables built successfully! You can now navigate to /dashboard and log in."

# ---------------------------------------------------------
# 1. PUBLIC ROUTE: Patient Booking Portal
# ---------------------------------------------------------
@app.route('/')
def home():
    """Unauthenticated landing page for Yamunanagar organic traffic."""
    return render_template('index.html')

# ---------------------------------------------------------
# 2. SECURE ROUTE: Master Operations Dashboard
# ---------------------------------------------------------
@app.route('/dashboard')
@role_required(['admin', 'receptionist', 'pathologist', 'technician'])
def dashboard():
    """Strictly authenticated staff operations portal."""
    conn = get_db()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # A. Fetch Master Catalog for POS
        cursor.execute("""
            SELECT t.id, t.name, t.code, COALESCE(lp.price, 0) as price 
            FROM tests t 
            LEFT JOIN lab_pricing lp ON t.id = lp.test_id 
            WHERE t.is_active = TRUE ORDER BY t.name
        """)
        all_tests = cursor.fetchall()
        
        # B. Fetch Pending LIMS Orders for Pathologist Verification
        cursor.execute("""
            SELECT o.id as order_id, o.order_ref, p.full_name as patient_name, 
                   o.clinical_status, o.created_at
            FROM orders o
            JOIN patients p ON o.patient_id = p.id
            WHERE o.clinical_status = 'PENDING_VERIFICATION' OR o.clinical_status = 'PENDING_COLLECTION'
            ORDER BY o.created_at DESC
        """)
        lims_orders = cursor.fetchall()
        
        # C. Fetch Financial Ledger
        cursor.execute("""
            SELECT i.invoice_ref, p.full_name as patient_name, i.total_amount, i.status 
            FROM invoices i
            JOIN orders o ON i.order_id = o.id
            JOIN patients p ON o.patient_id = p.id
            ORDER BY i.issued_at DESC LIMIT 50
        """)
        ledger_invoices = cursor.fetchall()
        
        # Render the secure dashboard with live database data
        return render_template('dashboard.html', 
                               user_role=session['role'],
                               all_tests=all_tests,
                               lims_orders=lims_orders,
                               ledger_invoices=ledger_invoices)
    except Exception as e:
        print(f"Dashboard Load Error: {e}")
        return "Error loading secure dashboard. Please contact IT."
    finally:
        conn.close()

if __name__ == '__main__':
    app.run(debug=True, port=5000)
