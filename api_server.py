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
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Connexion - ONECCA</title>
    <link href="https://fonts.googleapis.com/css2?family=Lato:wght@400;700;900&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Lato', sans-serif;
            background: linear-gradient(135deg, #021e79 0%, #011654 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .auth-container {
            background: white;
            border-radius: 24px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            width: 100%;
            max-width: 440px;
            overflow: hidden;
        }
        .auth-header {
            background: #021e79;
            padding: 30px;
            text-align: center;
        }
        .auth-header img {
            height: 60px;
            margin-bottom: 15px;
        }
        .auth-header h1 {
            color: white;
            font-size: 24px;
            margin-bottom: 5px;
        }
        .auth-header p {
            color: rgba(255,255,255,0.7);
            font-size: 14px;
        }
        .auth-tabs {
            display: flex;
            border-bottom: 2px solid #eef2f6;
        }
        .auth-tab {
            flex: 1;
            padding: 15px;
            text-align: center;
            font-weight: 700;
            cursor: pointer;
            color: #707070;
            transition: all 0.3s;
        }
        .auth-tab.active {
            color: #021e79;
            border-bottom: 2px solid #ffbf00;
            margin-bottom: -2px;
        }
        .auth-body { padding: 30px; }
        .auth-form { display: none; }
        .auth-form.active { display: block; }
        .form-group { margin-bottom: 20px; }
        .form-group label {
            display: block;
            font-size: 13px;
            font-weight: 700;
            margin-bottom: 6px;
            color: #333;
        }
        .form-group input {
            width: 100%;
            padding: 12px 15px;
            border: 2px solid #dfe1e6;
            border-radius: 10px;
            font-size: 15px;
            outline: none;
            transition: border-color 0.3s;
        }
        .form-group input:focus { border-color: #021e79; }
        .btn-auth {
            width: 100%;
            padding: 14px;
            background: #ffbf00;
            color: #212326;
            font-weight: 900;
            border: none;
            border-radius: 10px;
            font-size: 16px;
            cursor: pointer;
            transition: background 0.3s;
        }
        .btn-auth:hover { background: #e6ac00; }
        .error-message {
            background: #fee2e2;
            color: #dc2626;
            padding: 10px;
            border-radius: 8px;
            margin-bottom: 20px;
            display: none;
            font-size: 13px;
        }
        .success-message {
            background: #dcfce7;
            color: #16a34a;
            padding: 10px;
            border-radius: 8px;
            margin-bottom: 20px;
            display: none;
            font-size: 13px;
        }
        .error-message.show, .success-message.show { display: block; }
        .auth-footer {
            text-align: center;
            padding: 20px;
            background: #f8f9fb;
            font-size: 12px;
            color: #707070;
        }
        .auth-footer a { color: #021e79; text-decoration: none; font-weight: 700; cursor: pointer; }
    </style>
</head>
<body>
<div class="auth-container">
    <div class="auth-header">
        <img src="logoonecca.png" alt="ONECCA" onerror="this.style.display='none'">
        <h1>ONECCA</h1>
        <p>Ordre National des Experts-Comptables du Cameroun</p>
    </div>
    <div class="auth-tabs">
        <div class="auth-tab active" data-tab="login">Connexion</div>
        <div class="auth-tab" data-tab="register">Inscription</div>
    </div>
    <div class="auth-body">
        <div id="errorMsg" class="error-message"></div>
        <div id="successMsg" class="success-message"></div>
        
        <!-- Formulaire de connexion -->
        <form id="loginForm" class="auth-form active">
            <div class="form-group">
                <label>Email</label>
                <input type="email" id="loginEmail" required placeholder="votre@email.com">
            </div>
            <div class="form-group">
                <label>Mot de passe</label>
                <input type="password" id="loginPassword" required placeholder="••••••••">
            </div>
            <button type="submit" class="btn-auth">Se connecter</button>
        </form>
        
        <!-- Formulaire d'inscription -->
        <form id="registerForm" class="auth-form">
            <div class="form-group">
                <label>Nom complet</label>
                <input type="text" id="regName" required placeholder="Votre nom">
            </div>
            <div class="form-group">
                <label>Email</label>
                <input type="email" id="regEmail" required placeholder="votre@email.com">
            </div>
            <div class="form-group">
                <label>Téléphone (optionnel)</label>
                <input type="tel" id="regPhone" placeholder="6XX XX XX XX">
            </div>
            <div class="form-group">
                <label>Mot de passe</label>
                <input type="password" id="regPassword" required placeholder="Minimum 6 caractères">
            </div>
            <button type="submit" class="btn-auth">Créer mon compte</button>
        </form>
    </div>
    <div class="auth-footer">
        <a id="forgotPassword">Mot de passe oublié ?</a>
    </div>
</div>

<script>
const API = window.location.origin;

// Gestion des onglets
document.querySelectorAll('.auth-tab').forEach(tab => {
    tab.addEventListener('click', function() {
        // Retirer la classe active de tous les onglets
        document.querySelectorAll('.auth-tab').forEach(t => t.classList.remove('active'));
        // Ajouter la classe active à l'onglet cliqué
        this.classList.add('active');
        
        // Cacher tous les formulaires
        document.querySelectorAll('.auth-form').forEach(f => f.classList.remove('active'));
        
        // Afficher le formulaire correspondant
        const tabName = this.getAttribute('data-tab');
        document.getElementById(tabName + 'Form').classList.add('active');
        
        // Cacher les messages
        hideMessages();
    });
});

function hideMessages() {
    document.getElementById('errorMsg').classList.remove('show');
    document.getElementById('successMsg').classList.remove('show');
}

function showError(msg) {
    const errorEl = document.getElementById('errorMsg');
    errorEl.textContent = msg;
    errorEl.classList.add('show');
    setTimeout(() => errorEl.classList.remove('show'), 5000);
}

function showSuccess(msg) {
    const successEl = document.getElementById('successMsg');
    successEl.textContent = msg;
    successEl.classList.add('show');
    setTimeout(() => successEl.classList.remove('show'), 3000);
}

// Connexion
document.getElementById('loginForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    hideMessages();
    
    const email = document.getElementById('loginEmail').value;
    const password = document.getElementById('loginPassword').value;
    
    try {
        const res = await fetch(API + '/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password })
        });
        
        const data = await res.json();
        
        if (res.ok) {
            localStorage.setItem('userToken', data.token);
            localStorage.setItem('userEmail', data.user.email);
            localStorage.setItem('userName', data.user.name);
            window.location.href = '/?login=success';
        } else {
            showError(data.detail || 'Email ou mot de passe incorrect');
        }
    } catch (err) {
        showError('Erreur de connexion. Veuillez réessayer.');
    }
});

// Inscription
document.getElementById('registerForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    hideMessages();
    
    const name = document.getElementById('regName').value.trim();
    const email = document.getElementById('regEmail').value.trim();
    const phone = document.getElementById('regPhone').value.trim();
    const password = document.getElementById('regPassword').value;
    
    if (name === '') {
        showError('Veuillez entrer votre nom');
        return;
    }
    
    if (email === '') {
        showError('Veuillez entrer votre email');
        return;
    }
    
    if (password.length < 6) {
        showError('Le mot de passe doit contenir au moins 6 caractères');
        return;
    }
    
    try {
        const res = await fetch(API + '/api/auth/register', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, email, phone, password })
        });
        
        const data = await res.json();
        
        if (res.ok) {
            showSuccess('Compte créé avec succès ! Vous pouvez maintenant vous connecter.');
            // Réinitialiser le formulaire d'inscription
            document.getElementById('regName').value = '';
            document.getElementById('regEmail').value = '';
            document.getElementById('regPhone').value = '';
            document.getElementById('regPassword').value = '';
            
            // Basculer vers l'onglet connexion après 2 secondes
            setTimeout(() => {
                document.querySelector('.auth-tab[data-tab="login"]').click();
                document.getElementById('loginEmail').value = email;
            }, 2000);
        } else {
            showError(data.detail || 'Erreur lors de l\'inscription. Email peut-être déjà utilisé.');
        }
    } catch (err) {
        console.error('Erreur:', err);
        showError('Erreur serveur. Veuillez réessayer.');
    }
});

// Mot de passe oublié
document.getElementById('forgotPassword').addEventListener('click', (e) => {
    e.preventDefault();
    const email = prompt('Entrez votre email pour réinitialiser votre mot de passe :');
    if (email && email.trim()) {
        fetch(API + '/api/auth/forgot-password', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email: email.trim() })
        })
        .then(() => alert('Si cet email existe, un lien de réinitialisation vous a été envoyé.'))
        .catch(() => alert('Erreur, veuillez réessayer.'));
    }
});
</script>
</body>
</html>
    """


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
