import sqlite3
import os

DB_FILE = os.path.join(os.path.dirname(__file__), 'caredrop.db')

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute('''CREATE TABLE IF NOT EXISTS tests (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, category TEXT NOT NULL, sample_type TEXT, b2b_cost REAL DEFAULT 0, price REAL NOT NULL, is_active BOOLEAN DEFAULT 1
    );''')

    # NEW: Test Parameters Dictionary
    cur.execute('''CREATE TABLE IF NOT EXISTS test_parameters (
        id INTEGER PRIMARY KEY AUTOINCREMENT, test_id INTEGER, param_name TEXT NOT NULL, unit TEXT, ref_range TEXT,
        FOREIGN KEY(test_id) REFERENCES tests(id)
    );''')

    cur.execute('''CREATE TABLE IF NOT EXISTS partners (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, clinic_name TEXT, referral_code TEXT UNIQUE NOT NULL, phone TEXT, password TEXT NOT NULL, margin_pool_pct REAL DEFAULT 0.30, wallet_balance REAL DEFAULT 0.0, is_active BOOLEAN DEFAULT 1
    );''')

    cur.execute('''CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT, order_code TEXT UNIQUE, full_name TEXT NOT NULL, phone TEXT NOT NULL, email TEXT, address TEXT NOT NULL, tests_requested TEXT NOT NULL, gross_bill REAL NOT NULL, discount_given REAL DEFAULT 0, total_bill REAL NOT NULL, b2b_total_cost REAL DEFAULT 0, referral_code TEXT, partner_commission REAL DEFAULT 0, assigned_rider TEXT DEFAULT 'Unassigned', status TEXT DEFAULT 'Pending', barcode TEXT, temp_log TEXT, payment_mode TEXT DEFAULT 'Cash', is_paid BOOLEAN DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );''')

    # UPGRADED: Patient Test Results
    cur.execute('''CREATE TABLE IF NOT EXISTS test_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER, parameter_name TEXT NOT NULL, observed_value TEXT DEFAULT '', units TEXT, ref_interval TEXT,
        FOREIGN KEY (order_id) REFERENCES orders(id)
    );''')

    # Seed Default Data so it works immediately
    cur.execute('SELECT COUNT(*) FROM tests')
    if cur.fetchone()[0] == 0:
        cur.execute("INSERT INTO tests (name, category, sample_type, b2b_cost, price) VALUES ('Complete Blood Count (CBC)', 'Hematology', 'EDTA', 100, 250);")
        test_id = cur.lastrowid
        # Seed the CBC parameters
        params = [
            (test_id, 'Hemoglobin (Hb)', 'g/dL', '13.0 - 17.0'),
            (test_id, 'Total Leukocyte Count (TLC)', 'cells/cumm', '4,000 - 10,000'),
            (test_id, 'Packed Cell Volume (PCV)', '%', '40.0 - 50.0'),
            (test_id, 'Platelet Count', 'Lakhs/cumm', '1.50 - 4.50')
        ]
        cur.executemany("INSERT INTO test_parameters (test_id, param_name, unit, ref_range) VALUES (?, ?, ?, ?);", params)

    conn.commit()
    conn.close()

if __name__ == '__main__':
    init_db()
