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
    
    # 1. Diagnostic Tests Table
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

    # 2. Bookings / Orders Table
    cur.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_code TEXT UNIQUE,
            full_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            address TEXT NOT NULL,
            tests_requested TEXT NOT NULL,
            total_bill REAL NOT NULL,
            b2b_total_cost REAL DEFAULT 0,
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

    # 3. Clinical LIMS Parameters Table
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

    # Seed default routine tests if table is empty
    cur.execute('SELECT COUNT(*) FROM tests')
    if cur.fetchone()[0] == 0:
        default_catalog = [
            ('Complete Blood Count (CBC)', 'Hematology', 'EDTA Whole Blood', 100.0, 250.0, 'Checks overall health, detects anemia and infections.', '6 hrs', 'Accu Probe', 0),
            ('Thyroid Profile (T3, T4, TSH)', 'Endocrinology', 'Serum', 300.0, 800.0, 'Evaluates thyroid hormone production and metabolism.', '6 hrs', 'Accu Probe', 0),
            ('Lipid Profile', 'Biochemistry', 'Serum', 220.0, 600.0, 'Measures cholesterol and triglyceride levels.', '8 hrs', 'Kanika Lab', 1),
            ('Liver Function Test (LFT)', 'Biochemistry', 'Serum', 260.0, 700.0, 'Assesses hepatic enzymes, bilirubin, and proteins.', '8 hrs', 'Accu Probe', 0),
            ('Vitamin D (25 OH)', 'Immunology', 'Serum', 450.0, 1200.0, 'Assesses vitamin D levels for bone and immunity.', '24 hrs', 'Unique Wellness', 0)
        ]
        cur.executemany('''
            INSERT INTO tests (name, category, sample_type, b2b_cost, price, description, turnaround_time, partner_lab, fasting_required)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        ''', default_catalog)

    conn.commit()
    conn.close()

if __name__ == '__main__':
    init_db()
    print("Database schema successfully generated and seeded.")
