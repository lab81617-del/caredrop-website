import os
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from flask import session

# Ensure .env is loaded if running locally
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

def get_db():
    """Establish a fresh connection to the PostgreSQL database."""
    return psycopg2.connect(os.environ.get("DATABASE_URL"))

def safe_execute(query, params=None):
    """Executes a query and commits, rolling back on failure."""
    conn = get_db()
    try: 
        cursor = conn.cursor()
        cursor.execute(query, params)
        conn.commit()
    except Exception as e: 
        conn.rollback()
        print(f"DB Execution Error: {e}")
        raise e
    finally: 
        conn.close()

def log_audit(entity_type, entity_id, action, old_state, new_state, notes=""):
    """The Immutable Flight Recorder."""
    actor = session.get('role', 'system') if session else 'system'
    safe_execute("""
        INSERT INTO audit_logs (entity_type, entity_id, actor, action, old_state, new_state, notes) 
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (entity_type, entity_id, actor, action, old_state, new_state, notes))

def init_db():
    """
    CareDrop V3 Master Schema. 
    Creates the 12 domains specified by the Executive Council if they do not exist.
    """
    conn = get_db()
    try:
        cursor = conn.cursor()
        
        # 1. IDENTITY & PATIENTS (Account != Patient)
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
                merged_into_patient_id INT, status VARCHAR(50) DEFAULT 'ACTIVE', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 2. MASTER DATA (Catalog & Partners)
        cursor.execute("CREATE TABLE IF NOT EXISTS tests (id SERIAL PRIMARY KEY, code VARCHAR(50), name VARCHAR(255), is_active BOOLEAN DEFAULT TRUE)")
        cursor.execute("CREATE TABLE IF NOT EXISTS partners (id SERIAL PRIMARY KEY, partner_name VARCHAR(255), type VARCHAR(50), is_active BOOLEAN DEFAULT TRUE)")
        cursor.execute("CREATE TABLE IF NOT EXISTS lab_pricing (id SERIAL PRIMARY KEY, test_id INT, partner_id INT, price DECIMAL(10,2))")

        # 3. CLINICAL PIPELINE (Orders & Test Components)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id SERIAL PRIMARY KEY, order_ref VARCHAR(50) UNIQUE, patient_id INT REFERENCES patients(id),
                partner_id INT, source VARCHAR(50), clinical_status VARCHAR(50) DEFAULT 'PENDING_COLLECTION',
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

        # 4. LOGISTICS & CHAIN OF CUSTODY (Samples)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS samples (
                id SERIAL PRIMARY KEY, accession_id VARCHAR(50) UNIQUE, order_id INT REFERENCES orders(id),
                parent_sample_id INT, status VARCHAR(50) DEFAULT 'EXPECTED', tube_type VARCHAR(100),
                collected_by VARCHAR(100), collected_at TIMESTAMP, received_at TIMESTAMP
            )
        """)

        # 5. FINANCIAL LEDGER (Invoices & Payments with Idempotency)
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

        # 6. SYSTEM INFRASTRUCTURE (Audit, Outbox Jobs, Reports)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id SERIAL PRIMARY KEY, entity_type VARCHAR(50), entity_id INT, actor VARCHAR(100), 
                action VARCHAR(255), old_state VARCHAR(50), new_state VARCHAR(50), 
                notes TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS background_jobs (
                id SERIAL PRIMARY KEY, job_type VARCHAR(50), payload JSONB, 
                status VARCHAR(50) DEFAULT 'QUEUED', retry_count INT DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id SERIAL PRIMARY KEY, order_id INT REFERENCES orders(id), version INT DEFAULT 1,
                pdf_data BYTEA, status VARCHAR(50) DEFAULT 'GENERATED', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Schema Initialization Error: {e}")
    finally:
        conn.close()
