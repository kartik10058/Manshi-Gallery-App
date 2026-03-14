import os
from dotenv import load_dotenv

# This line magically loads the secrets from the .env file!
load_dotenv()
# Add these to your existing imports
import smtplib
from email.message import EmailMessage
from itsdangerous import URLSafeTimedSerializer
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_from_directory
import os, uuid, sqlite3, json
from werkzeug.security import generate_password_hash, check_password_hash
# NEW: Import the WebSocket libraries!
from flask_socketio import SocketIO, emit, join_room

app = Flask(__name__)
# Before: app.secret_key = 'manshi_tatty_super_secret_key'
app.secret_key = os.getenv('SECRET_KEY')

# Before: app.config['MAIL_USERNAME'] = 'your_actual_email@gmail.com'
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')

# Before: app.config['MAIL_PASSWORD'] = 'abcd1234efgh5678'
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')

# This creates secure, expiring tokens
serializer = URLSafeTimedSerializer(app.secret_key)
# NEW: Initialize the real-time Socket server
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
    # NEW: Table for our private DMs
    c.execute('''CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, sender TEXT, receiver TEXT, text TEXT)''')
    # NEW: Table to track who follows who!
    c.execute('''CREATE TABLE IF NOT EXISTS followers (follower TEXT, followed TEXT, PRIMARY KEY(follower, followed))''')
    # NEW: Table to store our live alerts
    c.execute('''CREATE TABLE IF NOT EXISTS notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, message TEXT, is_read INTEGER DEFAULT 0)''')
    conn.commit()
    conn.close()

init_db()

# --- AUTH & PROFILES (UNCHANGED) ---
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
    email = request.form.get('email') # Grab the email
    password = request.form.get('password')
    conn = get_db()
    c = conn.cursor()
    try:
        # Save the email to the database
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
        # 1. Create a secure token containing the user's email
        token = serializer.dumps(email, salt='password-reset-salt')
        
        # 2. Create the exact link they need to click
        reset_link = url_for('reset_password', token=token, _external=True)
        
        # 3. Draft the email
        msg = EmailMessage()
        msg['Subject'] = 'Reset Your Manshi{Tatty} Password'
        msg['From'] = app.config['MAIL_USERNAME']
        msg['To'] = email
        msg.set_content(f"Hello!\n\nClick the link below to reset your password. It expires in 15 minutes.\n\n{reset_link}")

        # 4. Send the email using Gmail's servers
        try:
            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
                smtp.login(app.config['MAIL_USERNAME'], app.config['MAIL_PASSWORD'])
                smtp.send_message(msg)
            return "Check your email for the reset link!", 200
        except Exception as e:
            return f"Error sending email: {e}", 500

    # We return success even if the email doesn't exist so hackers can't guess emails
    return "If an account exists, an email was sent.", 200

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    try:
        # Check if the token is valid and hasn't expired (900 seconds = 15 mins)
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

    # If it's a GET request, show them a simple HTML form to type the new password
    return f'''
        <form method="POST" style="text-align:center; margin-top:50px; font-family:sans-serif;">
            <h2>Reset Password for {email}</h2>
            <input type="password" name="new_password" placeholder="New Password" required style="padding:10px;">
            <button type="submit" style="padding:10px 20px;">Save New Password</button>
        </form>
    '''
@app.route('/logout')
def logout(): session.pop('username', None); return redirect(url_for('home'))

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

# --- FETCH ROUTES (UNCHANGED) ---
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
    
    # Count Followers & Following
    c.execute("SELECT COUNT(*) FROM followers WHERE followed=?", (target_username,))
    user_data['followers_count'] = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM followers WHERE follower=?", (target_username,))
    user_data['following_count'] = c.fetchone()[0]
    
    # Check if I am currently following this person
    user_data['is_following'] = False
    if 'username' in session:
        c.execute("SELECT 1 FROM followers WHERE follower=? AND followed=?", (session['username'], target_username))
        if c.fetchone(): user_data['is_following'] = True
        
    conn.close()
    return jsonify(user_data)
# --- PAGINATED FETCH ROUTES ---
@app.route('/api/user-posts/<target_username>')
def get_user_posts(target_username):
    page = request.args.get('page', 1, type=int)
    offset = (page - 1) * 10
    # LIMIT 10 OFFSET ? tells SQLite to only grab 10 posts at a time!
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
    
    # Who owns the post we are liking?
    c.execute("SELECT owner FROM posts WHERE id=?", (post_id,))
    post = c.fetchone()
    owner = post['owner'] if post else None

    c.execute("SELECT 1 FROM likes WHERE post_id=? AND username=?", (post_id, me))
    if c.fetchone(): 
        c.execute("DELETE FROM likes WHERE post_id=? AND username=?", (post_id, me))
    else: 
        c.execute("INSERT INTO likes (post_id, username) VALUES (?, ?)", (post_id, me))
        
        # NEW: Send a live alert if we aren't liking our own post!
        if owner and owner != me:
            msg = f"@{me} liked your post! ❤️"
            c.execute("INSERT INTO notifications (username, message) VALUES (?, ?)", (owner, msg))
            # Fire the live socket event to the owner's hidden room
            socketio.emit('receive_notification', {'message': msg}, to=f"notify_{owner}")
            
    conn.commit()
    conn.close()
    return "Success", 200

@app.route('/comment/<post_id>', methods=['POST'])
def add_comment(post_id):
    if 'username' not in session: return "Unauthorized", 401
    text = request.form.get('text')
    if not text: return "Empty", 400
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO comments (post_id, author, text) VALUES (?, ?, ?)", (post_id, session['username'], text))
    conn.commit()
    conn.close()
    return "Success", 200

@app.route('/uploads/<filename>')
def serve_file(filename): return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# --- NEW: REAL-TIME CHAT ROUTES & SOCKETS ---

# 1. Fetch all users to show in the chat sidebar
@app.route('/api/all-users')
def get_all_users():
    if 'username' not in session: return jsonify([])
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT username, pfp FROM users WHERE username != ?", (session['username'],))
    users = [dict(row) for row in c.fetchall()]
    conn.close()
    return jsonify(users)

# 2. Fetch the chat history between you and someone else
@app.route('/api/messages/<other_user>')
def get_messages(other_user):
    if 'username' not in session: return jsonify([])
    me = session['username']
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT sender, text FROM messages WHERE (sender=? AND receiver=?) OR (sender=? AND receiver=?) ORDER BY id ASC", (me, other_user, other_user, me))
    msgs = [dict(row) for row in c.fetchall()]
    conn.close()
    return jsonify(msgs)
@app.route('/follow/<target_user>', methods=['POST'])
def toggle_follow(target_user):
    if 'username' not in session: return "Unauthorized", 401
    me = session['username']
    if me == target_user: return "Cannot follow yourself", 400
    
    conn = get_db()
    c = conn.cursor()
    # Check if we are already following them
    c.execute("SELECT 1 FROM followers WHERE follower=? AND followed=?", (me, target_user))
    if c.fetchone():
        # If yes, Unfollow!
        c.execute("DELETE FROM followers WHERE follower=? AND followed=?", (me, target_user))
    else:
        # If no, Follow!
        c.execute("INSERT INTO followers (follower, followed) VALUES (?, ?)", (me, target_user))
    conn.commit()
    conn.close()
    return "Success", 200

@app.route('/get-following-media')
def get_following_media():
    if 'username' not in session: return jsonify([])
    page = request.args.get('page', 1, type=int)
    offset = (page - 1) * 10
    
    # THE MAGIC JOIN QUERY: Grab posts ONLY from people the current user follows
    query = """
        SELECT posts.* FROM posts 
        JOIN followers ON posts.owner = followers.followed 
        WHERE followers.follower = ? 
        ORDER BY posts.rowid DESC LIMIT 10 OFFSET ?
    """
    return jsonify(fetch_posts_with_details(query, (session['username'], offset)))
# 3. Create a unique "Room" when you click a user's name
@socketio.on('join_chat')
def on_join_chat(data):
    user1 = session.get('username')
    user2 = data['other_user']
    if not user1 or not user2: return
    # Create a unique room name by alphabetically sorting the two names!
    room = f"{min(user1, user2)}_{max(user1, user2)}"
    join_room(room)
@socketio.on('user_connected')
def handle_user_connect(data):
    # When a user logs in, they join a hidden room named after them
    username = data.get('username')
    if username: join_room(f"notify_{username}")

@app.route('/api/notifications')
def get_notifications():
    if 'username' not in session: return jsonify([])
    conn = get_db()
    c = conn.cursor()
    # Grab their 10 most recent alerts
    c.execute("SELECT message FROM notifications WHERE username=? ORDER BY id DESC LIMIT 10", (session['username'],))
    notifs = [dict(row) for row in c.fetchall()]
    conn.close()
    return jsonify(notifs)
# 4. Handle sending the message in real-time
@socketio.on('send_message')
def on_send_message(data):
    sender = session.get('username')
    receiver = data['receiver']
    text = data['text']
    if not sender or not receiver or not text: return

    # Save it to the SQLite vault
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO messages (sender, receiver, text) VALUES (?, ?, ?)", (sender, receiver, text))
    conn.commit()
    conn.close()

    # Instantly broadcast it to both users in the room
    room = f"{min(sender, receiver)}_{max(sender, receiver)}"
    emit('receive_message', {'sender': sender, 'text': text}, to=room)

if __name__ == '__main__':
    # NEW: We must run the app using socketio instead of standard app.run!
    socketio.run(app, debug=True, port=5000)