import streamlit as st
import anthropic
from duckduckgo_search import DDGS
from datetime import datetime
import json
import pandas as pd
import os
from pathlib import Path
import requests

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

# Se non trovata, prova config.py (locale)
if not API_KEY_LOADED:
    try:
        from config import ANTHROPIC_API_KEY
        API_KEY_LOADED = True
        # Prova a caricare anche Supabase da config
        try:
            from config import SUPABASE_URL, SUPABASE_KEY
        except ImportError:
            pass
    except ImportError:
        pass

# Flag per Supabase
SUPABASE_ENABLED = SUPABASE_URL is not None and SUPABASE_KEY is not None

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
- I DATI NUMERICI ti vengono forniti da TradingEconomics
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
- **GDP Growth + PMI**: Momentum economico
- **Current Account/Debt**: Solidità fiscale

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

Esempio attuale (verifica dalle notizie):
- Fed: ha tagliato, potrebbe tagliare ancora → USD potenzialmente debole
- BoJ: ha alzato, potrebbe alzare ancora → JPY potenzialmente forte
- ECB: sta tagliando → EUR potenzialmente debole
- Quindi USD/JPY potrebbe avere bias RIBASSISTA (non rialzista!)

## PARAMETRI DA VALUTARE (per ogni coppia A vs B):

1. **TASSI ATTUALI** (scala -1 a +1) - Differenziale tassi attuale
2. **ASPETTATIVE TASSI FUTURI** (scala -2 a +2) - ⭐⭐ IL PIÙ IMPORTANTE! Peso doppio! Chi taglia vs chi alza?
3. **INFLAZIONE** (scala -1 a +1) - ⚠️ Inflazione ALTA = POSITIVO! Chi ha inflazione sopra il 2%? (BC non può tagliare)
4. **CRESCITA/PIL** (scala -1 a +1) - Chi cresce di più? USA I PMI!
5. **RISK SENTIMENT** (scala -1 a +1) - Safe-haven vs cyclical nel contesto attuale
6. **BILANCIA/FISCALE** (scala -1 a +1) - Current Account + Debt/GDP

## NOTA SUI PESI:
- Solo ASPETTATIVE TASSI ha range -2/+2 (peso doppio!)
- Tutti gli altri parametri hanno range -1/+1
- Questo riflette l'importanza delle politiche monetarie future nel forex
- Range totale possibile per valuta: da -7 a +7

## OUTPUT RICHIESTO (JSON):
{
    "analysis_date": "YYYY-MM-DD",
    "currencies_data": {
        "EUR": {
            "interest_rate": "valore",
            "inflation_rate": "valore",
            "gdp_growth": "valore",
            "unemployment": "valore",
            "manufacturing_pmi": "valore",
            "services_pmi": "valore",
            "current_account_gdp": "valore",
            "debt_to_gdp": "valore"
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
                "growth": {"score": -1|0|+1, "comment": "confronto crescita e PMI"},
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
    "events_calendar": [
        {"date": "YYYY-MM-DD", "event": "descrizione", "currency": "XXX", "importance": "high|medium"},
        ...
    ]
}

## CHECKLIST FINALE:
✅ Tutti i testi in ITALIANO
✅ Aspettative tassi: range -2/+2 (peso doppio!)
✅ Altri parametri: range -1/+1
✅ INFLAZIONE: ricorda che inflazione ALTA = POSITIVO per valuta (BC hawkish)!
✅ Sintesi che spiega il PERCHÉ del bias
✅ Eventi futuri nei prossimi 30 giorni
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


def save_analysis(analysis: dict) -> bool:
    """Salva l'analisi su Supabase (o locale come fallback)"""
    try:
        now = datetime.now()
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

# URL TradingEconomics per ogni valuta
TRADING_ECONOMICS_URLS = {
    "EUR": "https://tradingeconomics.com/euro-area/indicators",
    "USD": "https://tradingeconomics.com/united-states/indicators",
    "GBP": "https://tradingeconomics.com/united-kingdom/indicators",
    "JPY": "https://tradingeconomics.com/japan/indicators",
    "CHF": "https://tradingeconomics.com/switzerland/indicators",
    "AUD": "https://tradingeconomics.com/australia/indicators",
    "CAD": "https://tradingeconomics.com/canada/indicators",
}

# Mappatura nomi indicatori TradingEconomics -> nostri nomi
INDICATORS_MAP = {
    "Interest Rate": "interest_rate",
    "Inflation Rate": "inflation_rate",
    "GDP Growth Rate": "gdp_growth",
    "Unemployment Rate": "unemployment",
    "Manufacturing PMI": "manufacturing_pmi",
    "Services PMI": "services_pmi",
    "Current Account to GDP": "current_account_gdp",
    "Government Debt to GDP": "debt_to_gdp",
}


def fetch_trading_economics_data(currency: str) -> dict:
    """Scarica i dati macro da TradingEconomics per una valuta"""
    import requests
    from bs4 import BeautifulSoup
    
    url = TRADING_ECONOMICS_URLS.get(currency)
    if not url:
        return {}
    
    data = {
        "interest_rate": "N/A",
        "inflation_rate": "N/A",
        "gdp_growth": "N/A",
        "unemployment": "N/A",
        "manufacturing_pmi": "N/A",
        "services_pmi": "N/A",
        "current_account_gdp": "N/A",
        "debt_to_gdp": "N/A",
    }
    
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Cerca la tabella degli indicatori
        # TradingEconomics usa tabelle con class "table"
        tables = soup.find_all('table')
        
        for table in tables:
            rows = table.find_all('tr')
            for row in rows:
                cells = row.find_all(['td', 'th'])
                if len(cells) >= 2:
                    # Prima cella = nome indicatore, seconda = valore
                    indicator_name = cells[0].get_text(strip=True)
                    value = cells[1].get_text(strip=True)
                    
                    # Cerca corrispondenza con i nostri indicatori
                    for te_name, our_name in INDICATORS_MAP.items():
                        if te_name.lower() in indicator_name.lower():
                            # Pulisci il valore
                            clean_value = value.replace('%', '').strip()
                            try:
                                # Prova a convertire in numero per validare
                                float(clean_value)
                                data[our_name] = clean_value
                            except:
                                data[our_name] = value
                            break
        
    except Exception as e:
        st.warning(f"⚠️ Errore fetch {currency}: {e}")
    
    return data


def fetch_all_currencies_data() -> dict:
    """Scarica i dati macro per tutte le valute da TradingEconomics"""
    all_data = {}
    
    for currency in CURRENCIES.keys():
        all_data[currency] = fetch_trading_economics_data(currency)
    
    return all_data


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
    """Cerca dati macro per TUTTE le valute - TradingEconomics + ricerche qualitative"""
    
    # 1. Scarica dati numerici da TradingEconomics
    st.info("📊 Scaricamento dati da TradingEconomics...")
    te_data = fetch_all_currencies_data()
    
    # 2. Ricerche qualitative approfondite
    st.info("🔍 Ricerca notizie, outlook e aspettative mercati...")
    qualitative_data = search_qualitative_data()
    
    return te_data, qualitative_data


def analyze_all_pairs(api_key: str, te_data: dict, search_text: str) -> dict:
    """Analizza TUTTE le coppie forex in una sola chiamata API"""
    
    client = anthropic.Anthropic(api_key=api_key)
    
    pairs_list = ", ".join(FOREX_PAIRS)
    currencies_info = "\n".join([f"- {k}: {v['name']} ({v['central_bank']}) - Tipo: {v['type']}" 
                                  for k, v in CURRENCIES.items()])
    
    # Formatta i dati TradingEconomics
    te_formatted = "\n\n".join([
        f"**{curr}:**\n" + "\n".join([f"  - {k}: {v}" for k, v in data.items()])
        for curr, data in te_data.items()
    ])
    
    today = datetime.now()
    events_limit = (today + pd.DateOffset(days=30)).strftime('%Y-%m-%d')
    
    user_prompt = f"""
Analizza TUTTE queste coppie forex: {pairs_list}

## ⚠️ DATA ODIERNA: {today.strftime('%Y-%m-%d')} ({today.strftime('%A, %d %B %Y')})

**Valute da analizzare:**
{currencies_info}

---

## 📊 DATI NUMERICI DA TRADINGECONOMICS:
{te_formatted}

---

## 📰 NOTIZIE, OUTLOOK E ASPETTATIVE MERCATI:
{search_text}

---

## ⭐ ISTRUZIONI CRITICHE:

1. **USA LE NOTIZIE** per capire chi sta tagliando/alzando i tassi!
   - Se leggi "Fed cuts rates" o "Fed dovish" → USD tendenzialmente debole
   - Se leggi "BoJ raises rates" o "BoJ hawkish" → JPY tendenzialmente forte
   
2. **ASPETTATIVE > TASSI ATTUALI**: il mercato guarda AVANTI, non indietro!

3. **analysis_date** = "{today.strftime('%Y-%m-%d')}"

4. **events_calendar** = solo eventi tra {today.strftime('%Y-%m-%d')} e {events_limit}

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
        
        # Se currencies_data manca o è incompleto, usa i dati TradingEconomics
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
    
    # Dati macro per valuta (dati da TradingEconomics)
    with st.expander("📈 Dati Macro per Valuta (fonte: TradingEconomics)", expanded=False):
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
                    "PMI Manif.": data.get('manufacturing_pmi', 'N/A'),
                    "PMI Serv.": data.get('services_pmi', 'N/A'),
                    "C/A % PIL": data.get('current_account_gdp', 'N/A'),
                    "Debito/PIL": data.get('debt_to_gdp', 'N/A'),
                })
            
            df_indicators = pd.DataFrame(indicators_table)
            st.dataframe(df_indicators, use_container_width=True, hide_index=True)
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
    
    # Calendario eventi
    st.markdown("---")
    st.markdown("### 📅 Prossimi Eventi da Monitorare")
    
    events = analysis.get("events_calendar", [])
    if events:
        for event in events[:10]:
            importance = event.get("importance", "medium")
            icon = "🔴" if importance == "high" else "🟡"
            date_formatted = format_date_ita(event.get('date', 'TBD'))
            st.markdown(f"{icon} **{date_formatted}** - {event.get('event', '')} ({event.get('currency', '')})")
    else:
        st.info("Nessun evento trovato")
    
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
- 🏭 PMI Manifatturiero: **{data_a.get('manufacturing_pmi', 'N/A')}**
- 🏢 PMI Servizi: **{data_a.get('services_pmi', 'N/A')}**
- 💰 Conto Corrente/PIL: **{data_a.get('current_account_gdp', 'N/A')}**
- 📉 Debito/PIL: **{data_a.get('debt_to_gdp', 'N/A')}**
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
- 🏭 PMI Manifatturiero: **{data_b.get('manufacturing_pmi', 'N/A')}**
- 🏢 PMI Servizi: **{data_b.get('services_pmi', 'N/A')}**
- 💰 Conto Corrente/PIL: **{data_b.get('current_account_gdp', 'N/A')}**
- 📉 Debito/PIL: **{data_b.get('debt_to_gdp', 'N/A')}**
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
    
    st.markdown("---")
    
    # Pulsante nuova analisi
    analyze_btn = st.button(
        "🔄 Nuova Analisi",
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
    
    progress.progress(5, text="🔍 Scaricamento dati da TradingEconomics...")
    te_data, search_text = search_all_currencies_data()
    
    progress.progress(50, text="🧠 Claude Sonnet 4 sta analizzando...")
    analysis = analyze_all_pairs(ANTHROPIC_API_KEY, te_data, search_text)
    
    if "error" not in analysis:
        analysis["model_used"] = "Claude Sonnet 4"
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
        2. Claude raccoglie dati macro e analizza tutte le 19 coppie
        3. L'analisi viene **salvata automaticamente** con la data odierna
        4. Alla prossima apertura, l'ultima analisi verrà caricata automaticamente
        
        **Funzionalità:**
        - 📊 **Punteggi specifici per coppia** (non generici per valuta)
        - 💾 **Storico analisi** - richiama analisi passate dalla sidebar
        - 🔄 **Aggiornamento giornaliero** - una nuova analisi al giorno
        
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
    📊 Forex Macro Analyst | Powered by Claude AI<br>
    ⚠️ Analisi qualitativa - Non costituisce consiglio di investimento
</div>
""", unsafe_allow_html=True)
