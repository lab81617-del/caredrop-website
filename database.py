import os
import psycopg2
from psycopg2.extras import RealDictCursor

DB_URL = os.environ.get('DATABASE_URL')

def get_db_connection():
    if not DB_URL:
        raise ValueError("DATABASE_URL environment variable missing.")
    conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    return conn

def init_db():
    if not DB_URL: return
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute('''CREATE TABLE IF NOT EXISTS tests (
        id SERIAL PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL, sample_type TEXT, b2b_cost REAL DEFAULT 0, price REAL NOT NULL, is_active BOOLEAN DEFAULT TRUE
    );''')

    cur.execute('''CREATE TABLE IF NOT EXISTS test_parameters (
        id SERIAL PRIMARY KEY, test_id INTEGER REFERENCES tests(id) ON DELETE CASCADE, param_name TEXT NOT NULL, unit TEXT, ref_range TEXT
    );''')

    cur.execute('''CREATE TABLE IF NOT EXISTS partners (
        id SERIAL PRIMARY KEY, name TEXT NOT NULL, clinic_name TEXT, referral_code TEXT UNIQUE NOT NULL, phone TEXT, password TEXT NOT NULL, margin_pool_pct REAL DEFAULT 0.30, is_active BOOLEAN DEFAULT TRUE
    );''')

    # ADDED: time_slot and uploaded_pdf
    cur.execute('''CREATE TABLE IF NOT EXISTS orders (
        id SERIAL PRIMARY KEY, order_code TEXT UNIQUE, full_name TEXT NOT NULL, age TEXT, gender TEXT, phone TEXT NOT NULL, email TEXT NOT NULL, address TEXT NOT NULL, time_slot TEXT, tests_requested TEXT NOT NULL, gross_bill REAL NOT NULL, discount_given REAL DEFAULT 0, total_bill REAL NOT NULL, b2b_total_cost REAL DEFAULT 0, referral_code TEXT, partner_commission REAL DEFAULT 0, is_commission_paid BOOLEAN DEFAULT FALSE, assigned_rider TEXT DEFAULT 'Unassigned', status TEXT DEFAULT 'Pending', barcode TEXT, temp_log TEXT, payment_mode TEXT DEFAULT 'Cash', is_paid BOOLEAN DEFAULT FALSE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, completed_at TIMESTAMP, uploaded_pdf TEXT
    );''')

    cur.execute('''CREATE TABLE IF NOT EXISTS test_results (
        id SERIAL PRIMARY KEY, order_id INTEGER REFERENCES orders(id) ON DELETE CASCADE, parameter_name TEXT NOT NULL, observed_value TEXT DEFAULT '', units TEXT, ref_interval TEXT
    );''')

    cur.execute('SELECT COUNT(*) FROM tests')
    if cur.fetchone()['count'] == 0:
        cur.execute("INSERT INTO tests (name, category, sample_type, b2b_cost, price) VALUES ('Complete Blood Count (CBC)', 'Hematology', 'EDTA', 100, 250) RETURNING id;")
        test_id = cur.fetchone()['id']
        cur.executemany("INSERT INTO test_parameters (test_id, param_name, unit, ref_range) VALUES (%s, %s, %s, %s);", [
            (test_id, 'Hemoglobin (Hb)', 'g/dL', '13.0 - 17.0'),
            (test_id, 'Total Leukocyte Count (TLC)', 'cells/cumm', '4000 - 10000')
        ])

    conn.commit()
    cur.close()
    conn.close()

if __name__ == '__main__':
    init_db()
