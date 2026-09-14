import os
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import session

def get_db():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise ValueError("DATABASE_URL is missing. Please check your Render environment variables.")
    # Fix Render's legacy postgres:// prefix
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(db_url)

def safe_execute(query, params=None):
    conn = get_db()
    try: 
        cursor = conn.cursor()
        cursor.execute(query, params)
        conn.commit()
    except Exception as e: 
        conn.rollback()
        raise e
    finally: 
        conn.close()

def log_audit(entity_type, entity_id, action, old_state, new_state, notes=""):
    actor = session.get('role', 'system') if session else 'system'
    safe_execute("""
        INSERT INTO audit_logs (entity_type, entity_id, actor, action, old_state, new_state, notes) 
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (entity_type, entity_id, actor, action, old_state, new_state, notes))

def init_db():
    conn = get_db()
    try:
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY, phone VARCHAR(20) UNIQUE, email VARCHAR(255), 
                password_hash VARCHAR(255), role VARCHAR(50) DEFAULT 'patient', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS patients (
                id SERIAL PRIMARY KEY, user_id INT REFERENCES users(id), patient_uid VARCHAR(50) UNIQUE,
                full_name VARCHAR(255), dob DATE, age INT, sex VARCHAR(10), mobile VARCHAR(20),
                status VARCHAR(50) DEFAULT 'ACTIVE', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE TABLE IF NOT EXISTS tests (id SERIAL PRIMARY KEY, code VARCHAR(50), name VARCHAR(255), is_active BOOLEAN DEFAULT TRUE)")
        cursor.execute("CREATE TABLE IF NOT EXISTS lab_pricing (id SERIAL PRIMARY KEY, test_id INT, partner_id INT, price DECIMAL(10,2))")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id SERIAL PRIMARY KEY, order_ref VARCHAR(50) UNIQUE, patient_id INT REFERENCES patients(id),
                source VARCHAR(50), clinical_status VARCHAR(50) DEFAULT 'PENDING_COLLECTION',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS test_components (
                id SERIAL PRIMARY KEY, order_id INT REFERENCES orders(id), test_id INT,
                result_value VARCHAR(255), status VARCHAR(50) DEFAULT 'LOGGED',
                verified_by VARCHAR(255), verified_at TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS invoices (
                id SERIAL PRIMARY KEY, order_id INT REFERENCES orders(id), invoice_ref VARCHAR(50) UNIQUE, 
                subtotal DECIMAL(10,2), discount DECIMAL(10,2) DEFAULT 0, total_amount DECIMAL(10,2), 
                status VARCHAR(50) DEFAULT 'DRAFT', issued_at TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id SERIAL PRIMARY KEY, invoice_id INT REFERENCES invoices(id), amount DECIMAL(10,2), 
                method VARCHAR(50), idempotency_key UUID UNIQUE, transaction_ref VARCHAR(100), 
                status VARCHAR(50) DEFAULT 'PENDING', recorded_by VARCHAR(50), timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, 
                notes TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id SERIAL PRIMARY KEY, entity_type VARCHAR(50), entity_id INT, actor VARCHAR(100), 
                action VARCHAR(255), old_state VARCHAR(50), new_state VARCHAR(50), 
                notes TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("SELECT COUNT(*) FROM tests")
        if cursor.fetchone()[0] == 0:
            catalog = [
                ('CBC', 'Complete Blood Count', 350.00),
                ('LFT', 'Liver Function Test', 600.00),
                ('KFT', 'Kidney Function Test', 550.00),
                ('TSH', 'Thyroid Stimulating Hormone', 300.00),
                ('HBA1C', 'HbA1c (Glycosylated Hemoglobin)', 450.00),
                ('LIPID', 'Lipid Profile', 700.00)
            ]
            for code, name, price in catalog:
                cursor.execute("INSERT INTO tests (code, name, is_active) VALUES (%s, %s, TRUE) RETURNING id", (code, name))
                t_id = cursor.fetchone()[0]
                cursor.execute("INSERT INTO lab_pricing (test_id, partner_id, price) VALUES (%s, NULL, %s)", (t_id, price))

        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Database Init Error: {e}")
    finally:
        conn.close()
