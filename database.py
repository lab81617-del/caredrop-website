import os
import psycopg2
from psycopg2.extras import RealDictCursor

DB_URL = os.environ.get('DATABASE_URL')

def get_db_connection():
    if not DB_URL:
        raise ValueError("DATABASE_URL environment variable is missing. Please check Render settings.")
    return psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)

def init_db():
    if not DB_URL: return
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 1. Create Core Tables
    cur.execute('''CREATE TABLE IF NOT EXISTS tests (
        id SERIAL PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL, sample_type TEXT, b2b_cost REAL DEFAULT 0, price REAL NOT NULL, is_active BOOLEAN DEFAULT TRUE
    );''')

    cur.execute('''CREATE TABLE IF NOT EXISTS test_parameters (
        id SERIAL PRIMARY KEY, test_id INTEGER REFERENCES tests(id) ON DELETE CASCADE, param_name TEXT NOT NULL, unit TEXT, ref_range TEXT
    );''')

    cur.execute('''CREATE TABLE IF NOT EXISTS partners (
        id SERIAL PRIMARY KEY, name TEXT NOT NULL, clinic_name TEXT, referral_code TEXT UNIQUE NOT NULL, phone TEXT, password TEXT NOT NULL, margin_pool_pct REAL DEFAULT 0.30, wallet_balance REAL DEFAULT 0, is_active BOOLEAN DEFAULT TRUE
    );''')

    cur.execute('''CREATE TABLE IF NOT EXISTS orders (
        id SERIAL PRIMARY KEY, order_code TEXT UNIQUE, full_name TEXT NOT NULL, age TEXT, gender TEXT, phone TEXT NOT NULL, email TEXT NOT NULL, address TEXT NOT NULL, time_slot TEXT, tests_requested TEXT NOT NULL, gross_bill REAL NOT NULL, discount_given REAL DEFAULT 0, total_bill REAL NOT NULL, b2b_total_cost REAL DEFAULT 0, referral_code TEXT, partner_commission REAL DEFAULT 0, is_commission_paid BOOLEAN DEFAULT FALSE, assigned_rider TEXT DEFAULT 'Unassigned', status TEXT DEFAULT 'Pending', barcode TEXT, temp_log TEXT, payment_mode TEXT DEFAULT 'Cash', is_paid BOOLEAN DEFAULT FALSE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, completed_at TIMESTAMP, uploaded_pdf TEXT
    );''')
    
    # 2. Auto-Patch Missing Columns (Prevents 500 Errors on old databases)
    patch_queries = [
        "ALTER TABLE partners ADD COLUMN IF NOT EXISTS wallet_balance REAL DEFAULT 0",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS uploaded_pdf TEXT",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS b2b_total_cost REAL DEFAULT 0",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS email TEXT",
        "ALTER TABLE tests ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE"
    ]
    
    for query in patch_queries:
        try:
            cur.execute(query)
        except Exception:
            conn.rollback() # Skip if it already exists or fails

    conn.commit()
    cur.close()
    conn.close()

if __name__ == '__main__':
    init_db()
