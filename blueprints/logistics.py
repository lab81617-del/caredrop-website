from flask import Blueprint, request, jsonify, session
from database import get_db, safe_execute, log_audit
from psycopg2.extras import RealDictCursor
from blueprints.auth import role_required

logistics_bp = Blueprint('logistics', __name__)

@logistics_bp.route('/api/reject-sample', methods=['POST'])
@role_required(['technician', 'pathologist', 'admin'])
def reject_sample():
    data = request.json
    sample_id = data.get('sample_id')
    reason = data.get('reason')
    
    conn = get_db()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # 1. Lock the original sample as REJECTED
        cursor.execute("SELECT order_id, accession_id, tube_type FROM samples WHERE id = %s", (sample_id,))
        sample = cursor.fetchone()
        cursor.execute("UPDATE samples SET status = 'REJECTED' WHERE id = %s", (sample_id,))
        
        # 2. Generate the Child Sample for Recollection (Lineage tracking)
        new_accession = sample['accession_id'] + "-R1"
        cursor.execute("""
            INSERT INTO samples (accession_id, order_id, parent_sample_id, status, tube_type)
            VALUES (%s, %s, %s, 'EXPECTED', %s) RETURNING id
        """, (new_accession, sample['order_id'], sample_id, sample['tube_type']))
        
        # 3. Update Order
        cursor.execute("UPDATE orders SET clinical_status = 'RECOLLECTION_REQUIRED' WHERE id = %s", (sample['order_id'],))
        
        log_audit('Sample', sample_id, "Sample Rejected", "ACCEPTED", "REJECTED", f"Reason: {reason}. Child accession generated: {new_accession}")
        conn.commit()
        return jsonify({"status": "success", "new_accession": new_accession})
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()
