from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_from_directory
import os, uuid, sqlite3, json
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = 'manshi_tatty_super_secret_key' 

UPLOAD_FOLDER = 'uploads'
DB_FILE = 'gallery.db'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# --- 1. DATABASE SETUP & MIGRATION ---
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password TEXT, pfp TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS posts (id TEXT PRIMARY KEY, filename TEXT, caption TEXT, font TEXT, filter TEXT, owner TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS likes (post_id TEXT, username TEXT, PRIMARY KEY(post_id, username))''')
    c.execute('''CREATE TABLE IF NOT EXISTS comments (id INTEGER PRIMARY KEY AUTOINCREMENT, post_id TEXT, author TEXT, text TEXT)''')
    
    # NEW: Safely upgrade the database to include Bios!
    try:
        c.execute("ALTER TABLE users ADD COLUMN bio TEXT")
    except sqlite3.OperationalError:
        pass # If the column already exists, just ignore and move on!
        
    conn.commit()
    conn.close()

init_db()

# --- 2. AUTHENTICATION & PROFILE ROUTES ---
@app.route('/')
def home():
    if 'username' in session:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT pfp, bio FROM users WHERE username=?", (session['username'],))
        user = c.fetchone()
        conn.close()
        pfp = user['pfp'] if user else None
        bio = user['bio'] if user and user['bio'] else '' # Grab the user's bio
        return render_template('index.html', username=session['username'], pfp=pfp, bio=bio)
    return render_template('index.html')

@app.route('/register', methods=['POST'])
def register():
    username, password = request.form.get('username'), request.form.get('password')
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO users (username, password, pfp, bio) VALUES (?, ?, ?, ?)", 
                  (username, generate_password_hash(password), None, ""))
        conn.commit()
        session['username'] = username
    except sqlite3.IntegrityError:
        return "Username taken", 400
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
    bio = request.form.get('bio', '')
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE users SET bio=? WHERE username=?", (bio, session['username']))
    conn.commit()
    conn.close()
    return "Success", 200

# --- 3. DYNAMIC USER PROFILES ---
@app.route('/api/user/<target_username>')
def get_user_info(target_username):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT username, pfp, bio FROM users WHERE username=?", (target_username,))
    user = c.fetchone()
    conn.close()
    if user: return jsonify(dict(user))
    return jsonify({'error': 'User not found'}), 404

@app.route('/api/user-posts/<target_username>')
def get_user_posts(target_username):
    return jsonify(fetch_posts_with_details("SELECT * FROM posts WHERE owner=? ORDER BY rowid DESC", (target_username,)))

# --- 4. SOCIAL FEATURES (Upload, Delete, Like, Comment remain exactly the same) ---
@app.route('/upload', methods=['POST'])
def upload_files():
    if 'username' not in session: return "Unauthorized", 401
    file = request.files.get('mediaFile')
    if not file: return 'Error', 400
    file.save(os.path.join(app.config['UPLOAD_FOLDER'], file.filename))
    post_id = str(uuid.uuid4())
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO posts (id, filename, caption, font, filter, owner) VALUES (?, ?, ?, ?, ?, ?)",
              (post_id, file.filename, request.form.get('caption', ''), request.form.get('font', ''), request.form.get('filter', 'none'), session['username']))
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
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT 1 FROM likes WHERE post_id=? AND username=?", (post_id, session['username']))
    if c.fetchone(): c.execute("DELETE FROM likes WHERE post_id=? AND username=?", (post_id, session['username']))
    else: c.execute("INSERT INTO likes (post_id, username) VALUES (?, ?)", (post_id, session['username']))
    conn.commit()
    conn.close()
    return "Success", 200

@app.route('/comment/<post_id>', methods=['POST'])
def add_comment(post_id):
    if 'username' not in session: return "Unauthorized", 401
    text = request.form.get('text')
    if not text: return "Empty comment", 400
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO comments (post_id, author, text) VALUES (?, ?, ?)", (post_id, session['username'], text))
    conn.commit()
    conn.close()
    return "Success", 200

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

@app.route('/get-public-media')
def get_public_media(): return jsonify(fetch_posts_with_details("SELECT * FROM posts ORDER BY rowid DESC"))

@app.route('/get-profile-media')
def get_profile_media():
    if 'username' not in session: return jsonify([])
    return jsonify(fetch_posts_with_details("SELECT * FROM posts WHERE owner=? ORDER BY rowid DESC", (session['username'],)))

@app.route('/uploads/<filename>')
def serve_file(filename): return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

if __name__ == '__main__':
    app.run(debug=True, port=5000)