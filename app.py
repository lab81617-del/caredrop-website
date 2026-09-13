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

# Import Blueprints (We will build these files in the next steps)
# from blueprints.auth import auth_bp
# from blueprints.pos import pos_bp
# from blueprints.financial import financial_bp
# from blueprints.clinical import clinical_bp
# from blueprints.logistics import logistics_bp
# from blueprints.settings import settings_bp

# Register Blueprints
# app.register_blueprint(auth_bp)
# app.register_blueprint(pos_bp)
# app.register_blueprint(financial_bp)
# app.register_blueprint(clinical_bp)
# app.register_blueprint(logistics_bp)
# app.register_blueprint(settings_bp)

@app.before_request
def startup_tasks():
    """Run once on the very first request to build the DB schema."""
    if not getattr(app, '_schema_checked', False):
        init_db()
        app._schema_checked = True

@app.route('/')
def home():
    # Temporary placeholder until we build the Patient Mobile UI
    return "CareDrop V3 Master Engine is Running."

if __name__ == '__main__':
    # Start the server
    app.run(debug=True, port=5000)
