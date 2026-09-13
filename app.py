import os
from flask import Flask, redirect, url_for, session, render_template
from dotenv import load_dotenv

# Import database utilities
from database import init_db, get_db
from psycopg2.extras import RealDictCursor

# Load environment variables
load_dotenv()

# Initialize Flask App
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "caredrop-v3-enterprise-key")

# Import Blueprints
from blueprints.auth import auth_bp
from blueprints.pos import pos_bp
from blueprints.financial import financial_bp
from blueprints.clinical import clinical_bp
from blueprints.logistics import logistics_bp

# Register Blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(pos_bp)
app.register_blueprint(financial_bp)
app.register_blueprint(clinical_bp)
app.register_blueprint(logistics_bp)

@app.before_request
def startup_tasks():
    """Run once on the very first request to build the DB schema."""
    if not getattr(app, '_schema_checked', False):
        init_db()
        app._schema_checked = True

@app.route('/')
def home():
    if 'role' not in session:
        return redirect(url_for('auth.unified_login'))
        
    conn = get_db()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # 1. Fetch Master Catalog for POS
        cursor.execute("""
            SELECT t.id, t.name, t.code, COALESCE(lp.price, 0) as price 
            FROM tests t 
            LEFT JOIN lab_pricing lp ON t.id = lp.test_id 
            WHERE t.is_active = TRUE ORDER BY t.name
        """)
        all_tests = cursor.fetchall()
        
        # 2. Fetch Pending LIMS Orders for Pathologist
        cursor.execute("""
            SELECT o.id as order_id, o.order_ref, p.full_name as patient_name, 
                   p.patient_uid, o.clinical_status, o.created_at
            FROM orders o
            JOIN patients p ON o.patient_id = p.id
            WHERE o.clinical_status = 'PENDING_VERIFICATION'
            ORDER BY o.created_at DESC
        """)
        lims_orders = cursor.fetchall()
        
        # 3. Fetch Financial Ledger
        cursor.execute("""
            SELECT i.invoice_ref, p.full_name as patient_name, i.total_amount, 
                   i.status, i.id as invoice_id
            FROM invoices i
            JOIN orders o ON i.order_id = o.id
            JOIN patients p ON o.patient_id = p.id
            ORDER BY i.issued_at DESC LIMIT 50
        """)
        ledger_invoices = cursor.fetchall()
        
        # Render the dashboard with live database data
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
