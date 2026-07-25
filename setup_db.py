import sqlite3
import os

if os.path.exists('caredrop.db'):
    os.remove('caredrop.db')
    print("🗑️ Resetting database...")

conn = sqlite3.connect('caredrop.db')
cursor = conn.cursor()

# Tests Table
cursor.execute('''
CREATE TABLE tests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_name TEXT, lab_name TEXT, b2b_price INTEGER, b2c_price INTEGER, category TEXT
)
''')

# Users Table
cursor.execute('''
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT UNIQUE, name TEXT
)
''')

# Orders Table
cursor.execute('''
CREATE TABLE orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER, patient_name TEXT, address TEXT, tests_ordered TEXT, total_amount INTEGER, 
    payment_mode TEXT DEFAULT 'Pay on Sample Collection', status TEXT DEFAULT 'Pending Collection',
    order_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id)
)
''')

# Prescriptions Table
cursor.execute('''
CREATE TABLE prescriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER, image_path TEXT, status TEXT DEFAULT 'Reviewing',
    upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id)
)
''')

# Insert Sample Tests
cursor.execute("INSERT INTO tests (test_name, lab_name, b2b_price, b2c_price, category) VALUES ('Thyroid Profile (T3, T4, TSH)', 'IHC Labs', 200, 400, 'Hormone')")
cursor.execute("INSERT INTO tests (test_name, lab_name, b2b_price, b2c_price, category) VALUES ('Complete Blood Count (CBC)', 'IHC Labs', 150, 250, 'Blood')")
cursor.execute("INSERT INTO tests (test_name, lab_name, b2b_price, b2c_price, category) VALUES ('HbA1c Blood Sugar', 'IHC Labs', 180, 300, 'Diabetes')")

conn.commit()
conn.close()
print("✅ Complete database built successfully!")