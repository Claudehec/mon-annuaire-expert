#!/usr/bin/env python3
"""ONECCA Directory API Server — FastAPI on port 8000."""
import uvicorn
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

@app.get("/auth.html", response_class=HTMLResponse)
async def serve_auth():
    try:
        with open("auth.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return HTMLResponse(content="""
        <!DOCTYPE html>
        <html>
        <head><title>Auth</title><meta charset="UTF-8"></head>
        <body style="font-family:Arial;padding:20px">
        <h2>Page d'authentification</h2>
        <p>Le fichier auth.html n'a pas encore été créé.</p>
        <p>Veuillez créer le fichier auth.html à la racine du projet.</p>
        <a href="/">Retour à l'accueil</a>
        </body>
        </html>
        """, status_code=404)

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

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
