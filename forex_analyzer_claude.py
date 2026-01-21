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


# ============================================================================
# FUNZIONI PREZZI FOREX IN TEMPO REALE
# ============================================================================

def fetch_forex_prices() -> dict:
    """
    Recupera i prezzi forex in tempo reale.
    Ordine tentativi:
    1. Yahoo Finance API (JSON, più affidabile)
    2. Frankfurter.app (ECB - fallback)
    
    Returns:
        dict con prezzi per ogni coppia forex
    """
    import time
    prices = {}
    errors = []
    
    # Mappa coppie forex -> simboli Yahoo Finance
    # Yahoo usa formato: EURUSD=X (senza slash)
    yahoo_pairs = {
        "EUR/USD": "EURUSD=X",
        "GBP/USD": "GBPUSD=X",
        "USD/JPY": "JPY=X",  # Yahoo usa JPY=X per USD/JPY
        "USD/CHF": "CHF=X",
        "AUD/USD": "AUDUSD=X",
        "USD/CAD": "CAD=X",
        "EUR/GBP": "EURGBP=X",
        "EUR/JPY": "EURJPY=X",
        "GBP/JPY": "GBPJPY=X",
        "AUD/JPY": "AUDJPY=X",
        "EUR/CHF": "EURCHF=X",
        "GBP/CHF": "GBPCHF=X",
        "AUD/CHF": "AUDCHF=X",
        "CAD/JPY": "CADJPY=X",
        "AUD/CAD": "AUDCAD=X",
        "EUR/CAD": "EURCAD=X",
        "EUR/AUD": "EURAUD=X",
        "GBP/AUD": "GBPAUD=X",
        "GBP/CAD": "GBPCAD=X"
    }
    
    # ===== TENTATIVO 1: Yahoo Finance API (PRIORITÀ) =====
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        for pair, symbol in yahoo_pairs.items():
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1m&range=1d"
                resp = requests.get(url, headers=headers, timeout=10)
                
                if resp.status_code == 200:
                    data = resp.json()
                    
                    # Estrai prezzo dal JSON
                    try:
                        # Il prezzo è in result[0].meta.regularMarketPrice
                        result = data.get("chart", {}).get("result", [])
                        if result:
                            meta = result[0].get("meta", {})
                            price = meta.get("regularMarketPrice")
                            
                            if price:
                                # Per coppie dove Yahoo inverte (USD/XXX -> XXX=X)
                                if symbol in ["JPY=X", "CHF=X", "CAD=X"]:
                                    # Yahoo restituisce XXX per 1 USD, noi vogliamo USD/XXX
                                    pass  # Il valore è già corretto
                                
                                if "JPY" in pair:
                                    prices[pair] = round(float(price), 3)
                                else:
                                    prices[pair] = round(float(price), 5)
                            else:
                                errors.append(f"{pair}: prezzo non trovato in JSON")
                        else:
                            errors.append(f"{pair}: nessun risultato")
                    except Exception as e:
                        errors.append(f"{pair}: parse error - {str(e)[:30]}")
                else:
                    errors.append(f"{pair}: HTTP {resp.status_code}")
                
                # Piccolo delay per evitare rate limit
                time.sleep(0.1)
                
            except Exception as e:
                errors.append(f"{pair}: {str(e)[:50]}")
                continue
        
        # Se abbiamo almeno 15 prezzi, consideriamo un successo
        if len(prices) >= 15:
            return {
                "prices": prices, 
                "source": "Yahoo Finance (Real-time)", 
                "success": True,
                "found": len(prices),
                "total": len(yahoo_pairs),
                "errors": errors if errors else None
            }
    except Exception as e:
        errors.append(f"Yahoo Finance API error: {str(e)[:100]}")
    
    # ===== TENTATIVO 2: yfinance library =====
    try:
        import yfinance as yf
        prices_yf = {}
        
        # Scarica tutti i ticker in un batch (più veloce)
        symbols = list(yahoo_pairs.values())
        tickers = yf.Tickers(" ".join(symbols))
        
        for pair, symbol in yahoo_pairs.items():
            try:
                ticker = tickers.tickers.get(symbol)
                if ticker:
                    info = ticker.fast_info
                    price = info.get('lastPrice') or info.get('regularMarketPrice')
                    if price:
                        if "JPY" in pair:
                            prices_yf[pair] = round(float(price), 3)
                        else:
                            prices_yf[pair] = round(float(price), 5)
            except:
                pass
        
        if len(prices_yf) >= 15:
            return {
                "prices": prices_yf, 
                "source": "yfinance (Real-time)", 
                "success": True,
                "found": len(prices_yf),
                "total": len(yahoo_pairs),
                "errors": errors if errors else None
            }
    except Exception as e:
        errors.append(f"yfinance error: {str(e)[:100]}")
    
    # ===== TENTATIVO 3: Frankfurter.app (ECB data - FALLBACK) =====
    # Nota: questi sono tassi ECB, aggiornati 1x/giorno, non real-time
    try:
        prices_fallback = {}
        
        # Ottieni tassi base USD
        resp_usd = requests.get(
            "https://api.frankfurter.app/latest?from=USD",
            timeout=10
        )
        
        # Ottieni tassi base EUR
        resp_eur = requests.get(
            "https://api.frankfurter.app/latest?from=EUR",
            timeout=10
        )
        
        # Ottieni tassi base GBP
        resp_gbp = requests.get(
            "https://api.frankfurter.app/latest?from=GBP",
            timeout=10
        )
        
        # Ottieni tassi base AUD
        resp_aud = requests.get(
            "https://api.frankfurter.app/latest?from=AUD",
            timeout=10
        )
        
        if resp_usd.status_code == 200 and resp_eur.status_code == 200:
            rates_usd = resp_usd.json().get("rates", {})
            rates_eur = resp_eur.json().get("rates", {})
            rates_gbp = resp_gbp.json().get("rates", {}) if resp_gbp.status_code == 200 else {}
            rates_aud = resp_aud.json().get("rates", {}) if resp_aud.status_code == 200 else {}
            
            # Calcola i prezzi per ogni coppia
            for pair in yahoo_pairs.keys():
                base, quote = pair.split("/")
                try:
                    if base == "USD":
                        if quote in rates_usd:
                            prices_fallback[pair] = round(rates_usd[quote], 5 if quote != "JPY" else 3)
                    elif quote == "USD":
                        if base in rates_usd:
                            prices_fallback[pair] = round(1 / rates_usd[base], 5)
                    elif base == "EUR":
                        if quote in rates_eur:
                            prices_fallback[pair] = round(rates_eur[quote], 5 if quote != "JPY" else 3)
                    elif base == "GBP":
                        if quote in rates_gbp:
                            prices_fallback[pair] = round(rates_gbp[quote], 5 if quote != "JPY" else 3)
                    elif base == "AUD":
                        if quote in rates_aud:
                            prices_fallback[pair] = round(rates_aud[quote], 5 if quote != "JPY" else 3)
                    else:
                        # Cross rate generico
                        if base in rates_usd and quote in rates_usd:
                            prices_fallback[pair] = round(rates_usd[quote] / rates_usd[base], 5 if quote != "JPY" else 3)
                except:
                    pass
            
            if prices_fallback:
                return {
                    "prices": prices_fallback, 
                    "source": "Frankfurter.app (ECB - NON real-time)", 
                    "success": True,
                    "warning": "⚠️ Prezzi ECB aggiornati 1x/giorno, non real-time!",
                    "found": len(prices_fallback),
                    "total": len(yahoo_pairs)
                }
    except Exception as e:
        errors.append(f"Frankfurter error: {str(e)[:100]}")
    
    return {
        "prices": prices if prices else {}, 
        "source": None, 
        "success": False, 
        "error": "Nessuna fonte disponibile",
        "details": errors
    }


# ============================================================================
# FUNZIONE SCRAPING FOREX FACTORY NEWS
# ============================================================================

def fetch_forexfactory_news() -> dict:
    """
    Recupera le news più recenti da ForexFactory tramite DuckDuckGo Search.
    (Lo scraping diretto è bloccato da Cloudflare/firewall)
    
    Returns:
        dict con lista di news e metadati
    """
    try:
        from duckduckgo_search import DDGS
        import time
        
        news_items = []
        
        # Cerca news recenti su ForexFactory via DuckDuckGo
        queries = [
            "site:forexfactory.com/news",
            "forexfactory forex news today",
        ]
        
        with DDGS() as ddgs:
            for query in queries:
                try:
                    # Usa news search per risultati più recenti
                    results = list(ddgs.news(query, max_results=8))
                    
                    for item in results:
                        title = item.get('title', '')
                        url = item.get('url', '')
                        date = item.get('date', '')
                        
                        # Evita duplicati
                        if title and title not in [n["title"] for n in news_items]:
                            news_items.append({
                                "title": title,
                                "url": url,
                                "time": date[:16] if date else "",
                                "currency": "",
                                "source": item.get('source', '')
                            })
                    
                    time.sleep(0.3)
                except:
                    continue
                
                if len(news_items) >= 10:
                    break
        
        # Se non trova notizie via news search, prova text search
        if len(news_items) < 5:
            with DDGS() as ddgs:
                try:
                    results = list(ddgs.text("forex market news today central bank", max_results=10))
                    for item in results:
                        title = item.get('title', '')
                        url = item.get('href', '')
                        
                        if title and title not in [n["title"] for n in news_items]:
                            news_items.append({
                                "title": title,
                                "url": url,
                                "time": "",
                                "currency": "",
                                "source": ""
                            })
                except:
                    pass
        
        return {
            "news": news_items[:15],
            "count": len(news_items),
            "source": "DuckDuckGo News Search",
            "success": len(news_items) > 0
        }
        
    except ImportError:
        return {"news": [], "error": "duckduckgo-search non installato", "success": False}
    except Exception as e:
        return {"news": [], "error": str(e), "success": False}


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
        "cb_history_only": "🏦 Solo Storico BC",
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
# CONFIGURAZIONE BANCHE CENTRALI - Per scraping automatico storico decisioni
# ============================================================================

CENTRAL_BANK_CONFIG = {
    "USD": {
        "bank_name": "Federal Reserve",
        "bank_short": "Fed",
        "event_id": 168,  # interest-rate-decision-168
        "country_codes": ["us"],  # Lista di country codes da provare
        "rate_type": "range",
    },
    "EUR": {
        "bank_name": "European Central Bank",
        "bank_short": "ECB",
        "event_id": 164,  # ecb-interest-rate-decision-164
        "country_codes": ["eu", "us"],
        "rate_type": "single",
    },
    "GBP": {
        "bank_name": "Bank of England",
        "bank_short": "BOE",
        "event_id": 170,  # boe-interest-rate-decision-170
        "country_codes": ["uk", "us"],
        "rate_type": "single",
    },
    "JPY": {
        "bank_name": "Bank of Japan",
        "bank_short": "BOJ",
        "event_id": 165,  # boj-interest-rate-decision-165
        "country_codes": ["jp", "us"],
        "rate_type": "single",
    },
    "CHF": {
        "bank_name": "Swiss National Bank",
        "bank_short": "SNB",
        "event_id": 169,  # snb-interest-rate-decision-169
        "country_codes": ["ch", "us"],
        "rate_type": "single",
    },
    "AUD": {
        "bank_name": "Reserve Bank of Australia",
        "bank_short": "RBA",
        "event_id": 171,  # interest-rate-decision-171 (RBA)
        "country_codes": ["au", "us"],
        "rate_type": "single",
    },
    "CAD": {
        "bank_name": "Bank of Canada",
        "bank_short": "BOC",
        "event_id": 166,  # boc-interest-rate-decision-166
        "country_codes": ["ca", "us"],
        "rate_type": "single",
    }
}


def fetch_central_bank_history_from_api(currency: str) -> dict:
    """
    Recupera lo storico decisioni tassi da Investing.com JSON API.
    Prova diversi country codes come fallback.
    
    Returns:
        dict con: current_rate, meetings (ultimi 2-3), trend
    """
    config = CENTRAL_BANK_CONFIG.get(currency)
    if not config:
        return {"error": f"Currency {currency} not configured"}
    
    event_id = config["event_id"]
    country_codes = config.get("country_codes", ["us"])
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json',
        'Referer': 'https://www.investing.com/'
    }
    
    last_error = None
    
    # Prova ogni country code finché uno funziona
    for country in country_codes:
        url = f"https://sbcharts.investing.com/events_charts/{country}/{event_id}.json"
        
        try:
            response = requests.get(url, headers=headers, timeout=15)
            
            if response.status_code != 200:
                last_error = f"HTTP {response.status_code} for {country}"
                continue
            
            data = response.json()
            attr = data.get("attr", [])
            
            if not attr or len(attr) < 2:
                last_error = f"Insufficient data for {country}"
                continue
            
            # Dati trovati! Processa
            # Prendi gli ultimi 3 meeting (per calcolare trend)
            recent_meetings = attr[-3:] if len(attr) >= 3 else attr
            recent_meetings.reverse()  # Più recente prima
            
            meetings = []
            for i, m in enumerate(recent_meetings):
                timestamp = m.get("timestamp", 0)
                actual = m.get("actual")
                actual_formatted = m.get("actual_formatted", "")
                
                # Converti timestamp in data
                from datetime import datetime
                try:
                    date = datetime.fromtimestamp(timestamp / 1000)
                    date_str = date.strftime("%Y-%m-%d")
                    date_formatted = date.strftime("%b %d, %Y")
                except:
                    date_str = "N/A"
                    date_formatted = "N/A"
                
                # Calcola variazione rispetto al meeting precedente
                change = None
                decision = "hold"
                if i < len(recent_meetings) - 1:
                    prev_actual = recent_meetings[i + 1].get("actual")
                    if actual is not None and prev_actual is not None:
                        try:
                            diff = float(actual) - float(prev_actual)
                            if abs(diff) < 0.001:  # Praticamente uguale
                                decision = "hold"
                                change = "0bp"
                            elif diff > 0:
                                decision = "hike"
                                change = f"+{int(diff * 100)}bp"
                            else:
                                decision = "cut"
                                change = f"{int(diff * 100)}bp"
                        except:
                            pass
                
                meetings.append({
                    "date": date_str,
                    "date_formatted": date_formatted,
                    "rate": actual_formatted if actual_formatted else f"{actual}%",
                    "decision": decision,
                    "change": change if change else "N/A",
                    "vote": "N/A",
                    "dissent": None
                })
            
            # Tasso attuale (ultimo meeting)
            current_rate = meetings[0]["rate"] if meetings else "N/A"
            
            # Calcola trend basato su ultimi 2 meeting
            trend_info = calculate_trend_from_meetings(meetings)
            
            return {
                "bank_name": config["bank_name"],
                "bank_short": config["bank_short"],
                "current_rate": current_rate,
                "meetings": meetings[:2],
                "trend": trend_info["trend"],
                "trend_label": trend_info["trend_label"],
                "trend_emoji": trend_info["trend_emoji"],
                "stance_hint": trend_info["stance_hint"],
                "source_country": country  # Debug: quale country ha funzionato
            }
            
        except Exception as e:
            last_error = f"{country}: {str(e)[:50]}"
            continue
    
    # Nessun country code ha funzionato
    return {"error": last_error or "All country codes failed"}


def calculate_trend_from_meetings(meetings: list) -> dict:
    """
    Calcola il trend basato sulle decisioni degli ultimi meeting.
    """
    if len(meetings) < 2:
        return {"trend": "unknown", "trend_label": "Sconosciuto", "trend_emoji": "❓", "stance_hint": None}
    
    d1 = meetings[0].get("decision", "hold")  # Più recente
    d2 = meetings[1].get("decision", "hold")  # Precedente
    
    # Logica trend
    if d1 == "hike" and d2 == "hike":
        return {"trend": "hiking", "trend_label": "Hiking", "trend_emoji": "🟢 ▲", "stance_hint": "hawkish"}
    elif d1 == "cut" and d2 == "cut":
        return {"trend": "cutting", "trend_label": "Cutting", "trend_emoji": "🔴 ▼", "stance_hint": "dovish"}
    elif d1 == "hold" and d2 == "hold":
        return {"trend": "holding", "trend_label": "Holding", "trend_emoji": "➖", "stance_hint": "neutral"}
    elif d1 == "hike" and d2 == "hold":
        return {"trend": "tightening", "trend_label": "Tightening", "trend_emoji": "🟢 ▲", "stance_hint": "hawkish"}
    elif d1 == "hold" and d2 == "hike":
        return {"trend": "pause_after_hike", "trend_label": "Pausa (post-rialzo)", "trend_emoji": "⏸️", "stance_hint": "hawkish"}
    elif d1 == "cut" and d2 == "hold":
        return {"trend": "easing", "trend_label": "Easing", "trend_emoji": "🔴 ▼", "stance_hint": "dovish"}
    elif d1 == "hold" and d2 == "cut":
        return {"trend": "pause_after_cut", "trend_label": "Pausa (post-taglio)", "trend_emoji": "⏸️", "stance_hint": "dovish"}
    else:
        return {"trend": "mixed", "trend_label": "Misto", "trend_emoji": "🔀", "stance_hint": None}


def fetch_all_central_bank_history() -> dict:
    """
    Recupera lo storico di tutte le banche centrali.
    """
    import time
    
    all_history = {}
    
    for currency in ["USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD"]:
        result = fetch_central_bank_history_from_api(currency)
        all_history[currency] = result
        time.sleep(0.5)  # Rate limiting
    
    return all_history


def get_central_bank_history_summary() -> dict:
    """
    Restituisce un riassunto dello storico formattato per visualizzazione e prompt.
    """
    all_history = fetch_all_central_bank_history()
    
    summary = {}
    
    for currency, data in all_history.items():
        if "error" in data:
            summary[currency] = {
                "bank_name": CENTRAL_BANK_CONFIG.get(currency, {}).get("bank_name", currency),
                "bank_short": CENTRAL_BANK_CONFIG.get(currency, {}).get("bank_short", currency),
                "current_rate": "N/A",
                "meeting_1": "N/A",
                "meeting_2": "N/A",
                "trend": "unknown",
                "trend_label": "Errore",
                "trend_emoji": "⚠️",
                "stance_hint": None,
                "next_meeting": "N/A"
            }
            continue
        
        meetings = data.get("meetings", [])
        
        # Formatta meeting 1 e 2
        meeting_1 = "N/A"
        meeting_2 = "N/A"
        
        if len(meetings) >= 1:
            m1 = meetings[0]
            vote_str = f" ({m1['vote']})" if m1.get('vote') and m1['vote'] != 'N/A' else ""
            meeting_1 = f"{m1.get('change', 'N/A')} ({m1.get('date_formatted', 'N/A')}){vote_str}"
        
        if len(meetings) >= 2:
            m2 = meetings[1]
            vote_str = f" ({m2['vote']})" if m2.get('vote') and m2['vote'] != 'N/A' else ""
            meeting_2 = f"{m2.get('change', 'N/A')} ({m2.get('date_formatted', 'N/A')}){vote_str}"
        
        summary[currency] = {
            "bank_name": data.get("bank_name"),
            "bank_short": data.get("bank_short"),
            "current_rate": data.get("current_rate", "N/A"),
            "meeting_1": meeting_1,
            "meeting_2": meeting_2,
            "trend": data.get("trend", "unknown"),
            "trend_label": data.get("trend_label", "N/A"),
            "trend_emoji": data.get("trend_emoji", "❓"),
            "stance_hint": data.get("stance_hint"),
            "next_meeting": "N/A"  # Da implementare separatamente
        }
    
    return summary

PMI_CONFIG = {
    "USD": {
        "manufacturing": {"id": 173, "name": "ism-manufacturing-pmi", "label": "ISM Manufacturing", "country": "us"},
        "services": {"id": 176, "name": "ism-non-manufacturing-pmi", "label": "ISM Services", "country": "us"}
    },
    "EUR": {
        "manufacturing": {"id": 201, "name": "manufacturing-pmi", "label": "Manufacturing PMI", "country": "eu"},
        "services": {"id": 272, "name": "services-pmi", "label": "Services PMI", "country": "eu"}
    },
    "GBP": {
        "manufacturing": {"id": 204, "name": "manufacturing-pmi", "label": "Manufacturing PMI", "country": "uk"},
        "services": {"id": 274, "name": "services-pmi", "label": "Services PMI", "country": "uk"}
    },
    "JPY": {
        "manufacturing": {"id": 202, "name": "manufacturing-pmi", "label": "Manufacturing PMI", "country": "jp"},
        "services": {"id": 1912, "name": "services-pmi", "label": "Services PMI", "country": "jp"}
    },
    "CHF": {
        "manufacturing": {"id": 278, "name": "procure.ch-pmi", "label": "procure.ch PMI", "country": "ch"},
        "services": None  # CHF Services PMI non disponibile su Investing.com
    },
    "AUD": {
        "manufacturing": {"id": 1838, "name": "manufacturing-pmi", "label": "Manufacturing PMI", "country": "au"},
        "services": {"id": 1839, "name": "services-pmi", "label": "Services PMI", "country": "au"}
    },
    "CAD": {
        "manufacturing": {"id": 1029, "name": "manufacturing-pmi", "label": "Manufacturing PMI", "country": "us"},
        "services": {"id": 2265, "name": "services-pmi", "label": "Services PMI", "country": "us"}
    }
}

# =============================================================================
# CONFIGURAZIONE EVENTI ECONOMICI PER NEWS CATALYST
# =============================================================================
ECONOMIC_EVENTS_CONFIG = {
    "USD": {
        "nfp": {
            "id": 227, 
            "name": "nonfarm-payrolls", 
            "label": "Nonfarm Payrolls",
            "country": "us",
            "unit": "k",
            "thresholds": {"strong_pos": 100, "pos": 30, "neg": -30, "strong_neg": -100},
            "impact": "high"
        },
        "cpi": {
            "id": 733, 
            "name": "cpi", 
            "label": "CPI YoY",
            "country": "us",
            "unit": "%",
            "thresholds": {"strong_pos": 0.3, "pos": 0.2, "neg": -0.2, "strong_neg": -0.3},
            "impact": "high",
            "interpretation": "hawkish"  # Inflazione alta = hawkish = positivo per valuta
        },
        "gdp": {
            "id": 375, 
            "name": "gdp", 
            "label": "GDP QoQ",
            "country": "us",
            "unit": "%",
            "thresholds": {"strong_pos": 0.5, "pos": 0.3, "neg": -0.3, "strong_neg": -0.5},
            "impact": "high"
        },
        "unemployment": {
            "id": 300, 
            "name": "unemployment-rate", 
            "label": "Unemployment Rate",
            "country": "us",
            "unit": "%",
            "thresholds": {"strong_pos": -0.3, "pos": -0.2, "neg": 0.2, "strong_neg": 0.3},
            "impact": "high",
            "interpretation": "inverse"  # Disoccupazione bassa = positivo
        },
        "retail_sales": {
            "id": 256, 
            "name": "retail-sales", 
            "label": "Retail Sales MoM",
            "country": "us",
            "unit": "%",
            "thresholds": {"strong_pos": 0.5, "pos": 0.3, "neg": -0.3, "strong_neg": -0.5},
            "impact": "medium"
        },
        "jobless_claims": {
            "id": 294, 
            "name": "initial-jobless-claims", 
            "label": "Initial Jobless Claims",
            "country": "us",
            "unit": "k",
            "thresholds": {"strong_pos": -30, "pos": -15, "neg": 15, "strong_neg": 30},
            "impact": "medium",
            "interpretation": "inverse"  # Meno claims = positivo
        }
    },
    "EUR": {
        "cpi": {
            "id": 68, 
            "name": "cpi", 
            "label": "CPI YoY",
            "country": "eu",
            "unit": "%",
            "thresholds": {"strong_pos": 0.3, "pos": 0.2, "neg": -0.2, "strong_neg": -0.3},
            "impact": "high",
            "interpretation": "hawkish"
        },
        "gdp": {
            "id": 121, 
            "name": "gdp", 
            "label": "GDP QoQ",
            "country": "eu",
            "unit": "%",
            "thresholds": {"strong_pos": 0.5, "pos": 0.3, "neg": -0.3, "strong_neg": -0.5},
            "impact": "high"
        },
        "unemployment": {
            "id": 304, 
            "name": "unemployment-rate", 
            "label": "Unemployment Rate",
            "country": "eu",
            "unit": "%",
            "thresholds": {"strong_pos": -0.3, "pos": -0.2, "neg": 0.2, "strong_neg": 0.3},
            "impact": "medium",
            "interpretation": "inverse"
        },
        "retail_sales": {
            "id": 212, 
            "name": "retail-sales", 
            "label": "Retail Sales MoM",
            "country": "eu",
            "unit": "%",
            "thresholds": {"strong_pos": 0.5, "pos": 0.3, "neg": -0.3, "strong_neg": -0.5},
            "impact": "medium"
        }
    },
    "GBP": {
        "cpi": {
            "id": 67, 
            "name": "cpi", 
            "label": "CPI YoY",
            "country": "uk",
            "unit": "%",
            "thresholds": {"strong_pos": 0.3, "pos": 0.2, "neg": -0.2, "strong_neg": -0.3},
            "impact": "high",
            "interpretation": "hawkish"
        },
        "gdp": {
            "id": 122, 
            "name": "gdp", 
            "label": "GDP QoQ",
            "country": "uk",
            "unit": "%",
            "thresholds": {"strong_pos": 0.5, "pos": 0.3, "neg": -0.3, "strong_neg": -0.5},
            "impact": "high"
        },
        "unemployment": {
            "id": 305, 
            "name": "unemployment-rate", 
            "label": "Unemployment Rate",
            "country": "uk",
            "unit": "%",
            "thresholds": {"strong_pos": -0.3, "pos": -0.2, "neg": 0.2, "strong_neg": 0.3},
            "impact": "medium",
            "interpretation": "inverse"
        },
        "retail_sales": {
            "id": 256, 
            "name": "retail-sales", 
            "label": "Retail Sales MoM",
            "country": "uk",
            "unit": "%",
            "thresholds": {"strong_pos": 0.5, "pos": 0.3, "neg": -0.3, "strong_neg": -0.5},
            "impact": "medium"
        }
    },
    "JPY": {
        "cpi": {
            "id": 722, 
            "name": "national-cpi", 
            "label": "CPI YoY",
            "country": "jp",
            "unit": "%",
            "thresholds": {"strong_pos": 0.3, "pos": 0.2, "neg": -0.2, "strong_neg": -0.3},
            "impact": "high",
            "interpretation": "hawkish"
        },
        "gdp": {
            "id": 119, 
            "name": "gdp", 
            "label": "GDP QoQ",
            "country": "jp",
            "unit": "%",
            "thresholds": {"strong_pos": 0.5, "pos": 0.3, "neg": -0.3, "strong_neg": -0.5},
            "impact": "high"
        },
        "unemployment": {
            "id": 495, 
            "name": "unemployment-rate", 
            "label": "Unemployment Rate",
            "country": "jp",
            "unit": "%",
            "thresholds": {"strong_pos": -0.3, "pos": -0.2, "neg": 0.2, "strong_neg": 0.3},
            "impact": "medium",
            "interpretation": "inverse"
        },
        "retail_sales": {
            "id": 492, 
            "name": "retail-sales", 
            "label": "Retail Sales YoY",
            "country": "jp",
            "unit": "%",
            "thresholds": {"strong_pos": 0.5, "pos": 0.3, "neg": -0.3, "strong_neg": -0.5},
            "impact": "medium"
        }
    },
    "CHF": {
        "cpi": {
            "id": 328, 
            "name": "cpi", 
            "label": "CPI YoY",
            "country": "ch",
            "unit": "%",
            "thresholds": {"strong_pos": 0.3, "pos": 0.2, "neg": -0.2, "strong_neg": -0.3},
            "impact": "high",
            "interpretation": "hawkish"
        },
        "gdp": {
            "id": 336, 
            "name": "gdp", 
            "label": "GDP QoQ",
            "country": "ch",
            "unit": "%",
            "thresholds": {"strong_pos": 0.5, "pos": 0.3, "neg": -0.3, "strong_neg": -0.5},
            "impact": "high"
        },
        "unemployment": {
            "id": 327, 
            "name": "unemployment-rate", 
            "label": "Unemployment Rate",
            "country": "ch",
            "unit": "%",
            "thresholds": {"strong_pos": -0.3, "pos": -0.2, "neg": 0.2, "strong_neg": 0.3},
            "impact": "medium",
            "interpretation": "inverse"
        },
        "retail_sales": {
            "id": 335, 
            "name": "retail-sales", 
            "label": "Retail Sales YoY",
            "country": "ch",
            "unit": "%",
            "thresholds": {"strong_pos": 0.5, "pos": 0.3, "neg": -0.3, "strong_neg": -0.5},
            "impact": "medium"
        }
    },
    "AUD": {
        "cpi": {
            "id": 329, 
            "name": "cpi", 
            "label": "CPI QoQ",
            "country": "au",
            "unit": "%",
            "thresholds": {"strong_pos": 0.3, "pos": 0.2, "neg": -0.2, "strong_neg": -0.3},
            "impact": "high",
            "interpretation": "hawkish"
        },
        "gdp": {
            "id": 330, 
            "name": "gdp", 
            "label": "GDP QoQ",
            "country": "au",
            "unit": "%",
            "thresholds": {"strong_pos": 0.5, "pos": 0.3, "neg": -0.3, "strong_neg": -0.5},
            "impact": "high"
        },
        "unemployment": {
            "id": 323, 
            "name": "unemployment-rate", 
            "label": "Unemployment Rate",
            "country": "au",
            "unit": "%",
            "thresholds": {"strong_pos": -0.3, "pos": -0.2, "neg": 0.2, "strong_neg": 0.3},
            "impact": "high",
            "interpretation": "inverse"
        },
        "retail_sales": {
            "id": 331, 
            "name": "retail-sales", 
            "label": "Retail Sales MoM",
            "country": "au",
            "unit": "%",
            "thresholds": {"strong_pos": 0.5, "pos": 0.3, "neg": -0.3, "strong_neg": -0.5},
            "impact": "medium"
        }
    },
    "CAD": {
        "cpi": {
            "id": 741, 
            "name": "cpi", 
            "label": "CPI YoY",
            "country": "ca",
            "unit": "%",
            "thresholds": {"strong_pos": 0.3, "pos": 0.2, "neg": -0.2, "strong_neg": -0.3},
            "impact": "high",
            "interpretation": "hawkish"
        },
        "gdp": {
            "id": 234, 
            "name": "gdp", 
            "label": "GDP MoM",
            "country": "ca",
            "unit": "%",
            "thresholds": {"strong_pos": 0.3, "pos": 0.2, "neg": -0.2, "strong_neg": -0.3},
            "impact": "high"
        },
        "unemployment": {
            "id": 298, 
            "name": "unemployment-rate", 
            "label": "Unemployment Rate",
            "country": "ca",
            "unit": "%",
            "thresholds": {"strong_pos": -0.3, "pos": -0.2, "neg": 0.2, "strong_neg": 0.3},
            "impact": "high",
            "interpretation": "inverse"
        },
        "retail_sales": {
            "id": 235, 
            "name": "retail-sales", 
            "label": "Retail Sales MoM",
            "country": "ca",
            "unit": "%",
            "thresholds": {"strong_pos": 0.5, "pos": 0.3, "neg": -0.3, "strong_neg": -0.5},
            "impact": "medium"
        }
    },
    # Dati cinesi per correlazione AUD
    "CNY": {
        "gdp": {
            "id": 461, 
            "name": "chinese-gdp", 
            "label": "GDP YoY",
            "country": "cn",
            "unit": "%",
            "thresholds": {"strong_pos": 0.5, "pos": 0.3, "neg": -0.3, "strong_neg": -0.5},
            "impact": "high",
            "affects": ["AUD"]  # Correlazione con AUD
        },
        "trade_balance": {
            "id": 464, 
            "name": "trade-balance", 
            "label": "Trade Balance",
            "country": "cn",
            "unit": "B",
            "thresholds": {"strong_pos": 20, "pos": 10, "neg": -10, "strong_neg": -20},
            "impact": "medium",
            "affects": ["AUD"]
        }
    }
}


def fetch_economic_event_data(currency: str, event_key: str) -> dict:
    """
    Recupera i dati di un evento economico da Investing.com API JSON.
    Restituisce actual, forecast (se disponibile), previous e calcola la sorpresa.
    
    Args:
        currency: Codice valuta (USD, EUR, etc.)
        event_key: Chiave evento (nfp, cpi, gdp, etc.)
    
    Returns:
        dict con actual, forecast, previous, surprise, date, impact_score
    """
    config = ECONOMIC_EVENTS_CONFIG.get(currency, {}).get(event_key)
    
    if not config:
        return {"error": f"Event {event_key} not configured for {currency}"}
    
    country = config.get("country", "us")
    event_id = config["id"]
    json_url = f"https://sbcharts.investing.com/events_charts/{country}/{event_id}.json"
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json',
            'Referer': 'https://www.investing.com/',
        }
        
        response = requests.get(json_url, headers=headers, timeout=15)
        
        if response.status_code != 200:
            return {"error": f"HTTP {response.status_code}", "source": json_url}
        
        data = response.json()
        attr = data.get("attr", [])
        
        if len(attr) < 1:
            return {"error": "No data", "source": json_url}
        
        # Ultimo dato (più recente)
        latest = attr[-1]
        previous_data = attr[-2] if len(attr) >= 2 else None
        
        actual = latest.get("actual")
        forecast = latest.get("forecast")  # Potrebbe non esserci
        revised = latest.get("revised")
        timestamp = latest.get("timestamp", 0)
        
        # Converti timestamp in data
        event_date = None
        days_ago = None
        if timestamp:
            try:
                from datetime import datetime, timedelta
                event_date = datetime.fromtimestamp(timestamp / 1000)
                days_ago = (datetime.now() - event_date).days
            except:
                pass
        
        # Valore precedente
        previous = previous_data.get("actual") if previous_data else None
        
        # Calcola sorpresa (actual - forecast)
        surprise = None
        surprise_pct = None
        if actual is not None and forecast is not None:
            try:
                surprise = float(actual) - float(forecast)
                if float(forecast) != 0:
                    surprise_pct = (surprise / abs(float(forecast))) * 100
            except:
                pass
        
        # Calcola impact score basato su soglie
        impact_score = 0
        thresholds = config.get("thresholds", {})
        interpretation = config.get("interpretation", "normal")
        
        if surprise is not None:
            if interpretation == "inverse":
                # Per unemployment/jobless claims: sorpresa negativa è positiva
                surprise = -surprise
            
            if surprise >= thresholds.get("strong_pos", 999):
                impact_score = 2
            elif surprise >= thresholds.get("pos", 999):
                impact_score = 1
            elif surprise <= thresholds.get("strong_neg", -999):
                impact_score = -2
            elif surprise <= thresholds.get("neg", -999):
                impact_score = -1
        
        # Applica decadimento temporale
        if days_ago is not None:
            if days_ago > 7:
                impact_score = 0  # Troppo vecchio
            elif days_ago >= 5:
                impact_score = int(impact_score * 0.25)
            elif days_ago >= 3:
                impact_score = int(impact_score * 0.5)
            # 0-2 giorni: peso pieno
        
        return {
            "event": config["label"],
            "currency": currency,
            "actual": actual,
            "forecast": forecast,
            "previous": previous,
            "surprise": round(surprise, 2) if surprise is not None else None,
            "surprise_pct": round(surprise_pct, 1) if surprise_pct is not None else None,
            "impact_score": impact_score,
            "date": event_date.strftime("%Y-%m-%d") if event_date else None,
            "days_ago": days_ago,
            "unit": config.get("unit", ""),
            "impact_level": config.get("impact", "medium"),
            "source": "Investing.com API"
        }
        
    except Exception as e:
        return {"error": str(e)[:100], "source": json_url}


def fetch_all_economic_events(currencies: list = None) -> dict:
    """
    Recupera tutti gli eventi economici per le valute specificate.
    
    Returns:
        dict con eventi per valuta e sommario
    """
    if currencies is None:
        currencies = ["USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD"]
    
    all_events = {}
    
    for currency in currencies:
        currency_events = {}
        config = ECONOMIC_EVENTS_CONFIG.get(currency, {})
        
        for event_key in config.keys():
            event_data = fetch_economic_event_data(currency, event_key)
            if "error" not in event_data:
                currency_events[event_key] = event_data
        
        if currency_events:
            all_events[currency] = currency_events
    
    # Aggiungi dati CNY per correlazione AUD
    if "AUD" in currencies:
        cny_config = ECONOMIC_EVENTS_CONFIG.get("CNY", {})
        cny_events = {}
        for event_key in cny_config.keys():
            event_data = fetch_economic_event_data("CNY", event_key)
            if "error" not in event_data:
                cny_events[event_key] = event_data
        if cny_events:
            all_events["CNY"] = cny_events
    
    return all_events


def format_economic_events_for_claude(economic_events: dict) -> str:
    """
    Formatta gli eventi economici in testo per il prompt di Claude.
    """
    lines = []
    lines.append("=" * 60)
    lines.append("📊 DATI ECONOMICI RECENTI (per calcolo News Catalyst)")
    lines.append("=" * 60)
    lines.append("")
    
    for currency, events in economic_events.items():
        if not events:
            continue
            
        lines.append(f"### {currency}:")
        for event_key, data in events.items():
            if "error" in data:
                continue
            
            actual = data.get("actual", "N/A")
            forecast = data.get("forecast", "N/A")
            surprise = data.get("surprise")
            days_ago = data.get("days_ago", "?")
            unit = data.get("unit", "")
            event_name = data.get("event", event_key)
            impact = data.get("impact_level", "medium")
            
            surprise_str = f"{surprise:+.2f}" if surprise is not None else "N/A"
            impact_emoji = "⭐⭐⭐" if impact == "high" else "⭐⭐" if impact == "medium" else "⭐"
            
            # Indica se sorpresa è significativa
            impact_score = data.get("impact_score", 0)
            if impact_score >= 2:
                signal = "🟢🟢 MOLTO POSITIVO"
            elif impact_score == 1:
                signal = "🟢 Positivo"
            elif impact_score <= -2:
                signal = "🔴🔴 MOLTO NEGATIVO"
            elif impact_score == -1:
                signal = "🔴 Negativo"
            else:
                signal = "⚪ Neutro"
            
            lines.append(f"  - {event_name} {impact_emoji}")
            lines.append(f"    Actual: {actual}{unit} | Forecast: {forecast}{unit} | Sorpresa: {surprise_str}{unit}")
            lines.append(f"    {days_ago} giorni fa | Impatto: {signal}")
            lines.append("")
    
    # Aggiungi nota su correlazioni
    lines.append("")
    lines.append("📌 CORRELAZIONI IMPORTANTI:")
    lines.append("  - AUD: considera anche dati CNY (Cina = primo partner commerciale)")
    lines.append("  - CAD: considera anche prezzo petrolio")
    lines.append("  - CHF/JPY: beneficiano da risk-off")
    lines.append("")
    
    return "\n".join(lines)


def fetch_pmi_from_investing_json(currency: str, pmi_type: str) -> dict:
    """
    Scarica i dati PMI dall'API JSON di Investing.com (più affidabile).
    
    Args:
        currency: Codice valuta (USD, EUR, GBP, JPY, CHF, AUD, CAD)
        pmi_type: "manufacturing" o "services"
    
    Returns:
        dict con: current, previous, delta, date, source
    """
    config = PMI_CONFIG.get(currency, {}).get(pmi_type)
    
    if config is None:
        return {"current": None, "previous": None, "delta": None, "date": None, "source": "N/A"}
    
    # API JSON endpoint con country code corretto
    country = config.get("country", "us")
    json_url = f"https://sbcharts.investing.com/events_charts/{country}/{config['id']}.json"
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json',
            'Referer': 'https://www.investing.com/',
        }
        
        response = requests.get(json_url, headers=headers, timeout=15)
        
        if response.status_code != 200:
            return {"current": None, "previous": None, "delta": None, "source": json_url, "error": f"HTTP {response.status_code}"}
        
        data = response.json()
        
        # Estrai dati dall'array "attr" (contiene i valori formattati)
        attr = data.get("attr", [])
        
        if len(attr) >= 2:
            # L'ultimo elemento è il più recente
            current_data = attr[-1]
            previous_data = attr[-2]
            
            current_value = current_data.get("actual")
            previous_value = previous_data.get("actual")
            
            # Verifica che siano numeri validi per PMI (30-70)
            if current_value and 30 <= float(current_value) <= 70:
                current_value = float(current_value)
            else:
                current_value = None
                
            if previous_value and 30 <= float(previous_value) <= 70:
                previous_value = float(previous_value)
            else:
                previous_value = None
            
            delta = None
            if current_value is not None and previous_value is not None:
                delta = round(current_value - previous_value, 1)
            
            return {
                "current": current_value,
                "previous": previous_value,
                "delta": delta,
                "date": None,
                "source": "Investing.com API",
                "label": config['label']
            }
        
        elif len(attr) == 1:
            current_data = attr[0]
            current_value = current_data.get("actual")
            
            if current_value and 30 <= float(current_value) <= 70:
                return {
                    "current": float(current_value),
                    "previous": None,
                    "delta": None,
                    "date": None,
                    "source": "Investing.com API",
                    "label": config['label']
                }
        
        return {"current": None, "previous": None, "delta": None, "source": json_url, "error": "No data in response"}
        
    except Exception as e:
        return {"current": None, "previous": None, "delta": None, "source": json_url, "error": str(e)[:50]}


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
                r'Actual.*?([0-9]{2}\.[0-9]{1,2})',  # Fixed: 1-2 decimali
                r'PMI[+\s]+([0-9]{2}\.[0-9]{1,2})',  # Pattern per Twitter share: PMI+46.50
                r'event_last_actual["\s:]+([0-9]{2}\.[0-9]{1,2})',  # JSON data
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
                r'Previous.*?([0-9]{2}\.[0-9]{1,2})',  # Fixed: 1-2 decimali
                r'event_last_previous["\s:]+([0-9]{2}\.[0-9]{1,2})',  # JSON data
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


def fetch_pmi_via_duckduckgo(currency: str, pmi_type: str) -> dict:
    """
    Fallback: cerca i dati PMI più recenti via DuckDuckGo.
    
    Args:
        currency: Codice valuta (USD, EUR, GBP, JPY, CHF, AUD, CAD)
        pmi_type: "manufacturing" o "services"
    
    Returns:
        dict con: current, previous, delta, date, source
    """
    currency_names = {
        "USD": "US ISM" if pmi_type == "manufacturing" else "US ISM Non-Manufacturing",
        "EUR": "Eurozone",
        "GBP": "UK",
        "JPY": "Japan Jibun Bank",
        "CHF": "Switzerland procure.ch" if pmi_type == "manufacturing" else "Switzerland Services",
        "AUD": "Australia",
        "CAD": "Canada Ivey" if pmi_type == "manufacturing" else "Canada Services"
    }
    
    search_term = f"{currency_names.get(currency, currency)} {pmi_type} PMI January 2026"
    
    try:
        results = DDGS().text(search_term, max_results=5)
        
        current_value = None
        previous_value = None
        
        for r in results:
            text = r.get('body', '') + ' ' + r.get('title', '')
            
            # Cerca pattern come "PMI 47.9" o "came in at 52.3"
            pmi_patterns = [
                r'PMI[:\s]+(\d{2}\.\d)',
                r'(?:came in|fell to|rose to|at|to)\s+(\d{2}\.\d)',
                r'(\d{2}\.\d)\s*(?:in|for|from)',
                r'(?:actual|reading)[:\s]+(\d{2}\.\d)',
            ]
            
            for pattern in pmi_patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    try:
                        val = float(match.group(1))
                        if 30 <= val <= 70:  # Range valido per PMI
                            if current_value is None:
                                current_value = val
                            elif previous_value is None and val != current_value:
                                previous_value = val
                            break
                    except:
                        pass
            
            if current_value and previous_value:
                break
        
        # Calcola delta
        delta = None
        if current_value is not None and previous_value is not None:
            delta = round(current_value - previous_value, 1)
        
        if current_value is not None:
            return {
                "current": current_value,
                "previous": previous_value,
                "delta": delta,
                "date": None,
                "source": "DuckDuckGo Search",
                "label": f"{pmi_type.capitalize()} PMI"
            }
        
        return {"current": None, "previous": None, "delta": None, "date": None, "source": "DuckDuckGo Search", "error": "No PMI found"}
        
    except Exception as e:
        return {"current": None, "previous": None, "delta": None, "date": None, "source": "DuckDuckGo Search", "error": str(e)}


def fetch_all_pmi_data() -> dict:
    """
    Recupera tutti i dati PMI per le 7 valute.
    Priorità: 1) API JSON Investing.com, 2) HTML scraping, 3) DuckDuckGo
    
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
        # 1) Prova API JSON (più affidabile)
        result = fetch_pmi_from_investing_json(currency, "manufacturing")
        
        # 2) Se fallisce, prova HTML scraping
        if result.get("current") is None:
            time.sleep(1.0)
            result = fetch_pmi_from_investing(currency, "manufacturing")
        
        # 3) Se ancora fallisce, prova DuckDuckGo
        if result.get("current") is None:
            time.sleep(0.5)
            fallback_result = fetch_pmi_via_duckduckgo(currency, "manufacturing")
            if fallback_result.get("current") is not None:
                result = fallback_result
        
        pmi_data[currency]["manufacturing"] = result
        
        # Delay tra richieste
        time.sleep(1.5)
        
        # Services PMI
        if currency == "CHF":
            # CHF Services da TradingEconomics (non disponibile su Investing.com)
            result = fetch_chf_services_pmi_tradingeconomics()
        else:
            # 1) Prova API JSON
            result = fetch_pmi_from_investing_json(currency, "services")
            
            # 2) Se fallisce, prova HTML scraping
            if result.get("current") is None:
                time.sleep(1.0)
                result = fetch_pmi_from_investing(currency, "services")
        
        # 3) Se ancora fallisce, prova DuckDuckGo
        if result.get("current") is None:
            time.sleep(0.5)
            fallback_result = fetch_pmi_via_duckduckgo(currency, "services")
            if fallback_result.get("current") is not None:
                result = fallback_result
        
        pmi_data[currency]["services"] = result
        
        # Delay tra valute (2 secondi)
        time.sleep(2.0)
    
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

SYSTEM_PROMPT_GLOBAL = """Sei un analista macroeconomico forex senior. Devi analizzare 7 VALUTE singolarmente.

## ⚠️ REGOLA CRITICA: USA I DATI FORNITI, NON CONOSCENZE OBSOLETE!

Le tue conoscenze potrebbero essere OBSOLETE. Devi:
1. **LEGGERE ATTENTAMENTE** tutti i dati macro, PMI e notizie web forniti
2. **BASARTI SOLO** sulle informazioni fornite nel prompt
3. **NON ASSUMERE** che le banche centrali mantengano politiche passate

## APPROCCIO: ANALISI PER VALUTA

Devi analizzare **7 VALUTE SEPARATAMENTE**: EUR, USD, GBP, JPY, CHF, AUD, CAD

Per ogni valuta assegna un punteggio **ASSOLUTO** su 8 parametri.
Il sistema calcolerà automaticamente i differenziali per le 19 coppie forex.

**Vantaggi di questo approccio:**
- Coerenza garantita: se EUR > GBP > CAD, allora EUR/CAD sarà coerente
- Analisi più precisa e meno soggetta a errori

## LINGUA: TUTTO IN ITALIANO

## STRUTTURA JSON OBBLIGATORIA
Rispondi SOLO con un JSON valido, senza markdown, senza ```json, senza commenti.

## ═══════════════════════════════════════════════════════════════════
## SISTEMA DI SCORING - 8 PARAMETRI CON CRITERI OGGETTIVI
## ═══════════════════════════════════════════════════════════════════

### 1️⃣ TASSI ATTUALI [-1 a +1]
**Logica:** Tassi più alti attirano capitali (carry trade).

| Tasso BC | Score | Motivo |
|----------|-------|--------|
| ≥ 3.5% | +1 | Rendimento attraente, flussi in entrata |
| 1.5% - 3.49% | 0 | Rendimento medio |
| < 1.5% | -1 | Rendimento basso, flussi in uscita |

---

### 2️⃣ ASPETTATIVE TASSI [-2 a +2] ⭐ PESO DOPPIO
**Logica:** Il mercato guarda avanti. Le aspettative future contano più del presente.

| Scenario | Score |
|----------|-------|
| BC hawkish, rialzi attesi, inflazione problematica | +2 |
| BC neutrale con bias hawkish, hold prolungato atteso | +1 |
| BC neutrale, incertezza elevata | 0 |
| BC leggermente dovish, tagli probabili entro 3-6 mesi | -1 |
| BC molto dovish, in ciclo di tagli attivo | -2 |

⚠️ USA SOLO LE NOTIZIE WEB E LO STORICO BC FORNITI per determinare stance!

---

### 3️⃣ INFLAZIONE [-1 a +1]
**Logica FOREX:** Inflazione alta → BC non può tagliare → tassi alti → valuta forte

| Inflazione | Score | Motivo |
|------------|-------|--------|
| > 3% | +1 | Pressione hawkish, BC non può tagliare |
| 2% - 3% | 0 | Al target, BC ha flessibilità |
| < 2% | -1 | Sotto target, BC può/deve tagliare |

---

### 4️⃣ CRESCITA/PIL [-1 a +1]
**Logica:** Crescita sana attira investimenti e rafforza la valuta.

| PIL YoY | Score | Condizione |
|---------|-------|------------|
| > 2% | +1 | Solo se inflazione < 4% (crescita sostenibile) |
| 1% - 2% | 0 | Crescita moderata |
| < 1% | -1 | Stagnazione o recessione |

⚠️ PIL alto con inflazione alta = 0 (crescita non sostenibile)

---

### 5️⃣ PMI [-1 a +1]
**Logica:** PMI > 50 = espansione, PMI < 50 = contrazione

**PESI SETTORIALI per valuta:**
| Valuta | Peso Manifattura | Peso Servizi | Motivo |
|--------|------------------|--------------|--------|
| EUR | 50% | 50% | Economia mista |
| USD | 30% | 70% | Economia servizi-dominante |
| GBP | 20% | 80% | Servizi finanziari dominanti |
| JPY | 60% | 40% | Export manifatturiero |
| CHF | 50% | 50% | Economia mista |
| AUD | 50% | 50% | Mining + servizi |
| CAD | 40% | 60% | Risorse + servizi |

**Calcolo:** PMI_pesato = (Manuf × Peso_M) + (Serv × Peso_S)

| PMI Pesato | Score |
|------------|-------|
| > 52 | +1 |
| 48 - 52 | 0 |
| < 48 | -1 |

---

### 6️⃣ RISK SENTIMENT [-1 a +1]
**Logica:** In risk-off, capitali verso safe-haven. In risk-on, verso cicliche.

**Classificazione valute:**
- **Safe-haven:** USD, JPY, CHF
- **Cicliche:** AUD, CAD, GBP
- **Semi-ciclica:** EUR

**Determina il regime di mercato dalle notizie:**
- VIX > 25 O tensioni geopolitiche acute → **Risk-OFF**
- VIX < 18 E sentiment positivo → **Risk-ON**
- Altrimenti → **Neutro**

| Regime | Safe-Haven | Cicliche | Semi-cicliche |
|--------|------------|----------|---------------|
| Risk-OFF | +1 | -1 | 0 |
| Neutro | 0 | 0 | 0 |
| Risk-ON | -1 | +1 | 0 |

---

### 7️⃣ BILANCIA/FISCALE [-1 a +1]
**Logica:** Importante nel lungo termine. Peso solo se notizie specifiche.

| Scenario | Score |
|----------|-------|
| Current Account surplus + debito gestibile | +1 |
| Situazione nella media O nessuna notizia | 0 |
| Deficit gemelli O crisi debito in corso | -1 |

**Regola pratica:** Se non ci sono notizie su crisi fiscali/debito → 0

---

### 8️⃣ NEWS CATALYST [-2 a +2] ⭐ PESO DOPPIO

**Logica:** Cattura SOLO le SORPRESE economiche recenti (actual ≠ forecast).

## ⛔ REGOLE RIGIDE - LEGGERE ATTENTAMENTE! ⛔

**PRIMA di assegnare qualsiasi punteggio, verifica:**
1. ✅ Ho un DATO CONCRETO con Actual vs Forecast? → Se NO → **Score = 0**
2. ✅ Questo fattore è GIÀ conteggiato in un altro parametro? → Se SÌ → **Score = 0**
3. ✅ La "sorpresa" è avvenuta negli ultimi 7 giorni? → Se NO → **Score = 0**

**CHECKLIST ANTI-DOPPIO CONTEGGIO:**
| Se hai già dato punti per... | NON puoi dare punti in News Catalyst per... |
|------------------------------|---------------------------------------------|
| Aspettative Tassi (BC hawkish/dovish) | "BC hawkish/dovish stance" |
| Tassi Attuali (differenziale tassi) | "Tassi alti/bassi", "carry trade" |
| Inflazione | "Inflazione alta/bassa" (senza sorpresa) |
| Risk Sentiment (tensioni geopolitiche) | "Tensioni geopolitiche", "safe-haven demand" |

**PARTE 1: DATI ECONOMICI (peso 70%)**

⚠️ OBBLIGATORIO: Devi citare Actual vs Forecast nella motivazione!

| Indicatore | +2 | +1 | -1 | -2 |
|------------|----|----|----|----|
| NFP (USD) | Sorpresa ≥+100k | +30k a +99k | -30k a -99k | ≤-100k |
| CPI YoY | Sorpresa ≥+0.3pp | +0.2pp | -0.2pp | ≤-0.3pp |
| GDP QoQ | Sorpresa ≥+0.5pp | +0.3pp | -0.3pp | ≤-0.5pp |
| Retail Sales | Sorpresa ≥+0.5% | +0.3% | -0.3% | ≤-0.5% |
| PMI Flash | Sorpresa ≥+3pt | +1.5pt | -1.5pt | ≤-3pt |

**Se NON ci sono dati con sorprese significative → Score Dati = 0**
**"Mancanza di dati positivi" NON è un motivo per dare -1 o -2!**

**PARTE 2: GEOPOLITICA (peso 30%)**

⚠️ SOLO per eventi NUOVI (<48h) NON ancora riflessi in Risk Sentiment!
Se Risk Sentiment ≠ 0 → Score Geopolitica = 0 (già conteggiato!)

| Evento | Safe-Haven | Cicliche |
|--------|------------|----------|
| Shock grave improvviso (<48h) e NON in Risk Sentiment | +2 | -2 |
| Tensione nuova (<48h) e NON in Risk Sentiment | +1 | -1 |
| Qualsiasi altra situazione | **0** | **0** |

**Formula:** News_Catalyst = round((0.7 × Score_Dati) + (0.3 × Score_Geo))

## ESEMPI DI MOTIVAZIONI

❌ **SBAGLIATE (score dovrebbe essere 0):**
- "BOJ hawkish stance unica nel G7" → già in Aspettative Tassi!
- "Mancanza di dati recenti positivi" → non è una sorpresa negativa!
- "Differenziale monetario favorevole" → già in Tassi Attuali!
- "Safe-haven demand per tensioni" → già in Risk Sentiment!
- "BOE dovish pesa negativamente" → già in Aspettative Tassi!

✅ **CORRETTE:**
- "NFP 256k vs 180k atteso, sorpresa +76k → +1" (dato concreto con sorpresa)
- "CPI 2.9% vs 2.6% atteso, sorpresa +0.3pp → +1" (dato concreto con sorpresa)
- "Nessuna sorpresa significativa nei dati recenti → 0" (corretto!)
- "Dati in linea con attese, geopolitica già in Risk Sentiment → 0" (corretto!)

**NEL DUBBIO → DAI 0!**

---

## ═══════════════════════════════════════════════════════════════════
## RANGE TOTALI PER VALUTA
## ═══════════════════════════════════════════════════════════════════

- **Aspettative Tassi**: da -2 a +2 (peso doppio)
- **News Catalyst**: da -2 a +2 (peso doppio)
- **Altri 6 parametri**: da -1 a +1
- **TOTALE per valuta**: da -10 a +10

## ═══════════════════════════════════════════════════════════════════
## FORMATO OUTPUT JSON
## ═══════════════════════════════════════════════════════════════════

{
    "analysis_date": "YYYY-MM-DD",
    "market_regime": "risk-on | risk-off | neutral",
    "market_summary": "Breve riassunto del contesto macro globale in italiano (2-3 frasi)",
    "currency_analysis": {
        "EUR": {
            "total_score": 0,
            "summary": "Sintesi della situazione EUR con dati numerici",
            "scores": {
                "tassi_attuali": {
                    "score": 0,
                    "motivation": "BCE 2.15%, livello medio nel contesto G7"
                },
                "aspettative_tassi": {
                    "score": -1,
                    "motivation": "BCE neutrale ma mercati prezzano 60% prob taglio entro marzo"
                },
                "inflazione": {
                    "score": 0,
                    "motivation": "2.14% vicino al target 2%, situazione controllata"
                },
                "crescita_pil": {
                    "score": -1,
                    "motivation": "PIL 0.7%, stagnazione con Germania in difficoltà"
                },
                "pmi": {
                    "score": 0,
                    "motivation": "PMI pesato 50.6 (Manuf 48.8 × 50% + Serv 52.4 × 50%), neutro"
                },
                "risk_sentiment": {
                    "score": 0,
                    "motivation": "EUR semi-ciclica, neutrale in regime attuale"
                },
                "bilancia_fiscale": {
                    "score": 0,
                    "motivation": "Nessuna notizia rilevante su crisi fiscale Eurozona"
                },
                "news_catalyst": {
                    "score": 0,
                    "motivation": "CPI in linea con attese. Nessuna sorpresa significativa"
                }
            }
        },
        "USD": {
            "total_score": 2,
            "summary": "Sintesi della situazione USD con dati numerici",
            "scores": {
                "tassi_attuali": {
                    "score": 1,
                    "motivation": "Fed 3.75%, tra i più alti G7, carry attraente"
                },
                "aspettative_tassi": {
                    "score": -1,
                    "motivation": "Fed in ciclo tagli (2 consecutivi), stance dovish"
                },
                "inflazione": {
                    "score": 0,
                    "motivation": "2.74% sopra target ma in calo, situazione gestibile"
                },
                "crescita_pil": {
                    "score": 1,
                    "motivation": "PIL 2.1% con inflazione in calo, crescita sostenibile"
                },
                "pmi": {
                    "score": 1,
                    "motivation": "PMI pesato 53.2 (Manuf 49.3 × 30% + Serv 54.8 × 70%), espansione"
                },
                "risk_sentiment": {
                    "score": 0,
                    "motivation": "USD safe-haven ma regime neutro, nessun flusso risk-off"
                },
                "bilancia_fiscale": {
                    "score": 0,
                    "motivation": "Deficit elevato ma nessun impatto immediato"
                },
                "news_catalyst": {
                    "score": 0,
                    "motivation": "NFP in linea con attese. Geopolitica già in risk sentiment"
                }
            }
        },
        "GBP": { "total_score": 0, "summary": "...", "scores": { ... } },
        "JPY": { "total_score": 0, "summary": "...", "scores": { ... } },
        "CHF": { "total_score": 0, "summary": "...", "scores": { ... } },
        "AUD": { "total_score": 0, "summary": "...", "scores": { ... } },
        "CAD": { "total_score": 0, "summary": "...", "scores": { ... } }
    },
    "weekly_events_warning": "⚠️ Eventi ad alto impatto: Mar 21 Fed Decision, Gio 23 ECB Decision"
}

## ⚠️ REGOLE CRITICHE FINALI

1. **TUTTE LE 7 VALUTE OBBLIGATORIE**: EUR, USD, GBP, JPY, CHF, AUD, CAD
2. **total_score = SOMMA degli 8 punteggi** (verifica che sia corretto!)
3. **USA SOLO I DATI FORNITI** - non inventare
4. **MOTIVAZIONI CON NUMERI**: cita sempre i valori specifici (tassi %, inflazione %, PMI)
5. **COERENZA**: se dai +1 a USD per tassi alti, non dare +1 anche a EUR che ha tassi più bassi

## ⛔ REGOLA CRITICA NEWS CATALYST ⛔

**News Catalyst richiede SORPRESE CONCRETE (Actual vs Forecast)!**

- ❌ "BC hawkish/dovish" → GIÀ IN ASPETTATIVE TASSI → News Catalyst = 0
- ❌ "Tassi alti/bassi" → GIÀ IN TASSI ATTUALI → News Catalyst = 0  
- ❌ "Safe-haven/tensioni" → GIÀ IN RISK SENTIMENT → News Catalyst = 0
- ❌ "Mancanza di dati positivi" → NON È UNA SORPRESA → News Catalyst = 0
- ✅ "CPI 2.9% vs 2.6% atteso (+0.3pp sorpresa)" → CORRETTO, è una sorpresa concreta

**Se non hai un dato Actual vs Forecast da citare → News Catalyst = 0**
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


# ============================================================================
# FUNZIONE CALCOLO DIFFERENZIALI COPPIE DA VALUTE
# ============================================================================

SCORE_PARAMETERS = [
    "tassi_attuali",
    "aspettative_tassi", 
    "inflazione",
    "crescita_pil",
    "pmi",
    "risk_sentiment",
    "bilancia_fiscale",
    "news_catalyst"
]

def calculate_pair_from_currencies(currency_analysis: dict) -> dict:
    """
    Calcola i punteggi per le 19 coppie forex a partire dai punteggi delle 7 valute.
    
    Args:
        currency_analysis: Dict con struttura {
            "EUR": {"total_score": X, "summary": "...", "scores": {...}},
            "USD": {...},
            ...
        }
    
    Returns:
        pair_analysis: Dict con struttura compatibile con UI esistente
    """
    pair_analysis = {}
    
    for pair in FOREX_PAIRS:
        base, quote = pair.split("/")
        
        # Verifica che entrambe le valute siano presenti
        if base not in currency_analysis or quote not in currency_analysis:
            continue
        
        base_data = currency_analysis[base]
        quote_data = currency_analysis[quote]
        
        # Calcola i punteggi per ogni parametro
        scores = {}
        for param in SCORE_PARAMETERS:
            base_score = base_data.get("scores", {}).get(param, {}).get("score", 0)
            quote_score = quote_data.get("scores", {}).get(param, {}).get("score", 0)
            base_motivation = base_data.get("scores", {}).get(param, {}).get("motivation", "")
            quote_motivation = quote_data.get("scores", {}).get(param, {}).get("motivation", "")
            
            scores[param] = {
                "base": base_score,
                "quote": quote_score,
                "motivation_base": f"{base}: {base_motivation}",
                "motivation_quote": f"{quote}: {quote_motivation}"
            }
        
        # Calcola totali
        score_base = base_data.get("total_score", 0)
        score_quote = quote_data.get("total_score", 0)
        differential = score_base - score_quote
        
        # Genera summary combinato
        base_summary = base_data.get("summary", "")
        quote_summary = quote_data.get("summary", "")
        combined_summary = f"{base}: {base_summary} | {quote}: {quote_summary}"
        
        pair_analysis[pair] = {
            "summary": combined_summary,
            "score_base": score_base,
            "score_quote": score_quote,
            "differential": differential,
            "scores": scores,
            # Manteniamo campi per compatibilità
            "key_drivers": [],
            "current_price": "",
            "price_scenarios": {}
        }
    
    return pair_analysis


def analyze_with_claude(api_key: str, macro_data: dict = None, news_text: str = "", additional_text: str = "", pmi_data: dict = None, forex_prices: dict = None, economic_events: dict = None, cb_history_data: dict = None) -> dict:
    """
    Esegue l'analisi con Claude AI.
    
    Args:
        api_key: Chiave API Anthropic
        macro_data: Dati macroeconomici (opzionale)
        news_text: Testo delle notizie web (opzionale)
        additional_text: Testo delle risorse aggiuntive (opzionale)
        pmi_data: Dati PMI per valuta (opzionale)
        forex_prices: Prezzi forex in tempo reale (opzionale)
        economic_events: Dati eventi economici recenti per News Catalyst (opzionale)
        cb_history_data: Storico decisioni banche centrali (opzionale)
    """
    client = anthropic.Anthropic(api_key=api_key)
    
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
    
    # Sezione prezzi forex (se presente)
    prices_section = ""
    if forex_prices and forex_prices.get("success") and forex_prices.get("prices"):
        prices = forex_prices["prices"]
        source = forex_prices.get("source", "API")
        prices_lines = [f"**{pair}:** {price}" for pair, price in prices.items()]
        prices_section = f"""
## 💱 PREZZI FOREX ATTUALI (fonte: {source}):
{chr(10).join(prices_lines)}

⚠️ USA QUESTI PREZZI REALI per le proiezioni "current_price" e "price_scenarios" nel JSON.

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
    
    # Sezione Storico Banche Centrali
    cb_history_section = ""
    cb_history = cb_history_data if cb_history_data else {}
    if cb_history:
        cb_lines = []
        for curr in ["USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD"]:
            data = cb_history.get(curr, {})
            if data:
                line = f"**{data.get('bank_short', curr)}** ({curr}): {data.get('meeting_1', 'N/A')}, {data.get('meeting_2', 'N/A')} → Trend: {data.get('trend_emoji', '')} {data.get('trend_label', 'N/A')}"
                stance_hint = data.get('stance_hint')
                if stance_hint:
                    line += f" [Stance hint: {stance_hint}]"
                cb_lines.append(line)
        
        cb_history_section = f"""
## 📜 STORICO DECISIONI BANCHE CENTRALI (ultimi 2 meeting):
{chr(10).join(cb_lines)}

⚠️ **REGOLE IMPORTANTI PER LA STANCE:**
- Se trend = "Hiking" (2 rialzi consecutivi) → La stance NON PUÒ essere "Dovish"
- Se trend = "Cutting" (2 tagli consecutivi) → La stance NON PUÒ essere "Hawkish"
- Considera anche il "dissent" (🕊️ = membri volevano tagliare, 🦅 = membri volevano alzare)
- Il trend storico deve essere COERENTE con la stance finale
- Le aspettative OIS sono importanti ma non possono contraddire il trend storico recente

---
"""
    
    # Sezione Dati Economici Recenti per News Catalyst
    economic_events_section = ""
    if economic_events:
        economic_events_section = format_economic_events_for_claude(economic_events)
        economic_events_section = f"""
{economic_events_section}

---
"""
    
    today = get_italy_now()
    
    currencies_list = ", ".join(CURRENCIES.keys())
    
    user_prompt = f"""
## ⛔ REQUISITO CRITICO: ANALIZZA TUTTE LE 7 VALUTE! ⛔
Devi analizzare OGNI SINGOLA valuta nella lista seguente. NON saltare nessuna valuta!

**Lista completa delle 7 valute (TUTTE obbligatorie):**
{currencies_list}

⚠️ Se l'output JSON non contiene tutte le 7 valute in "currency_analysis", l'analisi sarà INCOMPLETA!

## 📅 DATA ODIERNA: {today.strftime('%Y-%m-%d')} ({today.strftime('%A, %d %B %Y')})

**Dettagli valute:**
{currencies_info}

---

{macro_section}
{pmi_section}
{cb_history_section}
{economic_events_section}
{prices_section}
{news_section}
{additional_section}

## ⭐ ISTRUZIONI:

1. **ANALIZZA LE 7 VALUTE SINGOLARMENTE** - il sistema calcolerà i differenziali per le 19 coppie
2. **USA TUTTE LE INFORMAZIONI DISPONIBILI** per determinare il punteggio
3. **ASPETTATIVE > TASSI ATTUALI**: il mercato guarda AVANTI
4. **PMI sono LEADING indicators**: anticipano la crescita futura
5. **PIL è LAGGING indicator**: conferma la crescita passata
6. **analysis_date** = "{today.strftime('%Y-%m-%d')}"
7. Ogni **summary** deve spiegare la situazione della valuta con DATI NUMERICI
8. **total_score** = somma degli 8 punteggi parametro (verifica sia corretto!)

Produci l'analisi COMPLETA in formato JSON.
Restituisci SOLO il JSON valido, senza markdown o testo aggiuntivo.
"""
    
    try:
        # Usa streaming per evitare timeout su richieste lunghe
        response_text = ""
        with client.messages.stream(
            model="claude-sonnet-4-20250514",
            max_tokens=12000,  # Ridotto: ora analizziamo 7 valute invece di 19 coppie
            messages=[{"role": "user", "content": user_prompt}],
            system=SYSTEM_PROMPT_GLOBAL
        ) as stream:
            for text in stream.text_stream:
                response_text += text
        
        # Pulisci JSON da markdown
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0]
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0]
        
        response_text = response_text.strip()
        
        # Estrai solo il JSON
        start_idx = response_text.find('{')
        end_idx = response_text.rfind('}')
        
        if start_idx != -1 and end_idx != -1:
            response_text = response_text[start_idx:end_idx+1]
        
        # Primo tentativo: parsing diretto
        try:
            analysis = json.loads(response_text)
        except json.JSONDecodeError as e:
            import time
            error_msg = str(e)
            error_pos = e.pos if hasattr(e, 'pos') else None
            
            # ===== TENTATIVO 1: Riparazione locale con regex (veloce, gratis) =====
            def quick_fix_json(json_str, pos):
                """Prova a fixare velocemente aggiungendo virgole mancanti"""
                import re
                
                # Fix generico: aggiungi virgole dove mancano
                fixed = json_str
                
                # Pattern: "valore" seguito da newline e "chiave":
                fixed = re.sub(r'"\s*\n\s*"([^"]+)":', r'",\n"\1":', fixed)
                
                # Pattern: numero seguito da newline e "chiave":
                fixed = re.sub(r'(\d)\s*\n\s*"([^"]+)":', r'\1,\n"\2":', fixed)
                
                # Pattern: } seguito da newline e "chiave":
                fixed = re.sub(r'\}\s*\n\s*"([^"]+)":', r'},\n"\1":', fixed)
                
                # Pattern: ] seguito da newline e "chiave":
                fixed = re.sub(r'\]\s*\n\s*"([^"]+)":', r'],\n"\1":', fixed)
                
                # Pattern: true/false/null seguito da "chiave":
                fixed = re.sub(r'(true|false|null)\s*\n\s*"([^"]+)":', r'\1,\n"\2":', fixed)
                
                # Rimuovi virgole trailing
                fixed = re.sub(r',\s*\}', '}', fixed)
                fixed = re.sub(r',\s*\]', ']', fixed)
                
                # Se abbiamo la posizione dell'errore, prova fix mirato
                if pos and pos < len(json_str):
                    # Cerca indietro per trovare dove manca la virgola
                    for i in range(pos - 1, max(0, pos - 100), -1):
                        if json_str[i] in '"}]' and i < len(fixed):
                            # Verifica se dopo c'è una virgola
                            rest = json_str[i+1:pos].strip()
                            if rest and not rest.startswith(',') and not rest.startswith('}') and not rest.startswith(']'):
                                # Inserisci virgola
                                fixed = fixed[:i+1] + ',' + fixed[i+1:]
                                break
                
                return fixed
            
            # Prova fix locale
            try:
                fixed_local = quick_fix_json(response_text, error_pos)
                analysis = json.loads(fixed_local)
            except json.JSONDecodeError:
                # ===== TENTATIVO 2: Chiedi a Claude Sonnet con delay =====
                # Aspetta per evitare rate limit (la chiamata principale ha appena finito)
                time.sleep(15)  # 15 secondi di pausa
                
                fix_prompt = f"""Il seguente JSON ha un errore di sintassi:

ERRORE: {error_msg}

JSON DA CORREGGERE:
{response_text}

Correggi SOLO l'errore di sintassi (probabilmente una virgola mancante).
Restituisci SOLO il JSON corretto, senza spiegazioni, senza markdown, senza ```."""

                # Prova fino a 2 volte con delay
                for attempt in range(2):
                    try:
                        if attempt > 0:
                            time.sleep(20)  # Aspetta 20 secondi tra tentativi
                        
                        # Usa streaming anche per il fix
                        fixed_text = ""
                        with client.messages.stream(
                            model="claude-sonnet-4-20250514",
                            max_tokens=25000,
                            messages=[{"role": "user", "content": fix_prompt}],
                            system="Sei un correttore di JSON. Restituisci SOLO il JSON corretto, nient'altro."
                        ) as stream:
                            for text in stream.text_stream:
                                fixed_text += text
                        
                        fixed_text = fixed_text.strip()
                        
                        # Pulisci
                        if "```json" in fixed_text:
                            fixed_text = fixed_text.split("```json")[1].split("```")[0]
                        elif "```" in fixed_text:
                            fixed_text = fixed_text.split("```")[1].split("```")[0]
                        
                        fixed_text = fixed_text.strip()
                        start_idx = fixed_text.find('{')
                        end_idx = fixed_text.rfind('}')
                        
                        if start_idx != -1 and end_idx != -1:
                            fixed_text = fixed_text[start_idx:end_idx+1]
                        
                        analysis = json.loads(fixed_text)
                        break  # Successo!
                        
                    except json.JSONDecodeError:
                        if attempt == 1:
                            return {"error": f"Errore parsing JSON: {error_msg}. Correzione fallita."}
                        continue
                        
                    except Exception as fix_error:
                        if "rate_limit" in str(fix_error).lower() and attempt < 1:
                            time.sleep(30)  # Aspetta 30 secondi per rate limit
                            continue
                        return {"error": f"Errore parsing JSON: {error_msg}. Correzione fallita: {fix_error}"}
        
        analysis["pairs_analyzed"] = FOREX_PAIRS
        analysis["currencies"] = list(CURRENCIES.keys())
        
        # ===== NUOVO: Calcola pair_analysis da currency_analysis =====
        if "currency_analysis" in analysis and "pair_analysis" not in analysis:
            currency_analysis = analysis["currency_analysis"]
            
            # Verifica che tutte le 7 valute siano presenti
            missing_currencies = set(CURRENCIES.keys()) - set(currency_analysis.keys())
            if missing_currencies:
                analysis["warning"] = f"Valute mancanti: {', '.join(missing_currencies)}"
            
            # Calcola i differenziali per le 19 coppie
            pair_analysis = calculate_pair_from_currencies(currency_analysis)
            analysis["pair_analysis"] = pair_analysis
        
        return analysis
        
    except json.JSONDecodeError as e:
        return {"error": f"Errore parsing JSON: {e}"}
    except Exception as e:
        return {"error": f"Errore API Claude: {e}"}


# ============================================================================
# FUNZIONI VISUALIZZAZIONE
# ============================================================================

def display_forex_prices(forex_prices: dict):
    """Mostra la tabella dei prezzi forex recuperati"""
    
    st.markdown("### 💱 Prezzi Forex")
    
    if not forex_prices:
        st.warning("⚠️ Nessun dato prezzi disponibile")
        return
    
    success = forex_prices.get("success", False)
    source = forex_prices.get("source", "N/A")
    prices = forex_prices.get("prices", {})
    error = forex_prices.get("error", "")
    warning = forex_prices.get("warning", "")
    found = forex_prices.get("found", 0)
    total = forex_prices.get("total", 19)
    
    if not success or not prices:
        st.error(f"❌ Recupero prezzi fallito: {error}")
        if forex_prices.get("details"):
            with st.expander("📋 Dettagli errori"):
                for err in forex_prices.get("details", []):
                    st.text(f"• {err}")
        return
    
    # Header con fonte
    if "Yahoo" in source or "yfinance" in source:
        st.success(f"✅ Fonte: **{source}** ({found}/{total})")
    else:
        st.warning(f"⚠️ Fonte: **{source}** ({found}/{total})")
    
    # Warning se non real-time
    if warning:
        st.warning(warning)
    
    # Tabella prezzi (sempre visibile)
    pairs_order = [
        "EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "USD/CAD",
        "EUR/GBP", "EUR/JPY", "GBP/JPY", "AUD/JPY", "EUR/CHF", "GBP/CHF",
        "AUD/CHF", "CAD/JPY", "AUD/CAD", "EUR/CAD", "EUR/AUD", "GBP/AUD", "GBP/CAD"
    ]
    
    # Dividi in 3 colonne
    col1, col2, col3 = st.columns(3)
    
    for idx, pair in enumerate(pairs_order):
        price = prices.get(pair)
        
        if price is not None:
            if "JPY" in pair:
                price_str = f"{price:.3f}"
            else:
                price_str = f"{price:.5f}"
            display_text = f"**{pair}**: {price_str} ✅"
        else:
            display_text = f"**{pair}**: N/A ❌"
        
        # Distribuisci nelle colonne
        if idx < 7:
            col1.markdown(display_text)
        elif idx < 13:
            col2.markdown(display_text)
        else:
            col3.markdown(display_text)
    
    # Mostra errori se presenti
    if forex_prices.get("errors"):
        st.divider()
        st.caption("⚠️ Alcuni errori durante il recupero:")
        for err in forex_prices.get("errors", [])[:5]:
            st.text(f"• {err}")


def display_news_summary(news_structured: dict, links_structured: list = None):
    """Mostra il riepilogo delle notizie trovate con link"""
    
    st.markdown("### 📰 Notizie Web")
    
    # Conteggio fonti trovate
    sources_found = []
    if news_structured.get("forexfactory_direct"):
        sources_found.append(f"Forex News ({len(news_structured['forexfactory_direct'])})")
    if news_structured.get("forex_factory"):
        sources_found.append(f"ForexFactory Search ({len(news_structured['forex_factory'])})")
    if news_structured.get("rate_expectations"):
        sources_found.append(f"Tassi ({len(news_structured['rate_expectations'])})")
    if news_structured.get("meeting_calendar"):
        sources_found.append(f"Calendario ({len(news_structured['meeting_calendar'])})")
    if news_structured.get("geopolitics"):
        sources_found.append(f"Geopolitica ({len(news_structured['geopolitics'])})")
    
    if sources_found:
        st.success(f"✅ Fonti trovate: {', '.join(sources_found)}")
    else:
        st.warning("⚠️ Nessuna notizia trovata")
    
    # ForexFactory News (via DuckDuckGo News Search)
    if news_structured.get("forexfactory_direct"):
        with st.expander(f"🔴 FOREX NEWS LIVE ({len(news_structured['forexfactory_direct'])} news)", expanded=False):
            for item in news_structured["forexfactory_direct"][:12]:
                title = item.get('title', '')
                url = item.get('url', '')
                time_info = item.get('time', '')
                source = item.get('source', '')
                
                # Formatta la riga
                line = f"• **{title[:80]}**"
                if source:
                    line += f" _({source})_"
                if time_info:
                    line += f" - {time_info}"
                
                if url:
                    st.markdown(f"[{line}]({url})")
                else:
                    st.markdown(line)
            
            st.caption("🔗 [ForexFactory News](https://www.forexfactory.com/news) | [ForexFactory Calendar](https://www.forexfactory.com/calendar)")
    
    # Forex Factory (da DuckDuckGo text search - fallback)
    if news_structured.get("forex_factory"):
        with st.expander(f"🔴 FOREX FACTORY SEARCH ({len(news_structured['forex_factory'])} news)", expanded=False):
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
            st.divider()
            st.markdown("🔗 **Link utili:**")
            st.markdown("• [ForexFactory Calendar](https://www.forexfactory.com/calendar)")
            st.markdown("• [TradingEconomics Calendar](https://tradingeconomics.com/calendar)")
    
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
        with st.expander(f"📎 LINK AGGIUNTIVI ({len(links_structured)} URL processati)", expanded=False):
            for item in links_structured:
                status_icon = "✅" if item['status'] == 'success' else "❌"
                st.markdown(f"{status_icon} **[{item['title'][:50]}]({item['url']})**")
                if item['status'] == 'success':
                    st.caption(item['content_preview'][:200] + "...")
    
    # Sezione Calendario Economico (sempre visibile con link utili)
    with st.expander("📅 CALENDARIO ECONOMICO - Link Utili", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**ForexFactory:**")
            st.markdown("🔗 [Calendario Eventi](https://www.forexfactory.com/calendar)")
            st.markdown("🔗 [News Live](https://www.forexfactory.com/news)")
        with col2:
            st.markdown("**Altre Fonti:**")
            st.markdown("🔗 [TradingEconomics](https://tradingeconomics.com/calendar)")
            st.markdown("🔗 [Investing.com](https://www.investing.com/economic-calendar/)")


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


def display_central_bank_history(history_data: dict = None):
    """
    Mostra la tabella storico decisioni delle banche centrali.
    Con colori: verde = hike, rosso = cut
    
    Args:
        history_data: Dati storico già recuperati (opzionale). Se None, usa sessione o recupera.
    """
    st.markdown("### 📜 Storico Decisioni Banche Centrali")
    st.caption("Ultime 2 decisioni per ogni banca centrale")
    
    # Usa dati passati, dalla sessione, o recupera nuovi
    if history_data:
        history = history_data
    elif 'last_cb_history' in st.session_state and st.session_state['last_cb_history']:
        history = st.session_state['last_cb_history']
    else:
        history = get_central_bank_history_summary()
    
    # Mappa valuta -> banca
    currency_to_bank = {
        "USD": "Fed", "EUR": "ECB", "GBP": "BOE", "JPY": "BOJ",
        "CHF": "SNB", "AUD": "RBA", "CAD": "BOC"
    }
    
    currency_order = ["USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD"]
    
    table_rows = []
    for currency in currency_order:
        data = history.get(currency, {})
        if data:
            bank = data.get('bank_short', currency_to_bank.get(currency, currency))
            rate = data.get("current_rate", "N/A")
            meeting_2 = data.get("meeting_2", "N/A")  # Prima (più vecchio)
            meeting_1 = data.get("meeting_1", "N/A")  # Dopo (più recente)
            trend = f"{data.get('trend_emoji', '')} {data.get('trend_label', 'N/A')}"
            
            row = {
                "Valuta": currency,
                "Banca": bank,
                "Tasso Attuale": rate,
                "Meeting -2": meeting_2,
                "Meeting -1": meeting_1,
                "Trend": trend
            }
            table_rows.append(row)
    
    if table_rows:
        df = pd.DataFrame(table_rows)
        
        def color_decision(val):
            """Colora la decisione: verde hike, rosso cut"""
            if not isinstance(val, str):
                return ''
            if '+25bp' in val or '+50bp' in val or '+75bp' in val:
                return 'color: #28a745; font-weight: bold'
            elif '-25bp' in val or '-50bp' in val or '-75bp' in val:
                return 'color: #dc3545; font-weight: bold'
            return ''
        
        # Applica stile alle colonne dei meeting
        styled_df = df.style.applymap(
            color_decision, 
            subset=['Meeting -2', 'Meeting -1']
        )
        
        st.dataframe(styled_df, use_container_width=True, hide_index=True)
        
        # Legenda
        st.caption("🟢 Hike (+bp) | 🔴 Cut (-bp) | ⚫ Hold (0bp)")


def generate_summary_with_bias(summary: str, differential: int) -> str:
    """
    Genera il summary con il prefisso bias corretto basato SOLO sul differenziale.
    
    Regole:
    - diff >= 7  → "Strong bullish: ..."
    - diff 1-6   → "Bullish moderato: ..."
    - diff = 0   → "Bias neutrale: ..."
    - diff -1/-6 → "Bearish moderato: ..."
    - diff <= -7 → "Strong bearish: ..."
    
    Claude ora genera summary senza prefisso bias, quindi lo aggiungiamo noi.
    """
    if not summary:
        return summary
    
    # Rimuovi eventuali prefissi bias già presenti (per sicurezza)
    summary_clean = summary
    prefixes_to_remove = [
        "Strong bullish bias:", "Strong bullish:", 
        "Strong bearish bias:", "Strong bearish:",
        "Bullish moderato:", "Bearish moderato:",
        "Bias neutrale:", "Neutral:",
        "Bullish:", "Bearish:"
    ]
    for prefix in prefixes_to_remove:
        if summary_clean.lower().startswith(prefix.lower()):
            summary_clean = summary_clean[len(prefix):].strip()
            break
    
    # Determina il prefisso corretto dal differenziale
    if differential >= 7:
        prefix = "Strong bullish"
    elif differential > 0:
        prefix = "Bullish moderato"
    elif differential <= -7:
        prefix = "Strong bearish"
    elif differential < 0:
        prefix = "Bearish moderato"
    else:
        prefix = "Bias neutrale"
    
    return f"{prefix}: {summary_clean}"


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
        # Supporta sia "risk_sentiment" che "market_regime"
        sentiment = analysis.get("market_regime") or analysis.get("risk_sentiment")
        if sentiment:
            emoji = "🟢" if sentiment == "risk-on" else "🔴" if sentiment == "risk-off" else "🟡"
            st.markdown(f"**Risk Sentiment:** {emoji} {sentiment.upper()}")
    
    # Summary (supporta sia "summary" che "market_summary")
    summary_text = analysis.get("market_summary") or analysis.get("summary")
    if summary_text:
        st.info(f"📋 **Contesto:** {summary_text}")
    
    # Weekly Events Warning
    if "weekly_events_warning" in analysis:
        st.warning(f"📅 {analysis['weekly_events_warning']}")
    
    st.markdown("---")
    
    # ===== SEZIONE ANALISI VALUTE =====
    currency_analysis = analysis.get("currency_analysis", {})
    
    if currency_analysis:
        st.markdown("### 💱 Analisi per Valuta")
        st.caption("Punteggi assoluti per ogni valuta. I differenziali delle coppie sono calcolati automaticamente.")
        
        # Ordina valute per score (dalla più forte alla più debole)
        currencies_sorted = sorted(
            currency_analysis.items(),
            key=lambda x: x[1].get("total_score", 0),
            reverse=True
        )
        
        # Crea tabella valute
        currency_rows = []
        for curr, data in currencies_sorted:
            score = data.get("total_score", 0)
            summary = data.get("summary", "")[:100] + "..." if len(data.get("summary", "")) > 100 else data.get("summary", "")
            
            # Colore basato sullo score
            if score >= 3:
                indicator = "🟢🟢"
                strength = "Forte"
            elif score > 0:
                indicator = "🟢"
                strength = "Positivo"
            elif score <= -3:
                indicator = "🔴🔴"
                strength = "Debole"
            elif score < 0:
                indicator = "🔴"
                strength = "Negativo"
            else:
                indicator = "🟡"
                strength = "Neutro"
            
            currency_rows.append({
                "Valuta": curr,
                "Score": f"{indicator} {score:+d}",
                "Forza": strength,
                "Sintesi": summary
            })
        
        # Mostra tabella
        import pandas as pd
        df_currencies = pd.DataFrame(currency_rows)
        st.dataframe(df_currencies, use_container_width=True, hide_index=True)
        
        st.markdown("---")
    
    # ===== TOP BULLISH / TOP BEARISH =====
    pair_analysis = analysis.get("pair_analysis", {})
    
    if pair_analysis:
        # Calcola differenziale per ogni coppia e ordina
        pairs_with_diff = []
        for p, d in pair_analysis.items():
            # Prima prova a usare valori pre-calcolati (nuovo formato)
            if "differential" in d:
                diff = d["differential"]
            else:
                # Fallback: calcola dalla somma dei singoli punteggi (vecchio formato)
                scores = d.get("scores", {})
                score_base = 0
                score_quote = 0
                for param_key, param_scores in scores.items():
                    if isinstance(param_scores, dict):
                        score_base += param_scores.get("base", 0)
                        score_quote += param_scores.get("quote", 0)
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
        
        # ===== CONTROLLO VALUTE/COPPIE MANCANTI =====
        # Controlla valute mancanti
        if currency_analysis:
            analyzed_currencies = set(currency_analysis.keys())
            expected_currencies = set(CURRENCIES.keys())
            missing_currencies = expected_currencies - analyzed_currencies
            if missing_currencies:
                st.warning(f"⚠️ **Valute mancanti nell'analisi:** {', '.join(sorted(missing_currencies))} ({len(missing_currencies)} su 7)")
        
        # Controlla coppie mancanti
        analyzed_pairs = set(pair_analysis.keys())
        expected_pairs = set(FOREX_PAIRS)
        missing_pairs = expected_pairs - analyzed_pairs
        
        if missing_pairs:
            st.warning(f"⚠️ **Coppie mancanti:** {', '.join(sorted(missing_pairs))} ({len(missing_pairs)} su 19)")
        
        # ===== TABELLA TUTTE LE COPPIE CON SELEZIONE SINGOLA =====
        st.markdown("### 📋 Tutte le Coppie")
        st.caption("👆 **Seleziona una riga** per vedere la sintesi completa e tutti i dettagli sotto la tabella")
        
        # Crea lista con dati e ordina per differenziale (dal più bullish al più bearish)
        rows_data = []
        for pair, data in pair_analysis.items():
            summary = data.get("summary", "")
            
            # Prima prova a usare valori pre-calcolati (nuovo formato)
            if "differential" in data:
                differential = data["differential"]
            else:
                # Fallback: calcola dalla somma dei singoli punteggi (vecchio formato)
                scores = data.get("scores", {})
                score_base = 0
                score_quote = 0
                for param_key, param_scores in scores.items():
                    if isinstance(param_scores, dict):
                        score_base += param_scores.get("base", 0)
                        score_quote += param_scores.get("quote", 0)
                differential = score_base - score_quote
            
            # Genera il summary con prefisso bias corretto basato sul differenziale
            summary_with_bias = generate_summary_with_bias(summary, differential)
            
            # Pallini colorati basati SOLO sul DIFFERENZIALE (ignoriamo bias di Claude)
            if differential >= 7:
                bias_combined = "🟢🟢 BULLISH"
            elif differential > 0:
                bias_combined = "🟢 BULLISH"
            elif differential <= -7:
                bias_combined = "🔴🔴 BEARISH"
            elif differential < 0:
                bias_combined = "🔴 BEARISH"
            else:
                bias_combined = "🟡 NEUTRAL"
            
            rows_data.append({
                "pair": pair,
                "Coppia": pair,
                "Bias": bias_combined,
                "Diff": differential,
                "Sintesi": summary_with_bias  # Bias determinato dal differenziale
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
            
            summary = pair_data.get("summary", "")
            scores = pair_data.get("scores", {})
            
            # Usa valori pre-calcolati se disponibili (nuovo formato)
            if "differential" in pair_data:
                score_base = pair_data.get("score_base", 0)
                score_quote = pair_data.get("score_quote", 0)
                differential = pair_data["differential"]
            else:
                # Fallback: calcola dalla somma (vecchio formato)
                score_base = 0
                score_quote = 0
                for param_key, param_scores in scores.items():
                    if isinstance(param_scores, dict):
                        score_base += param_scores.get("base", 0)
                        score_quote += param_scores.get("quote", 0)
                differential = score_base - score_quote
            
            # Estrai valute dalla coppia
            base_curr, quote_curr = selected_pair.split("/")
            
            # Determina tipo bias basato SOLO sul DIFFERENZIALE (ignoriamo bias di Claude)
            if differential >= 7:
                bias_type = "RIALZISTA" 
                bias_strength = "(STRONG)"
                header_color = "#d4edda"
                header_border = "#28a745"
                header_emoji = "🟢🟢"
            elif differential > 0:
                bias_type = "RIALZISTA" 
                bias_strength = "(MODERATE)"
                header_color = "#d4edda"
                header_border = "#28a745"
                header_emoji = "🟢"
            elif differential <= -7:
                bias_type = "RIBASSISTA"
                bias_strength = "(STRONG)"
                header_color = "#f8d7da"
                header_border = "#dc3545"
                header_emoji = "🔴🔴"
            elif differential < 0:
                bias_type = "RIBASSISTA"
                bias_strength = "(MODERATE)"
                header_color = "#f8d7da"
                header_border = "#dc3545"
                header_emoji = "🔴"
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
            summary_with_bias = generate_summary_with_bias(summary, differential)
            st.markdown(f"**Sintesi:** {summary_with_bias}")
            
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
                "bilancia_fiscale": "Bilancia/Fiscale [-1/+1]",
                "news_catalyst": "⚡ News Catalyst [-2/+2]"
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
            
            # === EVIDENCE NEWS CATALYST ===
            if "news_catalyst" in scores and "evidence" in scores.get("news_catalyst", {}):
                evidence = scores["news_catalyst"]["evidence"]
                with st.expander("📊 Dettagli calcolo News Catalyst (verifica dati)", expanded=False):
                    col_ev_base, col_ev_quote = st.columns(2)
                    
                    with col_ev_base:
                        st.markdown(f"**{base_curr} - Dati usati:**")
                        base_data = evidence.get("base_data", [])
                        if base_data:
                            for item in base_data:
                                event = item.get("event", "N/A")
                                if item.get("actual"):
                                    st.markdown(f"- **{event}**: {item.get('actual')} vs {item.get('forecast', 'N/A')} → Sorpresa: {item.get('surprise', 'N/A')} → Score: {item.get('score', 0)}")
                                else:
                                    st.markdown(f"- **{event}**: {item.get('description', 'N/A')} → Score: {item.get('score', 0)}")
                        calc_base = evidence.get("calculation_base", "")
                        if calc_base:
                            st.markdown(f"**Calcolo:** {calc_base}")
                    
                    with col_ev_quote:
                        st.markdown(f"**{quote_curr} - Dati usati:**")
                        quote_data = evidence.get("quote_data", [])
                        if quote_data:
                            for item in quote_data:
                                event = item.get("event", "N/A")
                                if item.get("actual"):
                                    st.markdown(f"- **{event}**: {item.get('actual')} vs {item.get('forecast', 'N/A')} → Sorpresa: {item.get('surprise', 'N/A')} → Score: {item.get('score', 0)}")
                                else:
                                    st.markdown(f"- **{event}**: {item.get('description', 'N/A')} → Score: {item.get('score', 0)}")
                        calc_quote = evidence.get("calculation_quote", "")
                        if calc_quote:
                            st.markdown(f"**Calcolo:** {calc_quote}")
            
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
        
        opt_cb_history = st.checkbox(
            "🏦 Storico Banche Centrali",
            value=True,
            help="Recupera ultime 2 decisioni sui tassi per ogni BC (GRATIS)"
        )
        
        opt_prices = st.checkbox(
            "💱 Recupera Prezzi Forex",
            value=True,
            help="Recupera prezzi attuali delle 19 coppie forex da Yahoo Finance (GRATIS)"
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
        if opt_claude and not (opt_macro or opt_pmi or opt_cb_history or opt_news or opt_links):
            st.error("⚠️ Seleziona almeno una fonte dati per Claude!")
        
        st.markdown("---")
        
        # ===== BOTTONE ANALISI =====
        can_analyze = API_KEY_LOADED and (opt_macro or opt_pmi or opt_cb_history or opt_prices or opt_news or opt_links)
        
        analyze_btn = st.button(
            "🚀 AVVIA ANALISI",
            disabled=not can_analyze,
            use_container_width=True,
            type="primary"
        )
        
        # Calcola tipo analisi
        analysis_type = "custom"
        if opt_macro and opt_pmi and opt_cb_history and opt_news and opt_claude and not opt_links:
            analysis_type = "full"
        elif opt_macro and not opt_news and not opt_links:
            analysis_type = "macro_only"
        elif opt_news and not opt_macro and not opt_links:
            analysis_type = "news_only"
        elif opt_links and not opt_macro and not opt_news:
            analysis_type = "links_only"
        elif opt_cb_history and not opt_macro and not opt_pmi and not opt_news and not opt_links:
            analysis_type = "cb_history_only"
        
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
        economic_events = {}
        
        options_selected = {
            "macro": opt_macro,
            "pmi": opt_pmi,
            "cb_history": opt_cb_history,
            "prices": opt_prices,
            "news": opt_news,
            "links": opt_links,
            "claude": opt_claude
        }
        
        step = 0
        total_steps = sum([opt_macro, opt_pmi, opt_cb_history, opt_prices, opt_news, opt_links, opt_claude])
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
        
        # FASE 2.5: Storico Banche Centrali
        cb_history_data = {}
        if opt_cb_history:
            step += 1
            progress.progress(int(step/total_steps*80), text="🏦 Recupero storico banche centrali...")
            cb_history_data = get_central_bank_history_summary()
            st.session_state['last_cb_history'] = cb_history_data
        else:
            # Usa storico dalla sessione se disponibile
            if 'last_cb_history' in st.session_state and st.session_state['last_cb_history']:
                cb_history_data = st.session_state['last_cb_history']
        
        # FASE 3: Prezzi Forex
        forex_prices = {}
        if opt_prices:
            step += 1
            progress.progress(int(step/total_steps*80), text="💱 Recupero prezzi forex...")
            forex_prices = fetch_forex_prices()
            st.session_state['last_forex_prices'] = forex_prices
        else:
            # Usa prezzi dalla sessione se disponibili
            if 'last_forex_prices' in st.session_state:
                forex_prices = st.session_state['last_forex_prices']
        
        # FASE 3: Notizie Web
        if opt_news:
            step += 1
            progress.progress(int(step/total_steps*80), text="📰 Ricerca notizie web...")
            news_text, news_structured = search_web_news()
            
            # Aggiungi news dirette da ForexFactory
            progress.progress(int(step/total_steps*80), text="📰 Recupero ForexFactory news...")
            ff_news = fetch_forexfactory_news()
            if ff_news.get("success") and ff_news.get("news"):
                # Aggiungi alle news structured
                news_structured["forexfactory_direct"] = ff_news["news"]
                # Aggiungi al testo per Claude
                ff_text = "\n\n=== FOREX FACTORY NEWS (ULTIME) ===\n"
                for item in ff_news["news"][:15]:
                    ff_text += f"• {item['title']}"
                    if item.get('currency'):
                        ff_text += f" [{item['currency']}]"
                    if item.get('time'):
                        ff_text += f" ({item['time']})"
                    ff_text += "\n"
                news_text += ff_text
            
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
            progress.progress(int(step/total_steps*80), text="📊 Recupero dati economici per News Catalyst...")
            
            # Recupera dati economici recenti per News Catalyst
            economic_events = {}
            try:
                economic_events = fetch_all_economic_events()
                st.session_state['last_economic_events'] = economic_events
            except Exception as e:
                st.warning(f"⚠️ Errore recupero dati economici: {str(e)[:50]}")
                # Usa dati dalla sessione se disponibili
                if 'last_economic_events' in st.session_state:
                    economic_events = st.session_state['last_economic_events']
            
            step += 1
            progress.progress(int(step/total_steps*80), text="🤖 Claude sta analizzando...")
            
            # Usa dati dalla sessione se non aggiornati ora
            if not opt_news and 'last_news_text' in st.session_state:
                news_text = st.session_state['last_news_text']
            if not opt_links and 'last_links_text' in st.session_state:
                additional_text = st.session_state['last_links_text']
            if not opt_pmi and 'last_pmi_data' in st.session_state:
                pmi_data = st.session_state['last_pmi_data']
            if not opt_cb_history and 'last_cb_history' in st.session_state:
                cb_history_data = st.session_state['last_cb_history']
            
            # Recupera prezzi forex dalla sessione
            forex_prices = st.session_state.get('last_forex_prices', {})
            
            claude_analysis = analyze_with_claude(
                ANTHROPIC_API_KEY,
                macro_data,
                news_text,
                additional_text,
                pmi_data,
                forex_prices,
                economic_events,
                cb_history_data
            )
        
        # ===== SALVATAGGIO =====
        progress.progress(90, text="💾 Salvataggio...")
        
        analysis_result = {
            "macro_data": macro_data,
            "pmi_data": pmi_data,
            "cb_history_data": cb_history_data,
            "forex_prices": forex_prices,
            "economic_events": economic_events,
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
        
        # Storico Decisioni Banche Centrali (dati da Investing.com API, non Claude)
        cb_history = analysis.get("cb_history_data", {})
        if not cb_history and 'last_cb_history' in st.session_state:
            cb_history = st.session_state['last_cb_history']
        display_central_bank_history(cb_history)
        st.markdown("---")
        
        # 1️⃣ Mostra tabella prezzi forex se disponibili
        forex_prices = analysis.get("forex_prices", {})
        if not forex_prices and 'last_forex_prices' in st.session_state:
            forex_prices = st.session_state['last_forex_prices']
        
        if forex_prices and forex_prices.get("prices"):
            display_forex_prices(forex_prices)
            st.markdown("---")
        
        # 2️⃣ Notizie e Calendario (subito dopo prezzi)
        if news_structured or links_structured:
            display_news_summary(news_structured, links_structured)
            st.markdown("---")
        
        # 3️⃣ Analisi Claude
        if claude_analysis:
            display_analysis_matrix(claude_analysis)
    
    else:
        # Stato iniziale
        st.markdown("""
        ### 👋 Benvenuto!
        
        Seleziona le opzioni nella sidebar e clicca **🚀 AVVIA ANALISI**.
        
        **Opzioni disponibili:**
        - 📊 **Dati Macro** - Tassi, inflazione, PIL (gratis)
        - 📈 **Dati PMI** - Manufacturing & Services PMI (gratis)
        - 💱 **Prezzi Forex** - Prezzi attuali delle 19 coppie (gratis)
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
