from flask import Blueprint, request, jsonify, session
from database import get_db, safe_execute, log_audit
from psycopg2.extras import RealDictCursor
from blueprints.auth import role_required
import json

clinical_bp = Blueprint('clinical', __name__)

@clinical_bp.route('/api/save-results', methods=['POST'])
@role_required(['technician'])
def save_results():
    data = request.json
    order_id = data.get('order_id')
    results = data.get('results', []) # List of dicts: {test_component_id: val}
    
    conn = get_db()
    try:
        cursor = conn.cursor()
        for r in results:
            cursor.execute("""
                UPDATE test_components 
                SET result_value = %s, status = 'PENDING_VERIFICATION' 
                WHERE id = %s AND order_id = %s
            """, (r['value'], r['component_id'], order_id))
            
        # Update Order state
        cursor.execute("UPDATE orders SET clinical_status = 'PENDING_VERIFICATION' WHERE id = %s", (order_id,))
        log_audit('Order', order_id, "Results Entered", "PROCESSING", "PENDING_VERIFICATION", "By Technician")
        conn.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@clinical_bp.route('/api/verify-report', methods=['POST'])
@role_required(['pathologist'])
def verify_report():
    data = request.json
    order_id = data.get('order_id')
    pathologist_name = session.get('role') # In real UI, pull actual doctor name
    
    conn = get_db()
    try:
        cursor = conn.cursor()
        # 1. Mark Clinical State as Verified
        cursor.execute("UPDATE test_components SET status = 'VERIFIED', verified_by = %s, verified_at = CURRENT_TIMESTAMP WHERE order_id = %s", (pathologist_name, order_id))
        cursor.execute("UPDATE orders SET clinical_status = 'VERIFIED' WHERE id = %s", (order_id,))
        
        # 2. Outbox Pattern: Queue the PDF Generation so it survives server crashes
        payload = json.dumps({"order_id": order_id})
        cursor.execute("INSERT INTO background_jobs (job_type, payload) VALUES ('GENERATE_REPORT', %s)", (payload,))
        
        log_audit('Order', order_id, "Pathologist Verified", "PENDING_VERIFICATION", "VERIFIED", "PDF generation queued")
        conn.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()
