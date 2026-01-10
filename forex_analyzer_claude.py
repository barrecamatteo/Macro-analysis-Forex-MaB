import streamlit as st
import anthropic
from duckduckgo_search import DDGS
from datetime import datetime, timedelta
import json
import pandas as pd
import os
from pathlib import Path
import requests
import hashlib
import re

# Timezone Italia (con fallback)
try:
    from zoneinfo import ZoneInfo
    ITALY_TZ = ZoneInfo("Europe/Rome")
except ImportError:
    # Fallback per Python < 3.9
    ITALY_TZ = None

def get_italy_now():
    """Restituisce datetime italiano"""
    if ITALY_TZ:
        return datetime.now(ITALY_TZ)
    else:
        # Fallback: UTC + 1 ora (o +2 in estate, ma approssimativo)
        return datetime.utcnow() + timedelta(hours=1)

# Import modulo dati macro da API ufficiali
from macro_data_fetcher import MacroDataFetcher

# --- CONFIGURAZIONE PAGINA ---
st.set_page_config(
    page_title="Forex Macro Analyst - Claude AI",
    page_icon="📊",
    layout="wide"
)

# --- IMPORT API KEY ---
# Supporta sia config.py (locale) che st.secrets (Streamlit Cloud)
ANTHROPIC_API_KEY = None
API_KEY_LOADED = False
SUPABASE_URL = None
SUPABASE_KEY = None
API_NINJAS_KEY = None

# Prima prova st.secrets (Streamlit Cloud)
try:
    ANTHROPIC_API_KEY = st.secrets["ANTHROPIC_API_KEY"]
    API_KEY_LOADED = True
except (KeyError, FileNotFoundError):
    pass

# Supabase credentials da st.secrets
try:
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
except (KeyError, FileNotFoundError):
    pass

# API Ninjas Key da st.secrets
try:
    API_NINJAS_KEY = st.secrets["API_NINJAS_KEY"]
except (KeyError, FileNotFoundError):
    pass

# Se non trovata, prova config.py (locale)
if not API_KEY_LOADED:
    try:
        from config import ANTHROPIC_API_KEY
        API_KEY_LOADED = True
        # Prova a caricare anche Supabase e API Ninjas da config
        try:
            from config import SUPABASE_URL, SUPABASE_KEY
        except ImportError:
            pass
        try:
            from config import API_NINJAS_KEY
        except ImportError:
            pass
    except ImportError:
        pass

# Flag per Supabase
SUPABASE_ENABLED = SUPABASE_URL is not None and SUPABASE_KEY is not None

# Flag per API Ninjas (PIL e disoccupazione)
API_NINJAS_ENABLED = API_NINJAS_KEY is not None

# --- CARTELLA DATI ---
DATA_FOLDER = Path("data")
DATA_FOLDER.mkdir(exist_ok=True)


# ============================================================================
# SISTEMA AUTENTICAZIONE SUPABASE
# ============================================================================

def hash_password(password: str) -> str:
    """Hash password con SHA-256"""
    return hashlib.sha256(password.encode()).hexdigest()


def supabase_request(method: str, endpoint: str, data: dict = None) -> dict | list | None:
    """Esegue una richiesta REST a Supabase"""
    if not SUPABASE_ENABLED:
        return None
    
    url = f"{SUPABASE_URL}/rest/v1/{endpoint}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }
    
    try:
        if method == "GET":
            response = requests.get(url, headers=headers, timeout=30)
        elif method == "POST":
            response = requests.post(url, headers=headers, json=data, timeout=30)
        elif method == "PATCH":
            response = requests.patch(url, headers=headers, json=data, timeout=30)
        elif method == "DELETE":
            headers["Prefer"] = "return=minimal"
            response = requests.delete(url, headers=headers, timeout=30)
        else:
            return None
        
        if response.status_code in [200, 201, 204]:
            if response.text:
                return response.json()
            return {}
        else:
            # Log dettagliato dell'errore
            st.error(f"Supabase errore {response.status_code}: {response.text[:200] if response.text else 'Nessun dettaglio'}")
            return None
            
    except Exception as e:
        st.error(f"Errore connessione Supabase: {e}")
        return None


def authenticate_user(username: str, password: str) -> dict | None:
    """
    Autentica un utente verificando username e password.
    Restituisce i dati utente se autenticato, None altrimenti.
    """
    if not SUPABASE_ENABLED:
        # Fallback locale per testing
        local_users = {
            "MBARRECA": {"password": hash_password("mbarreca"), "id": "local-admin", "is_active": True}
        }
        if username in local_users:
            if local_users[username]["password"] == hash_password(password):
                return {"id": local_users[username]["id"], "username": username, "is_active": True}
        return None
    
    # Query Supabase
    password_hash = hash_password(password)
    result = supabase_request(
        "GET", 
        f"users?username=eq.{username}&password_hash=eq.{password_hash}&is_active=eq.true"
    )
    
    if result and len(result) > 0:
        return result[0]
    return None


def get_user_by_id(user_id: str) -> dict | None:
    """Recupera utente per ID"""
    if not SUPABASE_ENABLED:
        return None
    
    result = supabase_request("GET", f"users?id=eq.{user_id}")
    if result and len(result) > 0:
        return result[0]
    return None


def create_user(username: str, password: str, email: str = None) -> bool:
    """
    Crea un nuovo utente (per uso futuro con registrazione).
    """
    if not SUPABASE_ENABLED:
        return False
    
    data = {
        "username": username,
        "password_hash": hash_password(password),
        "email": email,
        "is_active": True,
        "created_at": get_italy_now().isoformat()
    }
    
    result = supabase_request("POST", "users", data)
    return result is not None


def show_login_page():
    """Mostra la pagina di login"""
    st.markdown("""
    <style>
        .login-container {
            max-width: 400px;
            margin: 100px auto;
            padding: 40px;
            background: linear-gradient(135deg, #1e3a5f 0%, #2d5a87 100%);
            border-radius: 20px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.3);
        }
        .login-title {
            text-align: center;
            color: white;
            font-size: 2rem;
            margin-bottom: 30px;
        }
    </style>
    """, unsafe_allow_html=True)
    
    col1, col2, col3 = st.columns([1, 2, 1])
    
    with col2:
        st.markdown("## 🔐 Forex Macro Analyst")
        st.markdown("### Login")
        
        with st.form("login_form"):
            username = st.text_input("👤 Username", placeholder="Inserisci username")
            password = st.text_input("🔑 Password", type="password", placeholder="Inserisci password")
            
            col_btn1, col_btn2 = st.columns(2)
            with col_btn1:
                submit = st.form_submit_button("🚀 Accedi", use_container_width=True, type="primary")
            
            if submit:
                if username and password:
                    user = authenticate_user(username, password)
                    if user:
                        st.session_state['authenticated'] = True
                        st.session_state['user'] = user
                        st.session_state['user_id'] = user.get('id')
                        st.success("✅ Accesso effettuato!")
                        st.rerun()
                    else:
                        st.error("❌ Credenziali non valide")
                else:
                    st.warning("⚠️ Inserisci username e password")
        
        st.markdown("---")
        st.caption("💡 Contatta l'amministratore per ottenere le credenziali")
        
        if not SUPABASE_ENABLED:
            st.info("🔧 Modalità locale: usa MBARRECA/mbarreca")


def logout():
    """Effettua il logout"""
    for key in ['authenticated', 'user', 'user_id', 'current_analysis']:
        if key in st.session_state:
            del st.session_state[key]
    st.rerun()


# ============================================================================
# FUNZIONI DATABASE ANALISI (aggiornate per multi-utente)
# ============================================================================

def save_analysis(analysis: dict, user_id: str, analysis_type: str, options_selected: dict) -> bool:
    """
    Salva un'analisi su Supabase con informazioni utente e tipo.
    
    Args:
        analysis: Dati dell'analisi
        user_id: ID utente
        analysis_type: Tipo di analisi (es: "full", "macro_only", "news_only", "custom")
        options_selected: Dict con le opzioni selezionate
    """
    try:
        now = get_italy_now()
        datetime_str = now.strftime("%Y-%m-%d_%H-%M-%S")
        
        analysis["analysis_datetime"] = datetime_str
        
        if SUPABASE_ENABLED:
            data = {
                "analysis_datetime": datetime_str,
                "user_id": user_id,
                "analysis_type": analysis_type,
                "options_selected": options_selected,
                "data": analysis
            }
            result = supabase_request("POST", "analyses", data)
            if result is None:
                st.error("Errore Supabase: impossibile salvare l'analisi")
                return False
            return True
        else:
            # Fallback locale
            filename = DATA_FOLDER / f"analysis_{user_id}_{datetime_str}.json"
            save_data = {
                "user_id": user_id,
                "analysis_type": analysis_type,
                "options_selected": options_selected,
                "data": analysis
            }
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(save_data, f, ensure_ascii=False, indent=2)
            return True
    except Exception as e:
        st.error(f"Errore salvataggio: {e}")
        return False


def load_analysis(datetime_str: str, user_id: str) -> dict | None:
    """Carica un'analisi da Supabase per un utente specifico"""
    try:
        if SUPABASE_ENABLED:
            result = supabase_request(
                "GET", 
                f"analyses?analysis_datetime=eq.{datetime_str}&user_id=eq.{user_id}"
            )
            if result and len(result) > 0:
                return result[0]
        else:
            filename = DATA_FOLDER / f"analysis_{user_id}_{datetime_str}.json"
            if filename.exists():
                with open(filename, "r", encoding="utf-8") as f:
                    return json.load(f)
    except Exception as e:
        st.error(f"Errore caricamento: {e}")
    return None


def delete_analysis(datetime_str: str, user_id: str) -> bool:
    """Cancella un'analisi da Supabase. Gestisce sia analisi con user_id che legacy."""
    try:
        if SUPABASE_ENABLED:
            # Prima prova a cancellare con user_id
            result = supabase_request(
                "DELETE", 
                f"analyses?analysis_datetime=eq.{datetime_str}&user_id=eq.{user_id}"
            )
            if result is not None:
                return True
            
            # Se non trovata, prova a cancellare analisi legacy (user_id NULL)
            result = supabase_request(
                "DELETE", 
                f"analyses?analysis_datetime=eq.{datetime_str}&user_id=is.null"
            )
            return result is not None
        else:
            # Locale: prova entrambi i formati di filename
            filename_new = DATA_FOLDER / f"analysis_{user_id}_{datetime_str}.json"
            filename_legacy = DATA_FOLDER / f"analysis_{datetime_str}.json"
            
            if filename_new.exists():
                filename_new.unlink()
                return True
            elif filename_legacy.exists():
                filename_legacy.unlink()
                return True
    except Exception as e:
        st.error(f"Errore cancellazione: {e}")
    return False


def get_user_analyses(user_id: str, limit: int = 50) -> list:
    """
    Restituisce tutte le analisi di un utente (più recente prima).
    Include anche analisi legacy senza user_id per retrocompatibilità.
    """
    analyses = []
    
    if SUPABASE_ENABLED:
        # Query che recupera sia analisi dell'utente che quelle senza user_id (legacy)
        result = supabase_request(
            "GET", 
            f"analyses?or=(user_id.eq.{user_id},user_id.is.null)&order=analysis_datetime.desc&limit={limit}"
        )
        if result:
            analyses = result
    else:
        # Locale: cerca sia file con user_id che senza
        for file in DATA_FOLDER.glob(f"analysis_{user_id}_*.json"):
            try:
                with open(file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    analyses.append(data)
            except:
                pass
        # Cerca anche file vecchio formato (senza user_id nel nome)
        for file in DATA_FOLDER.glob("analysis_2*.json"):
            try:
                with open(file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    analyses.append(data)
            except:
                pass
        analyses = sorted(analyses, key=lambda x: x.get("data", {}).get("analysis_datetime", "") or x.get("analysis_datetime", ""), reverse=True)
    
    return analyses[:limit]


def format_datetime_display(datetime_str: str) -> str:
    """Formatta datetime per visualizzazione: 28/12/2025 14:30 (senza secondi)"""
    try:
        if "_" in datetime_str:
            date_part, time_part = datetime_str.split("_")
            date_obj = datetime.strptime(date_part, "%Y-%m-%d")
            # Prendi solo ore e minuti (rimuovi i secondi)
            time_parts = time_part.split("-")
            time_formatted = f"{time_parts[0]}:{time_parts[1]}"  # Solo HH:MM
            return f"{date_obj.strftime('%d/%m/%Y')} {time_formatted}"
        else:
            date_obj = datetime.strptime(datetime_str, "%Y-%m-%d")
            return date_obj.strftime('%d/%m/%Y')
    except:
        return datetime_str


def get_analysis_type_label(analysis_type: str) -> str:
    """Restituisce etichetta leggibile per tipo analisi"""
    labels = {
        "full": "🔄 Completa",
        "macro_only": "📊 Solo Macro",
        "news_only": "📰 Solo Notizie",
        "links_only": "📎 Solo Link",
        "macro_news": "📊📰 Macro + Notizie",
        "macro_links": "📊📎 Macro + Link",
        "news_links": "📰📎 Notizie + Link",
        "claude_only": "🤖 Solo Claude",
        "custom": "⚙️ Personalizzata"
    }
    return labels.get(analysis_type, "📋 Analisi")


# ============================================================================
# LISTA VALUTE E COPPIE FOREX
# ============================================================================

CURRENCIES = {
    "EUR": {"name": "Euro", "central_bank": "ECB", "type": "semi-cyclical"},
    "USD": {"name": "US Dollar", "central_bank": "Federal Reserve", "type": "safe-haven"},
    "GBP": {"name": "British Pound", "central_bank": "Bank of England", "type": "cyclical"},
    "JPY": {"name": "Japanese Yen", "central_bank": "Bank of Japan", "type": "safe-haven"},
    "CHF": {"name": "Swiss Franc", "central_bank": "SNB", "type": "safe-haven"},
    "AUD": {"name": "Australian Dollar", "central_bank": "RBA", "type": "commodity/cyclical"},
    "CAD": {"name": "Canadian Dollar", "central_bank": "Bank of Canada", "type": "commodity/cyclical"},
}

FOREX_PAIRS = [
    "USD/JPY", "GBP/JPY", "AUD/JPY", "EUR/JPY", "CAD/JPY",
    "AUD/USD", "AUD/CAD", "GBP/AUD", "EUR/AUD", "EUR/CAD",
    "GBP/CAD", "USD/CHF", "EUR/CHF", "GBP/CHF", "CAD/CHF",
    "AUD/CHF", "EUR/USD", "EUR/GBP", "GBP/USD",
]


# ============================================================================
# CONFIGURAZIONE PMI - INVESTING.COM IDs
# ============================================================================

PMI_CONFIG = {
    "USD": {
        "manufacturing": {"id": 173, "name": "ism-manufacturing-pmi", "label": "ISM Manufacturing"},
        "services": {"id": 176, "name": "ism-non-manufacturing-pmi", "label": "ISM Services"}
    },
    "EUR": {
        "manufacturing": {"id": 201, "name": "manufacturing-pmi", "label": "Manufacturing PMI"},
        "services": {"id": 272, "name": "services-pmi", "label": "Services PMI"}
    },
    "GBP": {
        "manufacturing": {"id": 204, "name": "manufacturing-pmi", "label": "Manufacturing PMI"},
        "services": {"id": 274, "name": "services-pmi", "label": "Services PMI"}
    },
    "JPY": {
        "manufacturing": {"id": 202, "name": "manufacturing-pmi", "label": "Manufacturing PMI"},
        "services": {"id": 1912, "name": "services-pmi", "label": "Services PMI"}
    },
    "CHF": {
        "manufacturing": {"id": 278, "name": "procure.ch-pmi", "label": "procure.ch PMI"},
        "services": None  # CHF Services PMI non disponibile su Investing.com
    },
    "AUD": {
        "manufacturing": {"id": 1838, "name": "manufacturing-pmi", "label": "Manufacturing PMI"},
        "services": {"id": 1839, "name": "services-pmi", "label": "Services PMI"}
    },
    "CAD": {
        "manufacturing": {"id": 1029, "name": "manufacturing-pmi", "label": "Manufacturing PMI"},
        "services": {"id": 2265, "name": "services-pmi", "label": "Services PMI"}
    }
}


def fetch_pmi_from_investing(currency: str, pmi_type: str, max_retries: int = 5) -> dict:
    """
    Scarica i dati PMI da Investing.com per una valuta e tipo specifico.
    
    Args:
        currency: Codice valuta (USD, EUR, GBP, JPY, CHF, AUD, CAD)
        pmi_type: "manufacturing" o "services"
        max_retries: Numero massimo di tentativi
    
    Returns:
        dict con: current, previous, delta, date, source
    """
    import time
    import random
    
    config = PMI_CONFIG.get(currency, {}).get(pmi_type)
    
    if config is None:
        return {"current": None, "previous": None, "delta": None, "date": None, "source": "N/A"}
    
    url = f"https://www.investing.com/economic-calendar/{config['name']}-{config['id']}"
    
    for attempt in range(max_retries):
        try:
            headers = {
                'User-Agent': f'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{random.randint(100, 120)}.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
            }
            
            # Prova con cloudscraper se disponibile
            try:
                import cloudscraper
                scraper = cloudscraper.create_scraper(
                    browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False}
                )
                response = scraper.get(url, timeout=25)
            except ImportError:
                response = requests.get(url, headers=headers, timeout=25)
            
            if response.status_code != 200:
                if attempt < max_retries - 1:
                    time.sleep(2 + attempt * 2)
                    continue
                return {"current": None, "previous": None, "delta": None, "date": None, "source": url, "error": f"HTTP {response.status_code}"}
            
            html = response.text
            
            # Verifica contenuto valido
            if len(html) < 5000 or "Actual" not in html:
                if attempt < max_retries - 1:
                    time.sleep(2 + attempt * 2)
                    continue
            
            current_value = None
            previous_value = None
            release_date = None
            
            # ===== METODO 1: Pattern per "Latest Release" block =====
            actual_patterns = [
                r'Actual\s*\n+\s*([0-9]+\.?[0-9]*)',
                r'Actual\s+([0-9]+\.?[0-9]*)',
                r'Actual[:\s]*</span>\s*<span[^>]*>([0-9]+\.?[0-9]*)',
                r'"actual"\s*:\s*"?([0-9]+\.?[0-9]*)"?',
                r'Actual.*?([0-9]{2}\.[0-9])',
            ]
            
            for pattern in actual_patterns:
                match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
                if match:
                    try:
                        val = float(match.group(1))
                        if 30 <= val <= 70:
                            current_value = val
                            break
                    except:
                        pass
            
            # Cerca Previous
            previous_patterns = [
                r'Previous\s*\n+\s*([0-9]+\.?[0-9]*)',
                r'Previous\s+([0-9]+\.?[0-9]*)',
                r'Previous[:\s]*</span>\s*<span[^>]*>([0-9]+\.?[0-9]*)',
                r'"previous"\s*:\s*"?([0-9]+\.?[0-9]*)"?',
                r'Previous.*?([0-9]{2}\.[0-9])',
            ]
            
            for pattern in previous_patterns:
                match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
                if match:
                    try:
                        val = float(match.group(1))
                        if 30 <= val <= 70:
                            previous_value = val
                            break
                    except:
                        pass
            
            # ===== METODO 2: Tabella storica =====
            if current_value is None or previous_value is None:
                table_pattern = r'\|\s*([A-Za-z]{3}\s+\d{1,2},\s*\d{4})[^|]*\|\s*\d{1,2}:\d{2}\s*\|\s*([0-9]+\.?[0-9]*)\s*\|\s*[0-9.]*\s*\|\s*([0-9]+\.?[0-9]*)\s*\|'
                matches = re.findall(table_pattern, html)
                if matches:
                    try:
                        release_date = matches[0][0]
                        if current_value is None:
                            val = float(matches[0][1])
                            if 30 <= val <= 70:
                                current_value = val
                        if previous_value is None:
                            val = float(matches[0][2])
                            if 30 <= val <= 70:
                                previous_value = val
                    except:
                        pass
            
            # Calcola delta
            delta = None
            if current_value is not None and previous_value is not None:
                delta = round(current_value - previous_value, 1)
            
            # Se abbiamo trovato il valore current, restituiamo
            if current_value is not None:
                return {
                    "current": current_value,
                    "previous": previous_value,
                    "delta": delta,
                    "date": release_date,
                    "source": url,
                    "label": config['label']
                }
            
            # Se non abbiamo trovato dati, retry
            if attempt < max_retries - 1:
                time.sleep(2 + attempt * 2)
                continue
            
            # Ultimo tentativo fallito
            return {
                "current": current_value,
                "previous": previous_value,
                "delta": delta,
                "date": release_date,
                "source": url,
                "label": config['label']
            }
            
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2 + attempt * 2)
                continue
            return {"current": None, "previous": None, "delta": None, "date": None, "source": url, "error": str(e)}
    
    return {"current": None, "previous": None, "delta": None, "date": None, "source": url, "error": "Max retries exceeded"}


def fetch_chf_services_pmi_tradingeconomics() -> dict:
    """
    Scarica CHF Services PMI da TradingEconomics (unica fonte disponibile).
    
    Returns:
        dict con: current, previous, delta, date, source
    """
    import time
    import random
    
    url = "https://tradingeconomics.com/switzerland/services-pmi"
    
    for attempt in range(5):  # Max 5 tentativi
        try:
            headers = {
                'User-Agent': f'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{random.randint(100, 120)}.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
            }
            
            # Prova con cloudscraper
            try:
                import cloudscraper
                scraper = cloudscraper.create_scraper(
                    browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False}
                )
                response = scraper.get(url, timeout=25)
            except ImportError:
                response = requests.get(url, headers=headers, timeout=25)
            
            if response.status_code != 200:
                if attempt < 4:
                    time.sleep(2 + attempt * 2)
                    continue
                return {"current": None, "previous": None, "delta": None, "date": None, "source": url, "error": f"HTTP {response.status_code}"}
            
            html = response.text
            
            current_value = None
            previous_value = None
            
            # ===== Pattern per TradingEconomics =====
            
            # Pattern per Current (valore principale grande nella pagina)
            current_patterns = [
                r'id="p"[^>]*>([0-9]+\.?[0-9]*)<',  # id="p" è il valore principale
                r'"Last"\s*:\s*"?([0-9]+\.?[0-9]*)"?',  # JSON
                r'Switzerland Services PMI[^0-9]*([0-9]{2}\.[0-9])',  # Titolo + valore
                r'<span[^>]*class="[^"]*value[^"]*"[^>]*>([0-9]{2}\.[0-9])</span>',  # Span con classe value
            ]
            
            for pattern in current_patterns:
                match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
                if match:
                    try:
                        val = float(match.group(1))
                        if 30 <= val <= 70:
                            current_value = val
                            break
                    except:
                        pass
            
            # Pattern per Previous
            previous_patterns = [
                r'Previous[:\s]*</td>\s*<td[^>]*>([0-9]+\.?[0-9]*)',  # Tabella
                r'"Previous"\s*:\s*"?([0-9]+\.?[0-9]*)"?',  # JSON
                r'Previous\s*\n+\s*([0-9]+\.?[0-9]*)',  # Newline
                r'Previous\s+([0-9]+\.?[0-9]*)',  # Spazio
                r'>Previous<[^>]*>[^0-9]*([0-9]{2}\.[0-9])',  # Tag Previous
                r'Previous.*?([0-9]{2}\.[0-9])',  # Fallback generico
            ]
            
            for pattern in previous_patterns:
                match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
                if match:
                    try:
                        val = float(match.group(1))
                        if 30 <= val <= 70:
                            previous_value = val
                            break
                    except:
                        pass
            
            # Fallback: cerca tutti i numeri PMI-like nella pagina
            if current_value is None or previous_value is None:
                # Cerca numeri nel range 40-60 (tipico PMI)
                values = re.findall(r'>([0-9]{2}\.[0-9])<', html)
                pmi_values = []
                for v in values:
                    try:
                        val = float(v)
                        if 35 <= val <= 65:
                            pmi_values.append(val)
                    except:
                        pass
                
                # Rimuovi duplicati mantenendo l'ordine
                seen = set()
                unique_pmi = []
                for v in pmi_values:
                    if v not in seen:
                        seen.add(v)
                        unique_pmi.append(v)
                
                if len(unique_pmi) >= 1 and current_value is None:
                    current_value = unique_pmi[0]
                if len(unique_pmi) >= 2 and previous_value is None:
                    previous_value = unique_pmi[1]
            
            delta = None
            if current_value is not None and previous_value is not None:
                delta = round(current_value - previous_value, 1)
            
            # Se abbiamo almeno current, restituiamo
            if current_value is not None:
                return {
                    "current": current_value,
                    "previous": previous_value,
                    "delta": delta,
                    "date": None,
                    "source": url,
                    "label": "Services PMI"
                }
            
            # Retry
            if attempt < 4:
                time.sleep(2 + attempt * 2)
                continue
            
            return {
                "current": current_value,
                "previous": previous_value,
                "delta": delta,
                "date": None,
                "source": url,
                "label": "Services PMI"
            }
            
        except Exception as e:
            if attempt < 4:
                time.sleep(2 + attempt * 2)
                continue
            return {"current": None, "previous": None, "delta": None, "date": None, "source": url, "error": str(e)}
    
    return {"current": None, "previous": None, "delta": None, "date": None, "source": url, "error": "Max retries exceeded"}


def fetch_all_pmi_data() -> dict:
    """
    Recupera tutti i dati PMI per le 7 valute.
    
    Returns:
        dict con struttura:
        {
            "USD": {
                "manufacturing": {"current": 47.9, "previous": 48.2, "delta": -0.3, ...},
                "services": {"current": 54.4, "previous": 52.6, "delta": 1.8, ...}
            },
            ...
        }
    """
    import time
    
    pmi_data = {}
    
    for currency in PMI_CONFIG.keys():
        pmi_data[currency] = {}
        
        # Manufacturing PMI
        pmi_data[currency]["manufacturing"] = fetch_pmi_from_investing(currency, "manufacturing")
        
        # Delay per evitare rate limiting (1.5 secondi tra richieste)
        time.sleep(1.5)
        
        # Services PMI
        if currency == "CHF":
            # CHF Services da TradingEconomics
            pmi_data[currency]["services"] = fetch_chf_services_pmi_tradingeconomics()
        else:
            pmi_data[currency]["services"] = fetch_pmi_from_investing(currency, "services")
        
        # Delay tra valute
        time.sleep(1.0)
    
    return pmi_data


def get_pmi_interpretation(manuf_delta: float, services_delta: float) -> tuple:
    """
    Restituisce interpretazione e trend per i PMI.
    
    Returns:
        (trend_text, interpretation)
    """
    if manuf_delta is None:
        manuf_delta = 0
    if services_delta is None:
        services_delta = 0
    
    # Determina trend per ciascun settore con testo chiaro
    manuf_trend = "↑" if manuf_delta > 0.1 else "↓" if manuf_delta < -0.1 else "→"
    services_trend = "↑" if services_delta > 0.1 else "↓" if services_delta < -0.1 else "→"
    
    # Testo completo e leggibile
    trend_text = f"Manuf.{manuf_trend} Serv.{services_trend}"
    
    # Interpretazione
    if manuf_delta > 0.1 and services_delta > 0.1:
        interpretation = "Bullish"
    elif manuf_delta < -0.1 and services_delta < -0.1:
        interpretation = "Bearish"
    elif manuf_delta > 0.1 or services_delta > 0.1:
        interpretation = "Misto+"
    elif manuf_delta < -0.1 or services_delta < -0.1:
        interpretation = "Misto-"
    else:
        interpretation = "Neutro"
    
    return trend_text, interpretation


# ============================================================================
# SYSTEM PROMPT PER ANALISI GLOBALE
# ============================================================================

SYSTEM_PROMPT_GLOBAL = """Sei un analista macroeconomico forex senior. Devi analizzare 19 coppie forex separatamente.

## ⚠️ REGOLA CRITICA: USA I DATI FORNITI, NON CONOSCENZE OBSOLETE!

Le tue conoscenze potrebbero essere OBSOLETE. Devi:
1. **LEGGERE ATTENTAMENTE** tutti i dati macro, PMI e notizie web forniti
2. **BASARTI SOLO** sulle informazioni fornite nel prompt
3. **NON ASSUMERE** che le banche centrali mantengano politiche passate
4. **VERIFICARE** sempre nelle notizie web prima di scrivere

## REGOLA FONDAMENTALE: CONFRONTO DIRETTO TRA VALUTE

Devi fare **19 ANALISI INDIPENDENTI**, una per ogni coppia forex.
Per ogni coppia (es: EUR/USD) devi:
1. **CONFRONTARE DIRETTAMENTE** le due valute su ogni parametro
2. Assegnare punteggi basati su **CHI HA IL VANTAGGIO RELATIVO**
3. I punteggi sono RELATIVI al confronto, NON assoluti

⚠️ LA STESSA VALUTA PUÒ AVERE PUNTEGGI DIVERSI IN COPPIE DIVERSE!

## LINGUA: TUTTO IN ITALIANO

## STRUTTURA JSON OBBLIGATORIA
Rispondi SOLO con un JSON valido, senza markdown, senza ```json, senza commenti.

## ═══════════════════════════════════════════════════════════════════
## SISTEMA DI SCORING - 7 PARAMETRI CON CRITERI OGGETTIVI
## ═══════════════════════════════════════════════════════════════════

### 1️⃣ TASSI ATTUALI [-1 a +1]
**Logica:** Il differenziale di tasso (carry) attrae flussi di capitale verso la valuta con rendimento maggiore.

| Spread (Base - Quote) | Score Base | Score Quote |
|-----------------------|------------|-------------|
| ≥ +150 bp             | +1         | -1          |
| +50 bp a +149 bp      | +1         | 0           |
| -49 bp a +49 bp       | 0          | 0           |
| -50 bp a -149 bp      | 0          | +1          |
| ≤ -150 bp             | -1         | +1          |

**Esempio:** EUR (2.15%) vs USD (3.75%) → Spread = -160bp → EUR: -1, USD: +1

---

### 2️⃣ ASPETTATIVE TASSI [-2 a +2] ⭐ PESO DOPPIO
**Logica:** Il mercato guarda avanti. Le aspettative sui tassi futuri sono più importanti dei tassi attuali.

**Criteri per singola valuta:**
| Scenario | Score |
|----------|-------|
| BC hawkish con rialzi attesi O prob. taglio <20% nei prossimi 3 mesi | +2 |
| BC neutrale/leggermente hawkish O prob. taglio 20-40% | +1 |
| BC neutrale O incertezza elevata | 0 |
| BC leggermente dovish O prob. taglio 60-80% | -1 |
| BC molto dovish con tagli attesi O prob. taglio >80% | -2 |

**Confronto:** Assegna score a ciascuna valuta, poi il vantaggio relativo determina i punti.

⚠️ USA SOLO LE NOTIZIE WEB FORNITE per determinare stance e probabilità!

---

### 3️⃣ INFLAZIONE [-1 a +1]
**Logica:** Non conta solo il livello, ma quanto l'inflazione SUPPORTA la politica monetaria.

| Scenario | Score |
|----------|-------|
| Inflazione 1.5%-2.5% + trend stabile/discesa | +1 (situazione ideale) |
| Inflazione 2.5%-3.5% + trend incerto | 0 (gestibile) |
| Inflazione >3.5% + trend in salita | -1 (BC sotto pressione) |
| Inflazione <1.5% + trend in discesa | -1 (rischio deflazione) |

**Confronto DIRETTO:** Chi ha situazione inflattiva più favorevole per la propria BC?

---

### 4️⃣ CRESCITA/PIL [-1 a +1] - LAGGING INDICATOR
**Logica:** Il PIL da solo non basta. Va contestualizzato con inflazione e sostenibilità.

| Scenario | Score |
|----------|-------|
| PIL >2% + inflazione controllata + trend stabile | +1 (crescita sana) |
| PIL 1%-2% + situazione bilanciata | 0 (crescita moderata) |
| PIL <1% O trend in forte decelerazione | -1 (rischio recessione) |
| PIL alto MA inflazione fuori controllo | 0 (NON +1! crescita non sostenibile) |
| STAGFLAZIONE (PIL basso + inflazione alta) | -1 (scenario peggiore) |

**Confronto DIRETTO:**
- Differenziale PIL > 1.5pp → vantaggio netto per chi ha PIL maggiore
- Differenziale PIL 0.5-1.5pp → vantaggio leggero
- Differenziale PIL < 0.5pp → situazione simile, considera altri fattori

---

### 5️⃣ PMI [-1 a +1] - LEADING INDICATOR
**Logica:** PMI anticipa il PIL di 3-6 mesi. Considera livello (>50 = espansione) E direzione (delta).

**⚠️ PESI DIVERSI PER STRUTTURA ECONOMICA:**
| Valuta | Peso Services | Peso Manufacturing | Motivo |
|--------|---------------|-------------------|--------|
| USD    | 70%           | 30%               | Economia basata su servizi |
| EUR    | 50%           | 50%               | Mista (Germania manifattura + resto servizi) |
| GBP    | 70%           | 30%               | Finanza e servizi professionali |
| JPY    | 40%           | 60%               | Export e manifattura (auto, elettronica) |
| CHF    | 60%           | 40%               | Finanza + pharma |
| AUD    | 50%           | 50%               | Mining + servizi domestici |
| CAD    | 50%           | 50%               | Energia + servizi |

**Criteri di valutazione:**
| Condizione | Valutazione |
|------------|-------------|
| PMI ponderato ≥52 + Delta positivo | Forte espansione (+1) |
| PMI ponderato 50-52 + Delta positivo | Espansione moderata (+1) |
| PMI ponderato 50-52 + Delta negativo | Rallentamento (0) |
| PMI ponderato 48-50 + Delta positivo | Contrazione in recupero (0) |
| PMI ponderato 48-50 + Delta negativo | Contrazione in peggioramento (-1) |
| PMI ponderato <48 | Contrazione significativa (-1) |

**Confronto DIRETTO:** Chi ha momentum economico migliore considerando i pesi settoriali?

---

### 6️⃣ RISK SENTIMENT [-1 a +1]
**Logica:** In risk-off, capitali verso safe-haven. In risk-on, verso valute cicliche.

**Classificazione valute:**
- **Safe-haven:** USD, JPY, CHF
- **Cicliche/Commodity:** AUD, CAD, GBP
- **Semi-cicliche:** EUR

**Determinazione regime mercato:**
- VIX > 25 O equity in forte calo O tensioni geopolitiche acute → **Risk-OFF**
- VIX < 18 E equity positivo E sentiment ottimista → **Risk-ON**
- Altrimenti → **Neutro**

**Matrice punteggi in base al TIPO di coppia:**
| Tipo Coppia | Risk-OFF | Neutro | Risk-ON |
|-------------|----------|--------|---------|
| Ciclica vs Safe-haven (es: AUD/JPY) | Ciclica: -1, Safe: +1 | 0, 0 | Ciclica: +1, Safe: -1 |
| Semi-ciclica vs Safe-haven (es: EUR/USD) | Semi: -1, Safe: +1 | 0, 0 | Semi: +1, Safe: -1 |
| Entrambe cicliche (es: AUD/CAD) | 0, 0 | 0, 0 | 0, 0 |
| Entrambe safe-haven (es: USD/JPY) | Analisi specifica | 0, 0 | Analisi specifica |

---

### 7️⃣ BILANCIA/FISCALE [-1 a +1]
**Logica:** Importante nel lungo termine, meno nel breve. Assegnare peso solo se notizie specifiche.

| Scenario | Score |
|----------|-------|
| Current Account surplus >2% PIL + debito gestibile | +1 |
| Situazione nella media O nessuna notizia rilevante | 0 |
| Deficit gemelli elevati O crisi debito in corso | -1 |

**Regola pratica:** Se non ci sono notizie su crisi fiscali/debito, assegnare 0 a entrambe le valute.

## ═══════════════════════════════════════════════════════════════════
## RANGE TOTALI
## ═══════════════════════════════════════════════════════════════════

- **Aspettative Tassi**: da -2 a +2 (peso doppio)
- **Altri 6 parametri**: da -1 a +1
- **score_base**: da -8 a +8
- **score_quote**: da -8 a +8
- **differenziale**: da -16 a +16

## ═══════════════════════════════════════════════════════════════════
## MOTIVAZIONI DETTAGLIATE (OBBLIGATORIO!)
## ═══════════════════════════════════════════════════════════════════

Le motivazioni devono essere ESPLICATIVE e COMPLETE:
- Citare i VALORI NUMERICI specifici (tassi %, inflazione %, PIL %, PMI)
- Citare i DELTA dei PMI e il peso settoriale applicato
- Citare le ASPETTATIVE delle BC DALLE NOTIZIE WEB
- Spiegare il RAGIONAMENTO COMPARATIVO dietro il punteggio

ESEMPIO CORRETTO:
"EUR: PIL 0.7% vs USD: PIL 2.1% - differenziale di 1.4pp favorevole a USD. EUR in stagnazione con rischi recessione in Germania, mentre USA mostra crescita sostenibile con inflazione in calo. Vantaggio netto per USD nel confronto."

ESEMPIO SBAGLIATO:
"EUR crescita debole, USD crescita forte"

## FORMATO OUTPUT JSON:
{
    "analysis_date": "YYYY-MM-DD",
    "summary": "Breve riassunto del contesto macro globale in italiano",
    "pair_analysis": {
        "EUR/USD": {
            "bias": "bullish/bearish/neutral",
            "strength": 1-5,
            "summary": "Spiegazione del bias basata sul CONFRONTO DIRETTO con riferimenti numerici",
            "key_drivers": ["driver1", "driver2"],
            "score_base": 3,
            "score_quote": -3,
            "current_price": "1.0850",
            "price_scenarios": {
                "base_range": "1.0750 - 1.0950",
                "base_strong": "1.0950 - 1.1100",
                "quote_strong": "1.0600 - 1.0750"
            },
            "scores": {
                "tassi_attuali": {
                    "base": -1, "quote": 1,
                    "motivation_base": "EUR tasso BCE 2.15% vs USD Fed 3.75% - spread -160bp sfavorevole per EUR. Carry trade favorisce USD.",
                    "motivation_quote": "USD tasso Fed 3.75% vs EUR BCE 2.15% - spread +160bp favorevole. Rendimenti più attraenti per capitali."
                },
                "aspettative_tassi": {
                    "base": -2, "quote": 2,
                    "motivation_base": "BCE dovish: taglio a dicembre, mercati prezzano 80% prob. ulteriori tagli. Lagarde conferma stance accomodante.",
                    "motivation_quote": "Fed hawkish: Powell segnala hold prolungato, solo 25% prob. taglio prossimi 3 mesi. Dot plot indica 2 soli tagli nel 2025."
                },
                "inflazione": {
                    "base": 1, "quote": 0,
                    "motivation_base": "Inflazione EUR 2.14% vicina al target 2%, trend stabile. BCE ha margine di manovra.",
                    "motivation_quote": "Inflazione USA 2.74% sopra target, core PCE sticky. Situazione gestibile ma non ideale."
                },
                "crescita_pil": {
                    "base": -1, "quote": 1,
                    "motivation_base": "PIL EUR 0.7% vs PIL USA 2.1% - differenziale 1.4pp sfavorevole. Germania in stagnazione, rischi recessione.",
                    "motivation_quote": "PIL USA 2.1% con inflazione in calo - crescita sana e sostenibile. Mercato lavoro resiliente."
                },
                "pmi": {
                    "base": -1, "quote": 1,
                    "motivation_base": "PMI EUR pesato (50/50): Manuf 45.1 (Δ-1.2) + Services 51.4 (Δ-0.8) = 48.25. Contrazione manifatturiera, trend negativo.",
                    "motivation_quote": "PMI USA pesato (30/70): Manuf 49.3 (Δ+1.3) + Services 54.1 (Δ+2.1) = 52.66. Servizi forti, momentum positivo."
                },
                "risk_sentiment": {
                    "base": 0, "quote": 0,
                    "motivation_base": "Regime neutro (VIX 18-25). EUR semi-ciclica non beneficia né soffre particolarmente.",
                    "motivation_quote": "Regime neutro. USD safe-haven non attrae flussi risk-off significativi al momento."
                },
                "bilancia_fiscale": {
                    "base": 0, "quote": 0,
                    "motivation_base": "Nessuna notizia rilevante su crisi fiscale Eurozona. Situazione stabile.",
                    "motivation_quote": "Deficit USA elevato ma nessun impatto immediato su mercati. Situazione gestibile."
                }
            }
        }
    },
    "rate_outlook": {
        "USD": {
            "current_rate": "X.XX%",
            "next_meeting": "YYYY-MM-DD",
            "expectation": "hold/cut/hike",
            "probability": "XX%",
            "stance": "hawkish/dovish/neutral",
            "notes": "Spiegazione basata sulle notizie web"
        }
    },
    "risk_sentiment": "risk-on/risk-off/neutral",
    "events_calendar": []
}

## REGOLE CRITICHE FINALI:
- ⚠️ USA SOLO I DATI FORNITI (macro, PMI, notizie web)
- ⚠️ CONFRONTO DIRETTO tra le due valute su ogni parametro
- ⚠️ PMI: applica i PESI SETTORIALI corretti per ogni valuta
- ⚠️ PIL: contestualizza con inflazione (no punti per crescita non sostenibile)
- ⚠️ RISK SENTIMENT: dipende dal TIPO di coppia (safe-haven vs cicliche)
- score_base = SOMMA dei 7 punteggi "base"
- score_quote = SOMMA dei 7 punteggi "quote"
- differenziale = score_base - score_quote
"""


# ============================================================================
# FUNZIONI RICERCA E ANALISI
# ============================================================================

REQUIRED_INDICATORS = ["interest_rate", "inflation_rate", "gdp_growth", "unemployment"]

CURRENCY_TO_COUNTRY = {
    "EUR": "Euro Area / Eurozone / ECB",
    "USD": "United States / US / Federal Reserve",
    "GBP": "United Kingdom / UK / Bank of England",
    "JPY": "Japan / Bank of Japan",
    "CHF": "Switzerland / Swiss National Bank",
    "AUD": "Australia / Reserve Bank of Australia",
    "CAD": "Canada / Bank of Canada",
}


def fetch_macro_data() -> dict:
    """
    Recupera solo i dati macro da fonti gratuite (senza ricerche web).
    """
    api_key = API_NINJAS_KEY if API_NINJAS_ENABLED else ""
    
    try:
        fetcher = MacroDataFetcher(api_key)
        raw_data = fetcher.get_all_data()
        
        result = {}
        for currency, info in raw_data['data'].items():
            indicators = info['indicators']
            result[currency] = {
                'interest_rate': indicators.get('interest_rate', {}).get('value', 'N/A'),
                'inflation_rate': indicators.get('inflation', {}).get('value', 'N/A'),
                'gdp_growth': indicators.get('gdp_growth', {}).get('value', 'N/A'),
                'unemployment': indicators.get('unemployment', {}).get('value', 'N/A'),
            }
        
        return result
        
    except Exception as e:
        st.error(f"Errore nel recupero dati macro: {e}")
        # Fallback con dati di esempio
        return {
            'USD': {'interest_rate': 3.75, 'inflation_rate': 2.74, 'gdp_growth': 2.1, 'unemployment': 3.9},
            'EUR': {'interest_rate': 2.15, 'inflation_rate': 2.14, 'gdp_growth': 0.7, 'unemployment': 3.0},
            'GBP': {'interest_rate': 3.75, 'inflation_rate': 3.57, 'gdp_growth': 1.3, 'unemployment': 4.1},
            'JPY': {'interest_rate': 0.75, 'inflation_rate': 2.91, 'gdp_growth': 0.5, 'unemployment': 2.3},
            'CHF': {'interest_rate': 0.00, 'inflation_rate': 0.02, 'gdp_growth': 1.2, 'unemployment': 4.8},
            'AUD': {'interest_rate': 3.60, 'inflation_rate': 3.8, 'gdp_growth': 2.3, 'unemployment': 5.3},
            'CAD': {'interest_rate': 2.25, 'inflation_rate': 2.22, 'gdp_growth': 1.6, 'unemployment': 5.4},
        }


def search_web_news() -> tuple[str, dict]:
    """
    Esegue le ricerche web con DuckDuckGo.
    Restituisce: (testo completo per Claude, dizionario strutturato per riepilogo)
    """
    all_results = []
    structured_results = {
        "forex_factory": [],
        "rate_expectations": [],
        "meeting_calendar": [],
        "policy_comparison": [],
        "economic_outlook": [],
        "geopolitics": []
    }
    
    today = get_italy_now()
    current_year = today.year
    next_year = current_year + 1
    
    all_results.append(f"[DATE] Data odierna: {today.strftime('%d/%m/%Y')}")
    
    # =========================================================================
    # SEZIONE 0: FOREX FACTORY BREAKING NEWS
    # =========================================================================
    all_results.append(f"\n{'='*60}")
    all_results.append(f"[FOREX FACTORY - BREAKING NEWS]")
    all_results.append(f"{'='*60}")
    
    forex_factory_queries = [
        "site:forexfactory.com/news forex breaking news today",
        "site:forexfactory.com USD EUR GBP JPY news",
        "site:forexfactory.com central bank rate decision",
        "site:forexfactory.com forex market news this week",
    ]
    
    for query in forex_factory_queries:
        try:
            results = DDGS().text(query, max_results=8)
            for r in results:
                title = r.get('title', '')
                body = r.get('body', '')
                href = r.get('href', '')
                if any(kw in body.lower() for kw in ['dollar', 'euro', 'yen', 'pound', 'fed', 'ecb', 'boe', 'boj', 'rate', 'inflation', 'gdp', 'employment', 'tariff', 'trade']):
                    all_results.append(f"[FF-NEWS] {title}: {body[:500]} | URL: {href}")
                    structured_results["forex_factory"].append({
                        "title": title,
                        "body": body[:300],
                        "url": href
                    })
        except:
            pass
    
    # =========================================================================
    # SEZIONE 1: ASPETTATIVE TASSI
    # =========================================================================
    all_results.append(f"\n{'='*60}")
    all_results.append(f"[RATE EXPECTATIONS - SEZIONE CRUCIALE]")
    all_results.append(f"{'='*60}")
    
    rate_queries = {
        "USD": [
            f"Federal Reserve FOMC rate decision January February {current_year}",
            f"Fed funds rate forecast {current_year} CME FedWatch",
            f"Fed Powell hawkish dovish {current_year}",
            f"Fed rate cut hike probability {current_year}"
        ],
        "EUR": [
            f"ECB European Central Bank rate decision {current_year}",
            f"ECB Lagarde hawkish dovish {current_year}",
            f"ECB rate cut forecast {current_year}",
            f"eurozone interest rate outlook {current_year}"
        ],
        "GBP": [
            f"Bank of England BoE rate decision {current_year}",
            f"BoE MPC hawkish dovish {current_year}",
            f"UK interest rate forecast {current_year}",
            f"Bank of England rate cut hike {current_year}"
        ],
        "JPY": [
            f"Bank of Japan BOJ rate hike {current_year}",
            f"BOJ Ueda interest rate policy {current_year}",
            f"Japan interest rate forecast {current_year}",
            f"BOJ end negative rates policy normalization",
            f"Bank of Japan hawkish shift {current_year}"
        ],
        "CHF": [
            f"SNB Swiss National Bank rate decision {current_year}",
            f"SNB interest rate forecast {current_year}",
            f"Switzerland negative rates policy {current_year}"
        ],
        "AUD": [
            f"RBA Reserve Bank Australia rate decision {current_year}",
            f"RBA Bullock hawkish dovish {current_year}",
            f"Australia interest rate forecast {current_year}",
            f"RBA rate cut hike {current_year}"
        ],
        "CAD": [
            f"Bank of Canada BoC rate decision {current_year}",
            f"BoC Macklem hawkish dovish {current_year}",
            f"Canada interest rate forecast {current_year}",
            f"BoC rate cut hike {current_year}"
        ],
    }
    
    for currency, queries in rate_queries.items():
        for query in queries:
            try:
                results = DDGS().text(query, max_results=5)
                for r in results:
                    title = r.get('title', '')
                    body = r.get('body', '')
                    href = r.get('href', '')
                    all_results.append(f"[{currency}-RATE] {title}: {body[:400]} | URL: {href}")
                    structured_results["rate_expectations"].append({
                        "currency": currency,
                        "title": title,
                        "body": body[:250],
                        "url": href
                    })
            except:
                pass
    
    # =========================================================================
    # SEZIONE 2: CALENDARIO MEETING BC
    # =========================================================================
    all_results.append(f"\n{'='*60}")
    all_results.append(f"[CENTRAL BANK MEETING CALENDAR]")
    all_results.append(f"{'='*60}")
    
    # Query più specifiche
    calendar_queries = [
        f"FOMC meeting schedule {current_year}",
        f"ECB governing council meeting dates {current_year}",
        f"Bank of England MPC meeting dates {current_year}",
        f"Bank of Japan BOJ monetary policy meeting {current_year}",
        f"central bank meeting calendar {current_year}",
        f"Fed ECB BoE interest rate decision dates {current_year}",
    ]
    
    for query in calendar_queries:
        try:
            results = DDGS().text(query, max_results=3)
            for r in results:
                title = r.get('title', '')
                body = r.get('body', '')
                href = r.get('href', '')
                all_results.append(f"[CALENDAR] {title}: {body[:400]} | URL: {href}")
                structured_results["meeting_calendar"].append({
                    "title": title,
                    "body": body[:250],
                    "url": href
                })
        except:
            pass
    
    # =========================================================================
    # SEZIONE 3: CONFRONTO POLITICHE MONETARIE
    # =========================================================================
    all_results.append(f"\n{'='*60}")
    all_results.append(f"[MONETARY POLICY COMPARISON]")
    all_results.append(f"{'='*60}")
    
    # Query più specifiche
    comparison_queries = [
        f"Fed ECB interest rate comparison {current_year}",
        f"central bank monetary policy outlook {current_year}",
        f"hawkish dovish Fed ECB BoE BoJ {current_year}",
        f"interest rate divergence forex {current_year}",
        f"Fed vs ECB vs Bank of England rate policy {current_year}",
        f"central banks rate cuts hikes forecast {current_year}",
    ]
    
    for query in comparison_queries:
        try:
            results = DDGS().text(query, max_results=4)
            for r in results:
                title = r.get('title', '')
                body = r.get('body', '')
                href = r.get('href', '')
                all_results.append(f"[COMPARE] {title}: {body[:450]} | URL: {href}")
                structured_results["policy_comparison"].append({
                    "title": title,
                    "body": body[:250],
                    "url": href
                })
        except:
            pass
    
    # =========================================================================
    # SEZIONE 4: GEOPOLITICA E RISK SENTIMENT
    # =========================================================================
    all_results.append(f"\n{'='*60}")
    all_results.append(f"[GEOPOLITICS & RISK SENTIMENT]")
    all_results.append(f"{'='*60}")
    
    geopolitics_queries = [
        "forex market risk sentiment today",
        "US China trade war tariffs impact forex",
        "geopolitical risk currency markets",
        f"stock market risk sentiment {current_year}",
        "safe haven currencies demand",
    ]
    
    for query in geopolitics_queries:
        try:
            results = DDGS().text(query, max_results=5)
            for r in results:
                title = r.get('title', '')
                body = r.get('body', '')
                href = r.get('href', '')
                all_results.append(f"[GEOPOLITICS] {title}: {body[:400]} | URL: {href}")
                structured_results["geopolitics"].append({
                    "title": title,
                    "body": body[:250],
                    "url": href
                })
        except:
            pass
    
    return "\n".join(all_results), structured_results


def fetch_additional_resources(urls: list) -> tuple[str, list]:
    """
    Fetcha e estrae il contenuto testuale da una lista di URL.
    Restituisce: (testo per Claude, lista strutturata per riepilogo)
    """
    if not urls:
        return "", []
    
    results = []
    structured = []
    
    results.append("\n" + "="*70)
    results.append("📎 RISORSE AGGIUNTIVE FORNITE DALL'UTENTE")
    results.append("="*70)
    
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    })
    
    for i, url in enumerate(urls[:10], 1):
        url = url.strip()
        if not url or not url.startswith(('http://', 'https://')):
            continue
        
        try:
            response = session.get(url, timeout=15, allow_redirects=True)
            
            if response.status_code == 200:
                html = response.text
                
                # Estrai titolo
                title_match = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
                title = title_match.group(1).strip() if title_match else url
                
                # Rimuovi script, style, nav, footer
                for tag in ['script', 'style', 'nav', 'footer', 'aside', 'header']:
                    html = re.sub(f'<{tag}[^>]*>.*?</{tag}>', '', html, flags=re.DOTALL | re.IGNORECASE)
                
                # Estrai testo
                text = re.sub(r'<[^>]+>', ' ', html)
                text = re.sub(r'\s+', ' ', text).strip()
                
                # Decodifica entità HTML
                text = text.replace('&nbsp;', ' ').replace('&amp;', '&')
                text = text.replace('&lt;', '<').replace('&gt;', '>')
                text = text.replace('&quot;', '"')
                
                # Limita lunghezza
                text = text[:4000]
                
                results.append(f"\n[FONTE {i}: {url}]")
                results.append(f"Titolo: {title}")
                results.append(f"Contenuto: {text}")
                
                structured.append({
                    "url": url,
                    "title": title,
                    "content_preview": text[:500],
                    "status": "success"
                })
            else:
                results.append(f"\n[FONTE {i}: {url}] - Errore: HTTP {response.status_code}")
                structured.append({
                    "url": url,
                    "title": "Errore",
                    "content_preview": f"HTTP {response.status_code}",
                    "status": "error"
                })
                
        except Exception as e:
            results.append(f"\n[FONTE {i}: {url}] - Errore: {str(e)[:100]}")
            structured.append({
                "url": url,
                "title": "Errore",
                "content_preview": str(e)[:100],
                "status": "error"
            })
    
    return "\n".join(results), structured


def analyze_with_claude(api_key: str, macro_data: dict = None, news_text: str = "", additional_text: str = "", pmi_data: dict = None) -> dict:
    """
    Esegue l'analisi con Claude AI.
    
    Args:
        api_key: Chiave API Anthropic
        macro_data: Dati macroeconomici (opzionale)
        news_text: Testo delle notizie web (opzionale)
        additional_text: Testo delle risorse aggiuntive (opzionale)
        pmi_data: Dati PMI per valuta (opzionale)
    """
    client = anthropic.Anthropic(api_key=api_key)
    
    pairs_list = ", ".join(FOREX_PAIRS)
    currencies_info = "\n".join([f"- {k}: {v['name']} ({v['central_bank']}) - Tipo: {v['type']}" 
                                  for k, v in CURRENCIES.items()])
    
    # Formatta i dati macro (se presenti)
    macro_section = ""
    if macro_data:
        macro_formatted = "\n\n".join([
            f"**{curr}:**\n" + "\n".join([f"  - {k}: {v}" for k, v in data.items()])
            for curr, data in macro_data.items()
        ])
        macro_section = f"""
## 📊 DATI NUMERICI DA FONTI UFFICIALI:
{macro_formatted}

---
"""
    
    # Sezione PMI (se presente)
    pmi_section = ""
    if pmi_data:
        pmi_lines = []
        for curr in ["USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD"]:
            if curr in pmi_data:
                manuf = pmi_data[curr].get("manufacturing", {})
                serv = pmi_data[curr].get("services", {})
                
                manuf_current = manuf.get("current", "N/A")
                manuf_delta = manuf.get("delta")
                manuf_delta_str = f"(Δ {manuf_delta:+.1f})" if manuf_delta is not None else ""
                
                serv_current = serv.get("current", "N/A")
                serv_delta = serv.get("delta")
                serv_delta_str = f"(Δ {serv_delta:+.1f})" if serv_delta is not None else ""
                
                label = "ISM" if curr == "USD" else "PMI"
                pmi_lines.append(f"**{curr}:** Manufacturing {label}: {manuf_current} {manuf_delta_str} | Services {label}: {serv_current} {serv_delta_str}")
        
        if pmi_lines:
            pmi_section = f"""
## 📈 DATI PMI (LEADING INDICATORS):
{chr(10).join(pmi_lines)}

⚠️ NOTA: PMI > 50 = espansione, PMI < 50 = contrazione. Il delta indica la variazione rispetto al mese precedente.

---
"""
    
    # Sezione notizie (se presente)
    news_section = ""
    if news_text:
        news_section = f"""
## 📰 NOTIZIE, OUTLOOK, ASPETTATIVE:
{news_text}

---
"""
    
    # Sezione risorse aggiuntive (se presente)
    additional_section = ""
    if additional_text:
        additional_section = f"""
{additional_text}

---
"""
    
    today = get_italy_now()
    
    user_prompt = f"""
Analizza TUTTE queste coppie forex: {pairs_list}

## ⚠️ DATA ODIERNA: {today.strftime('%Y-%m-%d')} ({today.strftime('%A, %d %B %Y')})

**Valute da analizzare:**
{currencies_info}

---

{macro_section}
{pmi_section}
{news_section}
{additional_section}

## ⭐ ISTRUZIONI:

1. **USA TUTTE LE INFORMAZIONI DISPONIBILI** per determinare il bias
2. **ASPETTATIVE > TASSI ATTUALI**: il mercato guarda AVANTI
3. **PMI sono LEADING indicators**: anticipano la crescita futura
4. **PIL è LAGGING indicator**: conferma la crescita passata
5. **analysis_date** = "{today.strftime('%Y-%m-%d')}"
6. **events_calendar** = []
7. Ogni **summary** deve spiegare PERCHÉ quel bias
8. Se presenti risorse aggiuntive, considerale con priorità ma INTEGRA con altri dati

Produci l'analisi COMPLETA in formato JSON.
Restituisci SOLO il JSON valido, senza markdown o testo aggiuntivo.
"""
    
    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=20000,
            messages=[{"role": "user", "content": user_prompt}],
            system=SYSTEM_PROMPT_GLOBAL
        )
        
        response_text = message.content[0].text
        
        # Pulisci JSON
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0]
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0]
        
        response_text = response_text.strip()
        start_idx = response_text.find('{')
        end_idx = response_text.rfind('}')
        
        if start_idx != -1 and end_idx != -1:
            response_text = response_text[start_idx:end_idx+1]
        
        analysis = json.loads(response_text)
        analysis["pairs_analyzed"] = FOREX_PAIRS
        analysis["currencies"] = list(CURRENCIES.keys())
        
        return analysis
        
    except json.JSONDecodeError as e:
        return {"error": f"Errore parsing JSON: {e}"}
    except Exception as e:
        return {"error": f"Errore API Claude: {e}"}


# ============================================================================
# FUNZIONI VISUALIZZAZIONE
# ============================================================================

def display_news_summary(news_structured: dict, links_structured: list = None):
    """Mostra il riepilogo delle notizie trovate con link"""
    
    st.markdown("### 📰 Notizie Web")
    
    # Forex Factory
    if news_structured.get("forex_factory"):
        with st.expander(f"🔴 FOREX FACTORY ({len(news_structured['forex_factory'])} news)", expanded=True):
            for item in news_structured["forex_factory"][:8]:
                url = item.get('url', '')
                if url:
                    st.markdown(f"• **[{item['title'][:70]}...]({url})**")
                else:
                    st.markdown(f"• **{item['title'][:70]}...**")
                st.caption(item['body'][:200] + "...")
    
    # Rate Expectations
    if news_structured.get("rate_expectations"):
        with st.expander(f"🏦 ASPETTATIVE TASSI ({len(news_structured['rate_expectations'])} risultati)"):
            by_currency = {}
            for item in news_structured["rate_expectations"]:
                curr = item.get("currency", "OTHER")
                if curr not in by_currency:
                    by_currency[curr] = []
                by_currency[curr].append(item)
            
            for curr, items in by_currency.items():
                st.markdown(f"**{curr}:**")
                for item in items[:3]:
                    url = item.get('url', '')
                    if url:
                        st.markdown(f"• [{item['title'][:55]}...]({url})")
                    else:
                        st.caption(f"• {item['title'][:55]}...")
    
    # Meeting Calendar
    if news_structured.get("meeting_calendar"):
        with st.expander(f"📅 CALENDARIO MEETING ({len(news_structured['meeting_calendar'])} risultati)"):
            for item in news_structured["meeting_calendar"][:6]:
                url = item.get('url', '')
                if url:
                    st.markdown(f"• [{item['title'][:70]}]({url})")
                else:
                    st.markdown(f"• {item['title'][:70]}")
    
    # Policy Comparison
    if news_structured.get("policy_comparison"):
        with st.expander(f"⚖️ CONFRONTO POLITICHE ({len(news_structured['policy_comparison'])} risultati)"):
            for item in news_structured["policy_comparison"][:5]:
                url = item.get('url', '')
                if url:
                    st.markdown(f"• [{item['title'][:70]}]({url})")
                else:
                    st.markdown(f"• {item['title'][:70]}")
    
    # Geopolitics
    if news_structured.get("geopolitics"):
        with st.expander(f"🌍 GEOPOLITICA ({len(news_structured['geopolitics'])} risultati)"):
            for item in news_structured["geopolitics"][:5]:
                url = item.get('url', '')
                if url:
                    st.markdown(f"• [{item['title'][:70]}]({url})")
                else:
                    st.markdown(f"• {item['title'][:70]}")
    
    # Link aggiuntivi processati
    if links_structured:
        with st.expander(f"📎 LINK AGGIUNTIVI ({len(links_structured)} URL processati)", expanded=True):
            for item in links_structured:
                status_icon = "✅" if item['status'] == 'success' else "❌"
                st.markdown(f"{status_icon} **[{item['title'][:50]}]({item['url']})**")
                if item['status'] == 'success':
                    st.caption(item['content_preview'][:200] + "...")


def display_macro_data(macro_data: dict):
    """Mostra i dati macro in formato tabella"""
    st.markdown("### 📊 Dati Macroeconomici")
    
    if macro_data:
        table_rows = []
        for curr, data in macro_data.items():
            row = {"Valuta": curr}
            row["Tasso %"] = data.get('interest_rate', 'N/A')
            row["Inflazione %"] = data.get('inflation_rate', 'N/A')
            row["PIL %"] = data.get('gdp_growth', 'N/A')
            row["Disoccup. %"] = data.get('unemployment', 'N/A')
            table_rows.append(row)
        
        df = pd.DataFrame(table_rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
        
        # Verifica completezza
        missing = []
        for curr, data in macro_data.items():
            for key, value in data.items():
                if value == 'N/A' or value is None:
                    missing.append(f"{curr}-{key}")
        
        if missing:
            st.warning(f"⚠️ Dati mancanti: {', '.join(missing[:5])}...")
        else:
            st.success("✅ Tutti i dati recuperati!")


def display_pmi_table(pmi_data: dict):
    """
    Mostra i dati PMI in formato tabella con colorazione automatica.
    
    Design:
    | Valuta | 🏭 Manuf. | Prev | Δ | 🏢 Services | Prev | Δ | Analisi |
    |--------|----------|------|---|-------------|------|---|---------|
    | USD    | 47.9     | 48.2 |-0.3| 54.4       | 52.6 |+1.8| 🏭↓ 🏢↑ |
    """
    st.markdown("### 📈 Dati PMI (Manufacturing & Services)")
    
    if not pmi_data:
        st.warning("⚠️ Nessun dato PMI disponibile")
        return
    
    # Costruisci le righe della tabella
    table_rows = []
    missing_data = []
    
    # Ordine valute
    currency_order = ["USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD"]
    
    for curr in currency_order:
        if curr not in pmi_data:
            continue
            
        data = pmi_data[curr]
        manuf = data.get("manufacturing", {})
        services = data.get("services", {})
        
        # Estrai valori Manufacturing
        manuf_current = manuf.get("current")
        manuf_previous = manuf.get("previous")
        manuf_delta = manuf.get("delta")
        manuf_label = manuf.get("label", "Manufacturing")
        
        # Estrai valori Services
        services_current = services.get("current")
        services_previous = services.get("previous")
        services_delta = services.get("delta")
        services_label = services.get("label", "Services")
        
        # Formatta valori con label per USD (ISM)
        if curr == "USD":
            manuf_display = f"{manuf_current} (ISM)" if manuf_current else "N/A"
            services_display = f"{services_current} (ISM)" if services_current else "N/A"
        else:
            manuf_display = str(manuf_current) if manuf_current else "N/A"
            services_display = str(services_current) if services_current else "N/A"
        
        # Formatta delta con segno
        def format_delta(delta):
            if delta is None:
                return "N/A"
            elif delta > 0:
                return f"+{delta}"
            else:
                return str(delta)
        
        # Calcola interpretazione
        trend_text, interpretation = get_pmi_interpretation(manuf_delta, services_delta)
        
        # Traccia dati mancanti (controlla sia current che previous)
        if manuf_current is None:
            missing_data.append(f"{curr}-Manuf")
        elif manuf_previous is None:
            missing_data.append(f"{curr}-Manuf(Prev)")
        if services_current is None:
            missing_data.append(f"{curr}-Serv")
        elif services_previous is None:
            missing_data.append(f"{curr}-Serv(Prev)")
        
        row = {
            "Valuta": curr,
            "🏭 Manuf.": manuf_display,
            "Prev": str(manuf_previous) if manuf_previous else "N/A",
            "Δ Manuf": format_delta(manuf_delta),
            "🏢 Services": services_display,
            "Prev ": str(services_previous) if services_previous else "N/A",  # Spazio per evitare duplicato colonna
            "Δ Serv": format_delta(services_delta),
            "Trend": trend_text,  # Es: "M↑ S↓"
            "Outlook": interpretation  # Es: "Bullish", "Bearish", "Misto+", etc.
        }
        table_rows.append(row)
    
    if table_rows:
        df = pd.DataFrame(table_rows)
        
        # Funzione per colorare le celle
        def style_pmi_table(df):
            styles = pd.DataFrame('', index=df.index, columns=df.columns)
            
            for idx, row in df.iterrows():
                # Colora Manufacturing current
                try:
                    manuf_val = float(str(row["🏭 Manuf."]).replace(" (ISM)", "").replace("N/A", "0"))
                    if manuf_val >= 50:
                        styles.loc[idx, "🏭 Manuf."] = 'background-color: #d4edda; color: #155724'  # Verde
                    elif manuf_val > 0:
                        styles.loc[idx, "🏭 Manuf."] = 'background-color: #f8d7da; color: #721c24'  # Rosso
                except:
                    pass
                
                # Colora Services current
                try:
                    serv_val = float(str(row["🏢 Services"]).replace(" (ISM)", "").replace("N/A", "0"))
                    if serv_val >= 50:
                        styles.loc[idx, "🏢 Services"] = 'background-color: #d4edda; color: #155724'  # Verde
                    elif serv_val > 0:
                        styles.loc[idx, "🏢 Services"] = 'background-color: #f8d7da; color: #721c24'  # Rosso
                except:
                    pass
                
                # Colora Delta Manufacturing
                try:
                    delta_manuf = row["Δ Manuf"].replace("+", "").replace("N/A", "0")
                    delta_val = float(delta_manuf)
                    if delta_val > 0:
                        styles.loc[idx, "Δ Manuf"] = 'background-color: #d4edda; color: #155724'  # Verde
                    elif delta_val < 0:
                        styles.loc[idx, "Δ Manuf"] = 'background-color: #f8d7da; color: #721c24'  # Rosso
                except:
                    pass
                
                # Colora Delta Services
                try:
                    delta_serv = row["Δ Serv"].replace("+", "").replace("N/A", "0")
                    delta_val = float(delta_serv)
                    if delta_val > 0:
                        styles.loc[idx, "Δ Serv"] = 'background-color: #d4edda; color: #155724'  # Verde
                    elif delta_val < 0:
                        styles.loc[idx, "Δ Serv"] = 'background-color: #f8d7da; color: #721c24'  # Rosso
                except:
                    pass
                
                # Colora Outlook in base all'interpretazione
                try:
                    outlook = row["Outlook"]
                    if outlook == "Bullish":
                        styles.loc[idx, "Outlook"] = 'background-color: #d4edda; color: #155724; font-weight: bold'  # Verde
                    elif outlook == "Bearish":
                        styles.loc[idx, "Outlook"] = 'background-color: #f8d7da; color: #721c24; font-weight: bold'  # Rosso
                    elif outlook == "Misto+":
                        styles.loc[idx, "Outlook"] = 'background-color: #d1ecf1; color: #0c5460'  # Azzurro
                    elif outlook == "Misto-":
                        styles.loc[idx, "Outlook"] = 'background-color: #fff3cd; color: #856404'  # Giallo
                    else:  # Neutro
                        styles.loc[idx, "Outlook"] = 'background-color: #e2e3e5; color: #383d41'  # Grigio
                except:
                    pass
            
            return styles
        
        # Applica stile e mostra
        styled_df = df.style.apply(lambda _: style_pmi_table(df), axis=None)
        st.dataframe(styled_df, use_container_width=True, hide_index=True)
        
        # Legenda
        st.caption("""
        **Legenda:** 🟢 PMI ≥ 50 (espansione) | 🔴 PMI < 50 (contrazione) | 
        **Trend:** M = Manufacturing, S = Services (↑ miglioramento, ↓ peggioramento) |
        **Outlook:** Bullish (entrambi ↑) | Bearish (entrambi ↓) | Misto (+/-) | Neutro
        """)
        
        # Verifica completezza
        if missing_data:
            st.warning(f"⚠️ Dati PMI mancanti: {', '.join(missing_data[:5])}{'...' if len(missing_data) > 5 else ''}")
        else:
            st.success("✅ Tutti i dati PMI recuperati!")
    else:
        st.warning("⚠️ Nessun dato PMI da visualizzare")


def display_analysis_matrix(analysis: dict):
    """Mostra la matrice delle analisi forex - LAYOUT OTTIMIZZATO"""
    
    if "error" in analysis:
        st.error(f"Errore nell'analisi: {analysis['error']}")
        return
    
    # ===== HEADER E SUMMARY =====
    st.markdown("### 🤖 Analisi Claude AI")
    
    # Data analisi e Risk Sentiment nella stessa riga
    col_date, col_sentiment = st.columns([2, 2])
    
    with col_date:
        if "analysis_date" in analysis:
            st.caption(f"📅 Data analisi: {analysis['analysis_date']}")
    
    with col_sentiment:
        if "risk_sentiment" in analysis:
            sentiment = analysis["risk_sentiment"]
            emoji = "🟢" if sentiment == "risk-on" else "🔴" if sentiment == "risk-off" else "🟡"
            st.markdown(f"**Risk Sentiment:** {emoji} {sentiment.upper()}")
    
    # Summary
    if "summary" in analysis:
        st.info(f"📋 **Contesto:** {analysis['summary']}")
    
    st.markdown("---")
    
    # ===== OUTLOOK TASSI (subito dopo summary) =====
    rate_outlook = analysis.get("rate_outlook", {})
    if rate_outlook:
        st.markdown("### 🏦 Outlook Tassi di Interesse")
        
        rate_rows = []
        for curr, data in rate_outlook.items():
            expectation = data.get("expectation", "hold")
            exp_emoji = "📈" if expectation == "hike" else "📉" if expectation == "cut" else "➡️"
            
            stance = data.get("stance", "neutral")
            stance_emoji = "🦅" if stance == "hawkish" else "🕊️" if stance == "dovish" else "➖"
            
            rate_rows.append({
                "Valuta": curr,
                "Tasso Attuale": data.get("current_rate", "N/A"),
                "Prossimo Meeting": data.get("next_meeting", "N/A"),
                "Aspettativa": f"{exp_emoji} {expectation.upper()}",
                "Stance": f"{stance_emoji} {stance.capitalize()}",
                "Probabilità": data.get("probability", "N/A")
            })
        
        df_rates = pd.DataFrame(rate_rows)
        st.dataframe(df_rates, use_container_width=True, hide_index=True)
        
        # Mostra note dettagliate in expander
        with st.expander("📝 Note dettagliate sulle aspettative tassi"):
            for curr, data in rate_outlook.items():
                notes = data.get("notes", "")
                if notes:
                    st.markdown(f"**{curr}:** {notes}")
        
        st.markdown("---")
    
    # ===== TABELLA PMI INDICATORS =====
    pmi_data = st.session_state.get('last_pmi_data', {})
    if pmi_data:
        st.markdown("### 📈 PMI Indicators")
        
        pmi_rows = []
        for curr in ["AUD", "CAD", "CHF", "EUR", "GBP", "JPY", "USD"]:
            if curr in pmi_data:
                manuf = pmi_data[curr].get("manufacturing", {})
                serv = pmi_data[curr].get("services", {})
                
                # Manufacturing
                manuf_current = manuf.get("current")
                manuf_delta = manuf.get("delta")
                manuf_label = manuf.get("label", "Manuf.")
                
                # Services
                serv_current = serv.get("current")
                serv_delta = serv.get("delta")
                
                # Formattazione valori
                manuf_str = f"{manuf_current:.1f}" if manuf_current else "N/A"
                if curr == "USD":
                    manuf_str += " ISM"
                
                serv_str = f"{serv_current:.1f}" if serv_current else "N/A"
                if curr == "USD":
                    serv_str += " ISM"
                
                # Delta con frecce
                if manuf_delta is not None:
                    manuf_delta_str = f"{'↗' if manuf_delta > 0 else '↘' if manuf_delta < 0 else '→'} {manuf_delta:+.1f}"
                else:
                    manuf_delta_str = "N/A"
                
                if serv_delta is not None:
                    serv_delta_str = f"{'↗' if serv_delta > 0 else '↘' if serv_delta < 0 else '→'} {serv_delta:+.1f}"
                else:
                    serv_delta_str = "N/A"
                
                # Interpretazione
                trend_text, interpretation = get_pmi_interpretation(manuf_delta, serv_delta)
                
                pmi_rows.append({
                    "Valuta": curr,
                    "🏭 Manuf.": manuf_str,
                    "Δ Manuf.": manuf_delta_str,
                    "🏢 Services": serv_str,
                    "Δ Services": serv_delta_str,
                    "Trend": trend_text
                })
        
        if pmi_rows:
            df_pmi = pd.DataFrame(pmi_rows)
            st.dataframe(df_pmi, use_container_width=True, hide_index=True)
            
            # Legenda
            st.caption("📊 Legenda: PMI ≥ 50 = espansione | PMI < 50 = contrazione | 🏭 Manufacturing | 🏢 Services")
            st.caption("🔗 Fonte: Investing.com (CHF Services: TradingEconomics)")
        
        st.markdown("---")
    
    # ===== TOP BULLISH / TOP BEARISH =====
    pair_analysis = analysis.get("pair_analysis", {})
    
    if pair_analysis:
        # Calcola differenziale per ogni coppia e ordina
        pairs_with_diff = []
        for p, d in pair_analysis.items():
            score_base = d.get("score_base", 0)
            score_quote = d.get("score_quote", 0)
            diff = score_base - score_quote
            pairs_with_diff.append((p, d, diff))
        
        # Ordina per differenziale
        bullish_pairs = [(p, d, diff) for p, d, diff in pairs_with_diff if diff > 0]
        bearish_pairs = [(p, d, diff) for p, d, diff in pairs_with_diff if diff < 0]
        neutral_pairs = [(p, d, diff) for p, d, diff in pairs_with_diff if diff == 0]
        
        bullish_pairs.sort(key=lambda x: x[2], reverse=True)
        bearish_pairs.sort(key=lambda x: x[2])  # più negativo prima
        
        st.markdown("### 🎯 Top Opportunità")
        
        col_bull, col_bear = st.columns(2)
        
        with col_bull:
            st.markdown("#### 🏆 TOP BULLISH (Long)")
            for pair, data, diff in bullish_pairs[:5]:
                # Pallini basati sul differenziale (>=7 = forte)
                dots = "🟢🟢" if diff >= 7 else "🟢"
                st.markdown(f"**{pair}** {dots} → Diff: **+{diff}**")
        
        with col_bear:
            st.markdown("#### 📉 TOP BEARISH (Short)")
            for pair, data, diff in bearish_pairs[:5]:
                # Pallini basati sul differenziale (<=-7 = forte)
                dots = "🔴🔴" if diff <= -7 else "🔴"
                st.markdown(f"**{pair}** {dots} → Diff: **{diff}**")
        
        st.markdown("---")
        
        # ===== TABELLA TUTTE LE COPPIE CON SELEZIONE SINGOLA =====
        st.markdown("### 📋 Tutte le Coppie")
        st.caption("👆 Clicca su una riga per vedere il dettaglio completo")
        
        # Crea lista con dati e ordina per differenziale (dal più bullish al più bearish)
        rows_data = []
        for pair, data in pair_analysis.items():
            bias = data.get("bias", "neutral")
            summary = data.get("summary", "")
            score_base = data.get("score_base", 0)
            score_quote = data.get("score_quote", 0)
            differential = score_base - score_quote
            
            # Pallini colorati basati sul DIFFERENZIALE (>=7 o <=-7 = forte)
            if bias == "bullish" or differential > 0:
                bias_combined = "🟢🟢 BULLISH" if differential >= 7 else "🟢 BULLISH"
            elif bias == "bearish" or differential < 0:
                bias_combined = "🔴🔴 BEARISH" if differential <= -7 else "🔴 BEARISH"
            else:
                bias_combined = "🟡 NEUTRAL"
            
            rows_data.append({
                "pair": pair,
                "Coppia": pair,
                "Bias": bias_combined,
                "Diff": differential,
                "Sintesi": summary[:150] + "..." if len(summary) > 150 else summary
            })
        
        # Ordina per differenziale decrescente (bullish in alto, bearish in basso)
        rows_data.sort(key=lambda x: x["Diff"], reverse=True)
        
        # Estrai pair_list ordinato e righe per dataframe
        pair_list = [r["pair"] for r in rows_data]
        rows = [{k: v for k, v in r.items() if k != "pair"} for r in rows_data]
        
        df = pd.DataFrame(rows)
        
        # Configura colonne (larghezze ottimizzate)
        column_config = {
            "Coppia": st.column_config.TextColumn("Coppia", width=85),
            "Bias": st.column_config.TextColumn("Bias", width=120),
            "Diff": st.column_config.NumberColumn("Diff", width=50),
            "Sintesi": st.column_config.TextColumn("Sintesi", width=None),  # Prende tutto lo spazio rimanente
        }
        
        # Altezza calcolata: 35px per riga × numero righe + header
        table_height = (len(rows) * 35) + 38
        
        # Usa dataframe con selezione singola riga
        selection = st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            column_config=column_config,
            height=table_height,
            key="pair_table_selection"
        )
        
        # Legenda
        st.caption("Legenda: 🟢🟢/🔴🔴 = bias forte (diff ≥7 o ≤-7) | 🟢/🔴 = bias moderato | 🟡 = neutrale")
        
        # Trova la coppia selezionata
        selected_pair = None
        if selection and selection.selection and selection.selection.rows:
            selected_row_idx = selection.selection.rows[0]
            selected_pair = pair_list[selected_row_idx]
        
        st.markdown("---")
        
        # ===== DETTAGLIO COPPIA SELEZIONATA =====
        if selected_pair and selected_pair in pair_analysis:
            st.markdown("### 🔍 Dettaglio Coppia Selezionata")
            
            pair_data = pair_analysis[selected_pair]
            
            bias = pair_data.get("bias", "neutral")
            summary = pair_data.get("summary", "")
            score_base = pair_data.get("score_base", 0)
            score_quote = pair_data.get("score_quote", 0)
            differential = score_base - score_quote
            scores = pair_data.get("scores", {})
            
            # Estrai valute dalla coppia
            base_curr, quote_curr = selected_pair.split("/")
            
            # Determina tipo bias basato su DIFFERENZIALE
            if bias == "bullish" or differential > 0:
                bias_type = "RIALZISTA" 
                bias_strength = "(STRONG)" if differential >= 7 else "(MODERATE)"
                header_color = "#d4edda"
                header_border = "#28a745"
                header_emoji = "🟢🟢" if differential >= 7 else "🟢"
            elif bias == "bearish" or differential < 0:
                bias_type = "RIBASSISTA"
                bias_strength = "(STRONG)" if differential <= -7 else "(MODERATE)"
                header_color = "#f8d7da"
                header_border = "#dc3545"
                header_emoji = "🔴🔴" if differential <= -7 else "🔴"
            else:
                bias_type = "NEUTRALE"
                bias_strength = ""
                header_color = "#fff3cd"
                header_border = "#ffc107"
                header_emoji = "🟡"
            
            # === HEADER BOX ===
            st.markdown(f"""
            <div style="background-color: {header_color}; border-left: 5px solid {header_border}; padding: 20px; border-radius: 8px; margin-bottom: 20px;">
                <h3 style="margin: 0; color: #333;">{header_emoji} {selected_pair} - BIAS {bias_type} {bias_strength}</h3>
            </div>
            """, unsafe_allow_html=True)
            
            # === BOX PUNTEGGI ===
            col1, col2, col3 = st.columns(3)
            
            with col1:
                diff_color = "#28a745" if differential > 0 else "#dc3545" if differential < 0 else "#6c757d"
                st.markdown(f"""
                <div style="text-align: center; padding: 15px; background: #f8f9fa; border-radius: 8px;">
                    <p style="margin: 0; color: #666; font-size: 0.9em;">Differenziale</p>
                    <p style="margin: 5px 0 0 0; font-size: 2em; font-weight: bold; color: {diff_color};">{'+' if differential > 0 else ''}{differential}</p>
                </div>
                """, unsafe_allow_html=True)
            
            with col2:
                base_color = "#28a745" if score_base > 0 else "#dc3545" if score_base < 0 else "#6c757d"
                st.markdown(f"""
                <div style="text-align: center; padding: 15px; background: #f8f9fa; border-radius: 8px;">
                    <p style="margin: 0; color: #666; font-size: 0.9em;">Score {base_curr}</p>
                    <p style="margin: 5px 0 0 0; font-size: 2em; font-weight: bold; color: {base_color};">{'+' if score_base > 0 else ''}{score_base}</p>
                </div>
                """, unsafe_allow_html=True)
            
            with col3:
                quote_color = "#28a745" if score_quote > 0 else "#dc3545" if score_quote < 0 else "#6c757d"
                st.markdown(f"""
                <div style="text-align: center; padding: 15px; background: #f8f9fa; border-radius: 8px;">
                    <p style="margin: 0; color: #666; font-size: 0.9em;">Score {quote_curr}</p>
                    <p style="margin: 5px 0 0 0; font-size: 2em; font-weight: bold; color: {quote_color};">{'+' if score_quote > 0 else ''}{score_quote}</p>
                </div>
                """, unsafe_allow_html=True)
            
            # === SINTESI ===
            st.markdown("")
            st.markdown(f"**Sintesi:** {summary}")
            
            st.markdown("---")
            
            # === CONFRONTO DATI MACRO E PUNTEGGI ===
            st.markdown("### 📊 Confronto Dati Macro e Punteggi")
            
            # Legenda punteggi
            st.caption("📌 Range punteggi: **Aspettative Tassi** [-2 a +2] | **Altri parametri** [-1 a +1]")
            
            # Recupera dati macro se disponibili
            macro_data = st.session_state.get('last_macro_data', {})
            
            col_base, col_quote = st.columns(2)
            
            # Mappa nomi parametri con range
            param_names = {
                "tassi_attuali": "Tassi Attuali [-1/+1]",
                "aspettative_tassi": "Aspettative Tassi [-2/+2]",
                "inflazione": "Inflazione [-1/+1]",
                "crescita_pil": "Crescita/PIL [-1/+1]",
                "pmi": "PMI [-1/+1]",
                "risk_sentiment": "Risk Sentiment [-1/+1]",
                "bilancia_fiscale": "Bilancia/Fiscale [-1/+1]"
            }
            
            with col_base:
                st.markdown(f"### {base_curr}")
                
                # Dati economici
                if base_curr in macro_data:
                    st.markdown("**Dati Economici:**")
                    base_macro = macro_data[base_curr]
                    st.markdown(f"- 🏦 Tasso BC: **{base_macro.get('interest_rate', 'N/A')}%**")
                    st.markdown(f"- 📈 Inflazione: **{base_macro.get('inflation_rate', 'N/A')}%**")
                    st.markdown(f"- 📊 PIL: **{base_macro.get('gdp_growth', 'N/A')}%**")
                    st.markdown(f"- 👥 Disoccupazione: **{base_macro.get('unemployment', 'N/A')}%**")
                
                # Tabella punteggi BASE
                st.markdown(f"**Punteggi {base_curr} vs {quote_curr}:**")
                
                score_rows_base = []
                for param_key, param_label in param_names.items():
                    if param_key in scores:
                        score_val = scores[param_key].get("base", 0)
                        motivation = scores[param_key].get("motivation_base", "")
                        
                        # Emoji per punteggio
                        if score_val > 0:
                            score_display = f"🟢 +{score_val}"
                        elif score_val < 0:
                            score_display = f"🔴 {score_val}"
                        else:
                            score_display = f"⚪ 0"
                        
                        score_rows_base.append({
                            "Parametro": param_label,
                            "Score": score_display,
                            "Motivazione": motivation[:150] + "..." if len(motivation) > 150 else motivation
                        })
                
                if score_rows_base:
                    df_base = pd.DataFrame(score_rows_base)
                    st.dataframe(df_base, use_container_width=True, hide_index=True)
                
                # Totale
                total_color = "#28a745" if score_base > 0 else "#dc3545" if score_base < 0 else "#6c757d"
                total_emoji = "🟢" if score_base > 0 else "🔴" if score_base < 0 else "⚪"
                st.markdown(f"### {total_emoji} TOTALE: {'+' if score_base > 0 else ''}{score_base}")
            
            with col_quote:
                st.markdown(f"### {quote_curr}")
                
                # Dati economici
                if quote_curr in macro_data:
                    st.markdown("**Dati Economici:**")
                    quote_macro = macro_data[quote_curr]
                    st.markdown(f"- 🏦 Tasso BC: **{quote_macro.get('interest_rate', 'N/A')}%**")
                    st.markdown(f"- 📈 Inflazione: **{quote_macro.get('inflation_rate', 'N/A')}%**")
                    st.markdown(f"- 📊 PIL: **{quote_macro.get('gdp_growth', 'N/A')}%**")
                    st.markdown(f"- 👥 Disoccupazione: **{quote_macro.get('unemployment', 'N/A')}%**")
                
                # Tabella punteggi QUOTE
                st.markdown(f"**Punteggi {quote_curr} vs {base_curr}:**")
                
                score_rows_quote = []
                for param_key, param_label in param_names.items():
                    if param_key in scores:
                        score_val = scores[param_key].get("quote", 0)
                        motivation = scores[param_key].get("motivation_quote", "")
                        
                        # Emoji per punteggio
                        if score_val > 0:
                            score_display = f"🟢 +{score_val}"
                        elif score_val < 0:
                            score_display = f"🔴 {score_val}"
                        else:
                            score_display = f"⚪ 0"
                        
                        score_rows_quote.append({
                            "Parametro": param_label,
                            "Score": score_display,
                            "Motivazione": motivation[:150] + "..." if len(motivation) > 150 else motivation
                        })
                
                if score_rows_quote:
                    df_quote = pd.DataFrame(score_rows_quote)
                    st.dataframe(df_quote, use_container_width=True, hide_index=True)
                
                # Totale
                total_color = "#28a745" if score_quote > 0 else "#dc3545" if score_quote < 0 else "#6c757d"
                total_emoji = "🟢" if score_quote > 0 else "🔴" if score_quote < 0 else "⚪"
                st.markdown(f"### {total_emoji} TOTALE: {'+' if score_quote > 0 else ''}{score_quote}")
            
            st.markdown("---")
            
            # === SCENARI DI PREZZO ===
            price_scenarios = pair_data.get("price_scenarios", {})
            current_price = pair_data.get("current_price", "N/A")
            key_drivers = pair_data.get("key_drivers", [])
            
            if price_scenarios or current_price != "N/A":
                st.markdown("### 📊 Scenari di Prezzo")
                
                # Box prezzo attuale
                st.markdown(f"""
                <div style="background-color: #e3f2fd; padding: 15px; border-radius: 8px; margin-bottom: 15px;">
                    <p style="margin: 0;"><strong>Prezzo attuale:</strong> ~{current_price}</p>
                </div>
                """, unsafe_allow_html=True)
                
                if price_scenarios:
                    col_base_range, col_base_strong, col_quote_strong = st.columns(3)
                    
                    with col_base_range:
                        st.markdown(f"""
                        <div style="text-align: center; padding: 15px; background: #f8f9fa; border-radius: 8px;">
                            <p style="margin: 0;">🟡 <strong>Base</strong></p>
                            <p style="margin: 5px 0 0 0; font-size: 1.1em;">{price_scenarios.get('base_range', 'N/A')}</p>
                        </div>
                        """, unsafe_allow_html=True)
                    
                    with col_base_strong:
                        st.markdown(f"""
                        <div style="text-align: center; padding: 15px; background: #d4edda; border-radius: 8px;">
                            <p style="margin: 0;">🟢 <strong>{base_curr} Forte</strong></p>
                            <p style="margin: 5px 0 0 0; font-size: 1.1em;">{price_scenarios.get('base_strong', 'N/A')}</p>
                        </div>
                        """, unsafe_allow_html=True)
                    
                    with col_quote_strong:
                        st.markdown(f"""
                        <div style="text-align: center; padding: 15px; background: #f8d7da; border-radius: 8px;">
                            <p style="margin: 0;">🔴 <strong>{quote_curr} Forte</strong></p>
                            <p style="margin: 5px 0 0 0; font-size: 1.1em;">{price_scenarios.get('quote_strong', 'N/A')}</p>
                        </div>
                        """, unsafe_allow_html=True)
                
                st.markdown("")
            
            # === DRIVER CHIAVE ===
            if key_drivers:
                st.markdown("### 🔑 Driver Chiave")
                for driver in key_drivers:
                    st.markdown(f"• {driver}")
                st.markdown("")
        else:
            # Nessuna coppia selezionata
            st.markdown("### 🔍 Dettaglio Coppia Selezionata")
            st.info("👆 Seleziona una coppia dalla tabella sopra per vedere l'analisi dettagliata")
        
        st.markdown("---")
        
        # === CALENDARIO ECONOMICO (sempre visibile) ===
        st.markdown("### 📅 Calendario Economico")
        
        st.info("📊 Consulta i calendari economici per gli eventi della settimana")
        
        col_te, col_ff = st.columns(2)
        
        with col_te:
            st.markdown("🔗 [TradingEconomics Calendar](https://tradingeconomics.com/calendar)")
        
        with col_ff:
            st.markdown("🔗 [ForexFactory Calendar](https://www.forexfactory.com/calendar)")
        
        st.caption("💡 Filtra per impatto 2-3 stelle e per le valute che ti interessano")
        
        st.markdown("---")


def display_analysis_history(analyses: list, user_id: str):
    """Mostra lo storico delle analisi"""
    
    st.markdown("### 📜 Storico Analisi")
    
    if not analyses:
        st.info("Nessuna analisi salvata")
        return
    
    for i, analysis_record in enumerate(analyses[:20]):  # Max 20
        # Estrai informazioni - gestisci sia formato nuovo che legacy
        datetime_str = analysis_record.get("analysis_datetime", "")
        
        # Se non c'è analysis_datetime al primo livello, cerca in data (formato legacy)
        if not datetime_str:
            data_obj = analysis_record.get("data", {})
            if isinstance(data_obj, dict):
                datetime_str = data_obj.get("analysis_datetime", "")
        
        analysis_type = analysis_record.get("analysis_type") or "full"  # Legacy = full
        
        # options_selected può essere dict, string, o None
        options_raw = analysis_record.get("options_selected")
        options = {}
        if options_raw:
            if isinstance(options_raw, str):
                try:
                    options = json.loads(options_raw)
                except:
                    options = {}
            elif isinstance(options_raw, dict):
                options = options_raw
        
        # Per analisi legacy senza options, mostra come "completa"
        is_legacy = not options_raw
        
        # Formato display
        date_display = format_datetime_display(datetime_str) if datetime_str else "Data sconosciuta"
        type_label = get_analysis_type_label(analysis_type)
        
        # Badge opzioni
        badges = []
        if is_legacy:
            badges = ["🔄"]  # Legacy = analisi completa vecchio formato
        else:
            if options.get("macro"): badges.append("📊")
            if options.get("news"): badges.append("📰")
            if options.get("links"): badges.append("📎")
            if options.get("claude"): badges.append("🤖")
        badges_str = " ".join(badges) if badges else ""
        
        col1, col2, col3 = st.columns([3, 1, 1])
        
        with col1:
            label = f"**{date_display}** - {type_label} {badges_str}"
            if is_legacy:
                label += " *(legacy)*"
            st.markdown(label)
        
        with col2:
            if st.button("📂", key=f"load_{i}", help="Carica"):
                st.session_state['current_analysis'] = analysis_record
                st.session_state['analysis_source'] = 'loaded'
                st.rerun()
        
        with col3:
            if datetime_str:
                if st.button("🗑️", key=f"del_{i}", help="Elimina"):
                    # Per analisi legacy senza user_id, usa None
                    del_user_id = analysis_record.get("user_id") or user_id
                    if delete_analysis(datetime_str, del_user_id):
                        st.success("Eliminata!")
                        st.rerun()


# ============================================================================
# CSS STYLING
# ============================================================================

def apply_custom_css():
    st.markdown("""
    <style>
        .main-header {
            font-size: 2.5rem;
            font-weight: bold;
            color: #1e3a5f;
            margin-bottom: 0.5rem;
        }
        
        .stProgress > div > div > div > div {
            background-color: #1e3a5f;
        }
        
        div[data-testid="stExpander"] {
            border: 1px solid #e0e0e0;
            border-radius: 8px;
            margin-bottom: 8px;
        }
        
        .analysis-options {
            background: linear-gradient(135deg, #f5f7fa 0%, #e4e8ec 100%);
            padding: 20px;
            border-radius: 10px;
            margin-bottom: 20px;
        }
    </style>
    """, unsafe_allow_html=True)


# ============================================================================
# MAIN APP
# ============================================================================

def main():
    apply_custom_css()
    
    # ===== CHECK AUTENTICAZIONE =====
    if 'authenticated' not in st.session_state or not st.session_state['authenticated']:
        show_login_page()
        return
    
    # ===== UTENTE AUTENTICATO =====
    user = st.session_state.get('user', {})
    user_id = st.session_state.get('user_id', 'local')
    username = user.get('username', 'Utente')
    
    # --- HEADER ---
    col_header1, col_header2 = st.columns([4, 1])
    with col_header1:
        st.markdown('<p class="main-header">📊 Forex Macro Analyst</p>', unsafe_allow_html=True)
        st.markdown(f"**Powered by Claude AI** | 👤 {username}")
    with col_header2:
        if st.button("🚪 Logout", type="secondary"):
            logout()
    
    # --- SIDEBAR ---
    with st.sidebar:
        st.header("⚙️ Configurazione")
        
        # Status
        if API_KEY_LOADED:
            st.success("✅ API Claude configurata")
        else:
            st.error("❌ API Key mancante")
        
        if SUPABASE_ENABLED:
            st.success("☁️ Database Supabase attivo")
        else:
            st.warning("💾 Modalità locale")
        
        st.markdown("---")
        
        # ===== OPZIONI ANALISI =====
        st.markdown("### 🎛️ Opzioni Analisi")
        
        st.caption("Seleziona cosa includere nell'analisi:")
        
        opt_macro = st.checkbox(
            "📊 Aggiorna Dati Macro",
            value=True,
            help="Recupera tassi, inflazione, PIL, disoccupazione (GRATIS)"
        )
        
        opt_pmi = st.checkbox(
            "📈 Aggiorna Dati PMI",
            value=True,
            help="Recupera PMI Manufacturing e Services da Investing.com (GRATIS)"
        )
        
        opt_news = st.checkbox(
            "📰 Ricerca Notizie Web",
            value=True,
            help="Cerca su Forex Factory, outlook BC, geopolitica (GRATIS)"
        )
        
        opt_links = st.checkbox(
            "📎 Processa Link Aggiuntivi",
            value=False,
            help="Analizza gli URL inseriti sotto (GRATIS)"
        )
        
        # Textarea per link (visibile solo se opzione attiva)
        additional_urls = ""
        if opt_links:
            additional_urls = st.text_area(
                "URL (uno per riga)",
                height=100,
                placeholder="https://federalreserve.gov/...\nhttps://reuters.com/...",
                help="Max 10 URL",
                key="additional_urls"
            )
            
            if additional_urls.strip():
                url_count = len([u for u in additional_urls.split('\n') if u.strip().startswith('http')])
                st.info(f"📌 {url_count} URL da processare")
        
        st.markdown("---")
        
        opt_claude = st.checkbox(
            "🤖 Analisi Claude AI",
            value=True,
            help="Genera analisi completa forex ($$$ - costa token)"
        )
        
        if opt_claude:
            st.warning("⚠️ L'analisi Claude consuma token API")
        
        # Validazione: almeno un'opzione dati se Claude attivo
        if opt_claude and not (opt_macro or opt_pmi or opt_news or opt_links):
            st.error("⚠️ Seleziona almeno una fonte dati per Claude!")
        
        st.markdown("---")
        
        # ===== BOTTONE ANALISI =====
        can_analyze = API_KEY_LOADED and (opt_macro or opt_pmi or opt_news or opt_links)
        
        analyze_btn = st.button(
            "🚀 AVVIA ANALISI",
            disabled=not can_analyze,
            use_container_width=True,
            type="primary"
        )
        
        # Calcola tipo analisi
        analysis_type = "custom"
        if opt_macro and opt_pmi and opt_news and opt_claude and not opt_links:
            analysis_type = "full"
        elif opt_macro and not opt_news and not opt_links:
            analysis_type = "macro_only"
        elif opt_news and not opt_macro and not opt_links:
            analysis_type = "news_only"
        elif opt_links and not opt_macro and not opt_news:
            analysis_type = "links_only"
        
        st.caption(f"📋 Tipo: {get_analysis_type_label(analysis_type)}")
        
        st.markdown("---")
        st.markdown(f"**Coppie:** {len(FOREX_PAIRS)}")
        st.markdown(f"**Valute:** {', '.join(CURRENCIES.keys())}")
        
        st.markdown("---")
        
        # ===== STORICO ANALISI (menu a tendina) =====
        user_analyses = get_user_analyses(user_id, limit=30)
        
        if user_analyses:
            st.markdown("### 📁 Analisi Salvate")
            
            # Crea lista opzioni per selectbox
            analysis_options = []
            for analysis_record in user_analyses:
                datetime_str = analysis_record.get("analysis_datetime", "")
                if not datetime_str:
                    data_obj = analysis_record.get("data", {})
                    if isinstance(data_obj, dict):
                        datetime_str = data_obj.get("analysis_datetime", "")
                
                analysis_type = analysis_record.get("analysis_type") or "full"
                date_display = format_datetime_display(datetime_str) if datetime_str else "Data sconosciuta"
                type_label = get_analysis_type_label(analysis_type)
                
                analysis_options.append({
                    "label": f"{date_display} - {type_label}",
                    "datetime": datetime_str,
                    "record": analysis_record
                })
            
            # Selectbox
            selected_idx = st.selectbox(
                "Seleziona analisi:",
                range(len(analysis_options)),
                format_func=lambda x: analysis_options[x]["label"],
                key="analysis_selector"
            )
            
            selected_analysis = analysis_options[selected_idx]
            
            col1, col2 = st.columns(2)
            
            with col1:
                if st.button("📂 Carica", use_container_width=True):
                    st.session_state['current_analysis'] = selected_analysis["record"]
                    st.session_state['analysis_source'] = 'loaded'
                    st.rerun()
            
            with col2:
                if st.button("🗑️ Elimina", use_container_width=True, type="secondary"):
                    st.session_state['confirm_delete'] = selected_analysis["datetime"]
            
            # Conferma eliminazione
            if 'confirm_delete' in st.session_state and st.session_state['confirm_delete'] == selected_analysis["datetime"]:
                st.warning(f"⚠️ Eliminare questa analisi?")
                col_yes, col_no = st.columns(2)
                with col_yes:
                    if st.button("✅ Sì", use_container_width=True):
                        del_user_id = selected_analysis["record"].get("user_id") or user_id
                        if delete_analysis(selected_analysis["datetime"], del_user_id):
                            st.success("Eliminata!")
                            del st.session_state['confirm_delete']
                            st.rerun()
                with col_no:
                    if st.button("❌ No", use_container_width=True):
                        del st.session_state['confirm_delete']
                        st.rerun()
        else:
            st.info("📁 Nessuna analisi salvata")
    
    # --- MAIN AREA ---
    
    # ===== ESECUZIONE ANALISI =====
    if analyze_btn:
        progress = st.progress(0, text="Inizializzazione...")
        
        # Variabili per raccogliere i dati
        macro_data = None
        pmi_data = None
        news_text = ""
        news_structured = {}
        additional_text = ""
        links_structured = []
        claude_analysis = None
        
        options_selected = {
            "macro": opt_macro,
            "pmi": opt_pmi,
            "news": opt_news,
            "links": opt_links,
            "claude": opt_claude
        }
        
        step = 0
        total_steps = sum([opt_macro, opt_pmi, opt_news, opt_links, opt_claude])
        if total_steps == 0:
            total_steps = 1  # Evita divisione per zero
        
        # FASE 1: Dati Macro
        if opt_macro:
            step += 1
            progress.progress(int(step/total_steps*80), text="📊 Recupero dati macro...")
            macro_data = fetch_macro_data()
            st.session_state['last_macro_data'] = macro_data
        else:
            # Usa dati macro dalla sessione o dall'ultima analisi salvata
            if 'last_macro_data' in st.session_state and st.session_state['last_macro_data']:
                macro_data = st.session_state['last_macro_data']
            else:
                # Prova a caricare dall'ultima analisi salvata
                try:
                    recent = list_analyses(user_id, limit=1)
                    if recent and len(recent) > 0:
                        datetime_key = recent[0].get("analysis_datetime") or recent[0].get("data", {}).get("analysis_datetime")
                        if datetime_key:
                            last_analysis = load_analysis(datetime_key, user_id)
                            if last_analysis:
                                # I dati sono dentro 'data' per Supabase
                                data_container = last_analysis.get('data', last_analysis)
                                if data_container and 'macro_data' in data_container:
                                    macro_data = data_container['macro_data']
                                    st.session_state['last_macro_data'] = macro_data
                except Exception as e:
                    # Se fallisce il caricamento, continua senza dati macro
                    pass
        
        # FASE 2: Dati PMI
        if opt_pmi:
            step += 1
            progress.progress(int(step/total_steps*80), text="📈 Recupero dati PMI...")
            pmi_data = fetch_all_pmi_data()
            st.session_state['last_pmi_data'] = pmi_data
        else:
            # Usa dati PMI dalla sessione
            if 'last_pmi_data' in st.session_state and st.session_state['last_pmi_data']:
                pmi_data = st.session_state['last_pmi_data']
        
        # FASE 3: Notizie Web
        if opt_news:
            step += 1
            progress.progress(int(step/total_steps*80), text="📰 Ricerca notizie web...")
            news_text, news_structured = search_web_news()
            st.session_state['last_news_text'] = news_text
            st.session_state['last_news_structured'] = news_structured
        
        # FASE 4: Link Aggiuntivi
        if opt_links and additional_urls.strip():
            step += 1
            progress.progress(int(step/total_steps*80), text="📎 Processamento link...")
            url_list = [u.strip() for u in additional_urls.split('\n') if u.strip().startswith('http')]
            additional_text, links_structured = fetch_additional_resources(url_list)
            st.session_state['last_links_text'] = additional_text
            st.session_state['last_links_structured'] = links_structured
        
        # FASE 5: Analisi Claude
        if opt_claude:
            step += 1
            progress.progress(int(step/total_steps*80), text="🤖 Claude sta analizzando...")
            
            # Usa dati dalla sessione se non aggiornati ora
            if not opt_news and 'last_news_text' in st.session_state:
                news_text = st.session_state['last_news_text']
            if not opt_links and 'last_links_text' in st.session_state:
                additional_text = st.session_state['last_links_text']
            if not opt_pmi and 'last_pmi_data' in st.session_state:
                pmi_data = st.session_state['last_pmi_data']
            
            claude_analysis = analyze_with_claude(
                ANTHROPIC_API_KEY,
                macro_data,
                news_text,
                additional_text,
                pmi_data
            )
        
        # ===== SALVATAGGIO =====
        progress.progress(90, text="💾 Salvataggio...")
        
        analysis_result = {
            "macro_data": macro_data,
            "pmi_data": pmi_data,
            "news_structured": news_structured,
            "links_structured": links_structured,
            "claude_analysis": claude_analysis,
            "options_selected": options_selected
        }
        
        if save_analysis(analysis_result, user_id, analysis_type, options_selected):
            st.session_state['current_analysis'] = analysis_result
            st.session_state['analysis_source'] = 'new'
            progress.progress(100, text="✅ Completato!")
            st.rerun()
        else:
            progress.progress(100, text="❌ Errore")
            # L'errore dettagliato è già mostrato da save_analysis
    
    # ===== VISUALIZZAZIONE RISULTATI =====
    if 'current_analysis' in st.session_state:
        analysis = st.session_state['current_analysis']
        source = st.session_state.get('analysis_source', 'unknown')
        
        if source == 'new':
            st.success("✅ Nuova analisi completata!")
        elif source == 'loaded':
            st.info("📂 Analisi caricata da archivio")
        
        # DEBUG: mostra struttura (rimuovere dopo test)
        # with st.expander("🔍 Debug struttura dati"):
        #     st.json(analysis)
        
        # Estrai dati - gestisci multipli formati
        # Formato Supabase: { "data": {...}, "analysis_datetime": "...", ... }
        # Formato v3: data contiene { "macro_data": ..., "claude_analysis": ... }
        # Formato legacy: data contiene direttamente { "pair_analysis": ..., "market_summary": ... }
        
        data_container = analysis.get('data', analysis)
        
        # Se data_container è una stringa (JSON serializzato), deserializza
        if isinstance(data_container, str):
            try:
                data_container = json.loads(data_container)
            except:
                data_container = {}
        
        # Inizializza variabili
        macro_data = None
        pmi_data = None
        news_structured = {}
        links_structured = []
        claude_analysis = None
        
        # Rileva formato e estrai dati
        if 'claude_analysis' in data_container:
            # Formato v3 nuovo
            macro_data = data_container.get('macro_data')
            pmi_data = data_container.get('pmi_data')
            news_structured = data_container.get('news_structured', {})
            links_structured = data_container.get('links_structured', [])
            claude_analysis = data_container.get('claude_analysis')
        elif 'pair_analysis' in data_container:
            # Formato legacy - data_container È l'analisi Claude
            claude_analysis = data_container
        elif 'macro_data' in data_container:
            # Formato v3 senza Claude
            macro_data = data_container.get('macro_data')
            pmi_data = data_container.get('pmi_data')
            news_structured = data_container.get('news_structured', {})
            links_structured = data_container.get('links_structured', [])
        
        # Salva in session_state per visualizzazione tabella PMI
        if pmi_data:
            st.session_state['last_pmi_data'] = pmi_data
        
        # Verifica se c'è qualcosa da mostrare
        has_content = macro_data or pmi_data or news_structured or links_structured or claude_analysis
        
        if not has_content:
            st.warning("⚠️ Questa analisi non contiene dati visualizzabili")
            with st.expander("🔍 Dettagli struttura"):
                st.json(analysis)
        
        # === ORDINE VISUALIZZAZIONE ===
        # 1. Dati Macro
        # 2. Dati PMI
        # 3. Analisi Claude (outlook tassi, top bullish/bearish, coppie, valute)
        # 4. Notizie Web (alla fine)
        
        if macro_data:
            display_macro_data(macro_data)
            st.markdown("---")
        
        if pmi_data:
            display_pmi_table(pmi_data)
            st.markdown("---")
        
        if claude_analysis:
            display_analysis_matrix(claude_analysis)
            st.markdown("---")
        
        # Notizie alla fine
        if news_structured or links_structured:
            display_news_summary(news_structured, links_structured)
    
    else:
        # Stato iniziale
        st.markdown("""
        ### 👋 Benvenuto!
        
        Seleziona le opzioni nella sidebar e clicca **🚀 AVVIA ANALISI**.
        
        **Opzioni disponibili:**
        - 📊 **Dati Macro** - Tassi, inflazione, PIL (gratis)
        - 📈 **Dati PMI** - Manufacturing & Services PMI (gratis)
        - 📰 **Notizie Web** - Forex Factory, outlook BC (gratis)
        - 📎 **Link Aggiuntivi** - Analizza URL custom (gratis)
        - 🤖 **Claude AI** - Analisi completa forex (a pagamento)
        
        💡 **Suggerimento:** Puoi aggiornare solo le notizie senza richiamare Claude per risparmiare!
        """)
    
    # --- FOOTER ---
    st.markdown("---")
    st.markdown("""
    <div style="text-align: center; color: #6b7280; font-size: 0.8rem;">
        📊 Forex Macro Analyst v3.0 | Powered by Claude AI<br>
        ⚠️ Non costituisce consiglio di investimento
    </div>
    """, unsafe_allow_html=True)


# ============================================================================
# RUN
# ============================================================================

if __name__ == "__main__":
    main()
