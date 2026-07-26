import os
import sqlite3
from flask import Flask, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "caredrop_trademark_production_key"
app.config['UPLOAD_FOLDER'] = 'static/uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def get_db_connection():
    conn = sqlite3.connect("caredrop.db")
    conn.row_factory = sqlite3.Row
    return conn

def upgrade_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    # Advanced Tests Table (Now with Margin tracking)
    cursor.execute('''CREATE TABLE IF NOT EXISTS tests (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, category TEXT NOT NULL, 
        price INTEGER NOT NULL, margin INTEGER DEFAULT 0, description TEXT, 
        fasting_required TEXT, report_timing TEXT
    )''')
    # Advanced Bookings Table (Now with Lab routing and PDF reports)
    cursor.execute('''CREATE TABLE IF NOT EXISTS bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT, test_id INTEGER, patient_name TEXT NOT NULL, 
        phone TEXT NOT NULL, address TEXT NOT NULL, booking_date TEXT NOT NULL, 
        status TEXT DEFAULT 'Pending', lab_assigned TEXT DEFAULT 'Unassigned', report_pdf TEXT,
        FOREIGN KEY (test_id) REFERENCES tests (id)
    )''')
    
    # Safe Migrations
    migrations = [
        "ALTER TABLE tests ADD COLUMN margin INTEGER DEFAULT 0",
        "ALTER TABLE tests ADD COLUMN fasting_required TEXT DEFAULT 'No'",
        "ALTER TABLE tests ADD COLUMN report_timing TEXT DEFAULT '24 Hours'",
        "ALTER TABLE bookings ADD COLUMN lab_assigned TEXT DEFAULT 'Unassigned'",
        "ALTER TABLE bookings ADD COLUMN report_pdf TEXT"
    ]
    for migration in migrations:
        try: cursor.execute(migration)
        except Exception: pass
    
    conn.commit()
    conn.close()

upgrade_db()

@app.route("/")
def index():
    conn = get_db_connection()
    search_query = request.args.get("q", "").strip()
    category = request.args.get("cat", "").strip()
    try:
        if search_query:
            tests = conn.execute("SELECT * FROM tests WHERE name LIKE ? OR description LIKE ?", (f"%{search_query}%", f"%{search_query}%")).fetchall()
        elif category:
            tests = conn.execute("SELECT * FROM tests WHERE category = ?", (category,)).fetchall()
        else:
            tests = conn.execute("SELECT * FROM tests").fetchall()
        categories = conn.execute("SELECT DISTINCT category FROM tests").fetchall()
    except: tests, categories = [], []
    conn.close()
    return render_template("index.html", tests=tests, categories=[c["category"] for c in categories if c["category"]], cart_count=len(session.get("cart", [])))

@app.route("/cart/add/<int:test_id>")
def add_to_cart(test_id):
    if "cart" not in session: session["cart"] = []
    session["cart"].append(test_id) # Allow duplicates for multiple patients
    session.modified = True
    return redirect(url_for("view_cart"))

@app.route("/cart/remove/<int:index>")
def remove_from_cart(index):
    if "cart" in session and 0 <= index < len(session["cart"]):
        session["cart"].pop(index)
        session.modified = True
    return redirect(url_for("view_cart"))

@app.route("/cart")
def view_cart():
    cart_ids = session.get("cart", [])
    if not cart_ids: return render_template("cart.html", cart_items=[], total=0)
    
    conn = get_db_connection()
    cart_items = []
    total = 0
    for idx, test_id in enumerate(cart_ids):
        test = conn.execute("SELECT * FROM tests WHERE id = ?", (test_id,)).fetchone()
        if test:
            cart_items.append({"cart_index": idx, "test": test})
            total += int(test["price"])
    conn.close()
    return render_template("cart.html", cart_items=cart_items, total=total)

@app.route("/checkout", methods=["GET", "POST"])
def checkout():
    cart_ids = session.get("cart", [])
    if not cart_ids: return redirect(url_for("index"))
    
    if request.method == "POST":
        phone = request.form.get("primary_phone")
        address = request.form.get("address")
        date = request.form.get("date")
        
        conn = get_db_connection()
        # Multi-Patient Processing
        for idx, test_id in enumerate(cart_ids):
            patient_name = request.form.get(f"patient_name_{idx}")
            conn.execute("INSERT INTO bookings (test_id, patient_name, phone, address, booking_date) VALUES (?, ?, ?, ?, ?)",
                         (test_id, patient_name, phone, address, date))
        conn.commit()
        conn.close()
        
        session.pop("cart", None)
        session["user_phone"] = phone
        return redirect(url_for("my_bookings"))
    
    conn = get_db_connection()
    tests = [conn.execute("SELECT * FROM tests WHERE id = ?", (tid,)).fetchone() for tid in cart_ids]
    conn.close()
    return render_template("checkout.html", tests=tests)

@app.route("/my-bookings")
def my_bookings():
    user_phone = session.get("user_phone")
    if not user_phone: return redirect(url_for("index"))
    conn = get_db_connection()
    bookings = conn.execute('''SELECT bookings.*, tests.name as test_name, tests.price 
                               FROM bookings JOIN tests ON bookings.test_id = tests.id 
                               WHERE bookings.phone = ? ORDER BY bookings.id DESC''', (user_phone,)).fetchall()
    conn.close()
    return render_template("my_bookings.html", bookings=bookings)

# --- ADMIN COMMAND CENTER ---
@app.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("username") == "admin" and request.form.get("password") == "caredrop123":
            session["admin_logged_in"] = True
            return redirect(url_for("admin_dashboard"))
    return render_template("admin_login.html")

@app.route("/admin")
def admin_dashboard():
    if not session.get("admin_logged_in"): return redirect(url_for("admin_login"))
    conn = get_db_connection()
    try:
        bookings = conn.execute("SELECT bookings.*, tests.name as test_name, tests.price, tests.margin FROM bookings JOIN tests ON bookings.test_id = tests.id ORDER BY bookings.id DESC").fetchall()
        tests = conn.execute("SELECT * FROM tests ORDER BY id DESC").fetchall()
    except: bookings, tests = [], []
    conn.close()
    return render_template("admin.html", bookings=bookings, tests=tests)

@app.route("/admin/add-test", methods=["POST"])
def add_test():
    if not session.get("admin_logged_in"): return redirect(url_for("admin_login"))
    conn = get_db_connection()
    conn.execute("INSERT INTO tests (name, category, price, margin, description, fasting_required, report_timing) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (request.form.get("name"), request.form.get("category"), request.form.get("price", "0"), request.form.get("margin", "0"), 
                  request.form.get("description"), request.form.get("fasting", "No"), request.form.get("timing", "24 Hours")))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/update-booking/<int:booking_id>", methods=["POST"])
def update_booking(booking_id):
    if not session.get("admin_logged_in"): return redirect(url_for("admin_login"))
    status = request.form.get("status")
    lab = request.form.get("lab_assigned")
    
    conn = get_db_connection()
    
    # Handle PDF Upload
    pdf_file = request.files.get("report_pdf")
    if pdf_file and pdf_file.filename:
        filename = secure_filename(f"report_{booking_id}_{pdf_file.filename}")
        pdf_file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        conn.execute("UPDATE bookings SET status = ?, lab_assigned = ?, report_pdf = ? WHERE id = ?", (status, lab, filename, booking_id))
    else:
        conn.execute("UPDATE bookings SET status = ?, lab_assigned = ? WHERE id = ?", (status, lab, booking_id))
        
    conn.commit()
    conn.close()
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
