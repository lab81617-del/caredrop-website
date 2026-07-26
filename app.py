import os
import sqlite3
from flask import Flask, redirect, render_template, request, session, url_for

app = Flask(__name__)
app.secret_key = "caredrop_peak_production_key"

def get_db_connection():
    conn = sqlite3.connect("caredrop.db")
    conn.row_factory = sqlite3.Row
    return conn

def repair_db():
    """Failsafe: Rebuilds tables instantly if Render wipes the SQLite file."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS tests (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, category TEXT NOT NULL, 
        price INTEGER NOT NULL, description TEXT, fasting_required TEXT DEFAULT 'No', report_timing TEXT DEFAULT '24 Hours'
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT, test_id INTEGER, patient_name TEXT NOT NULL, 
        phone TEXT NOT NULL, address TEXT NOT NULL, booking_date TEXT NOT NULL, status TEXT DEFAULT 'Pending',
        FOREIGN KEY (test_id) REFERENCES tests (id)
    )''')
    conn.commit()
    conn.close()

# Front-End Routes
@app.route("/")
def index():
    repair_db()
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
    except Exception:
        tests, categories = [], []
    conn.close()
    
    return render_template("index.html", tests=tests, categories=[c["category"] for c in categories if c["category"]], 
                           user=session.get("user_phone"), cart_count=len(session.get("cart", [])))

@app.route("/cart/add/<int:test_id>")
def add_to_cart(test_id):
    if "cart" not in session: session["cart"] = []
    if test_id not in session["cart"]:
        session["cart"].append(test_id)
        session.modified = True
    return redirect(url_for("index"))

@app.route("/cart")
def view_cart():
    cart_ids = session.get("cart", [])
    if not cart_ids: return render_template("cart.html", tests=[], total=0)
    conn = get_db_connection()
    placeholders = ",".join(["?"] * len(cart_ids))
    tests = conn.execute(f"SELECT * FROM tests WHERE id IN ({placeholders})", cart_ids).fetchall()
    conn.close()
    return render_template("cart.html", tests=tests, total=sum(t["price"] for t in tests))

# Admin Routes
@app.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("username") == "admin" and request.form.get("password") == "caredrop123":
            session["admin_logged_in"] = True
            return redirect(url_for("admin_dashboard"))
        return render_template("admin_login.html", error="Invalid credentials.")
    return render_template("admin_login.html")

@app.route("/admin")
def admin_dashboard():
    if not session.get("admin_logged_in"): return redirect(url_for("admin_login"))
    repair_db()
    conn = get_db_connection()
    try:
        bookings = conn.execute("SELECT bookings.*, tests.name as test_name, tests.price FROM bookings JOIN tests ON bookings.test_id = tests.id ORDER BY bookings.id DESC").fetchall()
        tests = conn.execute("SELECT * FROM tests ORDER BY id DESC").fetchall()
    except Exception as e:
        bookings, tests = [], []
    conn.close()
    return render_template("admin.html", bookings=bookings, tests=tests)

@app.route("/admin/add-test", methods=["POST"])
def add_test():
    if not session.get("admin_logged_in"): return redirect(url_for("admin_login"))
    repair_db()
    conn = get_db_connection()
    conn.execute("INSERT INTO tests (name, category, price, description, fasting_required, report_timing) VALUES (?, ?, ?, ?, ?, ?)",
                 (request.form.get("name"), request.form.get("category"), request.form.get("price"), request.form.get("description"), request.form.get("fasting", "No"), request.form.get("timing", "24 Hours")))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/update-status/<int:booking_id>/<status>")
def update_status(booking_id, status):
    if not session.get("admin_logged_in"): return redirect(url_for("admin_login"))
    conn = get_db_connection()
    conn.execute("UPDATE bookings SET status = ? WHERE id = ?", (status, booking_id))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
