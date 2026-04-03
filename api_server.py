#!/usr/bin/env python3
"""ONECCA Directory API Server — FastAPI on port 8000."""
import sqlite3
import json
import hashlib
import os
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import os
# Ajoutez cette ligne juste après les imports existants
PORT = int(os.environ.get("PORT", 8000))

DB_PATH = os.path.join(os.path.dirname(__file__), "onecca.db")
DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "onecca_data.json")

ADMIN_PASSWORD = "ONECCA2026"

def get_db():
    db = sqlite3.connect(DB_PATH, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db

def init_db(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            section TEXT NOT NULL,
            num INTEGER,
            nom TEXT NOT NULL,
            inscription_num TEXT DEFAULT '',
            inscription_date TEXT DEFAULT '',
            bp TEXT DEFAULT '',
            tel1 TEXT DEFAULT '',
            tel2 TEXT DEFAULT '',
            email TEXT DEFAULT '',
            adresse TEXT DEFAULT '',
            ville TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS contact_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nom TEXT NOT NULL,
            entreprise TEXT DEFAULT '',
            email TEXT NOT NULL,
            telephone TEXT DEFAULT '',
            commentaire TEXT DEFAULT '',
            lu INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    db.commit()

def seed_data(db):
    """Import initial data from JSON if members table is empty."""
    count = db.execute("SELECT COUNT(*) FROM members").fetchone()[0]
    if count > 0:
        return
    
    if not os.path.exists(DATA_PATH):
        return
    
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    for section, entries in data.items():
        for entry in entries:
            db.execute("""
                INSERT INTO members (section, num, nom, inscription_num, inscription_date, bp, tel1, tel2, email, adresse, ville)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                section,
                entry.get("num", 0),
                entry.get("nom", ""),
                entry.get("inscription_num", ""),
                entry.get("inscription_date", ""),
                entry.get("bp", ""),
                entry.get("tel1", ""),
                entry.get("tel2", ""),
                entry.get("email", ""),
                entry.get("adresse", ""),
                entry.get("ville", "Autre"),
            ))
    
    # Set default: coordinates hidden
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('show_contacts', 'false')")
    db.commit()
    print(f"Seeded {sum(len(v) for v in data.values())} members")

db = get_db()
init_db(db)
seed_data(db)

@asynccontextmanager
async def lifespan(app):
    yield
    db.close()

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ===== AUTH HELPER =====
def check_admin(auth: str):
    if auth != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Non autorisé")

# ===== PUBLIC ENDPOINTS =====

@app.get("/api/members")
def get_members():
    """Public: get all members. Contacts hidden based on settings."""
    show = db.execute("SELECT value FROM settings WHERE key='show_contacts'").fetchone()
    show_contacts = show and show[0] == 'true'
    
    rows = db.execute("SELECT * FROM members ORDER BY section, num").fetchall()
    result = {}
    for r in rows:
        section = r["section"]
        if section not in result:
            result[section] = []
        member = {
            "id": r["id"],
            "num": r["num"],
            "nom": r["nom"],
            "inscription_num": r["inscription_num"],
            "inscription_date": r["inscription_date"],
            "bp": r["bp"],
            "adresse": r["adresse"],
            "ville": r["ville"],
        }
        if show_contacts:
            member["tel1"] = r["tel1"]
            member["tel2"] = r["tel2"]
            member["email"] = r["email"]
        result[section] = result.get(section, [])
        result[section].append(member)
    
    return {"members": result, "show_contacts": show_contacts}

@app.get("/api/settings/show_contacts")
def get_show_contacts():
    row = db.execute("SELECT value FROM settings WHERE key='show_contacts'").fetchone()
    return {"show_contacts": row[0] == 'true' if row else False}

# ===== CONTACT FORM =====
class ContactRequest(BaseModel):
    nom: str
    entreprise: str = ""
    email: str
    telephone: str = ""
    commentaire: str = ""

@app.post("/api/contact", status_code=201)
def submit_contact(req: ContactRequest):
    if not req.nom.strip() or not req.email.strip():
        raise HTTPException(status_code=400, detail="Nom et email requis")
    db.execute("""
        INSERT INTO contact_requests (nom, entreprise, email, telephone, commentaire)
        VALUES (?, ?, ?, ?, ?)
    """, (req.nom.strip(), req.entreprise.strip(), req.email.strip(), req.telephone.strip(), req.commentaire.strip()))
    db.commit()
    return {"message": "Votre demande a bien été envoyée. Nous vous contacterons rapidement."}

# ===== ADMIN ENDPOINTS =====

@app.post("/api/admin/login")
def admin_login(password: str = ""):
    if password == ADMIN_PASSWORD:
        return {"success": True}
    raise HTTPException(status_code=401, detail="Mot de passe incorrect")

@app.post("/api/admin/toggle_contacts")
def toggle_contacts(x_admin_auth: str = Header(default="")):
    check_admin(x_admin_auth)
    current = db.execute("SELECT value FROM settings WHERE key='show_contacts'").fetchone()
    new_val = 'false' if (current and current[0] == 'true') else 'true'
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('show_contacts', ?)", (new_val,))
    db.commit()
    return {"show_contacts": new_val == 'true'}

class MemberCreate(BaseModel):
    section: str
    nom: str
    inscription_num: str = ""
    inscription_date: str = ""
    bp: str = ""
    tel1: str = ""
    tel2: str = ""
    email: str = ""
    adresse: str = ""
    ville: str = "Autre"

@app.post("/api/admin/members", status_code=201)
def add_member(member: MemberCreate, x_admin_auth: str = Header(default="")):
    check_admin(x_admin_auth)
    # Auto-increment num within section
    max_num = db.execute("SELECT MAX(num) FROM members WHERE section=?", (member.section,)).fetchone()[0]
    new_num = (max_num or 0) + 1
    
    cur = db.execute("""
        INSERT INTO members (section, num, nom, inscription_num, inscription_date, bp, tel1, tel2, email, adresse, ville)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (member.section, new_num, member.nom, member.inscription_num, member.inscription_date,
          member.bp, member.tel1, member.tel2, member.email, member.adresse, member.ville))
    db.commit()
    return {"id": cur.lastrowid, "num": new_num}

@app.put("/api/admin/members/{member_id}")
def update_member(member_id: int, member: MemberCreate, x_admin_auth: str = Header(default="")):
    check_admin(x_admin_auth)
    db.execute("""
        UPDATE members SET section=?, nom=?, inscription_num=?, inscription_date=?,
        bp=?, tel1=?, tel2=?, email=?, adresse=?, ville=?, updated_at=?
        WHERE id=?
    """, (member.section, member.nom, member.inscription_num, member.inscription_date,
          member.bp, member.tel1, member.tel2, member.email, member.adresse, member.ville,
          datetime.now().isoformat(), member_id))
    db.commit()
    return {"updated": member_id}

@app.delete("/api/admin/members/{member_id}")
def delete_member(member_id: int, x_admin_auth: str = Header(default="")):
    check_admin(x_admin_auth)
    db.execute("DELETE FROM members WHERE id=?", (member_id,))
    db.commit()
    return {"deleted": member_id}

@app.get("/api/admin/members")
def admin_get_members(x_admin_auth: str = Header(default="")):
    """Admin: get all members with full contact info."""
    check_admin(x_admin_auth)
    rows = db.execute("SELECT * FROM members ORDER BY section, num").fetchall()
    result = {}
    for r in rows:
        section = r["section"]
        if section not in result:
            result[section] = []
        result[section].append(dict(r))
    return {"members": result}

@app.get("/api/admin/contacts")
def admin_get_contacts(x_admin_auth: str = Header(default="")):
    check_admin(x_admin_auth)
    rows = db.execute("SELECT * FROM contact_requests ORDER BY created_at DESC").fetchall()
    return {"contacts": [dict(r) for r in rows]}

@app.put("/api/admin/contacts/{contact_id}/read")
def mark_contact_read(contact_id: int, x_admin_auth: str = Header(default="")):
    check_admin(x_admin_auth)
    db.execute("UPDATE contact_requests SET lu=1 WHERE id=?", (contact_id,))
    db.commit()
    return {"marked_read": contact_id}

@app.delete("/api/admin/contacts/{contact_id}")
def delete_contact(contact_id: int, x_admin_auth: str = Header(default="")):
    check_admin(x_admin_auth)
    db.execute("DELETE FROM contact_requests WHERE id=?", (contact_id,))
    db.commit()
    return {"deleted": contact_id}

from fastapi.responses import HTMLResponse

@app.get("/", response_class=HTMLResponse)
async def read_root():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/index.html", response_class=HTMLResponse)
async def read_index():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()


# ===== AUTHENTIFICATION UTILISATEURS =====
import hashlib
import secrets
from datetime import datetime, timedelta

# Table des utilisateurs
def init_auth_db():
    conn = sqlite3.connect("onecca.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            phone TEXT,
            password_hash TEXT NOT NULL,
            reset_token TEXT,
            reset_token_expiry TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

init_auth_db()

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def generate_token():
    return secrets.token_urlsafe(32)

class LoginRequest(BaseModel):
    email: str
    password: str

class RegisterRequest(BaseModel):
    name: str
    email: str
    phone: str = ""
    password: str

class ForgotPasswordRequest(BaseModel):
    email: str

@app.post("/api/auth/register")
async def register(request: RegisterRequest):
    conn = sqlite3.connect("onecca.db")
    cursor = conn.cursor()
    
    cursor.execute("SELECT id FROM users WHERE email = ?", (request.email,))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Cet email est déjà utilisé")
    
    password_hash = hash_password(request.password)
    cursor.execute("""
        INSERT INTO users (name, email, phone, password_hash)
        VALUES (?, ?, ?, ?)
    """, (request.name, request.email, request.phone, password_hash))
    
    user_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return {"success": True, "user_id": user_id}

@app.post("/api/auth/login")
async def login(request: LoginRequest):
    conn = sqlite3.connect("onecca.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM users WHERE email = ?", (request.email,))
    user = cursor.fetchone()
    conn.close()
    
    if not user or user["password_hash"] != hash_password(request.password):
        raise HTTPException(status_code=401, detail="Email ou mot de passe incorrect")
    
    token = generate_token()
    
    return {
        "success": True,
        "token": token,
        "user": {
            "id": user["id"],
            "name": user["name"],
            "email": user["email"],
            "phone": user["phone"]
        }
    }

@app.post("/api/auth/forgot-password")
async def forgot_password(request: ForgotPasswordRequest):
    conn = sqlite3.connect("onecca.db")
    cursor = conn.cursor()
    
    cursor.execute("SELECT id FROM users WHERE email = ?", (request.email,))
    user = cursor.fetchone()
    
    if user:
        token = generate_token()
        expiry = (datetime.now() + timedelta(hours=1)).isoformat()
        cursor.execute("""
            UPDATE users SET reset_token = ?, reset_token_expiry = ?
            WHERE email = ?
        """, (token, expiry, request.email))
        conn.commit()
        print(f"Reset token for {request.email}: {token}")
    
    conn.close()
    return {"success": True}



@app.get("/auth.html", response_class=HTMLResponse)
async def serve_auth():
    return """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Connexion ONECCA</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: Arial, sans-serif;
            background: linear-gradient(135deg, #021e79 0%, #011654 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }
        .container {
            background: white;
            border-radius: 20px;
            width: 100%;
            max-width: 400px;
            overflow: hidden;
            box-shadow: 0 20px 40px rgba(0,0,0,0.2);
        }
        .header {
            background: #021e79;
            padding: 30px;
            text-align: center;
        }
        .header img {
            height: 50px;
            margin-bottom: 10px;
        }
        .header h1 {
            color: white;
            font-size: 20px;
        }
        .tabs {
            display: flex;
        }
        .tab-btn {
            flex: 1;
            padding: 15px;
            text-align: center;
            cursor: pointer;
            font-weight: bold;
            background: #f5f5f5;
            border: none;
            font-size: 16px;
            transition: all 0.3s;
        }
        .tab-btn.active {
            background: white;
            color: #021e79;
            border-bottom: 3px solid #ffbf00;
        }
        .form-container {
            padding: 30px;
        }
        .form {
            display: none;
        }
        .form.active {
            display: block;
        }
        input {
            width: 100%;
            padding: 12px;
            margin: 10px 0;
            border: 1px solid #ddd;
            border-radius: 8px;
            font-size: 14px;
            box-sizing: border-box;
        }
        button {
            width: 100%;
            padding: 12px;
            background: #ffbf00;
            color: #212326;
            border: none;
            border-radius: 8px;
            font-size: 16px;
            font-weight: bold;
            cursor: pointer;
            margin-top: 10px;
        }
        button:hover {
            background: #e6ac00;
        }
        .message {
            padding: 10px;
            margin: 10px 0;
            border-radius: 8px;
            display: none;
            font-size: 14px;
        }
        .message.error {
            background: #fee2e2;
            color: #dc2626;
            display: block;
        }
        .message.success {
            background: #dcfce7;
            color: #16a34a;
            display: block;
        }
        .footer {
            text-align: center;
            padding: 15px;
            background: #f8f9fb;
            font-size: 12px;
        }
        .footer a {
            color: #021e79;
            text-decoration: none;
            cursor: pointer;
        }
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <img src="logoonecca.png" alt="ONECCA" onerror="this.style.display='none'">
        <h1>ONECCA</h1>
    </div>
    
    <div class="tabs">
        <button class="tab-btn active" id="btnLogin">Connexion</button>
        <button class="tab-btn" id="btnRegister">Inscription</button>
    </div>
    
    <div class="form-container">
        <div id="message" class="message"></div>
        
        <div id="loginForm" class="form active">
            <input type="email" id="email" placeholder="Email" autocomplete="off">
            <input type="password" id="password" placeholder="Mot de passe">
            <button id="submitLogin">Se connecter</button>
        </div>
        
        <div id="registerForm" class="form">
            <input type="text" id="regName" placeholder="Nom complet">
            <input type="email" id="regEmail" placeholder="Email">
            <input type="tel" id="regPhone" placeholder="Téléphone (optionnel)">
            <input type="password" id="regPassword" placeholder="Mot de passe (min 6)">
            <button id="submitRegister">Créer mon compte</button>
        </div>
    </div>
    
    <div class="footer">
        <a id="forgotBtn">Mot de passe oublié ?</a>
    </div>
</div>

<script>
var API = window.location.origin;

// Onglets
document.getElementById('btnLogin').onclick = function() {
    document.getElementById('btnLogin').classList.add('active');
    document.getElementById('btnRegister').classList.remove('active');
    document.getElementById('loginForm').classList.add('active');
    document.getElementById('registerForm').classList.remove('active');
    document.getElementById('message').className = 'message';
    document.getElementById('message').style.display = 'none';
};

document.getElementById('btnRegister').onclick = function() {
    document.getElementById('btnRegister').classList.add('active');
    document.getElementById('btnLogin').classList.remove('active');
    document.getElementById('registerForm').classList.add('active');
    document.getElementById('loginForm').classList.remove('active');
    document.getElementById('message').className = 'message';
    document.getElementById('message').style.display = 'none';
};

function showMessage(msg, isError) {
    var msgDiv = document.getElementById('message');
    msgDiv.textContent = msg;
    msgDiv.className = 'message ' + (isError ? 'error' : 'success');
    setTimeout(function() {
        msgDiv.className = 'message';
    }, 3000);
}

// INSCRIPTION
document.getElementById('submitRegister').onclick = function() {
    var name = document.getElementById('regName').value;
    var email = document.getElementById('regEmail').value;
    var phone = document.getElementById('regPhone').value;
    var password = document.getElementById('regPassword').value;
    
    if (!name || !email || password.length < 6) {
        showMessage('Remplissez tous les champs (mot de passe 6+ caracteres)', true);
        return;
    }
    
    fetch(API + '/api/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name, email: email, phone: phone, password: password })
    })
    .then(function(response) { return response.json(); })
    .then(function(data) {
        if (data.success === true) {
            showMessage('Compte cree ! Connectez-vous maintenant', false);
            document.getElementById('btnLogin').click();
            document.getElementById('email').value = email;
            document.getElementById('regName').value = '';
            document.getElementById('regEmail').value = '';
            document.getElementById('regPhone').value = '';
            document.getElementById('regPassword').value = '';
        } else {
            showMessage(data.detail || 'Erreur lors de l\'inscription', true);
        }
    })
    .catch(function(err) {
        showMessage('Erreur serveur', true);
    });
};

// CONNEXION
document.getElementById('submitLogin').onclick = function() {
    var email = document.getElementById('email').value;
    var password = document.getElementById('password').value;
    
    if (!email || !password) {
        showMessage('Veuillez entrer email et mot de passe', true);
        return;
    }
    
    fetch(API + '/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email, password: password })
    })
    .then(function(response) { return response.json(); })
    .then(function(data) {
        if (data.success === true) {
            localStorage.setItem('userToken', data.token);
            localStorage.setItem('userEmail', data.user.email);
            localStorage.setItem('userName', data.user.name);
            window.location.href = '/?login=success';
        } else {
            showMessage(data.detail || 'Email ou mot de passe incorrect', true);
        }
    })
    .catch(function(err) {
        showMessage('Erreur de connexion', true);
    });
};

// Mot de passe oublie
document.getElementById('forgotBtn').onclick = function() {
    var email = prompt('Entrez votre email:');
    if (email) {
        fetch(API + '/api/auth/forgot-password', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email: email })
        })
        .then(function() {
            alert('Si cet email existe, un lien vous a ete envoye');
        })
        .catch(function() {
            alert('Erreur');
        });
    }
};
</script>
</body>
</html>
    """
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
