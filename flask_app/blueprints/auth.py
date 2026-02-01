from flask import Blueprint, render_template, redirect, url_for, request, flash, session
from core.auth.auth_service import AuthService

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        auth = AuthService()
        user = auth.authenticate(username, password)
        
        if user:
            session['username'] = user.username
            session['roles'] = user.roles
            return redirect(url_for('dashboard.index'))
        
        flash('Invalid username or password', 'error')
        
    return render_template('auth/login.html')

@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))
