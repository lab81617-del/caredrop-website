import uuid
from flask import Blueprint, request, jsonify, session
from database import get_db, safe_execute, log_audit
from psycopg2.extras import RealDictCursor
from blueprints.auth import role_required

pos_bp = Blueprint('pos', __name__)

@pos_bp.route('/api/search-tests')
@role_required(['receptionist', 'admin'])
def search_tests():
    query = request.args.get('q', '').strip()
    if len(query) < 2: return jsonify([])
    conn = get_db()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT t.id, t.name, t.code, COALESCE(lp.price, 0) as price 
            FROM tests t LEFT JOIN lab_pricing lp ON t.id = lp.test_id 
            WHERE t.is_active = TRUE AND (t.name ILIKE %s OR t.code ILIKE %s) LIMIT 15
        """, (f"%{query}%", f"%{query}%"))
        return jsonify(cursor.fetchall())
    except: return jsonify([])
    finally: conn.close()

@pos_bp.route('/api/create-order', methods=['POST'])
@role_required(['receptionist', 'admin'])
def create_order():
    data = request.json
    idempotency_key = data.get('idempotency_key')
    
    conn = get_db()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # 1. CPO Rule: Idempotency check (Prevent double-billing)
        cursor.execute("SELECT order_ref FROM orders WHERE source = %s", (idempotency_key,))
        if cursor.fetchone():
            return jsonify({"status": "success", "message": "Already processed"})

        # 2. Create Walk-in Patient Profile (Auto-generated for V1 speed)
        cursor.execute("INSERT INTO patients (patient_uid, full_name, status) VALUES (%s, %s, 'ACTIVE') RETURNING id", (f"CD-PAT-{str(uuid.uuid4())[:6].upper()}", "Walk-in Patient"))
        patient_id = cursor.fetchone()['id']

        # 3. Create Clinical Order
        order_ref = f"ORD-{str(uuid.uuid4())[:6].upper()}"
        cursor.execute("INSERT INTO orders (order_ref, patient_id, source) VALUES (%s, %s, %s) RETURNING id", (order_ref, patient_id, idempotency_key))
        order_id = cursor.fetchone()['id']
        
        # 4. Snaphot Prices & Build Cart
        subtotal = 0
        for test_id in data.get('tests', []):
            cursor.execute("SELECT price FROM lab_pricing WHERE test_id = %s", (test_id,))
            price_row = cursor.fetchone()
            price = price_row['price'] if price_row else 0
            subtotal += price
            cursor.execute("INSERT INTO test_components (order_id, test_id, status) VALUES (%s, %s, 'LOGGED')", (order_id, test_id))
            
        discount = float(data.get('discount', 0))
        total = max(0, subtotal - discount)
        
        # 5. Create Financial Invoice
        inv_ref = f"INV-{order_ref}"
        cursor.execute("INSERT INTO invoices (order_id, invoice_ref, subtotal, discount, total_amount, status, issued_at) VALUES (%s, %s, %s, %s, %s, 'ISSUED', CURRENT_TIMESTAMP) RETURNING id", (order_id, inv_ref, subtotal, discount, total))
        invoice_id = cursor.fetchone()['id']
        
        # 6. Apply Advance Payment
        advance = float(data.get('advance_payment', 0))
        if advance > 0:
            status = 'PAID' if advance >= total else 'PARTIALLY_PAID'
            cursor.execute("INSERT INTO payments (invoice_id, amount, method, idempotency_key, status, recorded_by) VALUES (%s, %s, %s, %s, 'SUCCESS', %s)", (invoice_id, advance, data.get('method', 'Cash'), str(uuid.uuid4()), session.get('role')))
            cursor.execute("UPDATE invoices SET status = %s WHERE id = %s", (status, invoice_id))
            
        conn.commit()
        log_audit('Invoice', invoice_id, "Order Checkout Complete", "DRAFT", "ISSUED", f"Total: ₹{total}")
        return jsonify({"status": "success", "invoice": inv_ref})
    except Exception as e:
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        conn.close()
