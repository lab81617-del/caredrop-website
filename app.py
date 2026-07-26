import sqlite3
from flask import Flask, redirect, render_template, request, session, url_for

app = Flask(__name__)
app.secret_key = "caredrop_elite_secret_key"


def get_db_connection():
  conn = sqlite3.connect("caredrop.db")
  conn.row_factory = sqlite3.Row
  return conn


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

  categories = conn.execute(
      "SELECT DISTINCT category FROM tests"
  ).fetchall()
  conn.close()

  user_phone = session.get("user_phone")
  return render_template(
      "index.html",
      tests=tests,
      categories=[c["category"] for c in categories],
      user=user_phone,
  )


@app.route("/book", methods=["GET", "POST"])
def book():
  conn = get_db_connection()
  test_id = request.args.get("test_id")

  if request.method == "POST":
    test_id = request.form.get("test_id")
    patient_name = request.form.get("patient_name")
    phone = request.form.get("phone")
    address = request.form.get("address")
    date = request.form.get("date")

    conn.execute(
        """INSERT INTO bookings (test_id, patient_name, phone, address, booking_date, status)
                  VALUES (?, ?, ?, ?, ?, 'Pending')""",
        (test_id, patient_name, phone, address, date),
    )
    conn.commit()
    conn.close()
    session["user_phone"] = phone
    return redirect(url_for("index"))

  test = conn.execute(
      "SELECT * FROM tests WHERE id = ?", (test_id,)
  ).fetchone()
  conn.close()
  return render_template("book.html", test=test)


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
  conn.close()
  return render_template("admin.html", bookings=bookings)


@app.route("/admin/logout")
def admin_logout():
  session.pop("admin_logged_in", None)
  return redirect(url_for("admin_login"))


import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
