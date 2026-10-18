import os
import re
import secrets
import tempfile
import threading
import time
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for, flash,
    send_file, session, jsonify, abort
)
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import yt_dlp

# ==================== APP CONFIG ====================
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL', 'sqlite:///instance/app.db'
).replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB

# Ensure instance folder exists
os.makedirs(os.path.join(app.root_path, 'instance'), exist_ok=True)
os.makedirs(os.path.join(app.root_path, 'temp_downloads'), exist_ok=True)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'warning'

# Admin credentials (CHANGE THESE AFTER FIRST DEPLOY!)
ADMIN_USERNAME = 'arnold60011'
ADMIN_PASSWORD = 'kipruto1732'
ADMIN_SECRET_PATH = 'admin_login254'

# ==================== MODELS ====================
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email_or_phone = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    is_banned = db.Column(db.Boolean, default=False)
    verification_code = db.Column(db.String(20), nullable=True)
    code_expires = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, nullable=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ==================== HELPERS ====================
def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('is_admin'):
            flash('Admin access required.', 'danger')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated

def generate_code(length=8):
    return secrets.token_hex(length // 2).upper()

def clean_old_temp_files():
    """Remove temp files older than 1 hour"""
    temp_dir = os.path.join(app.root_path, 'temp_downloads')
    now = time.time()
    for f in os.listdir(temp_dir):
        path = os.path.join(temp_dir, f)
        if os.path.isfile(path) and now - os.path.getmtime(path) > 3600:
            try:
                os.remove(path)
            except Exception:
                pass

def get_video_info(url):
    """Extract video info and available formats using yt-dlp"""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'extract_flat': False,
        'socket_timeout': 30,
        'retries': 3,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return None, "Could not extract video information."

            formats = []
            seen = set()

            # Prefer progressive + adaptive formats
            for f in info.get('formats', []):
                height = f.get('height')
                ext = f.get('ext', '')
                format_id = f.get('format_id')
                filesize = f.get('filesize') or f.get('filesize_approx')
                vcodec = f.get('vcodec', 'none')
                acodec = f.get('acodec', 'none')
                format_note = f.get('format_note') or ''

                # Skip pure audio or storyboards
                if vcodec == 'none' or 'storyboard' in str(format_note).lower():
                    continue
                if not height:
                    continue

                # Quality label
                if height >= 2160:
                    quality = '4K'
                elif height >= 1440:
                    quality = '1440p'
                elif height >= 1080:
                    quality = '1080p HD'
                elif height >= 720:
                    quality = '720p HD'
                elif height >= 480:
                    quality = '480p'
                else:
                    quality = f'{height}p'

                key = (height, ext)
                if key in seen:
                    continue
                seen.add(key)

                size_str = ''
                if filesize:
                    mb = filesize / (1024 * 1024)
                    size_str = f'{mb:.1f} MB' if mb < 1000 else f'{mb/1024:.1f} GB'

                formats.append({
                    'format_id': format_id,
                    'quality': quality,
                    'height': height,
                    'ext': ext,
                    'filesize': size_str,
                    'has_audio': acodec != 'none',
                    'note': format_note,
                })

            # Sort by height descending
            formats.sort(key=lambda x: x['height'], reverse=True)

            # Limit to best unique qualities
            unique = []
            seen_q = set()
            for f in formats:
                q = f['quality']
                if q not in seen_q:
                    seen_q.add(q)
                    unique.append(f)
                if len(unique) >= 8:
                    break

            title = info.get('title', 'Unknown Title')
            thumbnail = info.get('thumbnail')
            duration = info.get('duration')
            uploader = info.get('uploader') or info.get('channel') or ''
            platform = info.get('extractor_key', 'Unknown')

            return {
                'title': title,
                'thumbnail': thumbnail,
                'duration': duration,
                'uploader': uploader,
                'platform': platform,
                'formats': unique,
                'url': url,
            }, None
    except Exception as e:
        return None, str(e)

def download_video(url, format_id, out_path):
    """Download selected format. Prefer mp4 and merge audio when needed."""
    # If a specific format_id is given use it; otherwise fall back to best
    fmt = format_id if format_id else 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best'
    ydl_opts = {
        'format': fmt,
        'outtmpl': out_path,
        'quiet': True,
        'no_warnings': True,
        'merge_output_format': 'mp4',
        'socket_timeout': 90,
        'retries': 5,
        'fragment_retries': 5,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        },
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return True, None
    except Exception as e:
        return False, str(e)

# ==================== ROUTES ====================
@app.route('/')
def index():
    if current_user.is_authenticated:
        if current_user.is_banned:
            return redirect(url_for('banned'))
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email_or_phone = request.form.get('email_or_phone', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')

        if not username or not email_or_phone or not password:
            flash('All fields are required.', 'danger')
            return render_template('register.html')

        if len(username) < 3:
            flash('Username must be at least 3 characters.', 'danger')
            return render_template('register.html')

        if password != confirm:
            flash('Passwords do not match.', 'danger')
            return render_template('register.html')

        if len(password) < 6:
            flash('Password must be at least 6 characters.', 'danger')
            return render_template('register.html')

        if User.query.filter_by(username=username).first():
            flash('Username already taken.', 'danger')
            return render_template('register.html')

        if User.query.filter_by(email_or_phone=email_or_phone).first():
            flash('Email or phone already registered.', 'danger')
            return render_template('register.html')

        user = User(username=username, email_or_phone=email_or_phone)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        flash('Account created successfully! Please log in.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            if user.is_banned:
                login_user(user)
                return redirect(url_for('banned'))
            login_user(user)
            user.last_login = datetime.utcnow()
            db.session.commit()
            flash(f'Welcome back, {user.username}!', 'success')
            return redirect(url_for('dashboard'))
        flash('Invalid username or password.', 'danger')

    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        step = request.form.get('step', 'request')
        if step == 'request':
            email_or_phone = request.form.get('email_or_phone', '').strip()
            user = User.query.filter_by(email_or_phone=email_or_phone).first()
            if not user:
                flash('No account found with that email/phone.', 'danger')
                return render_template('forgot_password.html')
            # Instruct user to contact developer
            flash(
                'To reset your password, contact the developer on WhatsApp: '
                '0140204950 and request a verification code. '
                'Then come back here and enter the code + new password.',
                'info'
            )
            return render_template('forgot_password.html', show_reset=True, email_or_phone=email_or_phone)

        elif step == 'reset':
            email_or_phone = request.form.get('email_or_phone', '').strip()
            code = request.form.get('code', '').strip().upper()
            new_password = request.form.get('new_password', '')
            confirm = request.form.get('confirm_password', '')

            user = User.query.filter_by(email_or_phone=email_or_phone).first()
            if not user:
                flash('Account not found.', 'danger')
                return render_template('forgot_password.html')

            if not user.verification_code or user.verification_code != code:
                flash('Invalid verification code. Contact WhatsApp 0140204950.', 'danger')
                return render_template('forgot_password.html', show_reset=True, email_or_phone=email_or_phone)

            if user.code_expires and user.code_expires < datetime.utcnow():
                flash('Verification code has expired. Request a new one.', 'danger')
                return render_template('forgot_password.html', show_reset=True, email_or_phone=email_or_phone)

            if new_password != confirm or len(new_password) < 6:
                flash('Passwords must match and be at least 6 characters.', 'danger')
                return render_template('forgot_password.html', show_reset=True, email_or_phone=email_or_phone)

            user.set_password(new_password)
            user.verification_code = None
            user.code_expires = None
            db.session.commit()
            flash('Password reset successfully! You can now log in.', 'success')
            return redirect(url_for('login'))

    return render_template('forgot_password.html')

@app.route('/dashboard', methods=['GET', 'POST'])
@login_required
def dashboard():
    if current_user.is_banned:
        return redirect(url_for('banned'))

    video_info = None
    error = None

    if request.method == 'POST':
        url = request.form.get('url', '').strip()
        if not url:
            flash('Please enter a valid URL.', 'warning')
        else:
            # Basic URL validation for supported platforms
            supported = any(x in url.lower() for x in [
                'youtube.com', 'youtu.be', 'facebook.com', 'fb.watch',
                'tiktok.com', 'instagram.com', 'twitter.com', 'x.com'
            ])
            if not supported:
                flash('Supported: YouTube, Facebook, TikTok, Instagram, Twitter/X', 'warning')
            else:
                video_info, error = get_video_info(url)
                if error:
                    flash(f'Error analyzing video: {error}', 'danger')
                else:
                    session['last_url'] = url
                    session['last_info'] = {
                        'title': video_info['title'],
                        'thumbnail': video_info['thumbnail'],
                        'platform': video_info['platform'],
                    }

    return render_template('dashboard.html', video_info=video_info)

@app.route('/download', methods=['POST'])
@login_required
def download():
    if current_user.is_banned:
        return redirect(url_for('banned'))

    url = request.form.get('url')
    format_id = request.form.get('format_id')

    if not url:
        flash('Missing URL.', 'danger')
        return redirect(url_for('dashboard'))

    clean_old_temp_files()

    # Create unique temp filename
    temp_dir = os.path.join(app.root_path, 'temp_downloads')
    filename = f"{secrets.token_hex(8)}.%(ext)s"
    outtmpl = os.path.join(temp_dir, filename)

    success, err = download_video(url, format_id, outtmpl)
    if not success:
        flash(f'Download failed: {err}', 'danger')
        return redirect(url_for('dashboard'))

    # Find the actual downloaded file
    base = outtmpl.replace('%(ext)s', '')
    downloaded = None
    for f in os.listdir(temp_dir):
        if f.startswith(os.path.basename(base).split('.')[0]):
            downloaded = os.path.join(temp_dir, f)
            break

    if not downloaded or not os.path.exists(downloaded):
        flash('Downloaded file not found.', 'danger')
        return redirect(url_for('dashboard'))

    # Send file and schedule deletion
    def remove_later(path):
        time.sleep(30)
        try:
            os.remove(path)
        except Exception:
            pass

    threading.Thread(target=remove_later, args=(downloaded,), daemon=True).start()

    return send_file(
        downloaded,
        as_attachment=True,
        download_name=secure_filename(os.path.basename(downloaded))
    )

@app.route('/banned')
@login_required
def banned():
    if not current_user.is_banned:
        return redirect(url_for('dashboard'))
    return render_template('banned.html')

# ==================== ADMIN ROUTES ====================
@app.route(f'/{ADMIN_SECRET_PATH}', methods=['GET', 'POST'])
def admin_login():
    if session.get('is_admin'):
        return redirect(url_for('admin_panel'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['is_admin'] = True
            flash('Admin logged in successfully.', 'success')
            return redirect(url_for('admin_panel'))
        flash('Invalid admin credentials.', 'danger')

    return render_template('admin_login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    flash('Admin logged out.', 'info')
    return redirect(url_for('admin_login'))

@app.route('/admin')
@admin_required
def admin_panel():
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template('admin_panel.html', users=users)

@app.route('/admin/ban/<int:user_id>', methods=['POST'])
@admin_required
def ban_user(user_id):
    user = User.query.get_or_404(user_id)
    user.is_banned = True
    db.session.commit()
    flash(f'User {user.username} has been banned.', 'warning')
    return redirect(url_for('admin_panel'))

@app.route('/admin/unban/<int:user_id>', methods=['POST'])
@admin_required
def unban_user(user_id):
    user = User.query.get_or_404(user_id)
    user.is_banned = False
    db.session.commit()
    flash(f'User {user.username} has been unbanned.', 'success')
    return redirect(url_for('admin_panel'))

@app.route('/admin/generate_code/<int:user_id>', methods=['POST'])
@admin_required
def generate_verification_code(user_id):
    user = User.query.get_or_404(user_id)
    code = generate_code(8)
    user.verification_code = code
    user.code_expires = datetime.utcnow() + timedelta(hours=24)
    db.session.commit()
    flash(f'Verification code for {user.username}: {code} (valid 24h). Share via WhatsApp.', 'success')
    return redirect(url_for('admin_panel'))

# ==================== INIT ====================
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)