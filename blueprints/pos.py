from flask import Blueprint, request, jsonify
from database import get_db
from psycopg2.extras import RealDictCursor
from blueprints.auth import role_required

# Create the POS Blueprint
pos_bp = Blueprint('pos', __name__)

@pos_bp.route('/api/search-tests')
@role_required(['receptionist'])
def search_tests():
    """
    Lightning-fast JSON API for the Reception Desk debounced search.
    Searches tests by name or short code.
    """
    query = request.args.get('q', '').strip()
    
    # CPO Rule: Minimum query length to prevent massive DB dumps
    if len(query) < 2:
        return jsonify([])
    
    conn = get_db()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        # Search active tests and pull the default lab price
        cursor.execute("""
            SELECT t.id, t.name, t.code, COALESCE(lp.price, 0) as price 
            FROM tests t 
            LEFT JOIN lab_pricing lp ON t.id = lp.test_id 
            WHERE t.is_active = TRUE 
            AND (t.name ILIKE %s OR t.code ILIKE %s)
            LIMIT 15
        """, (f"%{query}%", f"%{query}%"))
        results = cursor.fetchall()
        return jsonify(results)
    except Exception as e:
        print(f"Search Error: {e}")
        return jsonify([])
    finally:
        conn.close()
