import os
from functools import wraps
from flask import Blueprint, request, session, redirect, url_for

# Create the Blueprint
auth_bp = Blueprint('auth', __name__)

# Load passwords from environment variables (Keep these secret on Render)
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "IHC2026!")
RECEPTION_PASSWORD = os.environ.get("RECEPTION_PASSWORD", "reception123")
TECH_PASSWORD = os.environ.get("TECH_PASSWORD", "tech123")
PATHOLOGIST_PASSWORD = os.environ.get("PATHOLOGIST_PASSWORD", "doc2026")

def role_required(allowed_roles):
    """The master security lock for CareDrop V3."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if session.get('role') == 'admin' or session.get('role') in allowed_roles:
                return f(*args, **kwargs)
            return redirect(url_for('auth.unified_login'))
        return decorated_function
    return decorator

@auth_bp.route('/login', methods=['GET', 'POST'])
def unified_login():
    if request.method == 'POST':
        role = request.form.get('role')
        password = request.form.get('password')
        
        if role == 'admin' and password == ADMIN_PASSWORD: 
            session['role'] = 'admin'
            return redirect('/')
        elif role == 'receptionist' and password == RECEPTION_PASSWORD: 
            session['role'] = 'receptionist'
            return redirect('/')
        elif role == 'technician' and password == TECH_PASSWORD: 
            session['role'] = 'technician'
            return redirect('/')
        elif role == 'pathologist' and password == PATHOLOGIST_PASSWORD: 
            session['role'] = 'pathologist'
            return redirect('/')
            
        return "Access Denied: Invalid Password."
    
    # Clean, lightweight login UI
    return '''
    <html><body style="background:#F1F5F9; display:flex; justify-content:center; align-items:center; height:100vh; font-family:'Plus Jakarta Sans', sans-serif;">
        <div style="background:white; padding:40px; border-radius:12px; box-shadow:0 4px 15px rgba(0,0,0,0.05); width:350px; text-align:center;">
            <h2 style="margin-top:0; color:#0F172A;">CareDrop V3</h2>
            <p style="font-size:12px; color:#64748B; margin-bottom:20px;">Master Operations Portal</p>
            <form method="POST">
                <select name="role" style="width:100%; padding:12px; margin-bottom:15px; border-radius:6px; border:1px solid #CBD5E1; font-weight:bold;">
                    <option value="admin">Master Administrator</option>
                    <option value="receptionist">Reception Desk</option>
                    <option value="technician">Lab Technician</option>
                    <option value="pathologist">Chief Pathologist</option>
                </select>
                <input type="password" name="password" placeholder="Access Password" required style="width:100%; padding:12px; margin-bottom:15px; border-radius:6px; border:1px solid #CBD5E1;">
                <button type="submit" style="width:100%; background:#0D9488; color:white; padding:12px; border:none; border-radius:6px; font-weight:bold; cursor:pointer;">Authenticate</button>
            </form>
        </div>
    </body></html>
    '''

@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.unified_login'))
