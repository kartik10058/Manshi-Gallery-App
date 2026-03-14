import os
from dotenv import load_dotenv

load_dotenv()

import smtplib
from email.message import EmailMessage
from itsdangerous import URLSafeTimedSerializer
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_from_directory
import os, uuid, sqlite3, json
from werkzeug.security import generate_password_hash, check_password_hash
from flask_socketio import SocketIO, emit, join_room

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')
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
    c.execute('''CREATE TABLE IF NOT EXISTS posts (id TEXT PRIMARY KEY, filename TEXT, caption TEXT, font TEXT, filter TEXT, owner TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS likes (post_id TEXT, username TEXT, PRIMARY KEY(post_id, username))''')
    c.execute('''CREATE TABLE IF NOT EXISTS comments (id INTEGER PRIMARY KEY AUTOINCREMENT, post_id TEXT, author TEXT, text TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, email TEXT, password TEXT, pfp TEXT, bio TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, sender TEXT, receiver TEXT, text TEXT, msg_type TEXT DEFAULT 'text', media_filename TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS followers (follower TEXT, followed TEXT, PRIMARY KEY(follower, followed))''')
    c.execute('''CREATE TABLE IF NOT EXISTS notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, message TEXT, is_read INTEGER DEFAULT 0)''')
    # NEW: Message reactions table
    c.execute('''CREATE TABLE IF NOT EXISTS message_reactions (id INTEGER PRIMARY KEY AUTOINCREMENT, message_id INTEGER, username TEXT, emoji TEXT)''')
    conn.commit()
    conn.close()

init_db()

# --- AUTH & PROFILES ---
@app.route('/')
def home():
    if 'username' in session:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT pfp, bio FROM users WHERE username=?", (session['username'],))
        user = c.fetchone()
        conn.close()
        return render_template('index.html', username=session['username'], pfp=user['pfp'] if user else None, bio=user['bio'] if user else '')
    return render_template('index.html')

@app.route('/register', methods=['POST'])
def register():
    username = request.form.get('username')
    email = request.form.get('email')
    password = request.form.get('password')
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO users (username, email, password, pfp, bio) VALUES (?, ?, ?, ?, ?)",
                  (username, email, generate_password_hash(password), None, ""))
        conn.commit()
        session['username'] = username
    except sqlite3.IntegrityError:
        return "Username or Email already taken", 400
    finally:
        conn.close()
    return redirect(url_for('home'))

@app.route('/login', methods=['POST'])
def login():
    username, password = request.form.get('username'), request.form.get('password')
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT password FROM users WHERE username=?", (username,))
    user = c.fetchone()
    conn.close()
    if user and check_password_hash(user['password'], password):
        session['username'] = username
        return redirect(url_for('home'))
    return "Login failed", 401

@app.route('/forgot-password', methods=['POST'])
def forgot_password():
    email = request.form.get('email')
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT username FROM users WHERE email=?", (email,))
    user = c.fetchone()
    conn.close()
    if user:
        token = serializer.dumps(email, salt='password-reset-salt')
        reset_link = url_for('reset_password', token=token, _external=True)
        msg = EmailMessage()
        msg['Subject'] = 'Reset Your Manshi{Tatty} Password'
        msg['From'] = app.config['MAIL_USERNAME']
        msg['To'] = email
        msg.set_content(f"Hello!\n\nClick the link below to reset your password. It expires in 15 minutes.\n\n{reset_link}")
        try:
            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
                smtp.login(app.config['MAIL_USERNAME'], app.config['MAIL_PASSWORD'])
                smtp.send_message(msg)
            return "Check your email for the reset link!", 200
        except Exception as e:
            return f"Error sending email: {e}", 500
    return "If an account exists, an email was sent.", 200

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    try:
        email = serializer.loads(token, salt='password-reset-salt', max_age=900)
    except:
        return "The reset link is invalid or has expired.", 400
    if request.method == 'POST':
        new_password = request.form.get('new_password')
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE users SET password=? WHERE email=?", (generate_password_hash(new_password), email))
        conn.commit()
        conn.close()
        return redirect(url_for('home'))
    return f'''
        <form method="POST" style="text-align:center; margin-top:50px; font-family:sans-serif;">
            <h2>Reset Password for {email}</h2>
            <input type="password" name="new_password" placeholder="New Password" required style="padding:10px;">
            <button type="submit" style="padding:10px 20px;">Save New Password</button>
        </form>
    '''

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('home'))

@app.route('/update-pfp', methods=['POST'])
def update_pfp():
    file = request.files.get('pfp')
    if file and file.filename != '':
        filename = f"pfp_{session['username']}_{file.filename}"
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE users SET pfp=? WHERE username=?", (filename, session['username']))
        conn.commit()
        conn.close()
    return redirect(url_for('home'))

@app.route('/update-bio', methods=['POST'])
def update_bio():
    if 'username' not in session: return "Unauthorized", 401
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE users SET bio=? WHERE username=?", (request.form.get('bio', ''), session['username']))
    conn.commit()
    conn.close()
    return "Success", 200

# --- FETCH ROUTES ---
@app.route('/api/user/<target_username>')
def get_user_info(target_username):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT username, pfp, bio FROM users WHERE username=?", (target_username,))
    user = c.fetchone()
    if not user:
        conn.close()
        return jsonify({'error': 'Not found'}), 404
    user_data = dict(user)
    c.execute("SELECT COUNT(*) FROM followers WHERE followed=?", (target_username,))
    user_data['followers_count'] = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM followers WHERE follower=?", (target_username,))
    user_data['following_count'] = c.fetchone()[0]
    user_data['is_following'] = False
    if 'username' in session:
        c.execute("SELECT 1 FROM followers WHERE follower=? AND followed=?", (session['username'], target_username))
        if c.fetchone(): user_data['is_following'] = True
    conn.close()
    return jsonify(user_data)

@app.route('/api/user-posts/<target_username>')
def get_user_posts(target_username):
    page = request.args.get('page', 1, type=int)
    offset = (page - 1) * 10
    return jsonify(fetch_posts_with_details("SELECT * FROM posts WHERE owner=? ORDER BY rowid DESC LIMIT 10 OFFSET ?", (target_username, offset)))

@app.route('/get-public-media')
def get_public_media():
    page = request.args.get('page', 1, type=int)
    offset = (page - 1) * 10
    return jsonify(fetch_posts_with_details("SELECT * FROM posts ORDER BY rowid DESC LIMIT 10 OFFSET ?", (offset,)))

@app.route('/get-profile-media')
def get_profile_media():
    if 'username' not in session: return jsonify([])
    page = request.args.get('page', 1, type=int)
    offset = (page - 1) * 10
    return jsonify(fetch_posts_with_details("SELECT * FROM posts WHERE owner=? ORDER BY rowid DESC LIMIT 10 OFFSET ?", (session['username'], offset)))

def fetch_posts_with_details(query, args=()):
    conn = get_db()
    c = conn.cursor()
    c.execute(query, args)
    posts = [dict(row) for row in c.fetchall()]
    for p in posts:
        c.execute("SELECT username FROM likes WHERE post_id=?", (p['id'],))
        p['likes'] = [row['username'] for row in c.fetchall()]
        c.execute("SELECT author, text FROM comments WHERE post_id=?", (p['id'],))
        p['comments'] = [dict(row) for row in c.fetchall()]
    conn.close()
    return posts

@app.route('/upload', methods=['POST'])
def upload_files():
    if 'username' not in session: return "Unauthorized", 401
    file = request.files.get('mediaFile')
    if not file: return 'Error', 400
    file.save(os.path.join(app.config['UPLOAD_FOLDER'], file.filename))
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO posts (id, filename, caption, font, filter, owner) VALUES (?, ?, ?, ?, ?, ?)",
              (str(uuid.uuid4()), file.filename, request.form.get('caption', ''), request.form.get('font', ''), request.form.get('filter', 'none'), session['username']))
    conn.commit()
    conn.close()
    return 'Success', 200

@app.route('/delete/<post_id>', methods=['POST'])
def delete_post(post_id):
    if 'username' not in session: return "Unauthorized", 401
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM posts WHERE id=? AND owner=?", (post_id, session['username']))
    if c.rowcount > 0:
        c.execute("DELETE FROM likes WHERE post_id=?", (post_id,))
        c.execute("DELETE FROM comments WHERE post_id=?", (post_id,))
    conn.commit()
    conn.close()
    return "Deleted", 200

@app.route('/like/<post_id>', methods=['POST'])
def like_post(post_id):
    if 'username' not in session: return "Unauthorized", 401
    me = session['username']
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT owner FROM posts WHERE id=?", (post_id,))
    post = c.fetchone()
    owner = post['owner'] if post else None
    c.execute("SELECT 1 FROM likes WHERE post_id=? AND username=?", (post_id, me))
    if c.fetchone():
        c.execute("DELETE FROM likes WHERE post_id=? AND username=?", (post_id, me))
    else:
        c.execute("INSERT INTO likes (post_id, username) VALUES (?, ?)", (post_id, me))
        if owner and owner != me:
            msg = f"@{me} liked your post! ❤️"
            c.execute("INSERT INTO notifications (username, message) VALUES (?, ?)", (owner, msg))
            socketio.emit('receive_notification', {'message': msg}, to=f"notify_{owner}")
    conn.commit()
    conn.close()
    return "Success", 200

@app.route('/comment/<post_id>', methods=['POST'])
def add_comment(post_id):
    if 'username' not in session: return "Unauthorized", 401
    text = request.form.get('text')
    if not text: return "Empty", 400
    me = session['username']
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO comments (post_id, author, text) VALUES (?, ?, ?)", (post_id, me, text))
    c.execute("SELECT owner FROM posts WHERE id=?", (post_id,))
    post = c.fetchone()
    owner = post['owner'] if post else None
    if owner and owner != me:
        msg = f"@{me} commented: '{text[:20]}...'"
        c.execute("INSERT INTO notifications (username, message) VALUES (?, ?)", (owner, msg))
        socketio.emit('receive_notification', {'message': msg}, to=f"notify_{owner}")
    conn.commit()
    conn.close()
    return "Success", 200

@app.route('/uploads/<filename>')
def serve_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# --- CHAT ROUTES ---

@app.route('/api/all-users')
def get_all_users():
    if 'username' not in session: return jsonify([])
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT username, pfp FROM users WHERE username != ?", (session['username'],))
    users = [dict(row) for row in c.fetchall()]
    conn.close()
    return jsonify(users)

@app.route('/api/messages/<other_user>')
def get_messages(other_user):
    if 'username' not in session: return jsonify([])
    me = session['username']
    conn = get_db()
    c = conn.cursor()
    c.execute("""SELECT id, sender, text, msg_type, media_filename 
                 FROM messages 
                 WHERE (sender=? AND receiver=?) OR (sender=? AND receiver=?) 
                 ORDER BY id ASC""",
              (me, other_user, other_user, me))
    msgs = [dict(row) for row in c.fetchall()]
    # Fetch reactions for each message
    for m in msgs:
        c.execute("SELECT emoji, username FROM message_reactions WHERE message_id=?", (m['id'],))
        m['reactions'] = [dict(r) for r in c.fetchall()]
    conn.close()
    return jsonify(msgs)

# NEW: Upload media file inside chat (photo/video/voice)
@app.route('/api/chat-upload', methods=['POST'])
def chat_upload():
    if 'username' not in session: return jsonify({'error': 'Unauthorized'}), 401
    file = request.files.get('file')
    if not file: return jsonify({'error': 'No file'}), 400
    ext = os.path.splitext(file.filename)[1].lower()
    filename = f"chat_{uuid.uuid4()}{ext}"
    file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
    # Determine type
    if ext in ['.mp4', '.webm', '.mov', '.avi']:
        msg_type = 'video'
    elif ext in ['.mp3', '.ogg', '.wav', '.m4a']:
        msg_type = 'voice'
    else:
        msg_type = 'image'
    return jsonify({'filename': filename, 'msg_type': msg_type})

# NEW: React to a message
@app.route('/api/message-react/<int:message_id>', methods=['POST'])
def react_to_message(message_id):
    if 'username' not in session: return "Unauthorized", 401
    me = session['username']
    emoji = request.form.get('emoji')
    conn = get_db()
    c = conn.cursor()
    # Toggle: if same emoji exists, remove it
    c.execute("SELECT id FROM message_reactions WHERE message_id=? AND username=? AND emoji=?", (message_id, me, emoji))
    existing = c.fetchone()
    if existing:
        c.execute("DELETE FROM message_reactions WHERE id=?", (existing['id'],))
    else:
        # Remove any previous reaction from this user on this message first
        c.execute("DELETE FROM message_reactions WHERE message_id=? AND username=?", (message_id, me))
        c.execute("INSERT INTO message_reactions (message_id, username, emoji) VALUES (?, ?, ?)", (message_id, me, emoji))
    conn.commit()
    conn.close()
    return "Success", 200

@app.route('/follow/<target_user>', methods=['POST'])
def toggle_follow(target_user):
    if 'username' not in session: return "Unauthorized", 401
    me = session['username']
    if me == target_user: return "Cannot follow yourself", 400
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT 1 FROM followers WHERE follower=? AND followed=?", (me, target_user))
    if c.fetchone():
        c.execute("DELETE FROM followers WHERE follower=? AND followed=?", (me, target_user))
    else:
        c.execute("INSERT INTO followers (follower, followed) VALUES (?, ?)", (me, target_user))
        msg = f"@{me} started following you! 👤"
        c.execute("INSERT INTO notifications (username, message) VALUES (?, ?)", (target_user, msg))
        socketio.emit('receive_notification', {'message': msg}, to=f"notify_{target_user}")
    conn.commit()
    conn.close()
    return "Success", 200

@app.route('/get-following-media')
def get_following_media():
    if 'username' not in session: return jsonify([])
    page = request.args.get('page', 1, type=int)
    offset = (page - 1) * 10
    query = """
        SELECT posts.* FROM posts 
        JOIN followers ON posts.owner = followers.followed 
        WHERE followers.follower = ? 
        ORDER BY posts.rowid DESC LIMIT 10 OFFSET ?
    """
    return jsonify(fetch_posts_with_details(query, (session['username'], offset)))

@app.route('/api/notifications')
def get_notifications():
    if 'username' not in session: return jsonify([])
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT message FROM notifications WHERE username=? ORDER BY id DESC LIMIT 10", (session['username'],))
    notifs = [dict(row) for row in c.fetchall()]
    conn.close()
    return jsonify(notifs)

# --- SOCKET EVENTS ---

@socketio.on('join_chat')
def on_join_chat(data):
    user1 = session.get('username')
    user2 = data['other_user']
    if not user1 or not user2: return
    room = f"{min(user1, user2)}_{max(user1, user2)}"
    join_room(room)

@socketio.on('user_connected')
def handle_user_connect(data):
    username = data.get('username')
    if username: join_room(f"notify_{username}")

@socketio.on('send_message')
def on_send_message(data):
    sender = session.get('username')
    receiver = data['receiver']
    text = data.get('text', '')
    msg_type = data.get('msg_type', 'text')          # text | image | video | voice | sticker | gif | drawing
    media_filename = data.get('media_filename', None)
    if not sender or not receiver: return

    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO messages (sender, receiver, text, msg_type, media_filename) VALUES (?, ?, ?, ?, ?)",
              (sender, receiver, text, msg_type, media_filename))
    new_id = c.lastrowid
    conn.commit()
    conn.close()

    room = f"{min(sender, receiver)}_{max(sender, receiver)}"
    emit('receive_message', {
        'id': new_id,
        'sender': sender,
        'text': text,
        'msg_type': msg_type,
        'media_filename': media_filename,
        'reactions': []
    }, to=room)

# NEW: Real-time video/voice call signaling
@socketio.on('call_user')
def on_call_user(data):
    """Relay the call invitation to the target user."""
    caller = session.get('username')
    target = data.get('target')
    call_type = data.get('call_type', 'audio')   # 'audio' or 'video'
    if not caller or not target: return
    emit('incoming_call', {
        'caller': caller,
        'call_type': call_type
    }, to=f"notify_{target}")

@socketio.on('call_response')
def on_call_response(data):
    """Relay accept/decline back to the caller."""
    responder = session.get('username')
    caller = data.get('caller')
    accepted = data.get('accepted', False)
    if not caller: return
    emit('call_answered', {
        'responder': responder,
        'accepted': accepted
    }, to=f"notify_{caller}")

@socketio.on('call_ended')
def on_call_ended(data):
    """Notify other party the call has ended."""
    ender = session.get('username')
    other = data.get('other_user')
    if other:
        emit('call_terminated', {'by': ender}, to=f"notify_{other}")

@socketio.on('typing')
def on_typing(data):
    """Broadcast typing indicator to the other user in the room."""
    sender = session.get('username')
    receiver = data.get('receiver')
    if not sender or not receiver: return
    room = f"{min(sender, receiver)}_{max(sender, receiver)}"
    emit('user_typing', {'sender': sender}, to=room)

@socketio.on('stop_typing')
def on_stop_typing(data):
    sender = session.get('username')
    receiver = data.get('receiver')
    if not sender or not receiver: return
    room = f"{min(sender, receiver)}_{max(sender, receiver)}"
    emit('user_stop_typing', {'sender': sender}, to=room)

if __name__ == '__main__':
    socketio.run(app, debug=True, port=5000)