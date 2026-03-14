import os, uuid, sqlite3, json, time, threading
from dotenv import load_dotenv
load_dotenv()
import smtplib
from email.message import EmailMessage
from itsdangerous import URLSafeTimedSerializer
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from flask_socketio import SocketIO, emit, join_room
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'manshi_tatty_dev_key')
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')

serializer = URLSafeTimedSerializer(app.secret_key)
socketio = SocketIO(app, cors_allowed_origins="*")

UPLOAD_FOLDER = 'uploads'
DB_FILE = 'gallery.db'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER


def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS posts (
        id TEXT PRIMARY KEY, filename TEXT, caption TEXT, font TEXT, filter TEXT, owner TEXT,
        is_reel INTEGER DEFAULT 0, is_collab INTEGER DEFAULT 0, collab_user TEXT,
        scheduled_at TEXT, close_friends_only INTEGER DEFAULT 0,
        post_order INTEGER DEFAULT 0, shares_count INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS likes (post_id TEXT, username TEXT, PRIMARY KEY(post_id, username))''')
    c.execute('''CREATE TABLE IF NOT EXISTS comments (id INTEGER PRIMARY KEY AUTOINCREMENT, post_id TEXT, author TEXT, text TEXT, is_hidden INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY, email TEXT, password TEXT, pfp TEXT, bio TEXT,
        is_private INTEGER DEFAULT 0, is_verified INTEGER DEFAULT 0,
        points INTEGER DEFAULT 0, twofa_secret TEXT, twofa_enabled INTEGER DEFAULT 0,
        profile_views INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS followers (follower TEXT, followed TEXT, status TEXT DEFAULT 'accepted', PRIMARY KEY(follower, followed))''')
    c.execute('''CREATE TABLE IF NOT EXISTS notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, message TEXT, is_read INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, sender TEXT, receiver TEXT,
        group_id TEXT, text TEXT, msg_type TEXT DEFAULT 'text',
        media_filename TEXT, disappear INTEGER DEFAULT 0, seen_by TEXT DEFAULT '[]',
        reply_to INTEGER, pinned INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS message_reactions (id INTEGER PRIMARY KEY AUTOINCREMENT, message_id INTEGER, username TEXT, emoji TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS group_chats (id TEXT PRIMARY KEY, name TEXT, photo TEXT, created_by TEXT, theme TEXT DEFAULT '#ff85a2', created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS group_members (group_id TEXT, username TEXT, role TEXT DEFAULT 'member', PRIMARY KEY(group_id, username))''')
    c.execute('''CREATE TABLE IF NOT EXISTS stories (id TEXT PRIMARY KEY, owner TEXT, filename TEXT, msg_type TEXT DEFAULT 'image', created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS story_views (story_id TEXT, username TEXT, PRIMARY KEY(story_id, username))''')
    c.execute('''CREATE TABLE IF NOT EXISTS bookmarks (username TEXT, post_id TEXT, PRIMARY KEY(username, post_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS close_friends (username TEXT, friend TEXT, PRIMARY KEY(username, friend))''')
    c.execute('''CREATE TABLE IF NOT EXISTS polls (id INTEGER PRIMARY KEY AUTOINCREMENT, post_id TEXT, question TEXT, option_a TEXT, option_b TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS poll_votes (poll_id INTEGER, username TEXT, choice TEXT, PRIMARY KEY(poll_id, username))''')
    c.execute('''CREATE TABLE IF NOT EXISTS highlights (id TEXT PRIMARY KEY, owner TEXT, name TEXT, cover TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS highlight_posts (highlight_id TEXT, post_id TEXT, PRIMARY KEY(highlight_id, post_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS blocked (blocker TEXT, blocked TEXT, PRIMARY KEY(blocker, blocked))''')
    c.execute('''CREATE TABLE IF NOT EXISTS comment_filters (username TEXT, word TEXT, PRIMARY KEY(username, word))''')
    c.execute('''CREATE TABLE IF NOT EXISTS chat_themes (user1 TEXT, user2 TEXT, theme TEXT, PRIMARY KEY(user1, user2))''')
    c.execute('''CREATE TABLE IF NOT EXISTS chat_streaks (user1 TEXT, user2 TEXT, streak INTEGER DEFAULT 0, last_msg_date TEXT, PRIMARY KEY(user1, user2))''')
    c.execute('''CREATE TABLE IF NOT EXISTS profile_photos (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, filename TEXT, display_order INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS activity_log (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, action TEXT, target TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS badges (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, badge TEXT, earned_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS collage_items (post_id TEXT, filename TEXT, item_order INTEGER)''')
    conn.commit()
    conn.close()


init_db()


def log_activity(username, action, target=''):
    try:
        conn = get_db()
        conn.execute("INSERT INTO activity_log (username,action,target) VALUES (?,?,?)", (username, action, target))
        conn.commit()
        conn.close()
    except:
        pass


def award_points(username, pts, badge=None):
    try:
        conn = get_db()
        conn.execute("UPDATE users SET points=points+? WHERE username=?", (pts, username))
        if badge:
            conn.execute("INSERT OR IGNORE INTO badges (username,badge) VALUES (?,?)", (username, badge))
        conn.commit()
        conn.close()
    except:
        pass


def update_streak(u1, u2):
    try:
        conn = get_db()
        c = conn.cursor()
        key = (min(u1, u2), max(u1, u2))
        today = datetime.utcnow().date().isoformat()
        c.execute("SELECT streak,last_msg_date FROM chat_streaks WHERE user1=? AND user2=?", key)
        row = c.fetchone()
        if row:
            yesterday = (datetime.utcnow().date() - timedelta(days=1)).isoformat()
            ns = (row['streak'] + 1) if row['last_msg_date'] == yesterday else (1 if row['last_msg_date'] != today else row['streak'])
            conn.execute("UPDATE chat_streaks SET streak=?,last_msg_date=? WHERE user1=? AND user2=?", (ns, today, key[0], key[1]))
        else:
            conn.execute("INSERT INTO chat_streaks VALUES (?,?,1,?)", (key[0], key[1], today))
        conn.commit()
        conn.close()
    except:
        pass


def get_streak_between(u1, u2):
    try:
        conn = get_db()
        c = conn.cursor()
        key = (min(u1, u2), max(u1, u2))
        c.execute("SELECT streak FROM chat_streaks WHERE user1=? AND user2=?", key)
        row = c.fetchone()
        conn.close()
        return row['streak'] if row else 0
    except:
        return 0


def send_notif(owner, me, msg, conn=None):
    close_c = conn is None
    try:
        if close_c:
            conn = get_db()
        conn.execute("INSERT INTO notifications (username,message) VALUES (?,?)", (owner, msg))
        if close_c:
            conn.commit()
            conn.close()
        socketio.emit('receive_notification', {'message': msg}, to=f"notify_{owner}")
    except:
        pass


def purge_expired_stories():
    try:
        conn = get_db()
        cutoff = (datetime.utcnow() - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
        conn.execute("DELETE FROM stories WHERE created_at < ?", (cutoff,))
        conn.commit()
        conn.close()
    except:
        pass


def _bg():
    while True:
        time.sleep(60)
        try:
            conn = get_db()
            now = datetime.utcnow().strftime('%Y-%m-%d %H:%M')
            conn.execute("UPDATE posts SET scheduled_at=NULL WHERE scheduled_at<=?", (now,))
            conn.commit()
            conn.close()
        except:
            pass


threading.Thread(target=_bg, daemon=True).start()


def is_blocked(me, other):
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT 1 FROM blocked WHERE (blocker=? AND blocked=?) OR (blocker=? AND blocked=?)", (me, other, other, me))
        r = c.fetchone()
        conn.close()
        return r is not None
    except:
        return False


def check_filter(owner, text):
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT word FROM comment_filters WHERE username=?", (owner,))
        words = [r['word'].lower() for r in c.fetchall()]
        conn.close()
        return any(w in text.lower() for w in words)
    except:
        return False


def get_badges(username):
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT badge FROM badges WHERE username=?", (username,))
        b = [r['badge'] for r in c.fetchall()]
        conn.close()
        return b
    except:
        return []


# ── AUTH ──────────────────────────────────────────────────────────────────────

@app.route('/')
def home():
    if 'username' in session:
        purge_expired_stories()
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT pfp,bio,is_verified,points FROM users WHERE username=?", (session['username'],))
        u = c.fetchone()
        conn.close()
        return render_template('index.html', username=session['username'],
                               pfp=u['pfp'] if u else None,
                               bio=u['bio'] if u else '',
                               is_verified=u['is_verified'] if u else 0,
                               points=u['points'] if u else 0)
    return render_template('index.html')


@app.route('/register', methods=['POST'])
def register():
    username = request.form.get('username')
    email = request.form.get('email')
    password = request.form.get('password')
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO users (username,email,password,pfp,bio) VALUES (?,?,?,?,?)",
                  (username, email, generate_password_hash(password), None, ""))
        conn.commit()
        session['username'] = username
        award_points(username, 10, '🌱 New Member')
        log_activity(username, 'registered')
    except sqlite3.IntegrityError:
        return "Username or Email already taken", 400
    finally:
        conn.close()
    return redirect(url_for('home'))


@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username')
    password = request.form.get('password')
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT password,twofa_secret,twofa_enabled FROM users WHERE username=?", (username,))
    u = c.fetchone()
    conn.close()
    if not u or not check_password_hash(u['password'], password):
        return "Login failed", 401
    if u['twofa_enabled'] and u['twofa_secret']:
        try:
            import pyotp
            if not pyotp.TOTP(u['twofa_secret']).verify(request.form.get('totp_code', '')):
                return "Invalid 2FA code", 401
        except:
            pass
    session['username'] = username
    log_activity(username, 'logged_in')
    return redirect(url_for('home'))


@app.route('/forgot-password', methods=['POST'])
def forgot_password():
    email = request.form.get('email')
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT username FROM users WHERE email=?", (email,))
    u = c.fetchone()
    conn.close()
    if u:
        token = serializer.dumps(email, salt='password-reset-salt')
        reset_link = url_for('reset_password', token=token, _external=True)
        msg = EmailMessage()
        msg['Subject'] = 'Reset Your Manshi{Tatty} Password'
        msg['From'] = app.config['MAIL_USERNAME']
        msg['To'] = email
        msg.set_content(f"Click to reset (expires 15 min):\n{reset_link}")
        try:
            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
                smtp.login(app.config['MAIL_USERNAME'], app.config['MAIL_PASSWORD'])
                smtp.send_message(msg)
        except:
            pass
    return "If an account exists, an email was sent.", 200


@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    try:
        email = serializer.loads(token, salt='password-reset-salt', max_age=900)
    except:
        return "Invalid or expired link.", 400
    if request.method == 'POST':
        conn = get_db()
        conn.execute("UPDATE users SET password=? WHERE email=?",
                     (generate_password_hash(request.form['new_password']), email))
        conn.commit()
        conn.close()
        return redirect(url_for('home'))
    return f'<form method="POST" style="text-align:center;margin-top:50px;font-family:sans-serif;"><h2>Reset for {email}</h2><input type="password" name="new_password" placeholder="New Password" required style="padding:10px;"><button type="submit" style="padding:10px 20px;">Save</button></form>'


@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('home'))


# ── 2FA ───────────────────────────────────────────────────────────────────────
# GET /setup-2fa  → returns current enabled status + sets up secret
# POST /setup-2fa with action=enable|disable
@app.route('/setup-2fa', methods=['GET', 'POST'])
def setup_2fa():
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    me = session['username']
    if request.method == 'GET':
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT twofa_enabled FROM users WHERE username=?", (me,))
        row = c.fetchone()
        conn.close()
        return jsonify({'enabled': bool(row and row['twofa_enabled'])})
    # POST
    action = request.form.get('action', '')
    if action == 'enable':
        try:
            import pyotp
            secret = pyotp.random_base32()
            conn = get_db()
            conn.execute("UPDATE users SET twofa_secret=?, twofa_enabled=1 WHERE username=?", (secret, me))
            conn.commit()
            conn.close()
            uri = pyotp.TOTP(secret).provisioning_uri(me, issuer_name="ManshiTatty")
            return jsonify({'secret': secret, 'uri': uri})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    elif action == 'disable':
        conn = get_db()
        conn.execute("UPDATE users SET twofa_enabled=0, twofa_secret=NULL WHERE username=?", (me,))
        conn.commit()
        conn.close()
        return jsonify({'ok': True})
    return jsonify({'error': 'Unknown action'}), 400


# ── PROFILE ───────────────────────────────────────────────────────────────────

@app.route('/update-pfp', methods=['POST'])
def update_pfp():
    file = request.files.get('pfp')
    if file and file.filename:
        filename = f"pfp_{session['username']}_{uuid.uuid4()}{os.path.splitext(file.filename)[1]}"
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        conn = get_db()
        conn.execute("UPDATE users SET pfp=? WHERE username=?", (filename, session['username']))
        conn.execute("INSERT INTO profile_photos (username,filename,display_order) VALUES (?,?,?)",
                     (session['username'], filename, int(time.time())))
        conn.commit()
        conn.close()
    return redirect(url_for('home'))


@app.route('/update-bio', methods=['POST'])
def update_bio():
    if 'username' not in session:
        return "Unauthorized", 401
    conn = get_db()
    conn.execute("UPDATE users SET bio=? WHERE username=?",
                 (request.form.get('bio', ''), session['username']))
    conn.commit()
    conn.close()
    return "Success", 200


# COMBINED privacy + bio update
@app.route('/update-privacy', methods=['POST'])
def update_privacy():
    if 'username' not in session:
        return "Unauthorized", 401
    is_private = 1 if request.form.get('is_private') == '1' else 0
    conn = get_db()
    conn.execute("UPDATE users SET is_private=? WHERE username=?", (is_private, session['username']))
    conn.commit()
    conn.close()
    return "Success", 200


@app.route('/update-blocked-words', methods=['POST'])
def update_blocked_words():
    if 'username' not in session:
        return "Unauthorized", 401
    me = session['username']
    words_raw = request.form.get('words', '')
    new_words = [w.strip().lower() for w in words_raw.split(',') if w.strip()]
    conn = get_db()
    conn.execute("DELETE FROM comment_filters WHERE username=?", (me,))
    for w in new_words:
        conn.execute("INSERT OR IGNORE INTO comment_filters (username,word) VALUES (?,?)", (me, w))
    conn.commit()
    conn.close()
    return "Saved", 200


@app.route('/api/user/<target_username>')
def get_user_info(target_username):
    me = session.get('username')
    if me and is_blocked(me, target_username):
        return jsonify({'error': 'Blocked'}), 403
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT username,pfp,bio,is_private,is_verified,points FROM users WHERE username=?", (target_username,))
    u = c.fetchone()
    if not u:
        conn.close()
        return jsonify({'error': 'Not found'}), 404
    data = dict(u)
    c.execute("SELECT COUNT(*) FROM followers WHERE followed=? AND status='accepted'", (target_username,))
    data['followers_count'] = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM followers WHERE follower=? AND status='accepted'", (target_username,))
    data['following_count'] = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM posts WHERE owner=? AND scheduled_at IS NULL", (target_username,))
    data['posts_count'] = c.fetchone()[0]
    data['badges'] = get_badges(target_username)
    data['is_following'] = False
    data['follow_requested'] = False
    data['is_blocked'] = False
    data['streak'] = 0
    data['profile_views'] = 0
    # pfp carousel
    c.execute("SELECT filename FROM profile_photos WHERE username=? ORDER BY display_order ASC", (target_username,))
    data['pfp_carousel'] = [r['filename'] for r in c.fetchall()]
    if me:
        c.execute("SELECT status FROM followers WHERE follower=? AND followed=?", (me, target_username))
        row = c.fetchone()
        if row:
            data['is_following'] = row['status'] == 'accepted'
            data['follow_requested'] = row['status'] == 'pending'
        c.execute("SELECT 1 FROM blocked WHERE blocker=? AND blocked=?", (me, target_username))
        data['is_blocked'] = c.fetchone() is not None
        if me != target_username:
            conn.execute("UPDATE users SET profile_views=profile_views+1 WHERE username=?", (target_username,))
            conn.commit()
        data['streak'] = get_streak_between(me, target_username)
    c.execute("SELECT profile_views FROM users WHERE username=?", (target_username,))
    pv = c.fetchone()
    data['profile_views'] = pv['profile_views'] if pv else 0
    conn.close()
    return jsonify(data)


# ── FOLLOW / BLOCK ────────────────────────────────────────────────────────────

@app.route('/follow/<target_user>', methods=['POST'])
def toggle_follow(target_user):
    if 'username' not in session:
        return "Unauthorized", 401
    me = session['username']
    if me == target_user:
        return "Cannot follow yourself", 400
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT is_private FROM users WHERE username=?", (target_user,))
    t = c.fetchone()
    is_private = t['is_private'] if t else 0
    c.execute("SELECT status FROM followers WHERE follower=? AND followed=?", (me, target_user))
    existing = c.fetchone()
    if existing:
        conn.execute("DELETE FROM followers WHERE follower=? AND followed=?", (me, target_user))
        conn.commit()
        conn.close()
        # Return 'unfollowed' so JS can detect correctly
        return jsonify({'status': 'unfollowed'})
    status = 'pending' if is_private else 'accepted'
    conn.execute("INSERT INTO followers (follower,followed,status) VALUES (?,?,?)", (me, target_user, status))
    conn.commit()
    if status == 'accepted':
        send_notif(target_user, me, f"@{me} started following you! 👤", conn)
        award_points(me, 2)
        log_activity(me, 'followed', target_user)
        conn.commit()
        conn.close()
        return jsonify({'status': 'followed'})
    else:
        send_notif(target_user, me, f"@{me} requested to follow you 🔒", conn)
        conn.commit()
        conn.close()
        return jsonify({'status': 'requested'})


@app.route('/api/follow-requests')
def get_follow_requests():
    if 'username' not in session:
        return jsonify([])
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT follower FROM followers WHERE followed=? AND status='pending'", (session['username'],))
    reqs = [r['follower'] for r in c.fetchall()]
    conn.close()
    return jsonify(reqs)


@app.route('/api/follow-request/<requester>/<action>', methods=['POST'])
def handle_follow_request(requester, action):
    if 'username' not in session:
        return "Unauthorized", 401
    me = session['username']
    conn = get_db()
    if action == 'accept':
        conn.execute("UPDATE followers SET status='accepted' WHERE follower=? AND followed=?", (requester, me))
        send_notif(requester, me, f"@{me} accepted your follow request! ✅")
    else:
        conn.execute("DELETE FROM followers WHERE follower=? AND followed=?", (requester, me))
    conn.commit()
    conn.close()
    return "Done", 200


# Keep old URL working too (used by some JS handle_follow_request calls)
@app.route('/handle-follow-request', methods=['POST'])
def handle_follow_request_legacy():
    if 'username' not in session:
        return "Unauthorized", 401
    me = session['username']
    requester = request.form.get('requester')
    action = request.form.get('action')
    conn = get_db()
    if action == 'accept':
        conn.execute("UPDATE followers SET status='accepted' WHERE follower=? AND followed=?", (requester, me))
        send_notif(requester, me, f"@{me} accepted your follow request! ✅")
    else:
        conn.execute("DELETE FROM followers WHERE follower=? AND followed=?", (requester, me))
    conn.commit()
    conn.close()
    return "Done", 200


@app.route('/block/<target>', methods=['POST'])
def block_user(target):
    if 'username' not in session:
        return "Unauthorized", 401
    me = session['username']
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO blocked (blocker,blocked) VALUES (?,?)", (me, target))
    conn.execute("DELETE FROM followers WHERE (follower=? AND followed=?) OR (follower=? AND followed=?)",
                 (me, target, target, me))
    conn.commit()
    conn.close()
    log_activity(me, 'blocked', target)
    return "blocked", 200


@app.route('/unblock/<target>', methods=['POST'])
def unblock_user(target):
    if 'username' not in session:
        return "Unauthorized", 401
    conn = get_db()
    conn.execute("DELETE FROM blocked WHERE blocker=? AND blocked=?", (session['username'], target))
    conn.commit()
    conn.close()
    return "unblocked", 200


# ── CLOSE FRIENDS ─────────────────────────────────────────────────────────────

@app.route('/toggle-close-friend/<friend>', methods=['POST'])
def toggle_close_friend(friend):
    if 'username' not in session:
        return "Unauthorized", 401
    me = session['username']
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT 1 FROM close_friends WHERE username=? AND friend=?", (me, friend))
    if c.fetchone():
        conn.execute("DELETE FROM close_friends WHERE username=? AND friend=?", (me, friend))
        conn.commit()
        conn.close()
        return "removed", 200
    conn.execute("INSERT OR IGNORE INTO close_friends (username,friend) VALUES (?,?)", (me, friend))
    conn.commit()
    conn.close()
    return "added", 200


# ── POSTS ─────────────────────────────────────────────────────────────────────

@app.route('/upload', methods=['POST'])
def upload_files():
    if 'username' not in session:
        return "Unauthorized", 401
    me = session['username']
    file = request.files.get('mediaFile')
    if not file:
        return 'Error', 400
    ext = os.path.splitext(file.filename)[1].lower()
    filename = f"{uuid.uuid4()}{ext}"
    file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
    post_id = str(uuid.uuid4())
    is_reel = 1 if request.form.get('is_reel') == '1' else 0
    cfo = 1 if request.form.get('close_friends_only') == '1' else 0
    scheduled_at = request.form.get('scheduled_at') or None
    collab_user = request.form.get('collab_user') or None
    poll_q = request.form.get('poll_question')
    poll_a = request.form.get('poll_option_a')
    poll_b = request.form.get('poll_option_b')
    conn = get_db()
    conn.execute(
        "INSERT INTO posts (id,filename,caption,font,filter,owner,is_reel,is_collab,collab_user,scheduled_at,close_friends_only) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (post_id, filename, request.form.get('caption', ''), request.form.get('font', ''),
         request.form.get('filter', 'none'), me, is_reel, 1 if collab_user else 0,
         collab_user, scheduled_at, cfo))
    for i, cf in enumerate(request.files.getlist('collageFiles')[:8]):
        cext = os.path.splitext(cf.filename)[1].lower()
        cfn = f"{uuid.uuid4()}{cext}"
        cf.save(os.path.join(app.config['UPLOAD_FOLDER'], cfn))
        conn.execute("INSERT INTO collage_items (post_id,filename,item_order) VALUES (?,?,?)", (post_id, cfn, i + 1))
    if poll_q and poll_a and poll_b:
        conn.execute("INSERT INTO polls (post_id,question,option_a,option_b) VALUES (?,?,?,?)",
                     (post_id, poll_q, poll_a, poll_b))
    conn.commit()
    conn.close()
    if not scheduled_at:
        award_points(me, 5)
        log_activity(me, 'posted', post_id)
        _check_post_badges(me)
    return 'Success', 200


# Upload reel (convenience alias — sets is_reel=1)
@app.route('/upload-reel', methods=['POST'])
def upload_reel():
    if 'username' not in session:
        return "Unauthorized", 401
    me = session['username']
    file = request.files.get('reelFile')
    if not file:
        return 'Error', 400
    ext = os.path.splitext(file.filename)[1].lower()
    filename = f"{uuid.uuid4()}{ext}"
    file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
    post_id = str(uuid.uuid4())
    conn = get_db()
    conn.execute(
        "INSERT INTO posts (id,filename,caption,font,filter,owner,is_reel) VALUES (?,?,?,?,?,?,1)",
        (post_id, filename, request.form.get('caption', ''), '', 'none', me))
    conn.commit()
    conn.close()
    award_points(me, 5)
    log_activity(me, 'posted_reel', post_id)
    return jsonify({'id': post_id})


# Upload collage (separate endpoint)
@app.route('/upload-collage', methods=['POST'])
def upload_collage():
    if 'username' not in session:
        return "Unauthorized", 401
    me = session['username']
    files = request.files.getlist('collageFiles')
    if len(files) < 2:
        return 'Need at least 2 photos', 400
    post_id = str(uuid.uuid4())
    # Use first file as the main filename placeholder
    first = files[0]
    ext = os.path.splitext(first.filename)[1].lower()
    main_fn = f"{uuid.uuid4()}{ext}"
    first.save(os.path.join(app.config['UPLOAD_FOLDER'], main_fn))
    conn = get_db()
    conn.execute(
        "INSERT INTO posts (id,filename,caption,font,filter,owner) VALUES (?,?,?,?,?,?)",
        (post_id, main_fn, request.form.get('caption', ''), '', 'none', me))
    conn.execute("INSERT INTO collage_items (post_id,filename,item_order) VALUES (?,?,?)", (post_id, main_fn, 0))
    for i, cf in enumerate(files[1:8], start=1):
        cext = os.path.splitext(cf.filename)[1].lower()
        cfn = f"{uuid.uuid4()}{cext}"
        cf.save(os.path.join(app.config['UPLOAD_FOLDER'], cfn))
        conn.execute("INSERT INTO collage_items (post_id,filename,item_order) VALUES (?,?,?)", (post_id, cfn, i))
    conn.commit()
    conn.close()
    award_points(me, 5)
    log_activity(me, 'posted_collage', post_id)
    return jsonify({'id': post_id})


def _check_post_badges(u):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM posts WHERE owner=? AND scheduled_at IS NULL", (u,))
    ct = c.fetchone()[0]
    conn.close()
    if ct == 1:
        award_points(u, 0, '📸 First Post')
    elif ct == 10:
        award_points(u, 20, '🔥 10 Posts')
    elif ct == 50:
        award_points(u, 50, '⭐ 50 Posts')
    elif ct == 100:
        award_points(u, 100, '🏆 100 Posts')


@app.route('/delete/<post_id>', methods=['POST'])
def delete_post(post_id):
    if 'username' not in session:
        return "Unauthorized", 401
    conn = get_db()
    conn.execute("DELETE FROM posts WHERE id=? AND owner=?", (post_id, session['username']))
    for t in ['likes', 'comments', 'bookmarks', 'collage_items']:
        conn.execute(f"DELETE FROM {t} WHERE post_id=?", (post_id,))
    conn.commit()
    conn.close()
    return "Deleted", 200


@app.route('/like/<post_id>', methods=['POST'])
def like_post(post_id):
    if 'username' not in session:
        return "Unauthorized", 401
    me = session['username']
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT owner FROM posts WHERE id=?", (post_id,))
    post = c.fetchone()
    owner = post['owner'] if post else None
    c.execute("SELECT 1 FROM likes WHERE post_id=? AND username=?", (post_id, me))
    if c.fetchone():
        conn.execute("DELETE FROM likes WHERE post_id=? AND username=?", (post_id, me))
    else:
        conn.execute("INSERT INTO likes (post_id,username) VALUES (?,?)", (post_id, me))
        if owner and owner != me:
            send_notif(owner, me, f"@{me} liked your post! ❤️", conn)
            award_points(owner, 1)
        log_activity(me, 'liked', post_id)
    conn.commit()
    conn.close()
    return "Success", 200


# Like a reel (same underlying post like, separate URL for clarity)
@app.route('/like-reel/<post_id>', methods=['POST'])
def like_reel(post_id):
    return like_post(post_id)


@app.route('/comment/<post_id>', methods=['POST'])
def add_comment(post_id):
    if 'username' not in session:
        return "Unauthorized", 401
    text = request.form.get('text')
    if not text:
        return "Empty", 400
    me = session['username']
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT owner FROM posts WHERE id=?", (post_id,))
    post = c.fetchone()
    owner = post['owner'] if post else None
    hidden = 1 if owner and check_filter(owner, text) else 0
    conn.execute("INSERT INTO comments (post_id,author,text,is_hidden) VALUES (?,?,?,?)", (post_id, me, text, hidden))
    if owner and owner != me and not hidden:
        send_notif(owner, me, f"@{me} commented: '{text[:20]}...'", conn)
        award_points(owner, 1)
    conn.commit()
    conn.close()
    log_activity(me, 'commented', post_id)
    return "Success", 200


# Repost / share a post — returns {status: 'reposted'/'unreposted', count}
@app.route('/repost/<post_id>', methods=['POST'])
def repost(post_id):
    if 'username' not in session:
        return "Unauthorized", 401
    me = session['username']
    conn = get_db()
    c = conn.cursor()
    # Check if user already has a share of this post
    c.execute("SELECT id FROM posts WHERE owner=? AND caption LIKE ?",
              (me, f'%[repost:{post_id}]%'))
    existing = c.fetchone()
    if existing:
        conn.execute("DELETE FROM posts WHERE id=?", (existing['id'],))
        conn.execute("UPDATE posts SET shares_count=MAX(0,shares_count-1) WHERE id=?", (post_id,))
        conn.commit()
        c.execute("SELECT shares_count FROM posts WHERE id=?", (post_id,))
        row = c.fetchone()
        conn.close()
        return jsonify({'status': 'unreposted', 'count': row['shares_count'] if row else 0})
    c.execute("SELECT * FROM posts WHERE id=?", (post_id,))
    orig = c.fetchone()
    if not orig:
        conn.close()
        return "Not found", 404
    new_id = str(uuid.uuid4())
    new_caption = f"{orig['caption']} [repost:{post_id}]"
    conn.execute(
        "INSERT INTO posts (id,filename,caption,font,filter,owner,is_reel,close_friends_only) VALUES (?,?,?,?,?,?,?,?)",
        (new_id, orig['filename'], new_caption, orig['font'], orig['filter'], me, orig['is_reel'], 0))
    conn.execute("UPDATE posts SET shares_count=shares_count+1 WHERE id=?", (post_id,))
    if orig['owner'] != me:
        send_notif(orig['owner'], me, f"@{me} reposted your post! 🔁", conn)
    conn.commit()
    c.execute("SELECT shares_count FROM posts WHERE id=?", (post_id,))
    row = c.fetchone()
    conn.close()
    award_points(me, 2)
    log_activity(me, 'reposted', post_id)
    return jsonify({'status': 'reposted', 'count': row['shares_count'] if row else 1})


# ── BOOKMARKS ─────────────────────────────────────────────────────────────────

@app.route('/bookmark/<post_id>', methods=['POST'])
def toggle_bookmark(post_id):
    if 'username' not in session:
        return "Unauthorized", 401
    me = session['username']
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT 1 FROM bookmarks WHERE username=? AND post_id=?", (me, post_id))
    if c.fetchone():
        conn.execute("DELETE FROM bookmarks WHERE username=? AND post_id=?", (me, post_id))
        conn.commit()
        conn.close()
        return "unsaved", 200
    conn.execute("INSERT INTO bookmarks (username,post_id) VALUES (?,?)", (me, post_id))
    conn.commit()
    conn.close()
    log_activity(me, 'bookmarked', post_id)
    return "saved", 200


@app.route('/api/bookmarks')
def get_bookmarks():
    if 'username' not in session:
        return jsonify([])
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT post_id FROM bookmarks WHERE username=?", (session['username'],))
    ids = [r['post_id'] for r in c.fetchall()]
    conn.close()
    if not ids:
        return jsonify([])
    return jsonify(fetch_posts_with_details(
        f"SELECT * FROM posts WHERE id IN ({','.join('?' * len(ids))})", tuple(ids)))


# ── POLLS ─────────────────────────────────────────────────────────────────────

@app.route('/vote/<int:poll_id>', methods=['POST'])
def vote_poll(poll_id):
    if 'username' not in session:
        return "Unauthorized", 401
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO poll_votes (poll_id,username,choice) VALUES (?,?,?)",
                 (poll_id, session['username'], request.form.get('choice')))
    conn.commit()
    conn.close()
    return "Voted", 200


# ── STORIES ───────────────────────────────────────────────────────────────────

@app.route('/upload-story', methods=['POST'])
def upload_story():
    if 'username' not in session:
        return "Unauthorized", 401
    file = request.files.get('storyFile')
    if not file:
        return "No file", 400
    ext = os.path.splitext(file.filename)[1].lower()
    filename = f"story_{uuid.uuid4()}{ext}"
    file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
    msg_type = 'video' if ext in ['.mp4', '.webm', '.mov'] else 'image'
    conn = get_db()
    conn.execute("INSERT INTO stories (id,owner,filename,msg_type) VALUES (?,?,?,?)",
                 (str(uuid.uuid4()), session['username'], filename, msg_type))
    conn.commit()
    conn.close()
    award_points(session['username'], 3)
    log_activity(session['username'], 'posted_story')
    return "Success", 200


@app.route('/api/stories')
def get_stories():
    purge_expired_stories()
    if 'username' not in session:
        return jsonify([])
    me = session['username']
    conn = get_db()
    c = conn.cursor()
    c.execute("""SELECT s.*,u.pfp FROM stories s JOIN users u ON s.owner=u.username
                 WHERE s.owner=? OR s.owner IN (SELECT followed FROM followers WHERE follower=? AND status='accepted')
                 ORDER BY s.created_at DESC""", (me, me))
    stories = [dict(r) for r in c.fetchall()]
    grouped = {}
    for s in stories:
        if s['owner'] not in grouped:
            grouped[s['owner']] = {'owner': s['owner'], 'pfp': s['pfp'], 'stories': [], 'has_unseen': False}
        c.execute("SELECT 1 FROM story_views WHERE story_id=? AND username=?", (s['id'], me))
        seen = c.fetchone() is not None
        if not seen:
            grouped[s['owner']]['has_unseen'] = True
        grouped[s['owner']]['stories'].append(
            {'id': s['id'], 'filename': s['filename'], 'msg_type': s['msg_type'], 'seen': seen})
    conn.close()
    return jsonify(list(grouped.values()))


@app.route('/view-story/<story_id>', methods=['POST'])
def view_story(story_id):
    if 'username' not in session:
        return "Unauthorized", 401
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO story_views (story_id,username) VALUES (?,?)",
                 (story_id, session['username']))
    conn.commit()
    conn.close()
    return "OK", 200


# ── HIGHLIGHTS ────────────────────────────────────────────────────────────────

@app.route('/api/highlights/<username>')
def get_highlights(username):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM highlights WHERE owner=?", (username,))
    highlights = []
    for h in c.fetchall():
        hd = dict(h)
        c.execute("SELECT p.filename FROM highlight_posts hp JOIN posts p ON hp.post_id=p.id WHERE hp.highlight_id=?",
                  (h['id'],))
        hd['posts'] = [r['filename'] for r in c.fetchall()]
        highlights.append(hd)
    conn.close()
    return jsonify(highlights)


@app.route('/create-highlight', methods=['POST'])
def create_highlight():
    if 'username' not in session:
        return "Unauthorized", 401
    hid = str(uuid.uuid4())
    name = request.form.get('title') or request.form.get('name') or 'Highlight'
    conn = get_db()
    conn.execute("INSERT INTO highlights (id,owner,name) VALUES (?,?,?)", (hid, session['username'], name))
    post_ids = request.form.getlist('post_ids')
    for pid in post_ids:
        conn.execute("INSERT OR IGNORE INTO highlight_posts (highlight_id,post_id) VALUES (?,?)", (hid, pid))
    conn.commit()
    conn.close()
    return jsonify({'id': hid})


@app.route('/api/highlights/<hid>/add/<post_id>', methods=['POST'])
def add_to_highlight(hid, post_id):
    if 'username' not in session:
        return "Unauthorized", 401
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO highlight_posts (highlight_id,post_id) VALUES (?,?)", (hid, post_id))
    conn.commit()
    conn.close()
    return "Done", 200


@app.route('/api/highlights/<hid>', methods=['DELETE'])
def delete_highlight(hid):
    if 'username' not in session:
        return "Unauthorized", 401
    conn = get_db()
    conn.execute("DELETE FROM highlights WHERE id=? AND owner=?", (hid, session['username']))
    conn.execute("DELETE FROM highlight_posts WHERE highlight_id=?", (hid,))
    conn.commit()
    conn.close()
    return "Deleted", 200


# ── EXPLORE ───────────────────────────────────────────────────────────────────

@app.route('/api/explore')
def explore():
    """Combined explore endpoint: trending hashtags + suggested users."""
    conn = get_db()
    c = conn.cursor()
    # Trending hashtags
    c.execute("SELECT caption FROM posts WHERE scheduled_at IS NULL ORDER BY rowid DESC LIMIT 200")
    captions = [r['caption'] for r in c.fetchall()]
    tags = {}
    for cap in captions:
        for word in cap.split():
            if word.startswith('#'):
                tags[word] = tags.get(word, 0) + 1
    trending = sorted(tags.items(), key=lambda x: x[1], reverse=True)[:10]

    # Suggested users
    suggested = []
    me = session.get('username', '')
    if me:
        c.execute("""SELECT DISTINCT f2.followed as username, u.pfp, u.is_verified,
                     (SELECT COUNT(*) FROM followers WHERE followed=f2.followed AND status='accepted') as fc
                     FROM followers f1
                     JOIN followers f2 ON f1.followed=f2.follower
                     JOIN users u ON f2.followed=u.username
                     WHERE f1.follower=? AND f2.followed!=?
                     AND f2.followed NOT IN (SELECT followed FROM followers WHERE follower=?)
                     AND f2.followed NOT IN (SELECT blocked FROM blocked WHERE blocker=?) LIMIT 5""",
                  (me, me, me, me))
        suggested = [dict(r) for r in c.fetchall()]
    conn.close()
    return jsonify({'trending': trending, 'suggested': suggested})


# ── LEADERBOARD ───────────────────────────────────────────────────────────────

@app.route('/api/leaderboard')
def leaderboard():
    conn = get_db()
    c = conn.cursor()
    # Top posts by likes
    c.execute("""SELECT p.id, p.filename, p.owner, u.is_verified,
                 COUNT(l.username) as like_count FROM posts p
                 LEFT JOIN likes l ON l.post_id=p.id
                 LEFT JOIN users u ON u.username=p.owner
                 WHERE p.scheduled_at IS NULL
                 GROUP BY p.id ORDER BY like_count DESC LIMIT 6""")
    top_posts = [dict(r) for r in c.fetchall()]
    # Top users by points
    c.execute("""SELECT u.username, u.points, u.pfp, u.is_verified,
                 COUNT(DISTINCT p.id) as post_count FROM users u
                 LEFT JOIN posts p ON p.owner=u.username
                 GROUP BY u.username ORDER BY u.points DESC LIMIT 10""")
    top_users = [dict(r) for r in c.fetchall()]
    conn.close()
    return jsonify({'top_posts': top_posts, 'top_users': top_users})


# ── CHAT ──────────────────────────────────────────────────────────────────────

@app.route('/api/all-users')
def get_all_users():
    if 'username' not in session:
        return jsonify([])
    me = session['username']
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT username, pfp, is_verified FROM users WHERE username!=?", (me,))
    users = []
    for row in c.fetchall():
        u = dict(row)
        u['streak'] = get_streak_between(me, u['username'])
        users.append(u)
    conn.close()
    return jsonify(users)


@app.route('/api/messages/<other_user>')
def get_messages(other_user):
    if 'username' not in session:
        return jsonify({'messages': [], 'theme': 'default'})
    me = session['username']
    conn = get_db()
    c = conn.cursor()
    # Mark delivered messages as seen
    c.execute("SELECT id,seen_by FROM messages WHERE sender=? AND receiver=? AND disappear=0", (other_user, me))
    for row in c.fetchall():
        seen = json.loads(row['seen_by'] or '[]')
        if me not in seen:
            seen.append(me)
            conn.execute("UPDATE messages SET seen_by=? WHERE id=?", (json.dumps(seen), row['id']))
    conn.commit()
    # Fetch messages
    c.execute("""SELECT id,sender,receiver,text,msg_type,media_filename,disappear,seen_by,reply_to,pinned,created_at
                 FROM messages WHERE ((sender=? AND receiver=?) OR (sender=? AND receiver=?))
                 AND group_id IS NULL ORDER BY id ASC""", (me, other_user, other_user, me))
    msgs = []
    for row in c.fetchall():
        m = dict(row)
        if m['disappear'] == 1:
            seen = json.loads(m['seen_by'] or '[]')
            if other_user in seen or me in seen:
                conn.execute("DELETE FROM messages WHERE id=?", (m['id'],))
                conn.commit()
                continue
        c.execute("SELECT emoji,username FROM message_reactions WHERE message_id=?", (m['id'],))
        m['reactions'] = [dict(r) for r in c.fetchall()]
        if m['reply_to']:
            c.execute("SELECT text,sender FROM messages WHERE id=?", (m['reply_to'],))
            ref = c.fetchone()
            m['reply_to'] = {'text': ref['text'] if ref else '', 'sender': ref['sender'] if ref else ''}
        else:
            m['reply_to'] = None
        # Seen status for sent messages
        seen_list = json.loads(m['seen_by'] or '[]')
        m['seen'] = other_user in seen_list
        msgs.append(m)
    # Get chat theme
    key = (min(me, other_user), max(me, other_user))
    c.execute("SELECT theme FROM chat_themes WHERE user1=? AND user2=?", key)
    theme_row = c.fetchone()
    theme = theme_row['theme'] if theme_row else 'default'
    conn.close()
    return jsonify({'messages': msgs, 'theme': theme})


@app.route('/api/pinned-messages/<other_user>')
def get_pinned_messages(other_user):
    if 'username' not in session:
        return jsonify([])
    me = session['username']
    conn = get_db()
    c = conn.cursor()
    c.execute("""SELECT id,sender,text,msg_type,media_filename FROM messages
                 WHERE ((sender=? AND receiver=?) OR (sender=? AND receiver=?)) AND pinned=1""",
              (me, other_user, other_user, me))
    msgs = [dict(r) for r in c.fetchall()]
    conn.close()
    return jsonify(msgs)


@app.route('/pin-message/<int:msg_id>', methods=['POST'])
def pin_message(msg_id):
    if 'username' not in session:
        return "Unauthorized", 401
    pinned = request.form.get('pinned', '1')
    conn = get_db()
    conn.execute("UPDATE messages SET pinned=? WHERE id=?", (pinned, msg_id))
    conn.commit()
    conn.close()
    return "Done", 200


@app.route('/api/chat-upload', methods=['POST'])
def chat_upload():
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'No file'}), 400
    ext = os.path.splitext(file.filename)[1].lower()
    filename = f"chat_{uuid.uuid4()}{ext}"
    file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
    if ext in ['.mp4', '.webm', '.mov', '.avi']:
        msg_type = 'video'
    elif ext in ['.mp3', '.ogg', '.wav', '.m4a']:
        msg_type = 'voice'
    else:
        msg_type = 'image'
    return jsonify({'filename': filename, 'msg_type': msg_type})


@app.route('/api/message-react/<int:message_id>', methods=['POST'])
def react_to_message(message_id):
    if 'username' not in session:
        return "Unauthorized", 401
    me = session['username']
    emoji = request.form.get('emoji')
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id FROM message_reactions WHERE message_id=? AND username=? AND emoji=?",
              (message_id, me, emoji))
    if c.fetchone():
        conn.execute("DELETE FROM message_reactions WHERE message_id=? AND username=? AND emoji=?",
                     (message_id, me, emoji))
    else:
        conn.execute("DELETE FROM message_reactions WHERE message_id=? AND username=?", (message_id, me))
        conn.execute("INSERT INTO message_reactions (message_id,username,emoji) VALUES (?,?,?)",
                     (message_id, me, emoji))
    conn.commit()
    conn.close()
    return "Success", 200


@app.route('/set-chat-theme', methods=['POST'])
def set_chat_theme():
    if 'username' not in session:
        return "Unauthorized", 401
    me = session['username']
    other = request.form.get('other_user')
    theme = request.form.get('theme', 'default')
    if not other:
        return "Missing other_user", 400
    key = (min(me, other), max(me, other))
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO chat_themes (user1,user2,theme) VALUES (?,?,?)", (*key, theme))
    conn.commit()
    conn.close()
    return "Saved", 200


# ── GROUPS ────────────────────────────────────────────────────────────────────

@app.route('/api/my-groups')
def get_my_groups():
    if 'username' not in session:
        return jsonify([])
    conn = get_db()
    c = conn.cursor()
    c.execute("""SELECT g.* FROM group_chats g JOIN group_members gm ON g.id=gm.group_id
                 WHERE gm.username=?""", (session['username'],))
    groups = [dict(r) for r in c.fetchall()]
    conn.close()
    return jsonify(groups)


@app.route('/create-group', methods=['POST'])
def create_group():
    if 'username' not in session:
        return "Unauthorized", 401
    me = session['username']
    name = request.form.get('name', 'New Group')
    members = request.form.getlist('members')
    gid = str(uuid.uuid4())
    photo_file = request.files.get('pfp')
    photo_filename = None
    if photo_file and photo_file.filename:
        ext = os.path.splitext(photo_file.filename)[1]
        photo_filename = f"grp_{uuid.uuid4()}{ext}"
        photo_file.save(os.path.join(app.config['UPLOAD_FOLDER'], photo_filename))
    conn = get_db()
    conn.execute("INSERT INTO group_chats (id,name,photo,created_by) VALUES (?,?,?,?)",
                 (gid, name, photo_filename, me))
    conn.execute("INSERT INTO group_members (group_id,username,role) VALUES (?,?,'admin')", (gid, me))
    for m in members:
        conn.execute("INSERT OR IGNORE INTO group_members (group_id,username) VALUES (?,?)", (gid, m))
        send_notif(m, me, f"@{me} added you to group '{name}' 👥", conn)
    conn.commit()
    conn.close()
    return jsonify({'group_id': gid})


@app.route('/api/group-messages/<gid>')
def get_group_messages(gid):
    if 'username' not in session:
        return jsonify([])
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT 1 FROM group_members WHERE group_id=? AND username=?", (gid, session['username']))
    if not c.fetchone():
        conn.close()
        return jsonify([])
    c.execute("""SELECT id,sender,text,msg_type,media_filename,reply_to,pinned,created_at
                 FROM messages WHERE group_id=? ORDER BY id ASC""", (gid,))
    msgs = [dict(r) for r in c.fetchall()]
    for m in msgs:
        c.execute("SELECT emoji,username FROM message_reactions WHERE message_id=?", (m['id'],))
        m['reactions'] = [dict(r) for r in c.fetchall()]
    conn.close()
    return jsonify(msgs)


# ── NOTIFICATIONS ─────────────────────────────────────────────────────────────

@app.route('/api/notifications')
def get_notifications():
    if 'username' not in session:
        return jsonify([])
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT message,created_at FROM notifications WHERE username=? ORDER BY id DESC LIMIT 20",
              (session['username'],))
    notifs = [dict(r) for r in c.fetchall()]
    conn.close()
    return jsonify(notifs)


# ── ACTIVITY LOG ──────────────────────────────────────────────────────────────

@app.route('/api/activity-log')
def get_activity_log():
    if 'username' not in session:
        return jsonify([])
    conn = get_db()
    c = conn.cursor()
    # Return created_at as ISO string (JS will parse it directly)
    c.execute("SELECT action,target,created_at FROM activity_log WHERE username=? ORDER BY id DESC LIMIT 50",
              (session['username'],))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return jsonify(rows)


# ── POSTS FEED HELPERS ────────────────────────────────────────────────────────

def fetch_posts_with_details(query, args=()):
    conn = get_db()
    c = conn.cursor()
    c.execute(query, args)
    posts = [dict(row) for row in c.fetchall()]
    me = session.get('username', '')
    for p in posts:
        if p.get('scheduled_at'):
            p['likes'] = []
            p['comments'] = []
            p['is_bookmarked'] = False
            p['poll'] = None
            p['collage_list'] = []
            continue
        c.execute("SELECT username FROM likes WHERE post_id=?", (p['id'],))
        p['likes'] = [r['username'] for r in c.fetchall()]
        c.execute("SELECT id,author,text FROM comments WHERE post_id=? AND is_hidden=0", (p['id'],))
        p['comments'] = [dict(r) for r in c.fetchall()]
        c.execute("SELECT 1 FROM bookmarks WHERE username=? AND post_id=?", (me, p['id']))
        p['is_bookmarked'] = c.fetchone() is not None
        # Poll with vote counts
        c.execute("SELECT * FROM polls WHERE post_id=?", (p['id'],))
        poll = c.fetchone()
        if poll:
            pd = dict(poll)
            c.execute("SELECT COUNT(*) FROM poll_votes WHERE poll_id=? AND choice='a'", (pd['id'],))
            pd['votes_a'] = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM poll_votes WHERE poll_id=? AND choice='b'", (pd['id'],))
            pd['votes_b'] = c.fetchone()[0]
            pd['my_vote'] = None
            if me:
                c.execute("SELECT choice FROM poll_votes WHERE poll_id=? AND username=?", (pd['id'], me))
                mv = c.fetchone()
                if mv:
                    pd['my_vote'] = mv['choice']
            p['poll'] = pd
        else:
            p['poll'] = None
        # Collage — use key 'collage_list' to match JS makeCard()
        c.execute("SELECT filename FROM collage_items WHERE post_id=? ORDER BY item_order ASC", (p['id'],))
        p['collage_list'] = [r['filename'] for r in c.fetchall()]
        # Owner verified flag
        c.execute("SELECT is_verified FROM users WHERE username=?", (p['owner'],))
        ov = c.fetchone()
        p['owner_verified'] = ov['is_verified'] if ov else 0
        # Repost count alias
        p['repost_count'] = p.get('shares_count', 0)
        # Bookmarked alias
        p['bookmarked'] = p['is_bookmarked']
    conn.close()
    return posts


@app.route('/api/user-posts/<target_username>')
def get_user_posts(target_username):
    page = request.args.get('page', 1, type=int)
    offset = (page - 1) * 10
    return jsonify(fetch_posts_with_details(
        "SELECT * FROM posts WHERE owner=? AND scheduled_at IS NULL ORDER BY post_order ASC,rowid DESC LIMIT 10 OFFSET ?",
        (target_username, offset)))


@app.route('/get-public-media')
def get_public_media():
    page = request.args.get('page', 1, type=int)
    offset = (page - 1) * 10
    tag = request.args.get('tag', '')
    if tag:
        return jsonify(fetch_posts_with_details(
            "SELECT * FROM posts WHERE caption LIKE ? AND scheduled_at IS NULL ORDER BY rowid DESC LIMIT 10 OFFSET ?",
            (f'%{tag}%', offset)))
    return jsonify(fetch_posts_with_details(
        "SELECT * FROM posts WHERE scheduled_at IS NULL AND is_reel=0 ORDER BY rowid DESC LIMIT 10 OFFSET ?",
        (offset,)))


@app.route('/get-profile-media')
def get_profile_media():
    if 'username' not in session:
        return jsonify([])
    page = request.args.get('page', 1, type=int)
    offset = (page - 1) * 10
    if request.args.get('include_scheduled') == '1':
        return jsonify(fetch_posts_with_details(
            "SELECT * FROM posts WHERE owner=? ORDER BY post_order ASC,rowid DESC LIMIT 10 OFFSET ?",
            (session['username'], offset)))
    return jsonify(fetch_posts_with_details(
        "SELECT * FROM posts WHERE owner=? AND scheduled_at IS NULL ORDER BY post_order ASC,rowid DESC LIMIT 10 OFFSET ?",
        (session['username'], offset)))


@app.route('/get-following-media')
def get_following_media():
    if 'username' not in session:
        return jsonify([])
    page = request.args.get('page', 1, type=int)
    offset = (page - 1) * 10
    return jsonify(fetch_posts_with_details(
        """SELECT posts.* FROM posts JOIN followers ON posts.owner=followers.followed
           WHERE followers.follower=? AND followers.status='accepted'
           AND posts.scheduled_at IS NULL AND posts.close_friends_only=0
           ORDER BY posts.rowid DESC LIMIT 10 OFFSET ?""",
        (session['username'], offset)))


@app.route('/get-reels')
def get_reels():
    page = request.args.get('page', 1, type=int)
    offset = (page - 1) * 10
    me = session.get('username', '')
    conn = get_db()
    c = conn.cursor()
    c.execute("""SELECT p.*, u.is_verified, COUNT(l.username) as like_count
                 FROM posts p
                 LEFT JOIN likes l ON l.post_id=p.id
                 LEFT JOIN users u ON u.username=p.owner
                 WHERE p.is_reel=1 AND p.scheduled_at IS NULL
                 GROUP BY p.id ORDER BY p.rowid DESC LIMIT 10 OFFSET ?""", (offset,))
    reels = [dict(r) for r in c.fetchall()]
    for r in reels:
        if me:
            c.execute("SELECT 1 FROM likes WHERE post_id=? AND username=?", (r['id'], me))
            r['liked'] = c.fetchone() is not None
        else:
            r['liked'] = False
    conn.close()
    return jsonify(reels)


# Legacy alias used by JS loadReels()
@app.route('/api/reels')
def api_reels():
    return get_reels()


@app.route('/uploads/<filename>')
def serve_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


# ── VERIFY (admin only) ───────────────────────────────────────────────────────

@app.route('/api/verify/<target>', methods=['POST'])
def verify_user(target):
    if 'username' not in session:
        return "Unauthorized", 401
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT username FROM users ORDER BY rowid ASC LIMIT 1")
    admin = c.fetchone()
    if not admin or admin['username'] != session['username']:
        conn.close()
        return "Not admin", 403
    conn.execute("UPDATE users SET is_verified=1 WHERE username=?", (target,))
    conn.commit()
    conn.close()
    send_notif(target, session['username'], "You've been verified! ✅")
    return "Verified", 200


# ── SOCKETS ───────────────────────────────────────────────────────────────────

@socketio.on('user_connected')
def handle_user_connect(data):
    username = data.get('username')
    if username:
        join_room(f"notify_{username}")


@socketio.on('join_chat')
def on_join_chat(data):
    u1 = session.get('username')
    u2 = data.get('other_user')
    if u1 and u2:
        join_room(f"{min(u1, u2)}_{max(u1, u2)}")


@socketio.on('join_group')
def on_join_group(data):
    gid = data.get('group_id')
    if gid:
        join_room(f"group_{gid}")


@socketio.on('send_message')
def on_send_message(data):
    sender = session.get('username')
    if not sender:
        return
    receiver = data.get('receiver')
    group_id = data.get('group_id')
    text = data.get('text', '')
    msg_type = data.get('msg_type', 'text')
    media_filename = data.get('media_filename')
    disappear = 1 if data.get('disappear') else 0
    reply_to = data.get('reply_to_id')
    conn = get_db()
    conn.execute(
        "INSERT INTO messages (sender,receiver,group_id,text,msg_type,media_filename,disappear,reply_to) VALUES (?,?,?,?,?,?,?,?)",
        (sender, receiver, group_id, text, msg_type, media_filename, disappear, reply_to))
    new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    if receiver:
        update_streak(sender, receiver)
    payload = {
        'id': new_id, 'sender': sender, 'text': text, 'msg_type': msg_type,
        'media_filename': media_filename, 'disappear': disappear,
        'reply_to': None, 'reactions': [], 'seen_by': '[]', 'seen': False,
        'created_at': datetime.utcnow().strftime('%H:%M')
    }
    if group_id:
        emit('receive_message', payload, to=f"group_{group_id}")
    elif receiver:
        emit('receive_message', payload, to=f"{min(sender, receiver)}_{max(sender, receiver)}")


@socketio.on('mark_seen')
def on_mark_seen(data):
    msg_id = data.get('msg_id')
    reader = session.get('username')
    if not msg_id or not reader:
        return
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT seen_by, sender FROM messages WHERE id=?", (msg_id,))
    row = c.fetchone()
    if row:
        seen = json.loads(row['seen_by'] or '[]')
        if reader not in seen:
            seen.append(reader)
            conn.execute("UPDATE messages SET seen_by=? WHERE id=?", (json.dumps(seen), msg_id))
            conn.commit()
        sender = row['sender']
        if sender:
            emit('seen_receipt', {'msg_id': msg_id, 'by': reader,
                                  'at': datetime.utcnow().strftime('%H:%M')}, to=f"notify_{sender}")
    conn.close()


@socketio.on('typing')
def on_typing(data):
    s = session.get('username')
    r = data.get('receiver')
    if s and r:
        emit('user_typing', {'sender': s}, to=f"{min(s, r)}_{max(s, r)}")


@socketio.on('stop_typing')
def on_stop_typing(data):
    s = session.get('username')
    r = data.get('receiver')
    if s and r:
        emit('user_stop_typing', {'sender': s}, to=f"{min(s, r)}_{max(s, r)}")


@socketio.on('call_user')
def on_call_user(data):
    caller = session.get('username')
    target = data.get('target')
    call_type = data.get('call_type', 'audio')
    if caller and target:
        emit('incoming_call', {'caller': caller, 'call_type': call_type}, to=f"notify_{target}")


@socketio.on('call_response')
def on_call_response(data):
    responder = session.get('username')
    caller = data.get('caller')
    if caller:
        emit('call_answered', {'responder': responder, 'accepted': data.get('accepted', False)},
             to=f"notify_{caller}")


@socketio.on('call_ended')
def on_call_ended(data):
    ender = session.get('username')
    other = data.get('other_user')
    if other:
        emit('call_terminated', {'by': ender}, to=f"notify_{other}")


if __name__ == '__main__':
    socketio.run(app, debug=True, port=5000)