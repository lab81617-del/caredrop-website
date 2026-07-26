import os
import sqlite3
from flask import Flask, redirect, render_template, request, session, url_for

app = Flask(__name__)
app.secret_key = "caredrop_enterprise_production_key"


def get_db_connection():
  conn = sqlite3.connect("caredrop.db")
  conn.row_factory = sqlite3.Row
  return conn


# Automatically initialize or update database schema on every boot
def init_db():
  conn = sqlite3.connect("caredrop.db")
  cursor = conn.cursor()

  # Create tests table if it doesn't exist
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            price INTEGER NOT NULL,
            description TEXT,
            fasting_required TEXT DEFAULT 'No',
            report_timing TEXT DEFAULT '24 Hours'
        )
    """)

  # Create bookings table if it doesn't exist
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id INTEGER,
            patient_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            address TEXT NOT NULL,
            booking_date TEXT NOT NULL,
            status TEXT DEFAULT 'Pending',
            FOREIGN KEY (test_id) REFERENCES tests (id)
        )
    """)

  # Seed default tests if table is empty
  cursor.execute("SELECT COUNT(*) FROM tests")
  if cursor.fetchone()[0] == 0:
    sample_tests = [
        (
            "Complete Blood Count (CBC)",
            "Pathology",
            499,
            "Measures key components of blood including RBC, WBC, and platelets.",
            "No",
            "12 Hours",
        ),
        (
            "Lipid Profile",
            "Cardiology",
            799,
            (
                "Evaluates cholesterol and triglyceride levels to assess heart"
                " disease risk."
            ),
            "Yes (10-12 hrs)",
            "24 Hours",
        ),
        (
            "Thyroid Profile (T3, T4, TSH)",
            "Hormone",
            699,
            (
                "Assesses overall thyroid gland function and metabolic"
                " health."
            ),
            "No",
            "24 Hours",
        ),
        (
            "Diabetes Screening (HbA1c)",
            "Pathology",
            500,
            (
                "Measures average blood sugar levels over the past 3 months."
            ),
            "No",
            "6 Hours",
        ),
        (
            "Liver Function Test (LFT)",
            "Pathology",
            899,
            "Checks liver enzymes, proteins, and bilirubin levels.",
            "Yes",
            "24 Hours",
        ),
    ]
    cursor.executemany(
        """
            INSERT INTO tests (name, category, price, description, fasting_required, report_timing)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
        sample_tests,
    )

  conn.commit()
  conn.close()


# Run initialization on startup
init_db()


@app.route("/")
def index():
  conn = get_db_connection()
  search_query = request.args.get("q", "").strip()
  category = request.args.get("cat", "").strip()

  if search_query:
    tests = conn.execute(
        "SELECT * FROM tests WHERE name LIKE ? OR description LIKE ?",
        (f"%{search_query}%", f"%{search_query}%"),
    ).fetchall()
  elif category:
    tests = conn.execute(
        "SELECT * FROM tests WHERE category = ?", (category,)
    ).fetchall()
  else:
    tests = conn.execute("SELECT * FROM tests").fetchall()

  categories = conn.execute("SELECT DISTINCT category FROM tests").fetchall()
  conn.close()

  user_phone = session.get("user_phone")
  cart_count = len(session.get("cart", []))
  return render_template(
      "index.html",
      tests=tests,
      categories=[c["category"] for c in categories if c["category"]],
      user=user_phone,
      cart_count=cart_count,
  )


@app.route("/cart/add/<int:test_id>")
def add_to_cart(test_id):
  if "cart" not in session:
    session["cart"] = []
  if test_id not in session["cart"]:
    session["cart"].append(test_id)
    session.modified = True
  return redirect(url_for("index"))


@app.route("/cart/remove/<int:test_id>")
def remove_from_cart(test_id):
  if "cart" in session:
    session["cart"] = [i for i in session["cart"] if i != test_id]
    session.modified = True
  return redirect(url_for("view_cart"))


@app.route("/cart")
def view_cart():
  cart_ids = session.get("cart", [])
  if not cart_ids:
    return render_template("cart.html", tests=[], total=0)

  conn = get_db_connection()
  placeholders = ",".join(["?"] * len(cart_ids))
  tests = conn.execute(
      f"SELECT * FROM tests WHERE id IN ({placeholders})", cart_ids
  ).fetchall()
  conn.close()

  total = sum(t["price"] for t in tests)
  return render_template("cart.html", tests=tests, total=total)


@app.route("/book", methods=["GET", "POST"])
def book():
  conn = get_db_connection()
  cart_ids = session.get("cart", [])

  if request.method == "POST":
    patient_name = request.form.get("patient_name")
    phone = request.form.get("phone")
    address = request.form.get("address")
    date = request.form.get("date")

    for test_id in cart_ids:
      conn.execute(
          """INSERT INTO bookings (test_id, patient_name, phone, address, booking_date, status)
                      VALUES (?, ?, ?, ?, ?, 'Pending')""",
          (test_id, patient_name, phone, address, date),
      )
    conn.commit()
    session.pop("cart", None)
    session["user_phone"] = phone
    conn.close()
    return redirect(url_for("booking_success"))

  tests = []
  total = 0
  if cart_ids:
    placeholders = ",".join(["?"] * len(cart_ids))
    tests = conn.execute(
        f"SELECT * FROM tests WHERE id IN ({placeholders})", cart_ids
    ).fetchall()
    total = sum(t["price"] for t in tests)

  conn.close()
  return render_template("book.html", tests=tests, total=total)


@app.route("/booking-success")
def booking_success():
  return render_template("booking_success.html")


@app.route("/my-bookings")
def my_bookings():
  user_phone = session.get("user_phone")
  if not user_phone:
    return redirect(url_for("index"))

  conn = get_db_connection()
  bookings = conn.execute(
      """
        SELECT bookings.*, tests.name as test_name, tests.price 
        FROM bookings 
        JOIN tests ON bookings.test_id = tests.id 
        WHERE bookings.phone = ? 
        ORDER BY bookings.id DESC
    """,
      (user_phone,),
  ).fetchall()
  conn.close()
  return render_template("my_bookings.html", bookings=bookings, user=user_phone)


@app.route("/logout")
def logout():
  session.pop("user_phone", None)
  return redirect(url_for("index"))


@app.route("/admin-login", methods=["GET", "POST"])
def admin_login():
  error = None
  if request.method == "POST":
    username = request.form.get("username")
    password = request.form.get("password")
    if username == "admin" and password == "caredrop123":
      session["admin_logged_in"] = True
      return redirect(url_for("admin_dashboard"))
    else:
      error = "Invalid administrator credentials."
  return render_template("admin_login.html", error=error)


@app.route("/admin")
def admin_dashboard():
  if not session.get("admin_logged_in"):
    return redirect(url_for("admin_login"))

  conn = get_db_connection()
  bookings = conn.execute("""
        SELECT bookings.*, tests.name as test_name, tests.price 
        FROM bookings 
        JOIN tests ON bookings.test_id = tests.id 
        ORDER BY bookings.id DESC
    """).fetchall()
  tests = conn.execute("SELECT * FROM tests ORDER BY id DESC").fetchall()
  conn.close()
  return render_template("admin.html", bookings=bookings, tests=tests)


@app.route("/admin/add-test", methods=["POST"])
def add_test():
  if not session.get("admin_logged_in"):
    return redirect(url_for("admin_login"))

  name = request.form.get("name")
  category = request.form.get("category")
  price = request.form.get("price")
  description = request.form.get("description")
  fasting = request.form.get("fasting", "No")
  timing = request.form.get("timing", "24 Hours")

  conn = get_db_connection()
  conn.execute(
      """INSERT INTO tests (name, category, price, description, fasting_required, report_timing) 
                 VALUES (?, ?, ?, ?, ?, ?)""",
      (name, category, price, description, fasting, timing),
  )
  conn.commit()
  conn.close()
  return redirect(url_for("admin_dashboard"))


@app.route("/admin/delete-test/<int:test_id>")
def delete_test(test_id):
  if not session.get("admin_logged_in"):
    return redirect(url_for("admin_login"))

  conn = get_db_connection()
  conn.execute("DELETE FROM tests WHERE id = ?", (test_id,))
  conn.commit()
  conn.close()
  return redirect(url_for("admin_dashboard"))


@app.route("/admin/update-status/<int:booking_id>/<status>")
def update_status(booking_id, status):
  if not session.get("admin_logged_in"):
    return redirect(url_for("admin_login"))

  conn = get_db_connection()
  conn.execute("UPDATE bookings SET status = ? WHERE id = ?", (status, booking_id))
  conn.commit()
  conn.close()
  return redirect(url_for("admin_dashboard"))


@app.route("/admin/logout")
def admin_logout():
  session.pop("admin_logged_in", None)
  return redirect(url_for("admin_login"))


if __name__ == "__main__":
  port = int(os.environ.get("PORT", 5000))
  app.run(host="0.0.0.0", port=port)
