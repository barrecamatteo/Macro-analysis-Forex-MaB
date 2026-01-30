# ============================================================================
# MODULO REGIMI ECONOMICI
# Analisi basata su matrice 4 quadranti (Goldilocks/Reflazione/Stagflazione/Deflazione)
# ============================================================================

import requests
from datetime import datetime, timedelta
from typing import Optional
import json
import re

# ============================================================================
# CONFIGURAZIONE ID INVESTING.COM
# ============================================================================

CPI_CONFIG = {
    "USD": {
        "headline": {"id": 733, "name": "cpi", "label": "CPI YoY"},
        "core": {"id": 736, "name": "core-cpi", "label": "Core CPI YoY"}
    },
    "EUR": {
        "headline": {"id": 68, "name": "cpi", "label": "CPI YoY"},
        "core": {"id": 317, "name": "core-cpi", "label": "Core CPI YoY"}
    },
    "GBP": {
        "headline": {"id": 67, "name": "cpi", "label": "CPI YoY"},
        "core": {"id": 55, "name": "core-cpi", "label": "Core CPI YoY"}
    },
    "JPY": {
        "headline": {"id": 992, "name": "national-cpi", "label": "National CPI YoY"},
        "core": {"id": 344, "name": "national-core-cpi", "label": "National Core CPI YoY"}
    },
    "CHF": {
        "headline": {"id": 956, "name": "swiss-cpi", "label": "CPI YoY"},
        "core": None  # Svizzera non pubblica CPI Core separato
    },
    "AUD": {
        "headline": {"id": 1011, "name": "cpi", "label": "CPI YoY"},
        "core": {"id": 1017, "name": "trimmed-mean-cpi", "label": "Trimmed Mean CPI YoY"}
    },
    "CAD": {
        "headline": {"id": 741, "name": "cpi", "label": "CPI YoY"},
        "core": {"id": 1020, "name": "core-cpi", "label": "Core CPI YoY"}
    }
}

# Pesi per calcolo PMI Composite (basati sulla struttura economica di ogni paese)
PMI_WEIGHTS = {
    "USD": {"manufacturing": 0.30, "services": 0.70},  # Economia servizi-dominante
    "EUR": {"manufacturing": 0.50, "services": 0.50},  # Economia mista
    "GBP": {"manufacturing": 0.20, "services": 0.80},  # Servizi finanziari dominanti
    "JPY": {"manufacturing": 0.60, "services": 0.40},  # Export manifatturiero
    "CHF": {"manufacturing": 1.00, "services": 0.00},  # Solo PMI procure.ch disponibile
    "AUD": {"manufacturing": 0.50, "services": 0.50},  # Mining + servizi
    "CAD": {"manufacturing": 1.00, "services": 0.00}   # Solo Ivey PMI disponibile
}

# Definizione regimi economici
REGIME_DEFINITIONS = {
    "espansione": {
        "name": "Espansione",
        "emoji": "🟢",
        "color": "#10B981",
        "description": "Crescita solida, inflazione in calo",
        "sentiment": "Risk-On - economia sana, attrattiva per investimenti",
        "condition": "PMI ↑ + Inflazione ↓",
        "forex_score": 1
    },
    "reflazione": {
        "name": "Reflazione",
        "emoji": "🟡",
        "color": "#F59E0B",
        "description": "Crescita forte, inflazione in aumento",
        "sentiment": "BC alzerà tassi - valuta attrattiva per carry trade",
        "condition": "PMI ↑ + Inflazione ↑",
        "forex_score": 2
    },
    "stagflazione": {
        "name": "Stagflazione",
        "emoji": "🔴",
        "color": "#EF4444",
        "description": "Crescita debole, inflazione alta",
        "sentiment": "BC paralizzata - investitori fuggono",
        "condition": "PMI ↓ + Inflazione ↑",
        "forex_score": -2
    },
    "deflazione": {
        "name": "Deflazione",
        "emoji": "🔵",
        "color": "#6366F1",
        "description": "Crescita debole, inflazione in calo",
        "sentiment": "BC taglierà tassi - valuta meno attrattiva",
        "condition": "PMI ↓ + Inflazione ↓",
        "forex_score": -1
    }
}


# ============================================================================
# FUNZIONI FETCH DATI DA INVESTING.COM
# ============================================================================

def fetch_investing_event_data(event_id: int, max_results: int = 6) -> list:
    """
    Recupera dati storici da Investing.com per un evento economico.
    
    Args:
        event_id: ID dell'evento su Investing.com
        max_results: numero massimo di risultati da recuperare
    
    Returns:
        Lista di dict con {date, actual, forecast, previous}
    """
    url = f"https://sbcharts.investing.com/events_charts/eu/{event_id}.json"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json',
        'Referer': 'https://www.investing.com/'
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code != 200:
            return []
        
        data = response.json()
        results = []
        
        # I dati sono in formato {"attr": [...], "data": [[timestamp, value], ...]}
        if "data" in data and "attr" in data:
            data_points = data["data"]
            attr = data["attr"]
            
            # Ordina per timestamp decrescente (più recenti prima)
            data_points_sorted = sorted(data_points, key=lambda x: x[0], reverse=True)
            
            for point in data_points_sorted[:max_results]:
                timestamp = point[0] / 1000 if point[0] > 9999999999 else point[0]  # ms vs s
                value = point[1] if len(point) > 1 else None
                
                if value is not None:
                    dt = datetime.fromtimestamp(timestamp)
                    results.append({
                        "date": dt.strftime("%Y-%m-%d"),
                        "year": dt.year,
                        "month": dt.month,
                        "actual": float(value)
                    })
        
        return results
        
    except Exception as e:
        print(f"Errore fetch Investing.com event {event_id}: {e}")
        return []


def fetch_cpi_data(currency: str) -> dict:
    """
    Recupera dati CPI (Headline e Core) per una valuta.
    
    Returns:
        {
            "headline": {"current": float, "history": list},
            "core": {"current": float, "history": list} o None se non disponibile
        }
    """
    config = CPI_CONFIG.get(currency)
    if not config:
        return {"headline": None, "core": None}
    
    result = {"headline": None, "core": None}
    
    # Fetch CPI Headline
    if config.get("headline"):
        headline_data = fetch_investing_event_data(config["headline"]["id"], max_results=6)
        if headline_data:
            result["headline"] = {
                "current": headline_data[0]["actual"] if headline_data else None,
                "history": headline_data
            }
    
    # Fetch CPI Core
    if config.get("core"):
        core_data = fetch_investing_event_data(config["core"]["id"], max_results=6)
        if core_data:
            result["core"] = {
                "current": core_data[0]["actual"] if core_data else None,
                "history": core_data
            }
    
    return result


# ============================================================================
# FUNZIONI CALCOLO REGIMI
# ============================================================================

def calculate_pmi_composite(pmi_manufacturing: float, pmi_services: float, currency: str) -> float:
    """
    Calcola PMI Composite basato sui pesi specifici per valuta.
    """
    weights = PMI_WEIGHTS.get(currency, {"manufacturing": 0.5, "services": 0.5})
    
    if pmi_services is None or weights["services"] == 0:
        return pmi_manufacturing
    
    return (pmi_manufacturing * weights["manufacturing"]) + (pmi_services * weights["services"])


def calculate_inflation_index(cpi_headline: float, cpi_core: Optional[float]) -> float:
    """
    Calcola indice inflazione ponderato.
    Core CPI x 0.7 + Headline CPI x 0.3
    Se Core non disponibile, usa solo Headline.
    """
    if cpi_core is None:
        return cpi_headline
    
    return (cpi_core * 0.7) + (cpi_headline * 0.3)


def calculate_delta(current: float, history: list, months: int = 3) -> float:
    """
    Calcola delta tra valore attuale e media ultimi N mesi.
    
    Args:
        current: valore attuale
        history: lista di valori storici (dal più recente)
        months: numero di mesi per la media
    
    Returns:
        Delta (current - media)
    """
    if not history or len(history) < months:
        return 0.0
    
    # Prendi gli ultimi 'months' valori (escluso il corrente se già presente)
    historical_values = [h["actual"] for h in history[1:months+1] if h.get("actual") is not None]
    
    if not historical_values:
        return 0.0
    
    avg = sum(historical_values) / len(historical_values)
    return current - avg


def identify_regime(delta_pmi: float, delta_inflation: float) -> str:
    """
    Identifica il regime economico basato sui delta.
    
    Returns:
        Chiave del regime: "espansione", "reflazione", "stagflazione", "deflazione"
    """
    # Soglia per considerare un cambiamento significativo
    threshold = 0.1
    
    pmi_up = delta_pmi > threshold
    pmi_down = delta_pmi < -threshold
    inflation_up = delta_inflation > threshold
    inflation_down = delta_inflation < -threshold
    
    # Matrice dei regimi
    if pmi_up and inflation_down:
        return "espansione"
    elif pmi_up and inflation_up:
        return "reflazione"
    elif pmi_down and inflation_up:
        return "stagflazione"
    elif pmi_down and inflation_down:
        return "deflazione"
    else:
        # Caso neutro/transizione - decide in base al peso maggiore
        if abs(delta_pmi) > abs(delta_inflation):
            return "reflazione" if pmi_up else "deflazione"
        else:
            return "stagflazione" if inflation_up else "espansione"


def calculate_momentum(delta: float) -> str:
    """
    Calcola indicatore momentum basato sul delta.
    
    Returns:
        Freccia indicante direzione e intensità
    """
    if delta > 1.0:
        return "⬆️⬆️"  # Forte aumento
    elif delta > 0.3:
        return "⬆️"     # Aumento
    elif delta > 0.1:
        return "↗️"     # Leggero aumento
    elif delta < -1.0:
        return "⬇️⬇️"  # Forte calo
    elif delta < -0.3:
        return "⬇️"     # Calo
    elif delta < -0.1:
        return "↘️"     # Leggero calo
    else:
        return "➡️"     # Stabile


def detect_cpi_divergence(cpi_headline: float, cpi_core: Optional[float]) -> Optional[dict]:
    """
    Rileva divergenza tra CPI Headline e Core.
    
    Returns:
        None se non c'è divergenza significativa, altrimenti dict con dettagli
    """
    if cpi_core is None:
        return None
    
    diff = cpi_headline - cpi_core
    
    # Divergenza significativa se > 0.5 punti percentuali
    if abs(diff) > 0.5:
        if diff > 0:
            return {
                "type": "headline_higher",
                "emoji": "⚠️",
                "message": f"Headline (+{diff:.1f}%) > Core: pressioni temporanee (energia/food)",
                "implication": "Inflazione potrebbe rientrare"
            }
        else:
            return {
                "type": "core_higher",
                "emoji": "🚨",
                "message": f"Core (+{abs(diff):.1f}%) > Headline: inflazione strutturale",
                "implication": "Banca centrale potrebbe restare hawkish"
            }
    
    return None


def get_regime_forex_score(regime: str) -> int:
    """
    Restituisce il punteggio forex per un regime economico.
    
    Args:
        regime: chiave del regime ("espansione", "surriscaldamento", "stagflazione", "recessione")
    
    Returns:
        Punteggio forex: +1 per espansione/surriscaldamento, -1 per stagflazione/recessione
    """
    regime_info = REGIME_DEFINITIONS.get(regime, {})
    return regime_info.get("forex_score", 0)


# ============================================================================
# FUNZIONI INTERAZIONE SUPABASE
# ============================================================================

def save_regime_to_supabase(supabase_request_func, currency: str, data: dict) -> bool:
    """
    Salva i dati del regime su Supabase.
    
    Args:
        supabase_request_func: funzione per fare richieste a Supabase
        currency: codice valuta (es. "USD")
        data: dict con tutti i dati calcolati
    
    Returns:
        True se salvato con successo
    """
    try:
        now = datetime.now()
        
        record = {
            "currency": currency,
            "year": now.year,
            "month": now.month,
            "pmi_manufacturing": data.get("pmi_manufacturing"),
            "pmi_services": data.get("pmi_services"),
            "pmi_composite": data.get("pmi_composite"),
            "pmi_avg_3m": data.get("pmi_avg_3m"),
            "cpi_headline": data.get("cpi_headline"),
            "cpi_core": data.get("cpi_core"),
            "inflation_index": data.get("inflation_index"),
            "inflation_avg_3m": data.get("inflation_avg_3m"),
            "delta_pmi": data.get("delta_pmi"),
            "delta_inflation": data.get("delta_inflation"),
            "regime": data.get("regime"),
            "updated_at": now.isoformat()
        }
        
        # Upsert: aggiorna se esiste, altrimenti inserisce
        # Prima prova UPDATE
        endpoint = f"economic_regimes_history?currency=eq.{currency}&year=eq.{now.year}&month=eq.{now.month}"
        existing = supabase_request_func("GET", endpoint)
        
        if existing and len(existing) > 0:
            # Update
            result = supabase_request_func("PATCH", endpoint, record)
        else:
            # Insert
            result = supabase_request_func("POST", "economic_regimes_history", record)
        
        return result is not None
        
    except Exception as e:
        print(f"Errore salvataggio regime Supabase: {e}")
        return False


def get_regime_history(supabase_request_func, currency: str, months: int = 6) -> list:
    """
    Recupera lo storico dei regimi da Supabase.
    
    Returns:
        Lista di dict con storico regimi ordinato per data decrescente
    """
    try:
        endpoint = f"economic_regimes_history?currency=eq.{currency}&order=year.desc,month.desc&limit={months}"
        result = supabase_request_func("GET", endpoint)
        return result if result else []
    except Exception as e:
        print(f"Errore lettura storico regimi: {e}")
        return []


def get_all_current_regimes(supabase_request_func) -> tuple[dict, datetime | None]:
    """
    Recupera il regime più recente per tutte le valute.
    
    Returns:
        tuple(dict con {currency: regime_data}, datetime più recente o None)
    """
    regimes = {}
    latest_timestamp = None
    
    for currency in CPI_CONFIG.keys():
        try:
            # Prendi l'ultimo record per ogni valuta (ordinato per year, month DESC)
            endpoint = f"economic_regimes_history?currency=eq.{currency}&order=year.desc,month.desc&limit=1"
            result = supabase_request_func("GET", endpoint)
            if result and len(result) > 0:
                data = result[0]
                
                # Aggiungi regime_info se mancante
                if data.get("regime") and "regime_info" not in data:
                    data["regime_info"] = REGIME_DEFINITIONS.get(data["regime"])
                
                regimes[currency] = data
                
                # Estrai timestamp più recente
                if "updated_at" in data and data["updated_at"]:
                    try:
                        ts = datetime.fromisoformat(data["updated_at"].replace("Z", "+00:00"))
                        if latest_timestamp is None or ts > latest_timestamp:
                            latest_timestamp = ts
                    except:
                        pass
        except:
            pass
    
    return regimes, latest_timestamp


# ============================================================================
# FUNZIONE PRINCIPALE ANALISI REGIME
# ============================================================================

def fetch_pmi_history(currency: str) -> list:
    """
    Recupera lo storico PMI da Investing.com per una valuta.
    Usa il PMI Manufacturing come riferimento principale.
    
    Returns:
        Lista di dict con storico PMI
    """
    # ID PMI Manufacturing per ogni valuta (evita import circolare)
    PMI_MANUFACTURING_IDS = {
        "USD": 173,  # ISM Manufacturing
        "EUR": 201,  # Eurozone Manufacturing PMI
        "GBP": 204,  # UK Manufacturing PMI
        "JPY": 202,  # Japan Manufacturing PMI
        "CHF": 278,  # procure.ch PMI
        "AUD": 1838, # Australia Manufacturing PMI
        "CAD": 185   # Ivey PMI
    }
    
    event_id = PMI_MANUFACTURING_IDS.get(currency)
    if not event_id:
        return []
    
    return fetch_investing_event_data(event_id, max_results=6)


def analyze_currency_regime(currency: str, pmi_data: dict) -> dict:
    """
    Analizza il regime economico per una valuta.
    
    Args:
        currency: codice valuta
        pmi_data: dict con dati PMI già recuperati {"manufacturing": float, "services": float}
    
    Returns:
        dict con analisi completa del regime
    """
    result = {
        "currency": currency,
        "regime": None,
        "regime_info": None,
        "pmi_manufacturing": None,
        "pmi_services": None,
        "pmi_composite": None,
        "pmi_history": None,
        "cpi_headline": None,
        "cpi_core": None,
        "cpi_history": None,
        "inflation_index": None,
        "delta_pmi": None,
        "delta_inflation": None,
        "pmi_avg_3m": None,
        "inflation_avg_3m": None,
        "momentum_pmi": None,
        "momentum_inflation": None,
        "divergence": None,
        "error": None
    }
    
    try:
        # 1. Dati PMI (già forniti)
        pmi_manuf = pmi_data.get("manufacturing")
        pmi_serv = pmi_data.get("services")
        
        if pmi_manuf is None:
            result["error"] = "PMI Manufacturing non disponibile"
            return result
        
        result["pmi_manufacturing"] = pmi_manuf
        result["pmi_services"] = pmi_serv
        
        # 2. Calcola PMI Composite
        pmi_composite = calculate_pmi_composite(pmi_manuf, pmi_serv, currency)
        result["pmi_composite"] = pmi_composite
        
        # 3. Fetch storico PMI per calcolo delta
        pmi_history = fetch_pmi_history(currency)
        result["pmi_history"] = pmi_history
        
        # 4. Fetch CPI
        cpi_data = fetch_cpi_data(currency)
        
        if not cpi_data.get("headline") or cpi_data["headline"].get("current") is None:
            result["error"] = "CPI Headline non disponibile"
            return result
        
        cpi_headline = cpi_data["headline"]["current"]
        cpi_core = cpi_data["core"]["current"] if cpi_data.get("core") else None
        cpi_history = cpi_data["headline"].get("history", [])
        
        result["cpi_headline"] = cpi_headline
        result["cpi_core"] = cpi_core
        result["cpi_history"] = cpi_history
        
        # 5. Calcola indice inflazione
        inflation_index = calculate_inflation_index(cpi_headline, cpi_core)
        result["inflation_index"] = inflation_index
        
        # 6. Calcola delta PMI (vs media ultimi 3 mesi)
        if pmi_history and len(pmi_history) >= 3:
            # Prendi i 3 valori precedenti (escluso l'attuale se presente)
            historical_pmi = [h["actual"] for h in pmi_history[1:4] if h.get("actual") is not None]
            if historical_pmi:
                pmi_avg_3m = sum(historical_pmi) / len(historical_pmi)
                delta_pmi = pmi_composite - pmi_avg_3m
                result["pmi_avg_3m"] = round(pmi_avg_3m, 1)
            else:
                delta_pmi = 0
        else:
            # Fallback: usa distanza da 50 se non abbiamo storico
            delta_pmi = pmi_composite - 50
            result["pmi_avg_3m"] = 50.0  # Indica che è il fallback
        
        # 7. Calcola delta Inflazione (vs media ultimi 3 mesi)
        if cpi_history and len(cpi_history) >= 3:
            historical_infl = [h["actual"] for h in cpi_history[1:4] if h.get("actual") is not None]
            if historical_infl:
                infl_avg_3m = sum(historical_infl) / len(historical_infl)
                delta_inflation = inflation_index - infl_avg_3m
                result["inflation_avg_3m"] = round(infl_avg_3m, 1)
            else:
                delta_inflation = 0
        else:
            delta_inflation = 0
            result["inflation_avg_3m"] = None
        
        result["delta_pmi"] = round(delta_pmi, 2)
        result["delta_inflation"] = round(delta_inflation, 2)
        
        # 8. Identifica regime
        regime = identify_regime(delta_pmi, delta_inflation)
        result["regime"] = regime
        result["regime_info"] = REGIME_DEFINITIONS.get(regime)
        
        # 9. Calcola momentum
        result["momentum_pmi"] = calculate_momentum(delta_pmi)
        result["momentum_inflation"] = calculate_momentum(delta_inflation)
        
        # 10. Rileva divergenza CPI
        result["divergence"] = detect_cpi_divergence(cpi_headline, cpi_core)
        
    except Exception as e:
        result["error"] = str(e)
    
    return result


def analyze_all_regimes(pmi_data_all: dict) -> dict:
    """
    Analizza i regimi per tutte le valute.
    
    Args:
        pmi_data_all: dict con {currency: {"manufacturing": float, "services": float}}
    
    Returns:
        dict con {currency: regime_analysis}
    """
    results = {}
    
    for currency in CPI_CONFIG.keys():
        pmi_data = pmi_data_all.get(currency, {})
        results[currency] = analyze_currency_regime(currency, pmi_data)
    
    return results
