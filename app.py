import os
from flask import Flask, redirect, url_for, session
from dotenv import load_dotenv

# Import the database initializer
from database import init_db

# Load environment variables
load_dotenv()

# Initialize Flask App
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "caredrop-v3-enterprise-key")

# Import Blueprints
from blueprints.auth import auth_bp
from blueprints.pos import pos_bp

# Register Blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(pos_bp)

@app.before_request
def startup_tasks():
    """Run once on the very first request to build the DB schema."""
    if not getattr(app, '_schema_checked', False):
        init_db()
        app._schema_checked = True

@app.route('/')
def home():
    # If not logged in, redirect to the new Auth module
    if 'role' not in session:
        return redirect(url_for('auth.unified_login'))
        
    return f"CareDrop V3 Master Engine Running. Logged in as: {session['role']}. (Dashboards loading next...)"

if __name__ == '__main__':
    # Start the server
    app.run(debug=True, port=5000)
