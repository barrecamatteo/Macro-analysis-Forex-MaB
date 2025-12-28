import streamlit as st
import anthropic
from duckduckgo_search import DDGS
from datetime import datetime
import json
import pandas as pd
import os
from pathlib import Path
import requests

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
FRED_API_KEY = None

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

# FRED API Key da st.secrets
try:
    FRED_API_KEY = st.secrets["FRED_API_KEY"]
except (KeyError, FileNotFoundError):
    pass

# Se non trovata, prova config.py (locale)
if not API_KEY_LOADED:
    try:
        from config import ANTHROPIC_API_KEY
        API_KEY_LOADED = True
        # Prova a caricare anche Supabase e FRED da config
        try:
            from config import SUPABASE_URL, SUPABASE_KEY
        except ImportError:
            pass
        try:
            from config import FRED_API_KEY
        except ImportError:
            pass
    except ImportError:
        pass

# Flag per Supabase
SUPABASE_ENABLED = SUPABASE_URL is not None and SUPABASE_KEY is not None

# Flag per FRED
FRED_ENABLED = FRED_API_KEY is not None

# --- CARTELLA DATI ---
DATA_FOLDER = Path("data")
DATA_FOLDER.mkdir(exist_ok=True)

# --- LISTA VALUTE ---
CURRENCIES = {
    "EUR": {"name": "Euro", "central_bank": "ECB", "type": "semi-cyclical"},
    "USD": {"name": "US Dollar", "central_bank": "Federal Reserve", "type": "safe-haven"},
    "GBP": {"name": "British Pound", "central_bank": "Bank of England", "type": "cyclical"},
    "JPY": {"name": "Japanese Yen", "central_bank": "Bank of Japan", "type": "safe-haven"},
    "CHF": {"name": "Swiss Franc", "central_bank": "SNB", "type": "safe-haven"},
    "AUD": {"name": "Australian Dollar", "central_bank": "RBA", "type": "commodity/cyclical"},
    "CAD": {"name": "Canadian Dollar", "central_bank": "Bank of Canada", "type": "commodity/cyclical"},
}

# --- COPPIE FOREX PREDEFINITE ---
FOREX_PAIRS = [
    "USD/JPY", "GBP/JPY", "AUD/JPY", "EUR/JPY", "CAD/JPY",
    "AUD/USD", "AUD/CAD", "GBP/AUD", "EUR/AUD", "EUR/CAD",
    "GBP/CAD", "USD/CHF", "EUR/CHF", "GBP/CHF", "CAD/CHF",
    "AUD/CHF", "EUR/USD", "EUR/GBP", "GBP/USD",
]

# --- SYSTEM PROMPT PER ANALISI GLOBALE ---
SYSTEM_PROMPT_GLOBAL = """Sei un analista macroeconomico forex senior. Devi analizzare TUTTE le coppie forex fornite.

## ⚠️ REGOLE CRITICHE:

### 1. LINGUA: TUTTO IN ITALIANO
- Tutti i commenti, sintesi, driver, eventi DEVONO essere in ITALIANO
- Mai usare inglese

### 2. DATA ODIERNA
- La DATA ODIERNA ti viene fornita nel prompt - USALA come riferimento!
- L'analisi DEVE essere datata con la data odierna fornita
- Gli EVENTI da monitorare devono essere FUTURI (entro i prossimi 30 giorni)

### 3. DATI NUMERICI + CONTESTO QUALITATIVO
- I DATI NUMERICI ti vengono forniti da API ufficiali (FRED/Banche Centrali/OECD)
- Le NOTIZIE e OUTLOOK ti vengono fornite dalle ricerche web
- USA ENTRAMBI per l'analisi! I numeri da soli non bastano!

### 4. ⭐ ASPETTATIVE SUI TASSI (CRUCIALE!)
- Questo è il fattore PIÙ IMPORTANTE per il forex!
- Analizza le notizie per capire:
  - Chi sta TAGLIANDO i tassi? (es. Fed, ECB, BoC → bearish per la valuta)
  - Chi sta ALZANDO i tassi? (es. BoJ → bullish per la valuta)
  - Quanti tagli/rialzi sono PREZZATI dal mercato per il 2026?
- Il TREND dei tassi conta più del livello attuale!

### 5. PUNTEGGI PER COPPIA
- I punteggi devono essere calcolati PER OGNI COPPIA SPECIFICA
- Lo stesso USD può avere punteggi DIVERSI in USD/JPY vs USD/EUR

## INDICATORI DA CONSIDERARE:
- **Interest Rate**: Tasso attuale (meno importante del trend!)
- **Rate Expectations**: CRUCIALE - tagli o rialzi previsti?
- **Inflation Rate**: ⚠️ ATTENZIONE ALLA LOGICA!
  - Inflazione ALTA (>2.5%) → BC non può tagliare tassi → POSITIVO per valuta
  - Inflazione BASSA (<2%) → BC può tagliare tassi → NEGATIVO per valuta
  - Il target è ~2%, quindi inflazione sopra target = hawkish = valuta forte
- **GDP Growth**: Momentum economico
- **Unemployment**: Salute del mercato del lavoro
- **Business Confidence (BCI)**: Sentiment imprese (>100 = ottimismo, <100 = pessimismo)

## COME VALUTARE LE ASPETTATIVE TASSI:
- Banca centrale che TAGLIA → score NEGATIVO per quella valuta
- Banca centrale che ALZA → score POSITIVO per quella valuta
- Banca centrale che PAUSA ma pronta a tagliare → leggermente negativo
- Banca centrale che PAUSA ma pronta ad alzare → leggermente positivo

## COME VALUTARE L'INFLAZIONE (IMPORTANTE!):
- Inflazione ALTA (es. 3-4%) → La BC deve mantenere tassi alti o alzarli → POSITIVO per valuta
- Inflazione sotto target (es. 0-1.5%) → La BC può tagliare tassi → NEGATIVO per valuta
- Esempio: AUD inflazione 3.3% vs USD inflazione 0.2%
  - AUD: inflazione alta = RBA non può tagliare = POSITIVO per AUD
  - USD: inflazione bassa = Fed può tagliare = NEGATIVO per USD
  - Quindi su INFLAZIONE: AUD score POSITIVO, USD score NEGATIVO

## PARAMETRI DA VALUTARE (per ogni coppia A vs B):

1. **TASSI ATTUALI** (scala -1 a +1) - Differenziale tassi attuale
2. **ASPETTATIVE TASSI FUTURI** (scala -2 a +2) - ⭐⭐ IL PIÙ IMPORTANTE! Peso doppio! Chi taglia vs chi alza?
3. **INFLAZIONE** (scala -1 a +1) - ⚠️ Inflazione ALTA = POSITIVO! Chi ha inflazione sopra il 2%? (BC non può tagliare)
4. **CRESCITA/PIL** (scala -1 a +1) - Chi cresce di più?
5. **RISK SENTIMENT** (scala -1 a +1) - Safe-haven vs cyclical nel contesto attuale
6. **BILANCIA/FISCALE** (scala -1 a +1) - Sostenibilità fiscale

## OUTPUT RICHIESTO (JSON):
{
    "analysis_date": "YYYY-MM-DD",
    "currencies_data": {
        "EUR": {
            "interest_rate": "valore",
            "inflation_rate": "valore",
            "gdp_growth": "valore",
            "unemployment": "valore",
            "business_confidence": "valore"
        },
        ... (per tutte le 7 valute)
    },
    "pairs_analysis": [
        {
            "pair": "USD/JPY",
            "currency_a": "USD",
            "currency_b": "JPY",
            "scores_a": {
                "rates_now": {"score": -1|0|+1, "comment": "confronto tassi attuali"},
                "rates_future": {"score": -2|-1|0|+1|+2, "comment": "⭐⭐ PESO DOPPIO! Fed taglia vs BoJ alza"},
                "inflation": {"score": -1|0|+1, "comment": "⚠️ Inflazione ALTA = POSITIVO!"},
                "growth": {"score": -1|0|+1, "comment": "confronto crescita"},
                "risk_sentiment": {"score": -1|0|+1, "comment": "contesto risk on/off"},
                "balance_fiscal": {"score": -1|0|+1, "comment": "confronto bilancia e debito"}
            },
            "scores_b": { ... },
            "total_a": int,
            "total_b": int,
            "differential": int,
            "bias": "bullish|bearish|neutral",
            "bias_strength": "strong|moderate|slight|neutral",
            "summary": "sintesi in italiano che SPIEGA il perché del bias basandosi su tassi futuri, outlook, etc.",
            "current_price": float,
            "scenarios": {
                "base": {"low": float, "high": float},
                "bullish": {"low": float, "high": float},
                "bearish": {"low": float, "high": float}
            },
            "key_drivers": ["driver1", "driver2"]
        },
        ... (per tutte le 19 coppie)
    ],
    "ranking": {
        "top_bullish": [{"pair": "XXX/YYY", "diff": int}, ...],
        "top_bearish": [{"pair": "XXX/YYY", "diff": int}, ...]
    },
    "events_calendar": []
}

## CHECKLIST FINALE:
✅ Tutti i testi in ITALIANO
✅ Aspettative tassi: range -2/+2 (peso doppio!)
✅ Altri parametri: range -1/+1
✅ INFLAZIONE: ricorda che inflazione ALTA = POSITIVO per valuta (BC hawkish)!
✅ Sintesi che spiega il PERCHÉ del bias
✅ events_calendar: lascia array VUOTO []
"""


# --- FUNZIONI SALVATAGGIO/CARICAMENTO (SUPABASE + LOCAL FALLBACK) ---

def supabase_request(method: str, endpoint: str, data: dict = None) -> dict | None:
    """Esegue una richiesta a Supabase REST API"""
    if not SUPABASE_ENABLED:
        return None
    
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }
    
    url = f"{SUPABASE_URL}/rest/v1/{endpoint}"
    
    try:
        if method == "GET":
            response = requests.get(url, headers=headers)
        elif method == "POST":
            response = requests.post(url, headers=headers, json=data)
        elif method == "DELETE":
            response = requests.delete(url, headers=headers)
        else:
            return None
        
        if response.status_code in [200, 201]:
            return response.json() if response.text else {}
        elif response.status_code == 204:
            return {}
        else:
            return None
    except Exception as e:
        st.error(f"Errore Supabase: {e}")
        return None


def get_italy_time():
    """Restituisce l'ora italiana (UTC+1, o UTC+2 con ora legale)"""
    from datetime import timezone, timedelta
    # Italia è UTC+1 (inverno) - per semplicità usiamo +1
    italy_tz = timezone(timedelta(hours=1))
    return datetime.now(italy_tz)


def save_analysis(analysis: dict) -> bool:
    """Salva l'analisi su Supabase (o locale come fallback)"""
    try:
        now = get_italy_time()
        datetime_str = now.strftime("%Y-%m-%d_%H-%M")
        
        analysis["analysis_date"] = now.strftime("%Y-%m-%d")
        analysis["analysis_time"] = now.strftime("%H:%M")
        analysis["analysis_datetime"] = datetime_str
        
        if SUPABASE_ENABLED:
            # Salva su Supabase
            data = {
                "analysis_datetime": datetime_str,
                "analysis_date": analysis["analysis_date"],
                "analysis_time": analysis["analysis_time"],
                "data": analysis
            }
            result = supabase_request("POST", "analyses", data)
            return result is not None
        else:
            # Fallback locale
            filename = DATA_FOLDER / f"analysis_{datetime_str}.json"
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(analysis, f, ensure_ascii=False, indent=2)
            return True
    except Exception as e:
        st.error(f"Errore salvataggio: {e}")
        return False


def load_analysis(datetime_str: str) -> dict | None:
    """Carica un'analisi da Supabase (o locale come fallback)"""
    try:
        if SUPABASE_ENABLED:
            result = supabase_request("GET", f"analyses?analysis_datetime=eq.{datetime_str}")
            if result and len(result) > 0:
                return result[0].get("data", {})
        else:
            filename = DATA_FOLDER / f"analysis_{datetime_str}.json"
            if filename.exists():
                with open(filename, "r", encoding="utf-8") as f:
                    return json.load(f)
    except Exception as e:
        st.error(f"Errore caricamento: {e}")
    return None


def delete_analysis(datetime_str: str) -> bool:
    """Cancella un'analisi da Supabase (o locale come fallback)"""
    try:
        if SUPABASE_ENABLED:
            result = supabase_request("DELETE", f"analyses?analysis_datetime=eq.{datetime_str}")
            return result is not None
        else:
            filename = DATA_FOLDER / f"analysis_{datetime_str}.json"
            if filename.exists():
                filename.unlink()
                return True
    except Exception as e:
        st.error(f"Errore cancellazione: {e}")
    return False


def get_available_dates() -> list:
    """Restituisce le date/ore delle analisi disponibili (più recente prima)"""
    dates = []
    
    if SUPABASE_ENABLED:
        result = supabase_request("GET", "analyses?select=analysis_datetime&order=analysis_datetime.desc")
        if result:
            dates = [r.get("analysis_datetime") for r in result if r.get("analysis_datetime")]
    else:
        for file in DATA_FOLDER.glob("analysis_*.json"):
            try:
                datetime_str = file.stem.replace("analysis_", "")
                dates.append(datetime_str)
            except:
                pass
        dates = sorted(dates, reverse=True)
    
    return dates


def get_latest_analysis() -> dict | None:
    """Carica l'ultima analisi disponibile"""
    dates = get_available_dates()
    if dates:
        return load_analysis(dates[0])
    return None


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


# --- FUNZIONI RICERCA E ANALISI ---

# Indicatori richiesti per ogni valuta
REQUIRED_INDICATORS = ["interest_rate", "inflation_rate", "gdp_growth", "unemployment", "business_confidence"]

# Mappa valuta -> paese/area per le ricerche
CURRENCY_TO_COUNTRY = {
    "EUR": "Euro Area / Eurozone / ECB",
    "USD": "United States / US / Federal Reserve",
    "GBP": "United Kingdom / UK / Bank of England",
    "JPY": "Japan / Bank of Japan",
    "CHF": "Switzerland / Swiss National Bank",
    "AUD": "Australia / Reserve Bank of Australia",
    "CAD": "Canada / Bank of Canada",
}


def fetch_all_currencies_data() -> dict:
    """
    Recupera dati macro da API ufficiali via FRED.
    Fonte: Federal Reserve Economic Data (dati OECD/Banche Centrali aggregati)
    """
    if not FRED_ENABLED:
        st.warning("⚠️ FRED API Key non configurata - usando dati di fallback")
        # Fallback con dati di esempio se FRED non disponibile
        return {
            'USD': {'interest_rate': 4.50, 'inflation_rate': 2.7, 'gdp_growth': 2.8, 'unemployment': 4.2, 'business_confidence': 101.5},
            'EUR': {'interest_rate': 3.00, 'inflation_rate': 2.4, 'gdp_growth': 0.4, 'unemployment': 6.3, 'business_confidence': 99.2},
            'GBP': {'interest_rate': 4.75, 'inflation_rate': 2.6, 'gdp_growth': 0.1, 'unemployment': 4.3, 'business_confidence': 98.5},
            'JPY': {'interest_rate': 0.25, 'inflation_rate': 2.9, 'gdp_growth': -0.2, 'unemployment': 2.5, 'business_confidence': 99.8},
            'CHF': {'interest_rate': 0.50, 'inflation_rate': 0.7, 'gdp_growth': 0.4, 'unemployment': 2.3, 'business_confidence': 100.1},
            'AUD': {'interest_rate': 4.35, 'inflation_rate': 2.8, 'gdp_growth': 0.3, 'unemployment': 4.1, 'business_confidence': 98.9},
            'CAD': {'interest_rate': 3.25, 'inflation_rate': 2.0, 'gdp_growth': 0.3, 'unemployment': 6.8, 'business_confidence': 99.5},
        }
    
    try:
        fetcher = MacroDataFetcher(FRED_API_KEY)
        raw_data = fetcher.get_all_data()
        
        # Converti nel formato atteso dal resto del codice
        result = {}
        for currency, info in raw_data['data'].items():
            indicators = info['indicators']
            result[currency] = {
                'interest_rate': indicators.get('interest_rate', {}).get('value', 'N/A'),
                'inflation_rate': indicators.get('inflation', {}).get('value', 'N/A'),
                'gdp_growth': indicators.get('gdp_growth', {}).get('value', 'N/A'),
                'unemployment': indicators.get('unemployment', {}).get('value', 'N/A'),
                'business_confidence': indicators.get('business_confidence', {}).get('value', 'N/A'),
            }
        
        return result
        
    except Exception as e:
        st.error(f"Errore nel recupero dati FRED: {e}")
        # Fallback con N/A
        return {curr: {ind: 'N/A' for ind in REQUIRED_INDICATORS} for curr in CURRENCY_TO_COUNTRY.keys()}


def search_qualitative_data() -> str:
    """Cerca notizie qualitative, outlook e aspettative per ogni valuta."""
    all_results = []
    
    today = datetime.now()
    
    all_results.append(f"[DATE] Data odierna: {today.strftime('%d/%m/%Y')}")
    
    # Ricerche specifiche per ogni banca centrale - POLITICA MONETARIA (in inglese)
    central_bank_queries = {
        "USD": [
            "Federal Reserve interest rate decision 2025",
            "Fed rate cuts 2026 forecast expectations",
            "FOMC December 2025 statement dovish hawkish",
            "US economy outlook 2026",
        ],
        "EUR": [
            "ECB interest rate decision 2025",
            "ECB rate cuts 2026 Lagarde forecast",
            "Eurozone economy outlook 2026",
            "Germany recession outlook",
        ],
        "GBP": [
            "Bank of England rate decision 2025",
            "BoE interest rate forecast 2026",
            "UK economy inflation outlook 2026",
        ],
        "JPY": [
            "Bank of Japan rate hike 2025 Ueda",
            "BoJ monetary policy outlook 2026",
            "Japan inflation wage growth",
            "Yen intervention outlook",
        ],
        "CHF": [
            "SNB Swiss National Bank rate decision 2025",
            "Switzerland interest rate outlook 2026",
            "Swiss franc safe haven",
        ],
        "AUD": [
            "RBA Reserve Bank Australia rate decision 2025",
            "Australia interest rate forecast 2026",
            "AUD China commodities outlook",
        ],
        "CAD": [
            "Bank of Canada rate decision 2025",
            "BoC interest rate forecast 2026",
            "Canada economy oil outlook",
        ],
    }
    
    for currency, queries in central_bank_queries.items():
        all_results.append(f"\n[{currency} - MONETARY POLICY & OUTLOOK]")
        for query in queries:
            try:
                results = DDGS().text(query, max_results=3)
                for r in results:
                    title = r.get('title', '')
                    snippet = r.get('body', '')
                    all_results.append(f"[{currency}] {title}: {snippet[:400]}")
            except:
                pass
    
    # Ricerche GEOPOLITICHE e RISK SENTIMENT
    geopolitical_queries = [
        "geopolitical risk 2026 market forex",
        "US China trade tariffs 2026",
        "global recession risk 2026",
        "risk sentiment forex 2025",
    ]
    
    all_results.append(f"\n[GEOPOLITICS & RISK SENTIMENT]")
    for query in geopolitical_queries:
        try:
            results = DDGS().text(query, max_results=2)
            for r in results:
                all_results.append(f"[GEO] {r['title']}: {r['body'][:350]}")
        except:
            pass
    
    # Ricerche FOREX OUTLOOK specifiche
    forex_queries = [
        "EUR USD forecast 2026",
        "USD JPY outlook 2026",
        "major currencies forecast 2026",
    ]
    
    all_results.append(f"\n[FOREX OUTLOOK]")
    for query in forex_queries:
        try:
            results = DDGS().text(query, max_results=2)
            for r in results:
                all_results.append(f"[FX] {r['title']}: {r['body'][:350]}")
        except:
            pass
    
    # Calendario eventi prossimi 30 giorni
    calendar_queries = [
        "FOMC meeting January 2026",
        "ECB meeting January 2026",
        "central bank meetings 2026 calendar",
    ]
    
    all_results.append(f"\n[ECONOMIC CALENDAR]")
    for query in calendar_queries:
        try:
            results = DDGS().text(query, max_results=1)
            for r in results:
                all_results.append(f"[CAL] {r['title']}: {r['body'][:250]}")
        except:
            pass
    
    return "\n".join(all_results)


def search_all_currencies_data() -> tuple[dict, str]:
    """Cerca dati macro per TUTTE le valute - API ufficiali + ricerche qualitative."""
    
    # 1. FASE 1: Scarica dati numerici da API ufficiali (FRED)
    st.info("📊 FASE 1: Scaricamento dati da API ufficiali (FRED/OECD)...")
    te_data = fetch_all_currencies_data()
    
    # Verifica completezza dati
    missing_data = []
    for curr, data in te_data.items():
        for key, value in data.items():
            if value == 'N/A' or value is None:
                missing_data.append(f"{curr}-{key}")
    
    if missing_data:
        st.warning(f"⚠️ Alcuni dati potrebbero essere in ritardo: {', '.join(missing_data[:5])}")
    else:
        st.success("✅ Tutti i dati macro recuperati con successo!")
    
    # 2. FASE 2: Ricerche qualitative approfondite (notizie, aspettative)
    st.info("📰 FASE 2: Ricerca notizie, outlook e aspettative mercati...")
    qualitative_data = search_qualitative_data()
    
    return te_data, qualitative_data


def analyze_all_pairs(api_key: str, te_data: dict, search_text: str) -> dict:
    """Analizza TUTTE le coppie forex in una sola chiamata API"""
    
    client = anthropic.Anthropic(api_key=api_key)
    
    pairs_list = ", ".join(FOREX_PAIRS)
    currencies_info = "\n".join([f"- {k}: {v['name']} ({v['central_bank']}) - Tipo: {v['type']}" 
                                  for k, v in CURRENCIES.items()])
    
    # Formatta i dati macro
    te_formatted = "\n\n".join([
        f"**{curr}:**\n" + "\n".join([f"  - {k}: {v}" for k, v in data.items()])
        for curr, data in te_data.items()
    ])
    
    today = datetime.now()
    
    user_prompt = f"""
Analizza TUTTE queste coppie forex: {pairs_list}

## ⚠️ DATA ODIERNA: {today.strftime('%Y-%m-%d')} ({today.strftime('%A, %d %B %Y')})

**Valute da analizzare:**
{currencies_info}

---

## 📊 DATI NUMERICI DA API UFFICIALI (FRED/Banche Centrali/OECD):
{te_formatted}

---

## 📰 NOTIZIE, OUTLOOK, ASPETTATIVE E CALENDARIO ECONOMICO:
{search_text}

---

## ⭐ ISTRUZIONI CRITICHE:

1. **USA LE NOTIZIE** per capire chi sta tagliando/alzando i tassi!
   - Se leggi "Fed cuts rates" o "Fed dovish" → USD tendenzialmente debole
   - Se leggi "BoJ raises rates" o "BoJ hawkish" → JPY tendenzialmente forte
   
2. **ASPETTATIVE > TASSI ATTUALI**: il mercato guarda AVANTI, non indietro!

3. **analysis_date** = "{today.strftime('%Y-%m-%d')}"

4. **events_calendar** = Lascia un array VUOTO []. Gli eventi verranno mostrati separatamente.

5. Ogni **summary** deve spiegare PERCHÉ quel bias basandosi sulle notizie

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
        
        # Pulisci JSON - rimuovi markdown
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0]
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0]
        
        # Trova l'inizio e la fine del JSON
        response_text = response_text.strip()
        
        # Cerca il primo { e l'ultimo }
        start_idx = response_text.find('{')
        end_idx = response_text.rfind('}')
        
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            response_text = response_text[start_idx:end_idx + 1]
        
        analysis = json.loads(response_text)
        
        # Se currencies_data manca o è incompleto, usa i dati API
        if "currencies_data" not in analysis or not analysis["currencies_data"]:
            analysis["currencies_data"] = te_data
        
        return analysis
        
    except json.JSONDecodeError as e:
        # Mostra più contesto per debug
        error_pos = e.pos if hasattr(e, 'pos') else 0
        context_start = max(0, error_pos - 100)
        context_end = min(len(response_text), error_pos + 100)
        context = response_text[context_start:context_end] if response_text else "N/A"
        return {
            "error": f"Errore parsing JSON: {e}",
            "raw": response_text[:3000] if response_text else "Risposta vuota",
            "context": f"...{context}..."
        }
    except Exception as e:
        return {"error": f"Errore API: {e}"}


# --- FUNZIONI VISUALIZZAZIONE ---

def format_date_ita(date_str: str) -> str:
    """Converte data da YYYY-MM-DD a gg/mm/aaaa"""
    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        return date_obj.strftime("%d/%m/%Y")
    except:
        return date_str


def get_bias_color(bias: str, diff: int) -> str:
    """Restituisce emoji in base al bias"""
    if bias == "bullish":
        return "🟢🟢" if abs(diff) >= 4 else "🟢"
    elif bias == "bearish":
        return "🔴🔴" if abs(diff) >= 4 else "🔴"
    return "⚪"


def display_matrix(analysis: dict):
    """Visualizza la matrice overview di tutte le coppie"""
    
    if "error" in analysis:
        st.error(f"❌ {analysis['error']}")
        if "raw" in analysis:
            with st.expander("Risposta raw"):
                st.code(analysis["raw"])
        return
    
    st.markdown("---")
    
    # Header
    st.markdown("## 📊 FOREX MACRO MATRIX")
    date_formatted = format_date_ita(analysis.get('analysis_date', 'N/A'))
    time_formatted = analysis.get('analysis_time', '')
    
    if time_formatted:
        st.caption(f"Analisi del {date_formatted} alle {time_formatted}")
    else:
        st.caption(f"Analisi del {date_formatted}")
    
    # Dati macro per valuta (da API ufficiali FRED)
    with st.expander("📈 Dati Macro per Valuta (fonte: API Ufficiali - FRED/OECD)", expanded=True):
        st.caption("Fonti: Federal Reserve, BCE, BoE, BoJ, SNB, RBA, BoC, Eurostat, OECD")
        currencies_data = analysis.get("currencies_data", {})
        
        if currencies_data:
            # Crea una tabella con tutti gli indicatori
            indicators_table = []
            for curr, data in currencies_data.items():
                indicators_table.append({
                    "Valuta": curr,
                    "Tasso %": data.get('interest_rate', 'N/A'),
                    "Inflaz. %": data.get('inflation_rate', data.get('inflation_cpi', 'N/A')),
                    "PIL %": data.get('gdp_growth', 'N/A'),
                    "Disocc. %": data.get('unemployment', 'N/A'),
                    "BCI": data.get('business_confidence', 'N/A'),
                })
            
            df_indicators = pd.DataFrame(indicators_table)
            st.dataframe(df_indicators, use_container_width=True, hide_index=True)
            
            st.caption("📌 BCI = Business Confidence Index OECD (>100 = ottimismo, <100 = pessimismo)")
        else:
            st.info("Dati numerici non disponibili per questa analisi")
    
    st.markdown("---")
    
    # Ranking migliori/peggiori
    ranking = analysis.get("ranking", {})
    
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("### 🏆 TOP BULLISH (Long)")
        for item in ranking.get("top_bullish", [])[:5]:
            pair = item.get("pair", "")
            diff = item.get("diff", 0)
            st.markdown(f"**{pair}** → Diff: **+{diff}**")
    
    with col2:
        st.markdown("### 📉 TOP BEARISH (Short)")
        for item in ranking.get("top_bearish", [])[:5]:
            pair = item.get("pair", "")
            diff = item.get("diff", 0)
            st.markdown(f"**{pair}** → Diff: **{diff}**")
    
    st.markdown("---")
    
    # Tabella principale con selezione
    st.markdown("### 📋 Tutte le Coppie")
    st.caption("👆 Clicca su una riga per vedere il dettaglio completo")
    
    pairs_data = analysis.get("pairs_analysis", [])
    
    # Crea DataFrame
    table_data = []
    for pair_info in pairs_data:
        pair = pair_info.get("pair", "")
        diff = pair_info.get("differential", 0)
        bias = pair_info.get("bias", "neutral")
        total_a = pair_info.get("total_a", 0)
        total_b = pair_info.get("total_b", 0)
        summary = pair_info.get("summary", "")
        
        bias_icon = get_bias_color(bias, diff)
        
        table_data.append({
            "Coppia": pair,
            "Bias": f"{bias_icon} {bias.upper()}",
            "Diff": diff,
            "A": total_a,
            "B": total_b,
            "Sintesi": summary,
        })
    
    df = pd.DataFrame(table_data)
    df_sorted = df.sort_values("Diff", ascending=False).reset_index(drop=True)
    
    # Mostra tabella con selezione
    selection = st.dataframe(
        df_sorted,
        use_container_width=True,
        hide_index=True,
        height=700,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "Coppia": st.column_config.TextColumn("Coppia", width=85),
            "Bias": st.column_config.TextColumn("Bias", width=120),
            "Diff": st.column_config.NumberColumn("Diff", width=55),
            "A": st.column_config.NumberColumn("A", width=45),
            "B": st.column_config.NumberColumn("B", width=45),
            "Sintesi": st.column_config.TextColumn("Sintesi", width=500),
        }
    )
    
    # Se una riga è selezionata, mostra il dettaglio
    if selection and selection.selection and selection.selection.rows:
        selected_idx = selection.selection.rows[0]
        selected_pair_name = df_sorted.iloc[selected_idx]["Coppia"]
        
        pair_detail = next((p for p in pairs_data if p.get("pair") == selected_pair_name), None)
        if pair_detail:
            st.markdown("---")
            display_pair_detail(pair_detail, analysis.get("currencies_data", {}))
    
    # Calendario eventi (link esterni)
    st.markdown("---")
    st.markdown("### 📅 Calendario Economico")
    st.info("📊 Consulta i calendari economici per gli eventi della settimana")
    
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("🔗 [**TradingEconomics Calendar**](https://tradingeconomics.com/calendar)")
    with col2:
        st.markdown("🔗 [**ForexFactory Calendar**](https://www.forexfactory.com/calendar)")
    
    st.caption("Filtra per impatto 2-3 stelle e per le valute: USD, EUR, GBP, JPY, CHF, AUD, CAD")
    
    # JSON Raw
    with st.expander("🔧 Dati Raw (JSON)"):
        st.json(analysis)


def display_pair_detail(pair_data: dict, currencies_data: dict):
    """Visualizza il dettaglio di una singola coppia"""
    
    pair = pair_data.get("pair", "")
    curr_a = pair_data.get("currency_a", "")
    curr_b = pair_data.get("currency_b", "")
    
    # Box bias
    bias = pair_data.get("bias", "neutral")
    diff = pair_data.get("differential", 0)
    strength = pair_data.get("bias_strength", "neutral")
    
    if bias == "bullish":
        st.success(f"### 🟢 {pair} - BIAS RIALZISTA ({strength.upper()})")
    elif bias == "bearish":
        st.error(f"### 🔴 {pair} - BIAS RIBASSISTA ({strength.upper()})")
    else:
        st.info(f"### ⚪ {pair} - NEUTRALE")
    
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Differenziale", f"{diff:+d}")
    with col2:
        st.metric(f"Score {curr_a}", f"{pair_data.get('total_a', 0):+d}")
    with col3:
        st.metric(f"Score {curr_b}", f"{pair_data.get('total_b', 0):+d}")
    
    st.markdown(f"**Sintesi:** {pair_data.get('summary', 'N/A')}")
    
    # Dati macro delle due valute
    st.markdown("#### 📈 Confronto Dati Macro e Punteggi")
    
    data_a = currencies_data.get(curr_a, {})
    data_b = currencies_data.get(curr_b, {})
    scores_a = pair_data.get("scores_a", {})
    scores_b = pair_data.get("scores_b", {})
    
    def get_score_info(scores, key):
        item = scores.get(key, {})
        if isinstance(item, dict):
            return item.get("score", 0), item.get("comment", "-")
        return item if isinstance(item, int) else 0, "-"
    
    col1, col2 = st.columns(2)
    
    params = [
        ("rates_now", "Tassi Attuali"),
        ("rates_future", "Aspettative Tassi"),
        ("inflation", "Inflazione"),
        ("growth", "Crescita/PIL"),
        ("risk_sentiment", "Risk Sentiment"),
        ("balance_fiscal", "Bilancia/Fiscale"),
    ]
    
    with col1:
        st.markdown(f"### {curr_a}")
        st.markdown(f"""
**Dati Economici:**
- 🏦 Tasso BC: **{data_a.get('interest_rate', 'N/A')}**
- 📊 Inflazione: **{data_a.get('inflation_rate', data_a.get('inflation_cpi', 'N/A'))}**
- 📈 PIL: **{data_a.get('gdp_growth', 'N/A')}**
- 👥 Disoccupazione: **{data_a.get('unemployment', 'N/A')}**
- 📉 Business Confidence: **{data_a.get('business_confidence', 'N/A')}**
""")
        
        st.markdown(f"**Punteggi {curr_a} vs {curr_b}:**")
        
        scores_table_a = []
        total_calc_a = 0
        for key, label in params:
            score, comment = get_score_info(scores_a, key)
            total_calc_a += score
            emoji = "🟢" if score > 0 else ("🔴" if score < 0 else "⚪")
            scores_table_a.append({
                "Parametro": label,
                "Score": f"{emoji} {score:+d}",
                "Motivazione": comment
            })
        
        df_scores_a = pd.DataFrame(scores_table_a)
        st.dataframe(df_scores_a, use_container_width=True, hide_index=True, height=250)
        
        total_a = pair_data.get("total_a", total_calc_a)
        emoji_total = "🟢" if total_a > 0 else ("🔴" if total_a < 0 else "⚪")
        st.markdown(f"### {emoji_total} TOTALE: **{total_a:+d}**")
    
    with col2:
        st.markdown(f"### {curr_b}")
        st.markdown(f"""
**Dati Economici:**
- 🏦 Tasso BC: **{data_b.get('interest_rate', 'N/A')}**
- 📊 Inflazione: **{data_b.get('inflation_rate', data_b.get('inflation_cpi', 'N/A'))}**
- 📈 PIL: **{data_b.get('gdp_growth', 'N/A')}**
- 👥 Disoccupazione: **{data_b.get('unemployment', 'N/A')}**
- 📉 Business Confidence: **{data_b.get('business_confidence', 'N/A')}**
""")
        
        st.markdown(f"**Punteggi {curr_b} vs {curr_a}:**")
        
        scores_table_b = []
        total_calc_b = 0
        for key, label in params:
            score, comment = get_score_info(scores_b, key)
            total_calc_b += score
            emoji = "🟢" if score > 0 else ("🔴" if score < 0 else "⚪")
            scores_table_b.append({
                "Parametro": label,
                "Score": f"{emoji} {score:+d}",
                "Motivazione": comment
            })
        
        df_scores_b = pd.DataFrame(scores_table_b)
        st.dataframe(df_scores_b, use_container_width=True, hide_index=True, height=250)
        
        total_b = pair_data.get("total_b", total_calc_b)
        emoji_total = "🟢" if total_b > 0 else ("🔴" if total_b < 0 else "⚪")
        st.markdown(f"### {emoji_total} TOTALE: **{total_b:+d}**")
    
    # Scenari
    st.markdown("---")
    st.markdown("#### 📊 Scenari di Prezzo")
    
    current_price = pair_data.get("current_price", 0)
    scenarios = pair_data.get("scenarios", {})
    
    if current_price:
        st.info(f"**Prezzo attuale:** ~{current_price:.4f}")
    else:
        st.info("Prezzo non disponibile")
    
    col1, col2, col3 = st.columns(3)
    with col1:
        base = scenarios.get("base", {})
        if base:
            st.markdown(f"**🟡 Base**\n\n{base.get('low', 0):.4f} - {base.get('high', 0):.4f}")
    with col2:
        bull = scenarios.get("bullish", {})
        if bull:
            st.markdown(f"**🟢 {curr_a} Forte**\n\n{bull.get('low', 0):.4f} - {bull.get('high', 0):.4f}")
    with col3:
        bear = scenarios.get("bearish", {})
        if bear:
            st.markdown(f"**🔴 {curr_b} Forte**\n\n{bear.get('low', 0):.4f} - {bear.get('high', 0):.4f}")
    
    # Key drivers
    st.markdown("#### 🔑 Driver Chiave")
    for driver in pair_data.get("key_drivers", []):
        st.markdown(f"• {driver}")


# --- STILE CSS ---
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: bold;
        background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }
    
    /* Migliora visualizzazione tabella */
    [data-testid="stDataFrame"] {
        width: 100%;
    }
    
    [data-testid="stDataFrame"] td {
        white-space: pre-wrap !important;
        word-wrap: break-word !important;
    }
    
    /* Aumenta altezza righe */
    [data-testid="stDataFrame"] [data-testid="StyledDataFrame"] {
        font-size: 14px;
    }
</style>
""", unsafe_allow_html=True)

# --- HEADER ---
st.markdown('<p class="main-header">📊 Forex Macro Analyst</p>', unsafe_allow_html=True)
st.markdown("**Powered by Claude AI** - Analisi macroeconomica globale di tutte le coppie forex")

# --- SIDEBAR ---
with st.sidebar:
    st.header("⚙️ Configurazione")
    
    if API_KEY_LOADED:
        st.success("✅ API Key configurata")
    else:
        st.error("❌ API Key mancante")
    
    if FRED_ENABLED:
        st.success("📊 Dati FRED attivi")
    else:
        st.warning("📊 FRED Key mancante")
    
    if SUPABASE_ENABLED:
        st.success("☁️ Database cloud attivo")
    else:
        st.info("💾 Salvataggio locale")
    
    st.markdown("---")
    
    # Analisi disponibili
    available_dates = get_available_dates()
    
    if available_dates:
        st.markdown("### 📁 Analisi Salvate")
        
        # Selettore data/ora
        date_options = [format_datetime_display(d) for d in available_dates]
        selected_date_idx = st.selectbox(
            "Seleziona analisi:",
            range(len(date_options)),
            format_func=lambda x: date_options[x],
            key="date_selector"
        )
        
        selected_date = available_dates[selected_date_idx]
        
        col1, col2 = st.columns(2)
        
        with col1:
            if st.button("📂 Carica", use_container_width=True):
                loaded = load_analysis(selected_date)
                if loaded:
                    st.session_state['current_analysis'] = loaded
                    st.session_state['analysis_source'] = 'loaded'
                    st.rerun()
        
        with col2:
            if st.button("🗑️ Elimina", use_container_width=True, type="secondary"):
                st.session_state['confirm_delete'] = selected_date
        
        # Conferma eliminazione
        if 'confirm_delete' in st.session_state and st.session_state['confirm_delete'] == selected_date:
            st.warning(f"⚠️ Eliminare l'analisi del {format_datetime_display(selected_date)}?")
            col1, col2 = st.columns(2)
            with col1:
                if st.button("✅ Sì", use_container_width=True):
                    if delete_analysis(selected_date):
                        st.success("Analisi eliminata!")
                        # Se l'analisi corrente è quella eliminata, rimuovila
                        if 'current_analysis' in st.session_state:
                            current_dt = st.session_state['current_analysis'].get('analysis_datetime', '')
                            if current_dt == selected_date:
                                del st.session_state['current_analysis']
                        del st.session_state['confirm_delete']
                        st.rerun()
            with col2:
                if st.button("❌ No", use_container_width=True):
                    del st.session_state['confirm_delete']
                    st.rerun()
    else:
        st.info("Nessuna analisi salvata")
    
    st.markdown("---")
    
    st.markdown(f"**Coppie analizzate:** {len(FOREX_PAIRS)}")
    st.markdown(f"**Valute:** {', '.join(CURRENCIES.keys())}")
    st.markdown(f"**Modello:** Claude Sonnet 4")
    st.markdown(f"**Dati:** API Banche Centrali")
    
    st.markdown("---")
    
    # Opzioni analisi
    st.markdown("### ⚙️ Opzioni")
    
    enable_claude_analysis = st.checkbox(
        "🧠 Analisi Claude (coppie forex)",
        value=True,
        help="Disabilita per testare solo il recupero dati dalle API"
    )
    
    if not enable_claude_analysis:
        st.info("💡 Solo recupero dati - nessun token Claude usato")
    
    st.markdown("---")
    
    # Pulsante nuova analisi
    analyze_btn = st.button(
        "🔄 Nuova Analisi" if enable_claude_analysis else "🔄 Test Recupero Dati",
        disabled=not API_KEY_LOADED,
        use_container_width=True,
        type="primary"
    )
    
    st.caption("📝 Ogni analisi viene salvata con data e ora")


# --- MAIN ---

# Carica automaticamente l'ultima analisi all'avvio
if 'current_analysis' not in st.session_state:
    latest = get_latest_analysis()
    if latest:
        st.session_state['current_analysis'] = latest
        st.session_state['analysis_source'] = 'auto'

# Nuova analisi
if analyze_btn:
    progress = st.progress(0, text="Inizializzazione...")
    
    progress.progress(5, text="📊 FASE 1: Scaricamento dati da API ufficiali (Banche Centrali)...")
    te_data, search_text = search_all_currencies_data()
    
    # Mostra i dati recuperati
    st.markdown("---")
    st.markdown("### 📊 Dati Recuperati dalle API")
    
    # Tabella dati
    if te_data:
        table_rows = []
        for curr, data in te_data.items():
            row = {"Valuta": curr}
            for key, value in data.items():
                row[key] = value
            table_rows.append(row)
        
        df_test = pd.DataFrame(table_rows)
        st.dataframe(df_test, use_container_width=True, hide_index=True)
        
        # Verifica completezza
        missing = []
        for curr, data in te_data.items():
            for key, value in data.items():
                if value == 'N/A' or value is None:
                    missing.append(f"{curr}-{key}")
        
        if missing:
            st.warning(f"⚠️ Dati mancanti: {', '.join(missing)}")
        else:
            st.success("✅ Tutti i dati recuperati con successo!")
    
    # Se analisi Claude abilitata, procedi
    if enable_claude_analysis:
        progress.progress(50, text="🧠 FASE 2: Claude sta analizzando le coppie forex...")
        analysis = analyze_all_pairs(ANTHROPIC_API_KEY, te_data, search_text)
        
        if "error" not in analysis:
            analysis["model_used"] = "Claude Sonnet 4"
            analysis["data_source"] = "API Ufficiali Banche Centrali"
            progress.progress(80, text="💾 Salvataggio analisi...")
            if save_analysis(analysis):
                st.session_state['current_analysis'] = analysis
                st.session_state['analysis_source'] = 'new'
                progress.progress(100, text="✅ Analisi completata!")
                st.rerun()
            else:
                st.error("❌ Errore nel salvataggio dell'analisi")
        else:
            progress.progress(100, text="❌ Errore nell'analisi")
            st.error(f"Errore: {analysis.get('error', 'Sconosciuto')}")
    else:
        # Solo test recupero dati
        progress.progress(100, text="✅ Test recupero dati completato!")
        st.success("✅ Test completato! I dati sopra sono stati recuperati dalle API delle banche centrali.")
        st.info("💡 Abilita 'Analisi Claude' nella sidebar per generare l'analisi completa delle coppie forex.")

# Mostra analisi corrente
if 'current_analysis' in st.session_state:
    source = st.session_state.get('analysis_source', 'unknown')
    if source == 'auto':
        st.info("📂 Caricata automaticamente l'ultima analisi salvata")
    elif source == 'loaded':
        st.info("📂 Analisi caricata da archivio")
    elif source == 'new':
        st.success("✅ Nuova analisi completata e salvata!")
    
    display_matrix(st.session_state['current_analysis'])

else:
    # Stato iniziale senza analisi
    if not API_KEY_LOADED:
        st.error("""
        ### ⚠️ File di configurazione mancante!
        
        Crea un file `config.py` nella stessa cartella con:
        ```python
        ANTHROPIC_API_KEY = "la-tua-api-key"
        ```
        """)
    else:
        st.markdown("""
        ### 👋 Benvenuto!
        
        Nessuna analisi salvata trovata.
        
        **Come funziona:**
        1. Clicca **"🔄 Nuova Analisi"** nella sidebar
        2. I dati macro vengono scaricati dalle **API ufficiali (FRED/OECD)**
        3. Claude analizza tutte le 19 coppie forex
        4. L'analisi viene **salvata automaticamente** con la data odierna
        
        **Fonti Dati Ufficiali:**
        - 🇺🇸 Federal Reserve (USD)
        - 🇪🇺 BCE/Eurostat (EUR)
        - 🇬🇧 Bank of England/ONS (GBP)
        - 🇯🇵 Bank of Japan (JPY)
        - 🇨🇭 SNB/BFS (CHF)
        - 🇦🇺 RBA/ABS (AUD)
        - 🇨🇦 Bank of Canada (CAD)
        - 📊 OECD (Business Confidence Index)
        
        ---
        
        **Coppie analizzate:**
        """)
        
        cols = st.columns(4)
        for i, pair in enumerate(FOREX_PAIRS):
            with cols[i % 4]:
                st.markdown(f"• {pair}")


# --- FOOTER ---
st.markdown("---")
st.markdown("""
<div style="text-align: center; color: #6b7280; font-size: 0.8rem;">
    📊 Forex Macro Analyst | Powered by Claude AI | Dati: FRED/OECD API<br>
    ⚠️ Analisi qualitativa - Non costituisce consiglio di investimento
</div>
""", unsafe_allow_html=True)
