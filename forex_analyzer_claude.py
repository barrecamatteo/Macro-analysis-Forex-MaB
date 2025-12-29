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
- I DATI NUMERICI ti vengono forniti da fonti ufficiali (global-rates.com/ABS/API Ninjas)
- Le NOTIZIE e OUTLOOK ti vengono fornite dalle ricerche web
- USA ENTRAMBI per l'analisi! I numeri da soli non bastano!

### 4. ⭐⭐⭐ ASPETTATIVE SUI TASSI - REGOLA FONDAMENTALE ⭐⭐⭐
Questa è la sezione PIÙ IMPORTANTE dell'analisi forex!

**DEVI OBBLIGATORIAMENTE per OGNI banca centrale (Fed, ECB, BoE, BoJ, SNB, RBA, BoC):**

1. **PROSSIMO MEETING**: Trova e riporta la DATA del prossimo meeting (es: "Fed: FOMC 28-29 Gennaio 2026")

2. **PROBABILITÀ DEL MERCATO**: Estrai le probabilità concrete dalle fonti:
   - Esempio: "Fed: 87% hold, 13% cut da 25bp (fonte: Polymarket/CME FedWatch)"
   - Esempio: "RBA: 30% probabilità hike a Febbraio (fonte: ASX/Reuters)"
   - Se non trovi percentuali esatte, riporta il sentiment: "mercato prezza hold" o "analisti divisi"

3. **STORICO RECENTE**: Quanti tagli/rialzi negli ultimi 6-12 mesi?
   - Esempio: "BoC: 5 tagli nel 2024-2025, da 5.00% a 2.25%"
   - Esempio: "BoJ: 2 rialzi nel 2024-2025, da 0% a 0.50%"

4. **PROIEZIONE A 6-12 MESI**: Quanti tagli/rialzi previsti?
   - Esempio: "Fed: mercato prezza 2 tagli nel 2026 (Goldman Sachs)"
   - Esempio: "ECB: previsto hold per tutto il 2026 (ING, Vanguard)"

5. **STANCE DELLA BC**: Hawkish/Neutrale/Dovish con motivazione
   - Esempio: "Fed: Hawkish - Powell ha indicato cautela sui tagli futuri"
   - Esempio: "RBA: Potenzialmente Hawkish - inflazione sopra target al 3.8%"

**FORMATO OUTPUT RICHIESTO per rates_future:**
Nel campo "comment" di rates_future, USA QUESTO FORMATO STRUTTURATO:
"[PROSSIMO MEETING: data] | [MERCATO: X% hold/cut/hike] | [STORICO: N tagli/rialzi in X mesi] | [OUTLOOK: N tagli/rialzi previsti in Y] | [STANCE: Hawkish/Neutrale/Dovish] | [FONTE: nome fonte]"

**ESEMPIO CONCRETO:**
rates_future.comment = "Fed: FOMC 29 Gen 2026 | Mercato: 87% hold, 13% cut | Storico: 3 tagli nel 2024-2025 (da 5.50% a 3.50%) | Outlook: 2 tagli previsti nel 2026 | Stance: Hawkish-Neutrale | Fonte: CME FedWatch, Goldman Sachs"

**NON DEVI MAI:**
- Inventare aspettative sui tassi senza fonte
- Dire "BoE più cauta" o "BoC aggressivo" senza dati a supporto
- Usare frasi generiche come "il mercato si aspetta tagli" senza specificare QUANTI, QUANDO e FONTE
- Omettere la data del prossimo meeting se disponibile nelle fonti

**ESEMPIO DI ANALISI CORRETTA:**
"Aspettative Tassi GBP vs CAD: 
GBP - BoE MPC 6 Feb 2026 | 60% hold, 40% cut | Storico: 2 tagli nel 2024 | Outlook: 2-3 tagli nel 2026 | Stance: Neutrale-Dovish | Reuters
CAD - BoC 28 Gen 2026 | 80% hold | Storico: 5 tagli nel 2024-2025 (5.00%→2.25%) | Outlook: hold tutto 2026 | Stance: Neutrale | TD Bank
→ Score GBP +2: BoE ha ancora spazio per tagliare, BoC ha già tagliato molto e ora in pausa"

**ESEMPIO DI ANALISI SBAGLIATA:**
"Aspettative Tassi GBP +2: BoE meno aggressiva nei tagli vs BoC" ← TROPPO GENERICO! Mancano date, probabilità, numeri e fonti!

### 5. PUNTEGGI PER COPPIA
- I punteggi devono essere calcolati PER OGNI COPPIA SPECIFICA
- Lo stesso USD può avere punteggi DIVERSI in USD/JPY vs USD/EUR

## INDICATORI DA CONSIDERARE:
- **Interest Rate**: Tasso attuale (meno importante del trend!)
- **Rate Expectations**: CRUCIALE - tagli o rialzi previsti? CITA LE FONTI!
- **Inflation Rate**: ⚠️ ATTENZIONE ALLA LOGICA!
  - Inflazione ALTA (>2.5%) → BC non può tagliare tassi → POSITIVO per valuta
  - Inflazione BASSA (<2%) → BC può tagliare tassi → NEGATIVO per valuta
  - Il target è ~2%, quindi inflazione sopra target = hawkish = valuta forte
- **GDP Growth**: Momentum economico
- **Unemployment**: Salute del mercato del lavoro

## COME VALUTARE LE ASPETTATIVE TASSI:
- Banca centrale che TAGLIA → score NEGATIVO per quella valuta
- Banca centrale che ALZA → score POSITIVO per quella valuta
- Banca centrale che PAUSA ma pronta a tagliare → leggermente negativo
- Banca centrale che PAUSA ma pronta ad alzare → leggermente positivo
- ⚠️ SEMPRE con riferimento alle fonti trovate nella ricerca!

## COME VALUTARE L'INFLAZIONE (IMPORTANTE!):
- Inflazione ALTA (es. 3-4%) → La BC deve mantenere tassi alti o alzarli → POSITIVO per valuta
- Inflazione sotto target (es. 0-1.5%) → La BC può tagliare tassi → NEGATIVO per valuta
- Esempio: AUD inflazione 3.3% vs USD inflazione 0.2%
  - AUD: inflazione alta = RBA non può tagliare = POSITIVO per AUD
  - USD: inflazione bassa = Fed può tagliare = NEGATIVO per USD
  - Quindi su INFLAZIONE: AUD score POSITIVO, USD score NEGATIVO

## PARAMETRI DA VALUTARE (per ogni coppia A vs B):

1. **TASSI ATTUALI** (scala -1 a +1) - Differenziale tassi attuale
2. **ASPETTATIVE TASSI FUTURI** (scala -2 a +2) - ⭐⭐ IL PIÙ IMPORTANTE! Peso doppio! Chi taglia vs chi alza? CITA FONTI!
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
            "unemployment": "valore"
        },
        ... (per tutte le 7 valute)
    },
    "rate_outlook": {
        "USD": {
            "current_rate": "X.XX%",
            "next_meeting": "YYYY-MM-DD",
            "market_probability": "X% hold | Y% cut | Z% hike",
            "recent_moves": "N tagli/rialzi negli ultimi X mesi (da X% a Y%)",
            "outlook_12m": "N tagli/rialzi previsti",
            "stance": "Hawkish|Neutrale|Dovish",
            "source": "nome fonte principale"
        },
        ... (per tutte le 7 valute: USD, EUR, GBP, JPY, CHF, AUD, CAD)
    },
    "pairs_analysis": [
        {
            "pair": "USD/JPY",
            "currency_a": "USD",
            "currency_b": "JPY",
            "scores_a": {
                "rates_now": {"score": -1|0|+1, "comment": "confronto tassi attuali"},
                "rates_future": {"score": -2|-1|0|+1|+2, "comment": "FORMATO: [MEETING: data] | [MERCATO: prob%] | [STORICO: N moves] | [OUTLOOK: previsione] | [STANCE] | [FONTE]"},
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
REQUIRED_INDICATORS = ["interest_rate", "inflation_rate", "gdp_growth", "unemployment"]

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
    Recupera dati macro da fonti gratuite:
    - Tassi interesse: global-rates.com (scraping)
    - Inflazione: global-rates.com + ABS Australia (scraping)
    - PIL: API Ninjas (gratuito)
    - Disoccupazione: API Ninjas (gratuito)
    """
    
    # Se API Ninjas non configurata, usa dati di fallback per PIL/disoccupazione
    # ma prova comunque lo scraping per tassi e inflazione
    api_key = API_NINJAS_KEY if API_NINJAS_ENABLED else ""
    
    try:
        fetcher = MacroDataFetcher(api_key)
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
            }
        
        # Se API Ninjas non disponibile, avvisa ma continua con i dati di scraping
        if not API_NINJAS_ENABLED:
            st.warning("⚠️ API Ninjas non configurata - PIL e disoccupazione potrebbero essere N/A")
        
        return result
        
    except Exception as e:
        st.error(f"Errore nel recupero dati: {e}")
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


def search_qualitative_data() -> str:
    """Cerca notizie qualitative, outlook e ASPETTATIVE TASSI per ogni valuta."""
    all_results = []
    
    today = datetime.now()
    current_year = today.year
    next_year = current_year + 1
    
    all_results.append(f"[DATE] Data odierna: {today.strftime('%d/%m/%Y')}")
    
    # =========================================================================
    # SEZIONE 1: ASPETTATIVE TASSI - LA PIÙ IMPORTANTE!
    # Ricerche molto specifiche su quanti tagli/rialzi sono previsti
    # =========================================================================
    all_results.append(f"\n{'='*60}")
    all_results.append(f"[RATE EXPECTATIONS - SEZIONE CRUCIALE PER L'ANALISI]")
    all_results.append(f"{'='*60}")
    
    # Query per cercare informazioni su MEETING e PROBABILITÀ per ogni BC
    # Struttura: ogni query è progettata per trovare info sul prossimo meeting,
    # probabilità di cut/hike/hold, e previsioni degli analisti
    
    rate_expectations_queries = {
        "USD": [
            f"Fed FOMC next meeting {current_year} rate decision probability",
            f"Federal Reserve rate probability cut hold hike percent {current_year}",
            f"CME FedWatch tool Fed rate expectations {current_year}",
            f"Fed interest rate forecast {current_year} {next_year} how many cuts analysts",
            "FOMC meeting schedule rate decision outlook Reuters Bloomberg",
        ],
        "EUR": [
            f"ECB next meeting {current_year} rate decision probability",
            f"ECB interest rate cut hold hike probability percent {current_year}",
            f"ECB rate forecast {current_year} {next_year} how many cuts Lagarde",
            "ECB governing council meeting schedule rate outlook Reuters",
            f"Eurozone deposit rate expectations analysts {current_year}",
        ],
        "GBP": [
            f"Bank of England MPC next meeting {current_year} rate decision",
            f"BoE rate cut probability percent {current_year}",
            f"UK interest rate forecast {current_year} {next_year} how many cuts",
            "BoE MPC meeting schedule rate outlook Reuters Bloomberg",
            f"Bank of England rate expectations analysts {current_year}",
        ],
        "JPY": [
            f"Bank of Japan BOJ next meeting {current_year} rate decision",
            f"BoJ rate hike probability percent {current_year}",
            f"Japan interest rate forecast {current_year} {next_year} Ueda",
            "BoJ policy board meeting schedule rate outlook Reuters",
            f"Bank of Japan rate expectations analysts {current_year}",
        ],
        "CHF": [
            f"SNB Swiss National Bank next meeting {current_year} rate decision",
            f"SNB rate cut probability percent {current_year}",
            f"Switzerland interest rate forecast {current_year} {next_year}",
            "SNB quarterly assessment rate outlook",
            f"Swiss National Bank rate expectations analysts {current_year}",
        ],
        "AUD": [
            f"RBA Reserve Bank Australia next meeting {current_year} rate decision",
            f"RBA rate cut hike probability percent {current_year}",
            f"Australia interest rate forecast {current_year} {next_year} Bullock",
            "RBA board meeting schedule rate outlook Reuters",
            f"ASX RBA rate tracker expectations {current_year}",
        ],
        "CAD": [
            f"Bank of Canada BoC next meeting {current_year} rate decision",
            f"BoC rate cut probability percent {current_year}",
            f"Canada interest rate forecast {current_year} {next_year} Macklem",
            "BoC announcement schedule rate outlook Reuters Bloomberg",
            f"Bank of Canada rate expectations analysts {current_year}",
        ],
    }
    
    for currency, queries in rate_expectations_queries.items():
        all_results.append(f"\n[{currency} - RATE EXPECTATIONS ⭐]")
        for query in queries:
            try:
                results = DDGS().text(query, max_results=3)
                for r in results:
                    title = r.get('title', '')
                    snippet = r.get('body', '')
                    all_results.append(f"[{currency}-RATES] {title}: {snippet[:500]}")
            except:
                pass
    
    # =========================================================================
    # SEZIONE 1B: CALENDARI MEETING BANCHE CENTRALI
    # Per trovare le date esatte dei prossimi meeting
    # =========================================================================
    all_results.append(f"\n{'='*60}")
    all_results.append(f"[CENTRAL BANK MEETING SCHEDULES]")
    all_results.append(f"{'='*60}")
    
    meeting_calendar_queries = [
        f"FOMC meeting schedule dates {current_year} {next_year}",
        f"ECB governing council meeting dates {current_year} {next_year}",
        f"Bank of England MPC meeting dates {current_year} {next_year}",
        f"Bank of Japan BOJ policy meeting dates {current_year} {next_year}",
        f"RBA Reserve Bank Australia board meeting dates {current_year} {next_year}",
        f"Bank of Canada BoC announcement dates {current_year} {next_year}",
        f"SNB Swiss National Bank quarterly assessment dates {current_year} {next_year}",
        f"central banks meeting calendar {current_year} {next_year}",
    ]
    
    for query in meeting_calendar_queries:
        try:
            results = DDGS().text(query, max_results=2)
            for r in results:
                all_results.append(f"[CALENDAR] {r['title']}: {r['body'][:400]}")
        except:
            pass
    
    # =========================================================================
    # SEZIONE 2: CONFRONTO DIRETTO POLITICHE MONETARIE
    # =========================================================================
    all_results.append(f"\n{'='*60}")
    all_results.append(f"[MONETARY POLICY COMPARISON]")
    all_results.append(f"{'='*60}")
    
    comparison_queries = [
        f"central banks rate cuts {current_year} comparison Fed ECB BoE",
        f"which central bank cutting rates fastest {current_year}",
        f"hawkish dovish central banks {current_year} ranking",
        f"monetary policy divergence {current_year} forex",
        f"Fed vs ECB vs BoE rate policy {current_year}",
        f"BoJ rate hike vs Fed rate cut {current_year}",
    ]
    
    for query in comparison_queries:
        try:
            results = DDGS().text(query, max_results=3)
            for r in results:
                all_results.append(f"[COMPARE] {r['title']}: {r['body'][:450]}")
        except:
            pass
    
    # =========================================================================
    # SEZIONE 3: OUTLOOK ECONOMICO PER VALUTA
    # =========================================================================
    all_results.append(f"\n{'='*60}")
    all_results.append(f"[ECONOMIC OUTLOOK BY CURRENCY]")
    all_results.append(f"{'='*60}")
    
    economic_outlook_queries = {
        "USD": [f"US economy outlook {current_year} {next_year} growth inflation"],
        "EUR": [f"Eurozone economy outlook {current_year} Germany recession"],
        "GBP": [f"UK economy outlook {current_year} inflation growth"],
        "JPY": [f"Japan economy outlook {current_year} inflation wages"],
        "CHF": [f"Switzerland economy outlook {current_year}"],
        "AUD": [f"Australia economy outlook {current_year} China commodities"],
        "CAD": [f"Canada economy outlook {current_year} oil trade"],
    }
    
    for currency, queries in economic_outlook_queries.items():
        for query in queries:
            try:
                results = DDGS().text(query, max_results=2)
                for r in results:
                    all_results.append(f"[{currency}-ECON] {r['title']}: {r['body'][:400]}")
            except:
                pass
    
    # =========================================================================
    # SEZIONE 4: GEOPOLITICA E RISK SENTIMENT
    # =========================================================================
    all_results.append(f"\n{'='*60}")
    all_results.append(f"[GEOPOLITICS & RISK SENTIMENT]")
    all_results.append(f"{'='*60}")
    
    geopolitical_queries = [
        f"geopolitical risk {current_year} market forex impact",
        f"US China trade tariffs {current_year}",
        f"global recession risk {current_year} probability",
        f"risk on risk off market sentiment {current_year}",
    ]
    
    for query in geopolitical_queries:
        try:
            results = DDGS().text(query, max_results=2)
            for r in results:
                all_results.append(f"[GEO] {r['title']}: {r['body'][:350]}")
        except:
            pass
    
    return "\n".join(all_results)


def search_all_currencies_data() -> tuple[dict, str]:
    """Cerca dati macro per TUTTE le valute - scraping + API Ninjas + ricerche qualitative."""
    
    # 1. FASE 1: Scarica dati numerici da scraping + API Ninjas
    st.info("📊 FASE 1: Scaricamento dati da global-rates.com + API Ninjas...")
    te_data = fetch_all_currencies_data()
    
    # Verifica completezza dati
    missing_data = []
    for curr, data in te_data.items():
        for key, value in data.items():
            if value == 'N/A' or value is None:
                missing_data.append(f"{curr}-{key}")
    
    if missing_data:
        st.warning(f"⚠️ Alcuni dati potrebbero essere mancanti: {', '.join(missing_data[:5])}")
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

## 📊 DATI NUMERICI DA FONTI UFFICIALI (global-rates.com/ABS/API Ninjas):
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
    
    # Dati macro per valuta (da global-rates.com + API Ninjas)
    with st.expander("📈 Dati Macro per Valuta (fonte: global-rates.com + API Ninjas)", expanded=True):
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
                })
            
            df_indicators = pd.DataFrame(indicators_table)
            st.dataframe(df_indicators, use_container_width=True, hide_index=True)
        else:
            st.info("Dati numerici non disponibili per questa analisi")
    
    # ====== NUOVA SEZIONE: PROIEZIONI TASSI DI INTERESSE ======
    rate_outlook = analysis.get("rate_outlook", {})
    if rate_outlook:
        with st.expander("🏦 Proiezioni Tassi Banche Centrali", expanded=True):
            st.caption("⭐ Sezione cruciale per l'analisi forex - Dati estratti dalle ricerche web")
            
            # Crea tabella rate outlook
            rate_table = []
            for curr, outlook in rate_outlook.items():
                if isinstance(outlook, dict):
                    rate_table.append({
                        "BC": curr,
                        "Tasso": outlook.get('current_rate', 'N/A'),
                        "Prossimo Meeting": outlook.get('next_meeting', 'N/A'),
                        "Probabilità Mercato": outlook.get('market_probability', 'N/A'),
                        "Mosse Recenti": outlook.get('recent_moves', 'N/A'),
                        "Outlook 12M": outlook.get('outlook_12m', 'N/A'),
                        "Stance": outlook.get('stance', 'N/A'),
                        "Fonte": outlook.get('source', 'N/A'),
                    })
            
            if rate_table:
                df_rates = pd.DataFrame(rate_table)
                st.dataframe(df_rates, use_container_width=True, hide_index=True)
            else:
                st.info("Formato rate_outlook non valido")
    
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
    
    if API_NINJAS_ENABLED:
        st.success("📊 API Ninjas attiva")
    else:
        st.warning("📊 API Ninjas Key mancante")
    
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
    st.markdown(f"**Dati:** global-rates.com + API Ninjas")
    
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
    
    progress.progress(5, text="📊 FASE 1: Scaricamento dati da global-rates.com + API Ninjas...")
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
        2. I dati macro vengono scaricati da **global-rates.com + API Ninjas**
        3. Claude analizza tutte le 19 coppie forex
        4. L'analisi viene **salvata automaticamente** con la data odierna
        
        **Fonti Dati:**
        - 🌐 global-rates.com (Tassi interesse + Inflazione)
        - 🇦🇺 ABS - Australian Bureau of Statistics (Inflazione AUD)
        - 📊 API Ninjas (PIL + Disoccupazione)
        - 📰 DuckDuckGo Search (Notizie + Outlook)
        
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
    📊 Forex Macro Analyst | Powered by Claude AI | Dati: global-rates.com + API Ninjas<br>
    ⚠️ Analisi qualitativa - Non costituisce consiglio di investimento
</div>
""", unsafe_allow_html=True)
