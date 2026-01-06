import streamlit as st
import anthropic
from duckduckgo_search import DDGS
from datetime import datetime
import json
import pandas as pd
import os
from pathlib import Path
import requests
import hashlib
import re

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
            return None
            
    except Exception as e:
        st.error(f"Errore Supabase: {e}")
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
        "created_at": datetime.now().isoformat()
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
        datetime_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        analysis["analysis_datetime"] = datetime_str
        
        if SUPABASE_ENABLED:
            data = {
                "analysis_datetime": datetime_str,
                "user_id": user_id,
                "analysis_type": analysis_type,
                "options_selected": options_selected,  # Supabase JSONB accetta dict direttamente
                "data": analysis
            }
            result = supabase_request("POST", "analyses", data)
            return result is not None
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
    """Formatta datetime per visualizzazione: 28/12/2025 14:30"""
    try:
        if "_" in datetime_str:
            date_part, time_part = datetime_str.split("_")
            date_obj = datetime.strptime(date_part, "%Y-%m-%d")
            time_formatted = time_part.replace("-", ":")
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
# SYSTEM PROMPT PER ANALISI GLOBALE
# ============================================================================

SYSTEM_PROMPT_GLOBAL = """Sei un analista macroeconomico forex senior. Devi analizzare TUTTE le coppie forex fornite.

## ⚠️ REGOLE CRITICHE:

### 1. LINGUA: TUTTO IN ITALIANO
Ogni parola della tua risposta deve essere in italiano. Non usare mai termini inglesi se esiste un equivalente italiano.

### 2. STRUTTURA JSON OBBLIGATORIA
Rispondi SOLO con un JSON valido, senza markdown, senza ```json, senza commenti.

### 3. ANALISI DEL BIAS
Per ogni coppia forex:
- **BULLISH** = la valuta BASE si rafforza (es: EUR/USD bullish = EUR forte)
- **BEARISH** = la valuta BASE si indebolisce (es: EUR/USD bearish = EUR debole)
- **NEUTRAL** = equilibrio o incertezza

### 4. FATTORI DA CONSIDERARE (in ordine di importanza):
1. **ASPETTATIVE sui tassi** (+ importante dei tassi attuali!)
2. **Comunicazioni delle banche centrali** (hawkish/dovish)
3. **Dati macro attuali** (inflazione, PIL, disoccupazione)
4. **Risk sentiment globale** (risk-on/risk-off)
5. **Fattori geopolitici**

### 5. FORMATO OUTPUT JSON:
{
    "analysis_date": "YYYY-MM-DD",
    "summary": "Breve riassunto del contesto macro globale in italiano",
    "currency_analysis": {
        "EUR": {"outlook": "bullish/bearish/neutral", "key_factors": ["fattore1", "fattore2"]},
        ...per ogni valuta
    },
    "pair_analysis": {
        "EUR/USD": {
            "bias": "bullish/bearish/neutral",
            "strength": 1-5,
            "summary": "Spiegazione in italiano del perché questo bias",
            "key_drivers": ["driver1", "driver2"]
        },
        ...per ogni coppia
    },
    "rate_outlook": {
        "USD": {"current_rate": "X.XX%", "next_meeting": "data", "expectation": "hold/cut/hike", "probability": "XX%"},
        ...per ogni valuta
    },
    "risk_sentiment": "risk-on/risk-off/neutral",
    "events_calendar": []
}

### 6. REGOLE SPECIALI:
- JPY: ricorda che è safe-haven, si rafforza in risk-off
- CHF: idem, safe-haven
- AUD/CAD: valute commodity, sensibili a Cina e materie prime
- Usa SEMPRE dati recenti dalle notizie, non solo i numeri
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
    
    today = datetime.now()
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
            results = DDGS().text(query, max_results=5)
            for r in results:
                title = r.get('title', '')
                body = r.get('body', '')
                if any(kw in body.lower() for kw in ['dollar', 'euro', 'yen', 'pound', 'fed', 'ecb', 'boe', 'boj', 'rate', 'inflation', 'gdp', 'employment', 'tariff', 'trade']):
                    all_results.append(f"[FF-NEWS] {title}: {body[:500]}")
                    structured_results["forex_factory"].append({
                        "title": title,
                        "body": body[:300]
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
        "USD": [f"Fed FOMC next meeting {current_year} rate decision probability", f"CME FedWatch tool Fed rate expectations {current_year}"],
        "EUR": [f"ECB next meeting {current_year} rate decision probability", f"ECB rate forecast {current_year} {next_year}"],
        "GBP": [f"Bank of England MPC next meeting {current_year} rate decision", f"BoE rate forecast {current_year}"],
        "JPY": [f"Bank of Japan BOJ meeting {current_year} rate hike probability", f"BOJ policy outlook {current_year}"],
        "CHF": [f"SNB Swiss National Bank meeting {current_year} rate decision"],
        "AUD": [f"RBA Reserve Bank Australia meeting {current_year} rate decision", f"RBA rate forecast {current_year}"],
        "CAD": [f"Bank of Canada BoC meeting {current_year} rate decision", f"BoC rate forecast {current_year}"],
    }
    
    for currency, queries in rate_queries.items():
        for query in queries:
            try:
                results = DDGS().text(query, max_results=3)
                for r in results:
                    title = r.get('title', '')
                    body = r.get('body', '')
                    all_results.append(f"[{currency}-RATE] {title}: {body[:400]}")
                    structured_results["rate_expectations"].append({
                        "currency": currency,
                        "title": title,
                        "body": body[:250]
                    })
            except:
                pass
    
    # =========================================================================
    # SEZIONE 2: CALENDARIO MEETING BC
    # =========================================================================
    all_results.append(f"\n{'='*60}")
    all_results.append(f"[CENTRAL BANK MEETING CALENDAR]")
    all_results.append(f"{'='*60}")
    
    calendar_queries = [
        f"FOMC meeting schedule dates {current_year} {next_year}",
        f"ECB governing council meeting dates {current_year}",
        f"Bank of England MPC meeting dates {current_year}",
        f"central banks meeting calendar {current_year}",
    ]
    
    for query in calendar_queries:
        try:
            results = DDGS().text(query, max_results=2)
            for r in results:
                title = r.get('title', '')
                body = r.get('body', '')
                all_results.append(f"[CALENDAR] {title}: {body[:400]}")
                structured_results["meeting_calendar"].append({
                    "title": title,
                    "body": body[:250]
                })
        except:
            pass
    
    # =========================================================================
    # SEZIONE 3: CONFRONTO POLITICHE MONETARIE
    # =========================================================================
    all_results.append(f"\n{'='*60}")
    all_results.append(f"[MONETARY POLICY COMPARISON]")
    all_results.append(f"{'='*60}")
    
    comparison_queries = [
        f"central banks rate cuts {current_year} comparison Fed ECB BoE",
        f"hawkish dovish central banks {current_year} ranking",
        f"monetary policy divergence {current_year} forex",
    ]
    
    for query in comparison_queries:
        try:
            results = DDGS().text(query, max_results=3)
            for r in results:
                title = r.get('title', '')
                body = r.get('body', '')
                all_results.append(f"[COMPARE] {title}: {body[:450]}")
                structured_results["policy_comparison"].append({
                    "title": title,
                    "body": body[:250]
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
    ]
    
    for query in geopolitics_queries:
        try:
            results = DDGS().text(query, max_results=3)
            for r in results:
                title = r.get('title', '')
                body = r.get('body', '')
                all_results.append(f"[GEOPOLITICS] {title}: {body[:400]}")
                structured_results["geopolitics"].append({
                    "title": title,
                    "body": body[:250]
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


def analyze_with_claude(api_key: str, macro_data: dict = None, news_text: str = "", additional_text: str = "") -> dict:
    """
    Esegue l'analisi con Claude AI.
    
    Args:
        api_key: Chiave API Anthropic
        macro_data: Dati macroeconomici (opzionale)
        news_text: Testo delle notizie web (opzionale)
        additional_text: Testo delle risorse aggiuntive (opzionale)
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
    
    today = datetime.now()
    
    user_prompt = f"""
Analizza TUTTE queste coppie forex: {pairs_list}

## ⚠️ DATA ODIERNA: {today.strftime('%Y-%m-%d')} ({today.strftime('%A, %d %B %Y')})

**Valute da analizzare:**
{currencies_info}

---

{macro_section}
{news_section}
{additional_section}

## ⭐ ISTRUZIONI:

1. **USA TUTTE LE INFORMAZIONI DISPONIBILI** per determinare il bias
2. **ASPETTATIVE > TASSI ATTUALI**: il mercato guarda AVANTI
3. **analysis_date** = "{today.strftime('%Y-%m-%d')}"
4. **events_calendar** = []
5. Ogni **summary** deve spiegare PERCHÉ quel bias
6. Se presenti risorse aggiuntive, considerale con priorità ma INTEGRA con altri dati

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
    """Mostra il riepilogo delle notizie trovate"""
    
    st.markdown("### 📰 Riepilogo Notizie Trovate")
    
    # Forex Factory
    if news_structured.get("forex_factory"):
        with st.expander(f"🔴 FOREX FACTORY ({len(news_structured['forex_factory'])} news)", expanded=True):
            for item in news_structured["forex_factory"][:5]:
                st.markdown(f"• **{item['title'][:80]}...**")
                st.caption(item['body'][:150] + "...")
    
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
                for item in items[:2]:
                    st.caption(f"• {item['title'][:60]}...")
    
    # Meeting Calendar
    if news_structured.get("meeting_calendar"):
        with st.expander(f"📅 CALENDARIO MEETING ({len(news_structured['meeting_calendar'])} risultati)"):
            for item in news_structured["meeting_calendar"][:3]:
                st.markdown(f"• {item['title'][:80]}")
    
    # Geopolitics
    if news_structured.get("geopolitics"):
        with st.expander(f"🌍 GEOPOLITICA ({len(news_structured['geopolitics'])} risultati)"):
            for item in news_structured["geopolitics"][:3]:
                st.markdown(f"• {item['title'][:80]}")
    
    # Link aggiuntivi processati
    if links_structured:
        with st.expander(f"📎 LINK AGGIUNTIVI ({len(links_structured)} URL processati)", expanded=True):
            for item in links_structured:
                status_icon = "✅" if item['status'] == 'success' else "❌"
                st.markdown(f"{status_icon} **{item['title'][:60]}**")
                st.caption(f"URL: {item['url'][:50]}...")
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


def display_analysis_matrix(analysis: dict):
    """Mostra la matrice delle analisi forex"""
    
    if "error" in analysis:
        st.error(f"Errore nell'analisi: {analysis['error']}")
        return
    
    # Header
    st.markdown(f"### 🤖 Analisi Claude AI")
    
    if "summary" in analysis:
        st.info(f"📋 **Contesto:** {analysis['summary']}")
    
    if "risk_sentiment" in analysis:
        sentiment = analysis["risk_sentiment"]
        emoji = "🟢" if sentiment == "risk-on" else "🔴" if sentiment == "risk-off" else "🟡"
        st.markdown(f"**Risk Sentiment:** {emoji} {sentiment.upper()}")
    
    # Analisi per coppia
    pair_analysis = analysis.get("pair_analysis", {})
    
    if pair_analysis:
        st.markdown("### 📈 Analisi per Coppia")
        
        # Crea dataframe
        rows = []
        for pair, data in pair_analysis.items():
            bias = data.get("bias", "neutral")
            strength = data.get("strength", 3)
            summary = data.get("summary", "")
            
            bias_emoji = "🟢" if bias == "bullish" else "🔴" if bias == "bearish" else "🟡"
            strength_bar = "●" * strength + "○" * (5 - strength)
            
            rows.append({
                "Coppia": pair,
                "Bias": f"{bias_emoji} {bias.upper()}",
                "Forza": strength_bar,
                "Analisi": summary[:200] + "..." if len(summary) > 200 else summary
            })
        
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
    
    # Rate Outlook
    rate_outlook = analysis.get("rate_outlook", {})
    if rate_outlook:
        st.markdown("### 🏦 Outlook Tassi")
        
        rate_rows = []
        for curr, data in rate_outlook.items():
            rate_rows.append({
                "Valuta": curr,
                "Tasso Attuale": data.get("current_rate", "N/A"),
                "Prossimo Meeting": data.get("next_meeting", "N/A"),
                "Aspettativa": data.get("expectation", "N/A"),
                "Probabilità": data.get("probability", "N/A")
            })
        
        df_rates = pd.DataFrame(rate_rows)
        st.dataframe(df_rates, use_container_width=True, hide_index=True)


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
        if opt_claude and not (opt_macro or opt_news or opt_links):
            st.error("⚠️ Seleziona almeno una fonte dati per Claude!")
        
        st.markdown("---")
        
        # ===== BOTTONE ANALISI =====
        can_analyze = API_KEY_LOADED and (opt_macro or opt_news or opt_links)
        
        analyze_btn = st.button(
            "🚀 AVVIA ANALISI",
            disabled=not can_analyze,
            use_container_width=True,
            type="primary"
        )
        
        # Calcola tipo analisi
        analysis_type = "custom"
        if opt_macro and opt_news and opt_claude and not opt_links:
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
        news_text = ""
        news_structured = {}
        additional_text = ""
        links_structured = []
        claude_analysis = None
        
        options_selected = {
            "macro": opt_macro,
            "news": opt_news,
            "links": opt_links,
            "claude": opt_claude
        }
        
        step = 0
        total_steps = sum([opt_macro, opt_news, opt_links, opt_claude])
        
        # FASE 1: Dati Macro
        if opt_macro:
            step += 1
            progress.progress(int(step/total_steps*80), text="📊 Recupero dati macro...")
            macro_data = fetch_macro_data()
            st.session_state['last_macro_data'] = macro_data
        
        # FASE 2: Notizie Web
        if opt_news:
            step += 1
            progress.progress(int(step/total_steps*80), text="📰 Ricerca notizie web...")
            news_text, news_structured = search_web_news()
            st.session_state['last_news_text'] = news_text
            st.session_state['last_news_structured'] = news_structured
        
        # FASE 3: Link Aggiuntivi
        if opt_links and additional_urls.strip():
            step += 1
            progress.progress(int(step/total_steps*80), text="📎 Processamento link...")
            url_list = [u.strip() for u in additional_urls.split('\n') if u.strip().startswith('http')]
            additional_text, links_structured = fetch_additional_resources(url_list)
            st.session_state['last_links_text'] = additional_text
            st.session_state['last_links_structured'] = links_structured
        
        # FASE 4: Analisi Claude
        if opt_claude:
            step += 1
            progress.progress(int(step/total_steps*80), text="🤖 Claude sta analizzando...")
            
            # Usa dati dalla sessione se non aggiornati ora
            if not opt_macro and 'last_macro_data' in st.session_state:
                macro_data = st.session_state['last_macro_data']
            if not opt_news and 'last_news_text' in st.session_state:
                news_text = st.session_state['last_news_text']
            if not opt_links and 'last_links_text' in st.session_state:
                additional_text = st.session_state['last_links_text']
            
            claude_analysis = analyze_with_claude(
                ANTHROPIC_API_KEY,
                macro_data,
                news_text,
                additional_text
            )
        
        # ===== SALVATAGGIO =====
        progress.progress(90, text="💾 Salvataggio...")
        
        analysis_result = {
            "macro_data": macro_data,
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
            st.error("❌ Errore nel salvataggio")
    
    # ===== VISUALIZZAZIONE RISULTATI =====
    if 'current_analysis' in st.session_state:
        analysis = st.session_state['current_analysis']
        source = st.session_state.get('analysis_source', 'unknown')
        
        if source == 'new':
            st.success("✅ Nuova analisi completata!")
        elif source == 'loaded':
            st.info("📂 Analisi caricata da archivio")
        
        # Estrai dati (gestisci sia formato nuovo che legacy)
        data_container = analysis.get('data', analysis)  # Se non c'è 'data', usa analysis stesso
        
        # Formato nuovo v3
        macro_data = data_container.get('macro_data')
        news_structured = data_container.get('news_structured', {})
        links_structured = data_container.get('links_structured', [])
        claude_analysis = data_container.get('claude_analysis')
        
        # Formato legacy: se non c'è claude_analysis ma ci sono pair_analysis, è formato vecchio
        if not claude_analysis and data_container.get('pair_analysis'):
            # Questo è un'analisi legacy - il data_container È l'analisi Claude
            claude_analysis = data_container
            macro_data = None  # Nel formato legacy non c'era separazione
            news_structured = {}
            links_structured = []
        
        # Mostra sezioni in base a cosa è disponibile
        if macro_data:
            display_macro_data(macro_data)
            st.markdown("---")
        
        if news_structured or links_structured:
            display_news_summary(news_structured, links_structured)
            st.markdown("---")
        
        if claude_analysis:
            display_analysis_matrix(claude_analysis)
    
    else:
        # Stato iniziale
        st.markdown("""
        ### 👋 Benvenuto!
        
        Seleziona le opzioni nella sidebar e clicca **🚀 AVVIA ANALISI**.
        
        **Opzioni disponibili:**
        - 📊 **Dati Macro** - Tassi, inflazione, PIL (gratis)
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
