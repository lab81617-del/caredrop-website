import os
from functools import wraps
from flask import Blueprint, request, session, redirect, url_for

auth_bp = Blueprint('auth', __name__)

# Authentication credentials pulled from secure environment variables
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "IHC2026!")
RECEPTION_PASSWORD = os.environ.get("RECEPTION_PASSWORD", "reception123")
PATHOLOGIST_PASSWORD = os.environ.get("PATHOLOGIST_PASSWORD", "doc2026")

def role_required(allowed_roles):
    """The explicit authorization lock for CareDrop V3."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user_role = session.get('role')
            # 'admin' has global override, others must be explicitly in allowed_roles list
            if user_role == 'admin' or user_role in allowed_roles:
                return f(*args, **kwargs)
            return redirect(url_for('auth.unified_login'))
        return decorated_function
    return decorator

@auth_bp.route('/login', methods=['GET', 'POST'])
def unified_login():
    if request.method == 'POST':
        role = request.form.get('role')
        pwd = request.form.get('password')
        
        if role == 'admin' and pwd == ADMIN_PASSWORD:
            session['role'] = 'admin'
        elif role == 'receptionist' and pwd == RECEPTION_PASSWORD:
            session['role'] = 'receptionist'
        elif role == 'pathologist' and pwd == PATHOLOGIST_PASSWORD:
            session['role'] = 'pathologist'
        else:
            return "Access Denied. Incorrect Credentials."
            
        return redirect(url_for('dashboard'))
        
    return '''
    <html>
    <head>
        <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@500;700;800&display=swap" rel="stylesheet">
    </head>
    <body style="background:#F1F5F9; display:flex; justify-content:center; align-items:center; height:100vh; font-family:'Plus Jakarta Sans', sans-serif; margin:0;">
        <div style="background:white; padding:40px; border-radius:12px; box-shadow:0 4px 15px rgba(0,0,0,0.05); text-align:center; width:350px;">
            <h2 style="color:#0D9488; margin-top:0; font-weight:800;">CareDrop V3 OS</h2>
            <p style="color:#64748B; margin-bottom:20px; font-size:14px;">Secure Staff Authentication</p>
            <form method="POST">
                <select name="role" style="width:100%; padding:12px; margin-bottom:15px; border-radius:6px; border:1px solid #CBD5E1; font-family:inherit;">
                    <option value="admin">System Admin</option>
                    <option value="receptionist">Reception & POS</option>
                    <option value="pathologist">Pathologist</option>
                </select>
                <input type="password" name="password" placeholder="Password" style="width:100%; padding:12px; margin-bottom:20px; border-radius:6px; border:1px solid #CBD5E1; font-family:inherit; box-sizing:border-box;">
                <button type="submit" style="width:100%; background:#0D9488; color:white; padding:12px; border:none; border-radius:6px; font-weight:bold; cursor:pointer; font-family:inherit;">Secure Login</button>
            </form>
        </div>
    </body>
    </html>
    '''

@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.unified_login'))
