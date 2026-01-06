#!/usr/bin/env python3
"""
Forex Analyzer - User Management Utility
Gestione utenti per il sistema di autenticazione

Uso:
    python user_manager.py add <username> <password> [email]
    python user_manager.py list
    python user_manager.py delete <username>
    python user_manager.py password <username> <new_password>
    python user_manager.py hash <password>
"""

import hashlib
import sys
import requests
import json

# Importa configurazione Supabase
try:
    from config import SUPABASE_URL, SUPABASE_KEY
except ImportError:
    SUPABASE_URL = None
    SUPABASE_KEY = None


def hash_password(password: str) -> str:
    """Hash password con SHA-256"""
    return hashlib.sha256(password.encode()).hexdigest()


def supabase_request(method: str, endpoint: str, data: dict = None):
    """Esegue richiesta REST a Supabase"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("❌ Errore: Configura SUPABASE_URL e SUPABASE_KEY in config.py")
        sys.exit(1)
    
    url = f"{SUPABASE_URL}/rest/v1/{endpoint}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }
    
    try:
        if method == "GET":
            response = requests.get(url, headers=headers)
        elif method == "POST":
            response = requests.post(url, headers=headers, json=data)
        elif method == "PATCH":
            response = requests.patch(url, headers=headers, json=data)
        elif method == "DELETE":
            headers["Prefer"] = "return=minimal"
            response = requests.delete(url, headers=headers)
        
        if response.status_code in [200, 201, 204]:
            return response.json() if response.text else {}
        else:
            print(f"❌ Errore HTTP {response.status_code}: {response.text}")
            return None
    except Exception as e:
        print(f"❌ Errore: {e}")
        return None


def add_user(username: str, password: str, email: str = None):
    """Aggiunge un nuovo utente"""
    password_hash = hash_password(password)
    
    data = {
        "username": username,
        "password_hash": password_hash,
        "email": email,
        "is_active": True
    }
    
    result = supabase_request("POST", "users", data)
    
    if result:
        print(f"✅ Utente '{username}' creato con successo!")
        print(f"   ID: {result[0].get('id', 'N/A')}")
    else:
        print(f"❌ Errore nella creazione dell'utente (potrebbe già esistere)")


def list_users():
    """Lista tutti gli utenti"""
    result = supabase_request("GET", "users?select=id,username,email,is_active,created_at")
    
    if result:
        print("\n📋 Lista Utenti:")
        print("-" * 80)
        print(f"{'Username':<20} {'Email':<30} {'Attivo':<10} {'Creato'}")
        print("-" * 80)
        
        for user in result:
            active = "✅" if user.get('is_active') else "❌"
            email = user.get('email', '-') or '-'
            created = user.get('created_at', 'N/A')[:10]
            print(f"{user['username']:<20} {email:<30} {active:<10} {created}")
        
        print("-" * 80)
        print(f"Totale: {len(result)} utenti")
    else:
        print("❌ Nessun utente trovato o errore nella query")


def delete_user(username: str):
    """Elimina un utente"""
    confirm = input(f"⚠️ Sei sicuro di voler eliminare l'utente '{username}'? (s/N): ")
    
    if confirm.lower() == 's':
        result = supabase_request("DELETE", f"users?username=eq.{username}")
        if result is not None:
            print(f"✅ Utente '{username}' eliminato")
        else:
            print(f"❌ Errore nell'eliminazione (utente non trovato?)")
    else:
        print("Operazione annullata")


def change_password(username: str, new_password: str):
    """Cambia password di un utente"""
    password_hash = hash_password(new_password)
    
    data = {"password_hash": password_hash}
    
    result = supabase_request("PATCH", f"users?username=eq.{username}", data)
    
    if result:
        print(f"✅ Password aggiornata per '{username}'")
    else:
        print(f"❌ Errore nell'aggiornamento password")


def show_hash(password: str):
    """Mostra l'hash SHA-256 di una password"""
    print(f"Password: {password}")
    print(f"SHA-256:  {hash_password(password)}")


def show_help():
    """Mostra help"""
    print(__doc__)


def main():
    if len(sys.argv) < 2:
        show_help()
        return
    
    command = sys.argv[1].lower()
    
    if command == "add":
        if len(sys.argv) < 4:
            print("Uso: python user_manager.py add <username> <password> [email]")
            return
        username = sys.argv[2]
        password = sys.argv[3]
        email = sys.argv[4] if len(sys.argv) > 4 else None
        add_user(username, password, email)
    
    elif command == "list":
        list_users()
    
    elif command == "delete":
        if len(sys.argv) < 3:
            print("Uso: python user_manager.py delete <username>")
            return
        delete_user(sys.argv[2])
    
    elif command == "password":
        if len(sys.argv) < 4:
            print("Uso: python user_manager.py password <username> <new_password>")
            return
        change_password(sys.argv[2], sys.argv[3])
    
    elif command == "hash":
        if len(sys.argv) < 3:
            print("Uso: python user_manager.py hash <password>")
            return
        show_hash(sys.argv[2])
    
    else:
        print(f"❌ Comando sconosciuto: {command}")
        show_help()


if __name__ == "__main__":
    main()
