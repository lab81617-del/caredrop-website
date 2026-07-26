import sqlite3

def init_db():
    conn = sqlite3.connect('caredrop.db')
    cursor = conn.cursor()
    
    # Drop old tables to ensure clean enterprise schema
    cursor.execute('DROP TABLE IF EXISTS bookings')
    cursor.execute('DROP TABLE IF EXISTS tests')
    
    # Tests Table with Fasting, Timing, and Pricing Controls
    cursor.execute('''
        CREATE TABLE tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            price INTEGER NOT NULL,
            description TEXT,
            fasting_required TEXT DEFAULT 'No',
            report_timing TEXT DEFAULT '24 Hours'
        )
    ''')
    
    # Bookings Table
    cursor.execute('''
        CREATE TABLE bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id INTEGER,
            patient_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            address TEXT NOT NULL,
            booking_date TEXT NOT NULL,
            status TEXT DEFAULT 'Pending',
            FOREIGN KEY (test_id) REFERENCES tests (id)
        )
    ''')
    
    # Insert Initial Professional Diagnostic Tests
    sample_tests = [
        ('Complete Blood Count (CBC)', 'Pathology', 499, 'Measures key components of blood including RBC, WBC, and platelets.', 'No', '12 Hours'),
        ('Lipid Profile', 'Cardiology', 799, 'Evaluates cholesterol and triglyceride levels to assess heart disease risk.', 'Yes (10-12 hrs)', '24 Hours'),
        ('Thyroid Profile (T3, T4, TSH)', 'Hormone', 699, 'Assesses overall thyroid gland function and metabolic health.', 'No', '24 Hours'),
        ('Diabetes Screening (HbA1c)', 'Pathology', 500, 'Measures average blood sugar levels over the past 3 months.', 'No', '6 Hours'),
        ('Liver Function Test (LFT)', 'Pathology', 899, 'Checks liver enzymes, proteins, and bilirubin levels.', 'Yes', '24 Hours')
    ]
    
    cursor.executemany('''
        INSERT INTO tests (name, category, price, description, fasting_required, report_timing)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', sample_tests)
    
    conn.commit()
    conn.close()
    print("Database successfully initialized with enterprise schema.")

if __name__ == '__main__':
    init_db()
