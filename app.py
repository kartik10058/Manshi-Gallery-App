from flask import Flask, render_template, request, jsonify, send_from_directory, session, redirect, url_for
import os
import json
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

# This is the "secret key" that locks your session cookies
app.secret_key = 'manshi_tatty_super_secret_key' 

UPLOAD_FOLDER = 'uploads'
DATA_FILE = 'data.json'
USERS_FILE = 'users.json' # Our new user database!

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Create the databases if they don't exist
if not os.path.exists(DATA_FILE):
    with open(DATA_FILE, 'w') as f: json.dump([], f)
if not os.path.exists(USERS_FILE):
    with open(USERS_FILE, 'w') as f: json.dump({}, f)

# Helper functions to read/write JSON data easily
def load_json(filepath):
    with open(filepath, 'r') as f: return json.load(f)

def save_json(filepath, data):
    with open(filepath, 'w') as f: json.dump(data, f)

# --- 1. PAGES & AUTHENTICATION ---

@app.route('/')
def home():
    # We pass the currently logged-in username to the HTML
    return render_template('index.html', username=session.get('username'))

@app.route('/register', methods=['POST'])
def register():
    username = request.form.get('username')
    password = request.form.get('password')
    
    users = load_json(USERS_FILE)
    if username in users:
        return "Username already taken! Go back and try another.", 400
        
    # Encrypt the password and save the user
    users[username] = generate_password_hash(password)
    save_json(USERS_FILE, users)
    
    # Log them in automatically
    session['username'] = username
    return redirect(url_for('home'))

@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username')
    password = request.form.get('password')
    
    users = load_json(USERS_FILE)
    # Check if user exists AND password is correct
    if username in users and check_password_hash(users[username], password):
        session['username'] = username
        return redirect(url_for('home'))
        
    return "Incorrect username or password. Go back and try again.", 401

@app.route('/logout')
def logout():
    session.pop('username', None) # Destroys the session cookie
    return redirect(url_for('home'))

# --- 2. GALLERY FUNCTIONS ---

@app.route('/upload', methods=['POST'])
def upload_files():
    if 'username' not in session: 
        return "You must be logged in to upload!", 401
    
    if 'mediaFile' not in request.files: 
        return 'No files uploaded', 400
    
    file = request.files['mediaFile']
    caption = request.form.get('caption', '')
    font = request.form.get('font', "'Segoe UI', sans-serif")

    if file.filename != '':
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], file.filename))
        
        data = load_json(DATA_FILE)
        data.append({
            'filename': file.filename,
            'caption': caption,
            'font': font,
            'owner': session['username'] # Tag this picture with the user's name!
        })
        save_json(DATA_FILE, data)
            
    return 'Success', 200

@app.route('/get-media')
def get_media():
    if 'username' not in session:
        return jsonify([]) # Send nothing if not logged in
        
    data = load_json(DATA_FILE)
    # FILTER: Only send pictures that belong to the logged-in user!
    user_data = [item for item in data if item.get('owner') == session['username']]
    
    return jsonify(user_data)

@app.route('/uploads/<filename>')
def serve_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

if __name__ == '__main__':
    app.run(debug=True, port=5000)