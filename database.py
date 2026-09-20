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
    
    # 1. Diagnostic Tests Catalog
    cur.execute('''
        CREATE TABLE IF NOT EXISTS tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            sample_type TEXT,
            b2b_cost REAL DEFAULT 0,
            price REAL NOT NULL,
            description TEXT,
            turnaround_time TEXT DEFAULT '6 hrs',
            partner_lab TEXT DEFAULT 'Accu Probe Diagnostics',
            fasting_required BOOLEAN DEFAULT 0,
            is_active BOOLEAN DEFAULT 1
        );
    ''')

    # 2. Partner Network (Clinics & Pharmacies)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS partners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            clinic_name TEXT,
            referral_code TEXT UNIQUE NOT NULL,
            phone TEXT,
            password TEXT NOT NULL,
            margin_pool_pct REAL DEFAULT 0.30, -- 30% Total Floating Margin
            wallet_balance REAL DEFAULT 0.0,
            is_active BOOLEAN DEFAULT 1
        );
    ''')

    # 3. Patient Orders (Now tracks commissions & discounts)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_code TEXT UNIQUE,
            full_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            address TEXT NOT NULL,
            tests_requested TEXT NOT NULL,
            gross_bill REAL NOT NULL,
            discount_given REAL DEFAULT 0,
            total_bill REAL NOT NULL,
            b2b_total_cost REAL DEFAULT 0,
            referral_code TEXT,
            partner_commission REAL DEFAULT 0,
            assigned_rider TEXT DEFAULT 'Partner (Primary Day Rider)',
            assigned_lab TEXT DEFAULT 'Accu Probe Diagnostics',
            status TEXT DEFAULT 'Pending',
            barcode TEXT,
            temp_log TEXT,
            payment_mode TEXT DEFAULT 'Cash',
            is_paid BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    ''')

    # 4. Clinical LIMS Parameters
    cur.execute('''
        CREATE TABLE IF NOT EXISTS test_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER,
            parameter_name TEXT NOT NULL,
            observed_value TEXT,
            flag TEXT DEFAULT 'NORMAL',
            units TEXT,
            ref_interval TEXT,
            FOREIGN KEY (order_id) REFERENCES orders(id)
        );
    ''')

    # Seed Default Data
    cur.execute('SELECT COUNT(*) FROM tests')
    if cur.fetchone()[0] == 0:
        default_catalog = [
            ('Complete Blood Count (CBC)', 'Hematology', 'EDTA Whole Blood', 100.0, 250.0, '6 hrs'),
            ('Thyroid Profile (T3, T4, TSH)', 'Endocrinology', 'Serum', 300.0, 800.0, '6 hrs'),
            ('Lipid Profile', 'Biochemistry', 'Serum', 220.0, 600.0, '8 hrs'),
            ('Liver Function Test (LFT)', 'Biochemistry', 'Serum', 260.0, 700.0, '8 hrs')
        ]
        cur.executemany('INSERT INTO tests (name, category, sample_type, b2b_cost, price, turnaround_time) VALUES (?, ?, ?, ?, ?, ?);', default_catalog)

    cur.execute('SELECT COUNT(*) FROM partners')
    if cur.fetchone()[0] == 0:
        # Create a demo partner (e.g., Dr. Verma)
        cur.execute('''
            INSERT INTO partners (name, clinic_name, referral_code, phone, password, margin_pool_pct)
            VALUES ('Dr. Verma', 'Verma Clinic', 'VERMA30', '9999999999', 'partner123', 0.30);
        ''')

    conn.commit()
    conn.close()

if __name__ == '__main__':
    init_db()
    print("Database schema upgraded with B2B2C Partner Engine.")
