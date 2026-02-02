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
import calendar
import time

# Import modulo regimi economici
try:
    from economic_regimes import (
        CPI_CONFIG, PMI_WEIGHTS, REGIME_DEFINITIONS,
        analyze_currency_regime, analyze_all_regimes,
        save_regime_to_supabase, get_regime_history, get_all_current_regimes,
        get_regime_forex_score
    )
    REGIMES_MODULE_LOADED = True
except ImportError:
    REGIMES_MODULE_LOADED = False

# Import modulo COT (Commitment of Traders)
try:
    from cot_data import (
        COTDataManager,
        get_cot_analysis,
        get_cot_scores_for_currency,
        format_cot_for_display
    )
    COT_MODULE_LOADED = True
    print("[INFO] Modulo COT caricato correttamente")
except ImportError as e:
    COT_MODULE_LOADED = False
    print(f"[WARNING] Modulo COT non caricato: {e}")

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


# ============================================================================
# CALENDARIO BANCHE CENTRALI 2025 (Date meeting ufficiali)
# ============================================================================

CB_MEETING_DATES_2025 = {
    "USD": [  # Federal Reserve FOMC
        "2025-01-29", "2025-03-19", "2025-05-07", 
        "2025-06-18", "2025-07-30", "2025-09-17",
        "2025-11-05", "2025-12-17"
    ],
    "EUR": [  # BCE / ECB
        "2025-01-30", "2025-03-06", "2025-04-17",
        "2025-06-05", "2025-07-17", "2025-09-11",
        "2025-10-30", "2025-12-18"
    ],
    "GBP": [  # Bank of England
        "2025-02-06", "2025-03-20", "2025-05-08",
        "2025-06-19", "2025-08-07", "2025-09-18",
        "2025-11-06", "2025-12-18"
    ],
    "JPY": [  # Bank of Japan
        "2025-01-24", "2025-03-14", "2025-05-01",
        "2025-06-13", "2025-07-31", "2025-09-19",
        "2025-10-31", "2025-12-19"
    ],
    "CHF": [  # Swiss National Bank (trimestrale)
        "2025-03-20", "2025-06-19", 
        "2025-09-18", "2025-12-11"
    ],
    "AUD": [  # Reserve Bank of Australia
        "2025-02-18", "2025-04-01", "2025-05-20",
        "2025-07-08", "2025-08-12", "2025-09-30",
        "2025-11-04", "2025-12-09"
    ],
    "CAD": [  # Bank of Canada
        "2025-01-29", "2025-03-12", "2025-04-16",
        "2025-06-04", "2025-07-30", "2025-09-17",
        "2025-10-29", "2025-12-10"
    ]
}


# ============================================================================
# FUNZIONI FRESHNESS DATI (Regole Euristiche)
# ============================================================================

def check_data_freshness(data_type: str, last_updated: datetime | None) -> dict:
    """
    Controlla se i dati sono aggiornati o da aggiornare.
    
    Args:
        data_type: "macro", "cb_history", "pmi", "prices", "news"
        last_updated: datetime dell'ultimo aggiornamento (o None se mai aggiornato)
    
    Returns:
        {
            "is_fresh": True/False,
            "status": "🟢" o "🟠",
            "message": "Descrizione stato",
            "reason": "Motivo se da aggiornare"
        }
    """
    now = get_italy_now()
    
    # Se non c'è timestamp, i dati non esistono
    if last_updated is None:
        return {
            "is_fresh": False,
            "status": "🟠",
            "message": "Da aggiornare",
            "reason": "Nessun dato disponibile"
        }
    
    # Rendi last_updated timezone-aware se necessario
    if last_updated.tzinfo is None and ITALY_TZ:
        last_updated = last_updated.replace(tzinfo=ITALY_TZ)
    
    age = now - last_updated
    age_days = age.days
    age_hours = age.total_seconds() / 3600
    day_of_month = now.day
    
    # ===== PREZZI FOREX =====
    if data_type == "prices":
        # Da aggiornare se non aggiornati oggi dopo le 7:00
        today_7am = now.replace(hour=7, minute=0, second=0, microsecond=0)
        if now.hour >= 7 and last_updated < today_7am:
            return {
                "is_fresh": False,
                "status": "🟠",
                "message": f"Da aggiornare (ieri)",
                "reason": "Non aggiornati oggi"
            }
        return {
            "is_fresh": True,
            "status": "🟢",
            "message": f"Aggiornato",
            "reason": ""
        }
    
    # ===== NOTIZIE =====
    if data_type == "news":
        # Da aggiornare se non aggiornate oggi dopo le 7:00
        today_7am = now.replace(hour=7, minute=0, second=0, microsecond=0)
        if now.hour >= 7 and last_updated < today_7am:
            return {
                "is_fresh": False,
                "status": "🟠",
                "message": f"Da aggiornare (ieri)",
                "reason": "Non aggiornate oggi"
            }
        return {
            "is_fresh": True,
            "status": "🟢",
            "message": f"Aggiornato",
            "reason": ""
        }
    
    # ===== STORICO BANCHE CENTRALI =====
    if data_type == "cb_history":
        # Controlla se c'è stato un meeting DOPO last_updated
        meetings_after = []
        for currency, dates in CB_MEETING_DATES_2025.items():
            for date_str in dates:
                meeting_date = datetime.strptime(date_str, "%Y-%m-%d")
                if ITALY_TZ:
                    meeting_date = meeting_date.replace(tzinfo=ITALY_TZ)
                # Meeting è passato E dopo l'ultimo aggiornamento
                if last_updated.replace(tzinfo=None) < meeting_date.replace(tzinfo=None) <= now.replace(tzinfo=None):
                    meetings_after.append(f"{currency} ({date_str})")
        
        if meetings_after:
            return {
                "is_fresh": False,
                "status": "🟠",
                "message": f"Da aggiornare (meeting recenti)",
                "reason": f"Meeting BC: {', '.join(meetings_after[:2])}"
            }
        
        # Trova prossimo meeting
        next_meetings = []
        for currency, dates in CB_MEETING_DATES_2025.items():
            for date_str in dates:
                meeting_date = datetime.strptime(date_str, "%Y-%m-%d")
                if meeting_date.replace(tzinfo=None) > now.replace(tzinfo=None):
                    days_until = (meeting_date.replace(tzinfo=None) - now.replace(tzinfo=None)).days
                    if days_until <= 7:
                        next_meetings.append(f"{currency} tra {days_until}gg")
                    break
        
        msg = f"Aggiornato" if age_days == 0 else f"Aggiornato {age_days}gg fa"
        if next_meetings:
            msg += f" | Prossimi: {', '.join(next_meetings[:2])}"
        
        return {
            "is_fresh": True,
            "status": "🟢",
            "message": msg,
            "reason": ""
        }
    
    # ===== PMI =====
    if data_type == "pmi":
        # Periodi critici: 1-3 (PMI finale) e 22-24 (PMI flash)
        in_pmi_period = (1 <= day_of_month <= 4) or (22 <= day_of_month <= 25)
        
        if in_pmi_period:
            # Siamo in periodo PMI, controlla se aggiornati in questo periodo
            if day_of_month <= 4:
                period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            else:
                period_start = now.replace(day=22, hour=0, minute=0, second=0, microsecond=0)
            
            if last_updated < period_start:
                return {
                    "is_fresh": False,
                    "status": "🟠",
                    "message": f"Da aggiornare (nuovi PMI)",
                    "reason": f"Periodo PMI ({'Flash' if day_of_month >= 22 else 'Finale'})"
                }
        
        # Fuori periodo critico, controlla solo età
        if age_days > 20:
            return {
                "is_fresh": False,
                "status": "🟠",
                "message": f"Da aggiornare ({age_days}gg fa)",
                "reason": "Dati troppo vecchi"
            }
        
        return {
            "is_fresh": True,
            "status": "🟢",
            "message": f"Aggiornato" if age_days == 0 else f"Aggiornato {age_days}gg fa",
            "reason": ""
        }
    
    # ===== DATI MACRO (Inflazione, PIL, etc.) =====
    if data_type == "macro":
        # Periodo critico: 10-15 del mese (CPI)
        in_cpi_period = 10 <= day_of_month <= 16
        
        if in_cpi_period:
            period_start = now.replace(day=10, hour=0, minute=0, second=0, microsecond=0)
            if last_updated < period_start:
                return {
                    "is_fresh": False,
                    "status": "🟠",
                    "message": f"Da aggiornare (periodo CPI)",
                    "reason": "Nuovi dati inflazione probabilmente disponibili"
                }
        
        # Controlla età generale
        if age_days > 7:
            return {
                "is_fresh": False,
                "status": "🟠",
                "message": f"Da aggiornare ({age_days}gg fa)",
                "reason": "Dati vecchi di oltre 7 giorni"
            }
        
        return {
            "is_fresh": True,
            "status": "🟢",
            "message": f"Aggiornato" if age_days == 0 else f"Aggiornato {age_days}gg fa",
            "reason": ""
        }
    
    # ===== REGIMI ECONOMICI =====
    if data_type == "regimes":
        # I regimi si basano su PMI (esce ~1° del mese) e CPI (esce ~10-15 del mese)
        current_month = now.month
        current_year = now.year
        last_update_month = last_updated.month
        last_update_year = last_updated.year
        
        # Se l'update è di un mese/anno precedente
        is_old_month = (last_update_year < current_year) or (last_update_year == current_year and last_update_month < current_month)
        
        # Dopo il 1° del mese, i PMI del mese precedente sono usciti
        if day_of_month >= 1 and is_old_month:
            return {
                "is_fresh": False,
                "status": "🟠",
                "message": f"Da aggiornare (nuovi PMI)",
                "reason": "Nuovi PMI disponibili (inizio mese)"
            }
        
        # Dopo il 15 del mese, i CPI del mese precedente sono usciti
        if day_of_month >= 15 and last_updated.day < 15 and last_update_month == current_month:
            return {
                "is_fresh": False,
                "status": "🟠",
                "message": f"Da aggiornare (nuovi CPI)",
                "reason": "Nuovi CPI disponibili (metà mese)"
            }
        
        return {
            "is_fresh": True,
            "status": "🟢",
            "message": f"Aggiornato" if age_days == 0 else f"Aggiornato {age_days}gg fa",
            "reason": ""
        }
    
    # ===== COT (Commitment of Traders) =====
    if data_type == "cot":
        # I dati COT escono il venerdì (riferiti al martedì)
        # Considera "vecchi" se non aggiornati da più di 7 giorni
        if age_days > 7:
            return {
                "is_fresh": False,
                "status": "🟠",
                "message": f"Da aggiornare ({age_days}gg fa)",
                "reason": "Nuovi dati COT probabilmente disponibili"
            }
        
        return {
            "is_fresh": True,
            "status": "🟢",
            "message": f"Aggiornato" if age_days == 0 else f"Aggiornato {age_days}gg fa",
            "reason": ""
        }
    
    # ===== RISK SENTIMENT (VIX + S&P 500) =====
    if data_type == "risk_sentiment":
        # Da aggiornare se non aggiornato oggi dopo le 8:00
        today_8am = now.replace(hour=8, minute=0, second=0, microsecond=0)
        if now.hour >= 8 and last_updated < today_8am:
            return {
                "is_fresh": False,
                "status": "🟠",
                "message": f"Da aggiornare (ieri)",
                "reason": "Aggiorna per avere VIX e S&P di oggi"
            }
        return {
            "is_fresh": True,
            "status": "🟢",
            "message": f"Aggiornato",
            "reason": ""
        }
    
    # Default
    return {
        "is_fresh": True,
        "status": "🟢",
        "message": "OK",
        "reason": ""
    }


def get_all_data_freshness(timestamps: dict) -> tuple[bool, dict]:
    """
    Controlla la freshness di tutti i tipi di dati.
    
    Args:
        timestamps: dict con chiavi "macro", "cb_history", "pmi", "prices", "news"
                   e valori datetime o None
    
    Returns:
        (all_fresh: bool, details: dict con stato per ogni tipo)
    """
    data_types = ["macro", "cb_history", "pmi", "prices", "news"]
    details = {}
    all_fresh = True
    
    for dt in data_types:
        last_updated = timestamps.get(dt)
        freshness = check_data_freshness(dt, last_updated)
        details[dt] = freshness
        if not freshness["is_fresh"]:
            all_fresh = False
    
    return all_fresh, details


# ============================================================================
# FUNZIONI GESTIONE TIMESTAMPS DATI
# ============================================================================

def save_data_timestamp(data_type: str, user_id: str):
    """Salva il timestamp di aggiornamento per un tipo di dato."""
    key = f"timestamp_{data_type}"
    now = get_italy_now()
    st.session_state[key] = now
    
    # Per ora salviamo solo in session_state
    # La persistenza avviene attraverso l'analisi salvata che contiene i dati


def load_data_timestamps(user_id: str) -> dict:
    """Carica tutti i timestamps dei dati per un utente."""
    timestamps = {}
    data_types = ["macro", "cb_history", "pmi", "prices", "news"]
    
    # Prima controlla session_state
    for dt in data_types:
        key = f"timestamp_{dt}"
        if key in st.session_state:
            timestamps[dt] = st.session_state[key]
    
    # Se mancano timestamps, prova a recuperarli dall'ultima analisi salvata
    if len(timestamps) < len(data_types):
        try:
            cached = get_latest_analysis_data(user_id)
            if cached.get("cached_datetime"):
                cached_dt = datetime.strptime(cached["cached_datetime"], "%Y-%m-%d_%H-%M-%S")
                if ITALY_TZ:
                    cached_dt = cached_dt.replace(tzinfo=ITALY_TZ)
                
                # Usa il datetime dell'analisi come fallback per i dati mancanti
                data_keys = {
                    "macro": "macro_data",
                    "cb_history": "cb_history_data", 
                    "pmi": "pmi_data",
                    "prices": "forex_prices",
                    "news": "news_structured"
                }
                for dt in data_types:
                    data_key = data_keys.get(dt, f"{dt}_data")
                    if dt not in timestamps and cached.get(data_key):
                        timestamps[dt] = cached_dt
                        st.session_state[f"timestamp_{dt}"] = cached_dt
        except:
            pass
    
    return timestamps


# Import modulo dati macro da API ufficiali
from macro_data_fetcher import MacroDataFetcher


# ============================================================================
# FUNZIONE RISK SENTIMENT QUANTITATIVO
# ============================================================================

def fetch_risk_sentiment_data() -> dict:
    """
    Calcola il Risk Sentiment basato su indicatori quantitativi:
    - VIX (indice volatilità/paura)
    - S&P 500 variazione % giornaliera
    
    Returns:
        dict con regime, score, dati raw e punteggi per valuta
    """
    import yfinance as yf
    
    result = {
        "status": "error",
        "regime": "neutral",
        "risk_score": 0,
        "vix": None,
        "vix_contribution": 0,
        "sp500_change_pct": None,
        "sp500_contribution": 0,
        "currency_scores": {},
        "interpretation": "",
        "debug": []
    }
    
    try:
        # === FETCH VIX ===
        vix_ticker = yf.Ticker("^VIX")
        vix_data = vix_ticker.history(period="2d")
        
        if len(vix_data) > 0:
            vix_value = float(vix_data['Close'].iloc[-1])
            result["vix"] = round(vix_value, 2)
            result["debug"].append(f"VIX: {vix_value:.2f}")
            
            # Calcola contributo VIX
            if vix_value < 15:
                result["vix_contribution"] = 1  # Molto basso = risk-on
                result["debug"].append("VIX < 15: contributo +1 (risk-on)")
            elif vix_value <= 20:
                result["vix_contribution"] = 0  # Normale
                result["debug"].append("VIX 15-20: contributo 0 (normale)")
            elif vix_value <= 25:
                result["vix_contribution"] = -1  # Elevato
                result["debug"].append("VIX 20-25: contributo -1 (elevato)")
            else:
                result["vix_contribution"] = -2  # Molto elevato
                result["debug"].append(f"VIX > 25: contributo -2 (paura)")
        else:
            result["debug"].append("VIX: dati non disponibili")
        
        # === FETCH S&P 500 ===
        sp_ticker = yf.Ticker("^GSPC")
        sp_data = sp_ticker.history(period="5d")
        
        if len(sp_data) >= 2:
            current_close = float(sp_data['Close'].iloc[-1])
            prev_close = float(sp_data['Close'].iloc[-2])
            sp_change_pct = ((current_close - prev_close) / prev_close) * 100
            result["sp500_change_pct"] = round(sp_change_pct, 2)
            result["debug"].append(f"S&P 500: {sp_change_pct:+.2f}%")
            
            # Calcola contributo S&P
            if sp_change_pct > 1.0:
                result["sp500_contribution"] = 1  # Rally forte
                result["debug"].append("S&P > +1%: contributo +1 (rally)")
            elif sp_change_pct < -1.0:
                result["sp500_contribution"] = -1  # Sell-off
                result["debug"].append("S&P < -1%: contributo -1 (sell-off)")
            else:
                result["sp500_contribution"] = 0  # Normale
                result["debug"].append("S&P -1% a +1%: contributo 0 (normale)")
        else:
            result["debug"].append("S&P 500: dati non disponibili")
        
        # === CALCOLA RISK SCORE TOTALE ===
        risk_score = result["vix_contribution"] + result["sp500_contribution"]
        result["risk_score"] = risk_score
        result["debug"].append(f"Risk Score totale: {risk_score}")
        
        # === DETERMINA REGIME ===
        if risk_score >= 1:
            result["regime"] = "risk-on"
            result["interpretation"] = f"📈 RISK-ON (VIX: {result['vix']}, S&P: {result['sp500_change_pct']:+.1f}%)"
        elif risk_score <= -2:
            result["regime"] = "risk-off"
            result["interpretation"] = f"📉 RISK-OFF (VIX: {result['vix']}, S&P: {result['sp500_change_pct']:+.1f}%)"
        else:
            result["regime"] = "neutral"
            result["interpretation"] = f"⚪ NEUTRO (VIX: {result['vix']}, S&P: {result['sp500_change_pct']:+.1f}%)"
        
        result["debug"].append(f"Regime: {result['regime'].upper()}")
        
        # === ASSEGNA PUNTEGGI PER VALUTA ===
        # Risk-on: favorisce AUD, CAD / penalizza JPY, CHF, USD
        # Risk-off: favorisce JPY, CHF, USD / penalizza AUD, CAD
        
        if result["regime"] == "risk-on":
            result["currency_scores"] = {
                "AUD": {"score": 1, "reason": "Commodity currency, beneficia da risk-on"},
                "CAD": {"score": 1, "reason": "Commodity currency, beneficia da risk-on"},
                "EUR": {"score": 0, "reason": "Semi-neutrale in risk-on"},
                "GBP": {"score": 0, "reason": "Semi-neutrale in risk-on"},
                "USD": {"score": -1, "reason": "Safe haven, penalizzato in risk-on"},
                "JPY": {"score": -1, "reason": "Safe haven classico, penalizzato in risk-on"},
                "CHF": {"score": -1, "reason": "Safe haven classico, penalizzato in risk-on"}
            }
        elif result["regime"] == "risk-off":
            result["currency_scores"] = {
                "AUD": {"score": -1, "reason": "Commodity currency, penalizzata in risk-off"},
                "CAD": {"score": -1, "reason": "Commodity currency, penalizzata in risk-off"},
                "EUR": {"score": 0, "reason": "Semi-neutrale in risk-off"},
                "GBP": {"score": 0, "reason": "Semi-neutrale in risk-off"},
                "USD": {"score": 1, "reason": "Safe haven, beneficia da risk-off"},
                "JPY": {"score": 1, "reason": "Safe haven classico, beneficia da risk-off"},
                "CHF": {"score": 1, "reason": "Safe haven classico, beneficia da risk-off"}
            }
        else:  # neutral
            result["currency_scores"] = {
                "AUD": {"score": 0, "reason": "Regime neutro, nessun bias"},
                "CAD": {"score": 0, "reason": "Regime neutro, nessun bias"},
                "EUR": {"score": 0, "reason": "Regime neutro, nessun bias"},
                "GBP": {"score": 0, "reason": "Regime neutro, nessun bias"},
                "USD": {"score": 0, "reason": "Regime neutro, nessun bias"},
                "JPY": {"score": 0, "reason": "Regime neutro, nessun bias"},
                "CHF": {"score": 0, "reason": "Regime neutro, nessun bias"}
            }
        
        result["status"] = "ok"
        
    except Exception as e:
        result["debug"].append(f"Errore: {str(e)}")
        result["interpretation"] = "⚠️ Dati non disponibili"
        # In caso di errore, tutti i punteggi sono 0 (neutro)
        result["currency_scores"] = {
            curr: {"score": 0, "reason": "Dati risk sentiment non disponibili"} 
            for curr in ["AUD", "CAD", "EUR", "GBP", "USD", "JPY", "CHF"]
        }
    
    return result


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


def get_latest_analysis_data(user_id: str) -> dict:
    """
    Carica i dati dall'ultima analisi salvata per usarli come fallback.
    
    Returns:
        Dict con i dati disponibili (macro_data, pmi_data, ecc.) o dict vuoto
    """
    cached_data = {}
    
    try:
        # Ottieni lista analisi recenti
        recent = get_user_analyses(user_id, limit=1)
        if not recent or len(recent) == 0:
            return cached_data
        
        # Trova il datetime key
        datetime_key = recent[0].get("analysis_datetime") or recent[0].get("data", {}).get("analysis_datetime")
        if not datetime_key:
            return cached_data
        
        # Carica l'analisi completa
        last_analysis = load_analysis(datetime_key, user_id)
        if not last_analysis:
            return cached_data
        
        # I dati sono dentro 'data' per Supabase, direttamente per locale
        data_container = last_analysis.get('data', last_analysis)
        
        # Estrai tutti i dati disponibili
        for key in ['macro_data', 'pmi_data', 'cb_history_data', 'forex_prices', 
                    'economic_events', 'news_structured', 'links_structured']:
            if key in data_container and data_container[key]:
                cached_data[key] = data_container[key]
        
        # Aggiungi anche il datetime per mostrare quanto sono vecchi i dati
        cached_data['cached_datetime'] = datetime_key
        
    except Exception as e:
        # Se fallisce, ritorna dict vuoto
        pass
    
    return cached_data


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


def get_currency_scores_history(user_id: str, limit: int = 30) -> dict:
    """
    Estrae lo storico dei punteggi per ogni valuta dalle analisi salvate.
    
    Returns:
        dict con {currency: [{"date": "...", "date_obj": datetime, "score": X}, ...]}
    """
    analyses = get_user_analyses(user_id, limit=limit)
    
    # Dizionario per ogni valuta
    history = {curr: [] for curr in ["USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD"]}
    
    for analysis in analyses:
        # Estrai datetime
        dt_str = analysis.get("analysis_datetime") or analysis.get("data", {}).get("analysis_datetime", "")
        if not dt_str:
            continue
        
        # Formatta data per display
        try:
            if "_" in dt_str:
                date_part = dt_str.split("_")[0]
                date_obj = datetime.strptime(date_part, "%Y-%m-%d")
            else:
                date_obj = datetime.strptime(dt_str, "%Y-%m-%d")
            date_display = date_obj.strftime("%d/%m")
        except:
            date_display = dt_str[:10]
            date_obj = None
        
        # Estrai currency_analysis
        claude_data = analysis.get("claude_analysis") or analysis.get("data", {}).get("claude_analysis", {})
        currency_analysis = claude_data.get("currency_analysis", {})
        
        for curr in history.keys():
            if curr in currency_analysis:
                score = currency_analysis[curr].get("total_score", 0)
                history[curr].append({
                    "date": date_display,
                    "date_obj": date_obj,
                    "datetime": dt_str,
                    "score": score
                })
    
    # Inverti l'ordine (dal più vecchio al più recente per i grafici)
    for curr in history:
        history[curr] = list(reversed(history[curr]))
    
    return history


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
        "manufacturing": {"id": 185, "name": "ivey-pmi", "label": "Ivey PMI", "country": "ca"},
        "services": None  # DuckDuckGo fallback cercherà Canada Services PMI
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
        # CHF e CAD hanno solo PMI unico (non separato manufacturing/services)
        if currency in ["CHF", "CAD"]:
            # Nessun Services PMI disponibile - non è un errore
            result = {
                "current": None, 
                "previous": None, 
                "delta": None, 
                "date": None, 
                "source": "N/D",  # Non Disponibile (non errore)
                "not_available": True  # Flag per indicare che è normale
            }
        else:
            # 1) Prova API JSON
            result = fetch_pmi_from_investing_json(currency, "services")
            
            # 2) Se fallisce, prova HTML scraping
            if result.get("current") is None:
                time.sleep(1.0)
                result = fetch_pmi_from_investing(currency, "services")
            
            # 3) Se ancora fallisce, prova DuckDuckGo (solo per valute con Services PMI)
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


def get_pmi_interpretation_single(pmi_delta: float) -> tuple:
    """
    Restituisce interpretazione e trend per valute con PMI unico (CHF, CAD).
    
    Returns:
        (trend_text, interpretation)
    """
    if pmi_delta is None:
        pmi_delta = 0
    
    # Trend solo per il PMI unico
    pmi_trend = "↑" if pmi_delta > 0.1 else "↓" if pmi_delta < -0.1 else "→"
    
    # Testo indica che è PMI unico
    trend_text = f"PMI{pmi_trend}"
    
    # Interpretazione basata solo sul PMI unico
    if pmi_delta > 0.5:
        interpretation = "Bullish"
    elif pmi_delta > 0.1:
        interpretation = "Misto+"
    elif pmi_delta < -0.5:
        interpretation = "Bearish"
    elif pmi_delta < -0.1:
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

### 2️⃣ ASPETTATIVE TASSI [-1 a +1]
**Logica:** Il mercato guarda avanti. Le aspettative future influenzano la valuta.

| Scenario | Score |
|----------|-------|
| BC hawkish, rialzi attesi o hold prolungato | +1 |
| BC neutrale, incertezza elevata | 0 |
| BC dovish, in ciclo di tagli o tagli attesi | -1 |

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

### 5️⃣ RISK SENTIMENT [-1 a +1]
**Logica:** In risk-off, capitali verso safe-haven. In risk-on, verso cicliche.

**IMPORTANTE:** Il Risk Sentiment è PRE-CALCOLATO basandosi su VIX e S&P 500.
USA il punteggio fornito nei dati di input, NON ricalcolare!

**Classificazione valute:**
- **Safe-haven (beneficiano da risk-off):** USD, JPY, CHF
- **Cicliche (beneficiano da risk-on):** AUD, CAD
- **Semi-neutre:** EUR, GBP

| Regime | AUD/CAD | EUR/GBP | USD/JPY/CHF |
|--------|---------|---------|-------------|
| Risk-ON | +1 | 0 | -1 |
| Neutro | 0 | 0 | 0 |
| Risk-OFF | -1 | 0 | +1 |

---

### 6️⃣ COT SCORE [-2 a +2] ⭐ PESO DOPPIO
**Logica:** Posizionamento degli speculatori (Non-Commercial) combinando Net Position, COT Index e Momentum.

**IMPORTANTE:** Il COT Score è PRE-CALCOLATO e fornito nei dati di input con la sua interpretazione.
USA il punteggio e l'interpretazione forniti direttamente, NON ricalcolare.

**Come viene calcolato (per tua comprensione):**
- **Net Position** → LONG (>0) o SHORT (<0)
- **COT Index** → Intensità: Alto (>70%), Medio (30-70%), Basso (<30%)
- **Momentum** → Accelerazione acquisti (🟢), Stabile (⚪), Accelerazione vendite (🔴)

| Situazione | Score | Significato |
|------------|-------|-------------|
| LONG forte + Momentum positivo | **+2** | Molto bullish, trend forte |
| LONG + Momentum positivo | **+1** | Bullish in costruzione |
| LONG forte ma Momentum negativo | **0** | ⚠️ Possibile inversione |
| SHORT + Momentum positivo (chiudono short) | **+1** | Sentiment in miglioramento |
| Posizione neutra o segnali misti | **0** | Nessun segnale chiaro |
| LONG ma Momentum negativo (chiudono long) | **-1** | Sentiment in peggioramento |
| SHORT + Momentum negativo | **-1** | Bearish in costruzione |
| SHORT forte + Momentum negativo | **-2** | Molto bearish, trend forte |

⚠️ Se il dato COT non è disponibile → Score = 0

---

### 7️⃣ NEWS BONUS [-1 a +1]

**Logica:** Bonus/malus giornaliero basato sulle notizie delle ultime 24h che potrebbero muovere la valuta OGGI.

**IMPORTANTE:** Questo parametro è un BONUS semplice. Non cercare sorprese complesse.

| Notizie ultime 24h | Score | Esempi |
|-------------------|-------|--------|
| Notizie POSITIVE per la valuta | +1 | Dati economici sopra attese, dichiarazioni BC favorevoli, upgrade rating |
| Nessuna notizia rilevante O notizie miste | 0 | Dati in linea, nessuna sorpresa, situazione stabile |
| Notizie NEGATIVE per la valuta | -1 | Dati economici sotto attese, dichiarazioni BC sfavorevoli, tensioni |

**Regole:**
- NON considerare temi già valutati in altri parametri (tassi, inflazione, PIL)
- Valuta SOLO l'impatto potenziale sul movimento di OGGI
- Nel dubbio → 0

---

## ═══════════════════════════════════════════════════════════════════
## RANGE TOTALI PER VALUTA
## ═══════════════════════════════════════════════════════════════════

- **COT Score**: da -2 a +2 (peso doppio)
- **Altri 6 parametri**: da -1 a +1
- **TOTALE per valuta**: da -8 a +8

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
                    "motivation": "BCE in ciclo tagli, stance dovish"
                },
                "inflazione": {
                    "score": 0,
                    "motivation": "2.14% vicino al target 2%, situazione controllata"
                },
                "crescita_pil": {
                    "score": -1,
                    "motivation": "PIL 0.7%, stagnazione con Germania in difficoltà"
                },
                "risk_sentiment": {
                    "score": 0,
                    "motivation": "Regime neutro, EUR semi-neutra"
                },
                "cot_score": {
                    "score": 1,
                    "motivation": "📈 Long in costruzione - speculatori stanno accumulando EUR"
                },
                "news_bonus": {
                    "score": 0,
                    "motivation": "Nessuna notizia rilevante nelle ultime 24h"
                }
            }
        },
        "USD": {
            "total_score": 1,
            "summary": "Sintesi della situazione USD con dati numerici",
            "scores": {
                "tassi_attuali": {
                    "score": 1,
                    "motivation": "Fed 3.75%, tra i più alti G7, carry attraente"
                },
                "aspettative_tassi": {
                    "score": -1,
                    "motivation": "Fed in ciclo tagli, stance dovish"
                },
                "inflazione": {
                    "score": 0,
                    "motivation": "2.74% sopra target ma in calo, situazione gestibile"
                },
                "crescita_pil": {
                    "score": 1,
                    "motivation": "PIL 2.1% con inflazione in calo, crescita sostenibile"
                },
                "risk_sentiment": {
                    "score": 0,
                    "motivation": "Regime neutro, USD safe-haven ma nessun flusso risk-off"
                },
                "cot_score": {
                    "score": -1,
                    "motivation": "📉 Short in costruzione - speculatori vendono USD"
                },
                "news_bonus": {
                    "score": 1,
                    "motivation": "Retail sales sopra attese ieri"
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
2. **total_score = SOMMA dei 10 punteggi** (verifica che sia corretto!)
3. **USA SOLO I DATI FORNITI** - non inventare
4. **MOTIVAZIONI CON NUMERI**: cita sempre i valori specifici (tassi %, inflazione %, PMI, COT Index %)
5. **COERENZA**: se dai +1 a USD per tassi alti, non dare +1 anche a EUR che ha tassi più bassi

## ⛔ REGOLA CRITICA NEWS CATALYST ⛔

**News Catalyst richiede SORPRESE CONCRETE (Actual vs Forecast)!**

## 🚨 ALGORITMO OBBLIGATORIO PER NEWS CATALYST 🚨

**STEP 1:** Hai un dato concreto con Actual vs Forecast?
- NO → **STOP! Score = 0**
- SÌ → vai a Step 2

**STEP 2:** La sorpresa è negli ultimi 7 giorni?
- NO → **STOP! Score = 0**
- SÌ → vai a Step 3

**STEP 3:** Questo fattore è già conteggiato in un altro parametro?
- SÌ (es: BC hawkish → già in Aspettative Tassi) → **STOP! Score = 0**
- NO → Calcola il punteggio basato sulla tabella delle sorprese

## 🚫 PAROLE VIETATE NELLE MOTIVAZIONI DI NEWS CATALYST:

**Se la motivazione contiene una di queste parole → Score DEVE essere 0:**

| Categoria | Parole vietate | Motivo |
|-----------|----------------|--------|
| **Tassi** | tassi, tasso, interest rate, carry trade | Già in Tassi Attuali |
| **BC Stance** | dovish, hawkish, easing, tightening, taglio, rialzo | Già in Aspettative Tassi |
| **Inflazione** | inflazione, CPI, prezzi, deflazione | Già in Inflazione |
| **Crescita** | PIL, GDP, crescita, recessione, stagnazione | Già in Crescita/PIL |
| **PMI** | PMI, manifatturiero, manufacturing, servizi, services, espansione, contrazione | Già in Regime Economico |
| **Sentiment** | safe-haven, risk-off, risk-on, tensioni, geopolitica, VIX | Già in Risk Sentiment |
| **Fiscale** | debito, deficit, fiscale, bilancia | Già in Bilancia/Fiscale |
| **Assenza** | nessuna sorpresa, nessun dato, mancanza | Non è una sorpresa! |

## ❌ ERRORI GRAVI DA NON COMMETTERE MAI:

❌ **"PMI crollo -3.9 punti → -2"** → Il PMI è già nel Regime Economico! → **0**
❌ **"BOC dovish pesa → -2"** → La stance BC è già in Aspettative Tassi! → **0**
❌ **"Inflazione sopra target → +1"** → L'inflazione è già nel parametro Inflazione! → **0**
❌ **"Nessuna sorpresa... pesa negativamente → -2"** → Contraddizione! → **0**

## ✅ UNICI CASI IN CUI NEWS CATALYST ≠ 0:

1. **Retail Sales** con sorpresa significativa (Actual vs Forecast)
2. **Consumer Confidence** con sorpresa significativa
3. **Employment Change** con sorpresa (non NFP per USD)
4. **Trade Balance** con sorpresa significativa
5. **Evento geopolitico NUOVO** (<48h) NON già in Risk Sentiment

**REGOLA D'ORO: Nel 90% dei casi, News Catalyst = 0!**

**Se non hai un dato SECONDARIO con Actual vs Forecast → News Catalyst = 0**
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
    "regime_economico",
    "aspettative_tassi", 
    "inflazione",
    "crescita_pil",
    "risk_sentiment",
    "cot_score",
    "news_bonus"
]


def add_regime_scores_to_analysis(currency_analysis: dict, regimes_data: dict) -> dict:
    """
    Aggiunge il punteggio del regime economico ai punteggi delle valute.
    
    Args:
        currency_analysis: Dict con punteggi delle valute da Claude
        regimes_data: Dict con dati dei regimi da economic_regimes module
    
    Returns:
        currency_analysis aggiornato con regime_economico
    """
    if not REGIMES_MODULE_LOADED:
        return currency_analysis
    
    for currency, data in currency_analysis.items():
        if not isinstance(data, dict) or "scores" not in data:
            continue
        
        regime_info = regimes_data.get(currency, {})
        regime = regime_info.get("regime")
        
        if regime:
            forex_score = get_regime_forex_score(regime)
            regime_name = REGIME_DEFINITIONS.get(regime, {}).get("name", regime)
            
            # Aggiungi il punteggio regime_economico
            data["scores"]["regime_economico"] = {
                "score": forex_score,
                "motivation": f"Regime: {regime_name} ({'+' if forex_score > 0 else ''}{forex_score})"
            }
            
            # Aggiorna total_score
            data["total_score"] = data.get("total_score", 0) + forex_score
        else:
            # Nessun dato regime disponibile
            data["scores"]["regime_economico"] = {
                "score": 0,
                "motivation": "Regime non disponibile"
            }
    
    return currency_analysis


def validate_and_fix_currency_scores(currency_analysis: dict) -> dict:
    """
    Valida e corregge i punteggi delle valute.
    
    Regole di validazione:
    1. Ogni parametro deve essere nel suo range corretto
    2. Ricalcola total_score dopo le correzioni
    
    Args:
        currency_analysis: Dict con struttura {"EUR": {"total_score": X, "scores": {...}}, ...}
    
    Returns:
        currency_analysis corretto
    """
    # Range per ogni parametro
    score_ranges = {
        "tassi_attuali": (-1, 1),
        "aspettative_tassi": (-1, 1),
        "inflazione": (-1, 1),
        "crescita_pil": (-1, 1),
        "risk_sentiment": (-1, 1),
        "cot_score": (-2, 2),
        "news_bonus": (-1, 1),
        "regime_economico": (-2, 2)
    }
    
    corrections_made = []
    
    for currency, data in currency_analysis.items():
        if not isinstance(data, dict) or "scores" not in data:
            continue
        
        scores = data.get("scores", {})
        
        for param, score_data in scores.items():
            if not isinstance(score_data, dict):
                continue
            
            score = score_data.get("score", 0)
            original_score = score
            
            # Controlla range
            if param in score_ranges:
                min_val, max_val = score_ranges[param]
                if score < min_val:
                    score = min_val
                    corrections_made.append(f"{currency}/{param}: {original_score} → {score} (fuori range)")
                elif score > max_val:
                    score = max_val
                    corrections_made.append(f"{currency}/{param}: {original_score} → {score} (fuori range)")
            
            # Aggiorna il punteggio
            score_data["score"] = score
        
        # Ricalcola total_score
        new_total = 0
        for param_name in SCORE_PARAMETERS:
            if param_name in scores and isinstance(scores[param_name], dict):
                new_total += scores[param_name].get("score", 0)
        
        old_total = data.get("total_score", 0)
        if old_total != new_total:
            corrections_made.append(f"{currency}/total_score: {old_total} → {new_total}")
            data["total_score"] = new_total
    
    # Log delle correzioni (opzionale, per debug)
    if corrections_made:
        currency_analysis["_corrections"] = corrections_made
    
    return currency_analysis


def calculate_pair_from_currencies(currency_analysis: dict, forex_prices: dict = None, existing_pair_analysis: dict = None) -> dict:
    """
    Calcola i punteggi per le 19 coppie forex a partire dai punteggi delle 7 valute.
    
    Args:
        currency_analysis: Dict con struttura {
            "EUR": {"total_score": X, "summary": "...", "scores": {...}},
            "USD": {...},
            ...
        }
        forex_prices: Dict con prezzi forex (opzionale)
        existing_pair_analysis: pair_analysis esistente da cui preservare current_price e price_scenarios (opzionale)
    
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
        
        # Recupera current_price e price_scenarios da fonti esistenti
        current_price = ""
        price_scenarios = {}
        
        # Prima prova da existing_pair_analysis (preserva dati Claude)
        if existing_pair_analysis and pair in existing_pair_analysis:
            current_price = existing_pair_analysis[pair].get("current_price", "")
            price_scenarios = existing_pair_analysis[pair].get("price_scenarios", {})
        
        # Se non ci sono, prova da forex_prices
        if not current_price and forex_prices:
            prices_dict = forex_prices.get("prices", {}) if isinstance(forex_prices, dict) else {}
            if pair in prices_dict:
                price_val = prices_dict[pair]
                if isinstance(price_val, dict):
                    current_price = str(price_val.get("price", price_val.get("value", "")))
                else:
                    current_price = str(price_val)
        
        pair_analysis[pair] = {
            "summary": combined_summary,
            "score_base": score_base,
            "score_quote": score_quote,
            "differential": differential,
            "scores": scores,
            "key_drivers": [],
            "current_price": current_price,
            "price_scenarios": price_scenarios
        }
    
    return pair_analysis


def analyze_with_claude(api_key: str, macro_data: dict = None, news_text: str = "", additional_text: str = "", pmi_data: dict = None, forex_prices: dict = None, economic_events: dict = None, cb_history_data: dict = None, cot_data: dict = None, risk_sentiment_data: dict = None) -> dict:
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
        cot_data: Dati COT (Commitment of Traders) per valuta (opzionale)
        risk_sentiment_data: Dati Risk Sentiment (VIX + S&P 500) pre-calcolati (opzionale)
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

    # Sezione COT Data (Commitment of Traders)
    cot_section = ""
    if cot_data and cot_data.get('status') == 'ok':
        currencies_cot = cot_data.get('currencies', {})
        cot_lines = []
        for curr in ["USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD"]:
            if curr in currencies_cot:
                data = currencies_cot[curr]
                if data.get('status') == 'ok':
                    net_pos = data.get('net_position', 0)
                    cot_index = data.get('cot_index', 50)
                    momentum = data.get('momentum', {})
                    scores = data.get('scores', {})
                    
                    # Estrai il COT Score unificato e l'interpretazione
                    cot_score = scores.get('cot_score', 0)
                    interpretation = scores.get('interpretation', 'N/A')
                    
                    # Direzione Net Position
                    net_direction = "LONG" if net_pos > 0 else "SHORT"
                    
                    # Delta momentum
                    delta = momentum.get('delta_current', 0)
                    
                    cot_lines.append(
                        f"**{curr}:** Net Position: {net_pos:+,} ({net_direction}) | "
                        f"COT Index: {cot_index:.0f}% | "
                        f"Momentum: Δ {delta:+,} | "
                        f"**COT Score: {cot_score:+d}** → {interpretation}"
                    )
        
        if cot_lines:
            cot_section = f"""
## 📊 DATI COT (Commitment of Traders - Non-Commercial/Speculatori):
{chr(10).join(cot_lines)}

⚠️ **USA IL COT SCORE PRE-CALCOLATO per il parametro cot_score!**
- Il COT Score (-2 a +2) combina: Net Position (LONG/SHORT), COT Index (intensità), Momentum (direzione)
- Riporta il punteggio e l'interpretazione esattamente come forniti sopra
- **NOTA USD:** Il COT USD è basato sul Dollar Index (DXY), interpretazione diretta (Long DXY = Bullish USD)

---
"""

    # Sezione Risk Sentiment (VIX + S&P 500)
    risk_section = ""
    if risk_sentiment_data and risk_sentiment_data.get('status') == 'ok':
        regime = risk_sentiment_data.get('regime', 'neutral')
        vix = risk_sentiment_data.get('vix')
        sp_change = risk_sentiment_data.get('sp500_change_pct')
        currency_scores = risk_sentiment_data.get('currency_scores', {})
        
        # Costruisci linee per ogni valuta
        risk_lines = []
        for curr in ["USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD"]:
            score_data = currency_scores.get(curr, {})
            score = score_data.get('score', 0)
            reason = score_data.get('reason', '')
            risk_lines.append(f"**{curr}:** Score: {score:+d} ({reason})")
        
        risk_section = f"""
## 📊 RISK SENTIMENT (PRE-CALCOLATO da VIX + S&P 500):
**Regime: {regime.upper()}** | VIX: {vix} | S&P 500 Δ: {sp_change:+.2f}%

Punteggi pre-calcolati per risk_sentiment:
{chr(10).join(risk_lines)}

⚠️ **USA I PUNTEGGI PRE-CALCOLATI per il parametro risk_sentiment!**
- I punteggi sono già calcolati in base a VIX e S&P 500
- Riporta esattamente i punteggi forniti sopra per ogni valuta

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
{cot_section}
{risk_section}
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
            
            # ===== VALIDAZIONE E CORREZIONE PUNTEGGI =====
            currency_analysis = validate_and_fix_currency_scores(currency_analysis)
            analysis["currency_analysis"] = currency_analysis
            
            # Log correzioni se presenti
            if "_corrections" in currency_analysis:
                analysis["score_corrections"] = currency_analysis.pop("_corrections")
            
            # Verifica che tutte le 7 valute siano presenti
            missing_currencies = set(CURRENCIES.keys()) - set(currency_analysis.keys())
            if missing_currencies:
                analysis["warning"] = f"Valute mancanti: {', '.join(missing_currencies)}"
            
            # Calcola i differenziali per le 19 coppie
            pair_analysis = calculate_pair_from_currencies(currency_analysis, forex_prices)
            analysis["pair_analysis"] = pair_analysis
        
        return analysis
        
    except json.JSONDecodeError as e:
        return {"error": f"Errore parsing JSON: {e}"}
    except Exception as e:
        return {"error": f"Errore API Claude: {e}"}


# ============================================================================
# FUNZIONI VISUALIZZAZIONE
# ============================================================================

def display_cot_data(cot_data: dict):
    """
    Mostra la tabella dei dati COT con colori per indicare condizioni positive/negative.
    
    Args:
        cot_data: Dict restituito da get_cot_analysis() o caricato da session_state
    """
    if not cot_data:
        st.warning("⚠️ Nessun dato COT disponibile")
        return
    
    if cot_data.get('status') != 'ok':
        st.error(f"❌ Errore dati COT: {cot_data.get('message', 'Errore sconosciuto')}")
        if cot_data.get('debug'):
            with st.expander("🔍 Debug Log"):
                for msg in cot_data['debug'][-10:]:  # Ultimi 10 messaggi
                    st.text(msg)
        return
    
    currencies_data = cot_data.get('currencies', {})
    
    if not currencies_data:
        st.warning("⚠️ Nessun dato COT per le valute")
        return
    
    # Costruisci la tabella
    rows = []
    for currency in ['USD', 'EUR', 'GBP', 'JPY', 'CHF', 'AUD', 'CAD']:
        data = currencies_data.get(currency, {})
        
        if data.get('status') != 'ok':
            rows.append({
                'Valuta': currency,
                'Net Position': '❌ N/A',
                'COT Index': 'N/A',
                'Δ Sett.': 'N/A',
                'Score': 'N/A',
                'Interpretazione': data.get('scores', {}).get('interpretation', 'Dati insufficienti'),
            })
            continue
        
        # Dati
        net_pos = data.get('net_position', 0)
        cot_index = data.get('cot_index', 50)
        momentum = data.get('momentum', {})
        delta_current = momentum.get('delta_current', 0)
        p75 = momentum.get('percentile_75', 0)
        p25 = momentum.get('percentile_25', 0)
        scores = data.get('scores', {})
        cot_score = scores.get('cot_score', 0)
        interpretation = scores.get('interpretation', 'N/A')
        
        # Colore per Net Position (LONG/SHORT)
        net_color = "🟢" if net_pos > 0 else "🔴" if net_pos < 0 else "⚪"
        
        # Colore per COT Index (intensità)
        if cot_index > 70:
            idx_color = "🔵"  # Alto
        elif cot_index >= 30:
            idx_color = "⚪"  # Medio
        else:
            idx_color = "🟠"  # Basso
        
        # Colori Momentum
        if delta_current > p75:
            mom_color = "🟢"  # Accelerazione acquisti
        elif delta_current < p25:
            mom_color = "🔴"  # Accelerazione vendite
        else:
            mom_color = "⚪"  # Stabile
        
        # Colore score
        if cot_score >= 2:
            score_display = f"🟢🟢 +{cot_score}"
        elif cot_score == 1:
            score_display = f"🟢 +{cot_score}"
        elif cot_score == 0:
            score_display = f"⚪ {cot_score}"
        elif cot_score == -1:
            score_display = f"🔴 {cot_score}"
        else:  # -2
            score_display = f"🔴🔴 {cot_score}"
        
        rows.append({
            'Valuta': currency,
            'Net Position': f"{net_color} {net_pos:+,}",
            'COT Index': f"{idx_color} {cot_index:.0f}%",
            'Δ Sett.': f"{mom_color} {delta_current:+,}",
            'Score': score_display,
            'Interpretazione': interpretation,
        })
    
    # Mostra tabella
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)
    
    # Info aggiornamento
    if cot_data.get('last_update'):
        last_upd = cot_data['last_update']
        if isinstance(last_upd, str):
            try:
                last_upd = datetime.fromisoformat(last_upd.replace('Z', '+00:00'))
                last_upd_str = last_upd.strftime('%d/%m/%Y %H:%M')
            except:
                last_upd_str = last_upd
        else:
            last_upd_str = str(last_upd)
        
        # Trova la data del report più recente
        report_dates = []
        for curr, data in currencies_data.items():
            if data.get('report_date'):
                report_dates.append(data['report_date'])
        
        report_date_str = max(report_dates) if report_dates else "N/A"
        
        st.caption(f"📅 Ultimo aggiornamento: {last_upd_str} | Dati report: martedì {report_date_str}")
    
    # Legenda
    with st.expander("ℹ️ Cos'è il COT e come interpretarlo"):
        st.markdown("""
        ### 📊 Cos'è il COT Report?
        
        Il **Commitment of Traders (COT)** è un report settimanale pubblicato dalla CFTC che mostra il posizionamento 
        dei grandi operatori sui mercati futures. I **Non-Commercial** (hedge fund, speculatori istituzionali) 
        sono i "big players" che muovono il mercato - seguire il loro posizionamento può anticipare i trend.
        
        ---
        
        ### 🔢 Le 3 variabili che analizziamo
        
        | Variabile | Cosa misura | Come si legge |
        |-----------|-------------|---------------|
        | **Net Position** | Direzione: sono LONG o SHORT? | 🟢 Positivo = LONG, 🔴 Negativo = SHORT |
        | **COT Index** | Intensità rispetto a 52 settimane | 🔵 >70% = Forte, ⚪ 30-70% = Medio, 🟠 <30% = Debole |
        | **Δ Sett. (Momentum)** | Stanno aumentando o diminuendo? | 🟢 Accelerano acquisti, 🔴 Accelerano vendite |
        
        ---
        
        ### 📈 COT Score Unificato (-2 a +2)
        
        Combiniamo le 3 variabili per ottenere un **unico punteggio**:
        
        #### Quando sono LONG (Net > 0):
        
        | COT Index | Momentum | Score | Significato |
        |-----------|----------|-------|-------------|
        | >70% (Forte) | 🟢 Positivo | **+2** | Long forte + stanno ancora comprando |
        | >70% (Forte) | ⚪ Stabile | **+1** | Long forte consolidato |
        | >70% (Forte) | 🔴 Negativo | **0** | ⚠️ Long forte MA stanno vendendo |
        | 30-70% (Medio) | 🟢 Positivo | **+1** | Long in costruzione |
        | 30-70% (Medio) | ⚪ Stabile | **0** | Neutro |
        | 30-70% (Medio) | 🔴 Negativo | **-1** | Stanno chiudendo i long |
        | <30% (Debole) | 🟢 Positivo | **+1** | Ricostruendo posizioni long |
        | <30% (Debole) | ⚪ Stabile | **0** | Neutro |
        | <30% (Debole) | 🔴 Negativo | **-1** | Long in esaurimento |
        
        #### Quando sono SHORT (Net < 0):
        
        | COT Index | Momentum | Score | Significato |
        |-----------|----------|-------|-------------|
        | <30% (Forte) | 🔴 Negativo | **-2** | Short forte + stanno ancora vendendo |
        | <30% (Forte) | ⚪ Stabile | **-1** | Short forte consolidato |
        | <30% (Forte) | 🟢 Positivo | **0** | ⚠️ Short forte MA stanno comprando |
        | 30-70% (Medio) | 🔴 Negativo | **-1** | Bearish in costruzione |
        | 30-70% (Medio) | ⚪ Stabile | **0** | Neutro |
        | 30-70% (Medio) | 🟢 Positivo | **+1** | Stanno chiudendo gli short |
        | >70% (Debole) | 🔴 Negativo | **-1** | Ricostruendo posizioni short |
        | >70% (Debole) | ⚪ Stabile | **0** | Neutro |
        | >70% (Debole) | 🟢 Positivo | **+1** | Short in esaurimento → Bullish |
        
        ---
        
        ### 💡 Logica chiave
        
        - **Per avere +2**: LONG + posizione FORTE (Index >70%) + Momentum POSITIVO
        - **Per avere -2**: SHORT + posizione FORTE (Index <30%) + Momentum NEGATIVO
        - **Score 0**: Segnali misti o possibile inversione (cautela!)
        - **Il Momentum conferma o smentisce** la direzione della Net Position
        
        ---
        
        ### 📝 Note tecniche
        
        - **Net Position**: Long - Short dei Non-Commercial (numero di contratti futures)
        - **USD**: Basato sul Dollar Index (DXY). Long DXY = Bullish USD
        - **Altre valute**: Long EUR futures = Bullish EUR / Bearish USD
        - **Aggiornamento**: I dati escono il venerdì (riferiti al martedì precedente)
        """)



def display_forex_prices(forex_prices: dict):
    """Mostra la tabella dei prezzi forex recuperati"""
    
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
        services_not_available = services.get("not_available", False)
        
        # Formatta valori con label per USD (ISM)
        if curr == "USD":
            manuf_display = f"{manuf_current} (ISM)" if manuf_current else "N/A"
            services_display = f"{services_current} (ISM)" if services_current else "N/A"
        else:
            manuf_display = str(manuf_current) if manuf_current else "N/A"
            # Per CHF e CAD, mostra "-" (non disponibile) invece di "N/A" (errore)
            if services_not_available:
                services_display = "-"
            else:
                services_display = str(services_current) if services_current else "N/A"
        
        # Formatta delta con segno
        def format_delta(delta, not_available=False):
            if not_available:
                return "-"
            elif delta is None:
                return "N/A"
            elif delta > 0:
                return f"+{delta}"
            else:
                return str(delta)
        
        # Formatta previous
        def format_previous(prev, not_available=False):
            if not_available:
                return "-"
            elif prev is None:
                return "N/A"
            else:
                return str(prev)
        
        # Calcola interpretazione (per CHF/CAD usa solo manufacturing)
        if services_not_available:
            # Per valute con solo PMI unico, valuta solo manufacturing
            trend_text, interpretation = get_pmi_interpretation_single(manuf_delta)
        else:
            trend_text, interpretation = get_pmi_interpretation(manuf_delta, services_delta)
        
        # Traccia dati mancanti (NON includere CHF/CAD services perché non è un errore)
        if manuf_current is None:
            missing_data.append(f"{curr}-Manuf")
        elif manuf_previous is None:
            missing_data.append(f"{curr}-Manuf(Prev)")
        if not services_not_available:  # Solo se services dovrebbe esistere
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
            "Prev ": format_previous(services_previous, services_not_available),  # Spazio per evitare duplicato colonna
            "Δ Serv": format_delta(services_delta, services_not_available),
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


# ============================================================================
# FUNZIONE DISPLAY REGIMI ECONOMICI
# ============================================================================

def display_economic_regimes(regimes_data: dict):
    """
    Mostra l'analisi dei regimi economici per tutte le valute.
    
    Design:
    - Matrice 2x2 con quadranti (Goldilocks, Reflation, Stagflation, Deflation)
    - Tabella con regime per ogni valuta
    - Indicatori di momentum
    - Alert divergenze CPI
    """
    if not regimes_data:
        st.info("ℹ️ Nessun dato regime disponibile. Clicca 🔄 per aggiornare.")
        return
    
    # === MATRICE 2x2 DEI REGIMI ===
    st.markdown("#### 📊 Matrice Regimi Economici")
    
    # Conta valute per ogni regime (nuovi nomi)
    regime_counts = {"espansione": [], "reflazione": [], "stagflazione": [], "deflazione": []}
    for currency, data in regimes_data.items():
        regime = data.get("regime")
        if regime and regime in regime_counts:
            regime_counts[regime].append(currency)
    
    # Mostra matrice 2x2
    col1, col2 = st.columns(2)
    
    with col1:
        # Espansione (PMI ↑, Inflazione ↓)
        exp_currencies = regime_counts.get("espansione", [])
        st.markdown(f"""
        <div style="background-color: #d1fae5; border-radius: 10px; padding: 15px; margin: 5px; min-height: 120px;">
            <h4 style="color: #059669; margin: 0;">🟢 Espansione</h4>
            <p style="color: #065f46; font-size: 12px; margin: 5px 0;">PMI ↑ + Inflazione ↓</p>
            <p style="color: #047857; font-weight: bold; font-size: 18px;">{', '.join(exp_currencies) if exp_currencies else 'Nessuna'}</p>
            <p style="color: #065f46; font-size: 11px;">📈 Forex: +1 (economia attrattiva)</p>
        </div>
        """, unsafe_allow_html=True)
        
        # Deflazione (PMI ↓, Inflazione ↓)
        defl_currencies = regime_counts.get("deflazione", [])
        st.markdown(f"""
        <div style="background-color: #e0e7ff; border-radius: 10px; padding: 15px; margin: 5px; min-height: 120px;">
            <h4 style="color: #4f46e5; margin: 0;">🔵 Deflazione</h4>
            <p style="color: #3730a3; font-size: 12px; margin: 5px 0;">PMI ↓ + Inflazione ↓</p>
            <p style="color: #4338ca; font-weight: bold; font-size: 18px;">{', '.join(defl_currencies) if defl_currencies else 'Nessuna'}</p>
            <p style="color: #3730a3; font-size: 11px;">📉 Forex: -1 (BC taglia tassi)</p>
        </div>
        """, unsafe_allow_html=True)
    
    with col2:
        # Reflazione (PMI ↑, Inflazione ↑)
        refl_currencies = regime_counts.get("reflazione", [])
        st.markdown(f"""
        <div style="background-color: #fef3c7; border-radius: 10px; padding: 15px; margin: 5px; min-height: 120px;">
            <h4 style="color: #d97706; margin: 0;">🟡 Reflazione</h4>
            <p style="color: #92400e; font-size: 12px; margin: 5px 0;">PMI ↑ + Inflazione ↑</p>
            <p style="color: #b45309; font-weight: bold; font-size: 18px;">{', '.join(refl_currencies) if refl_currencies else 'Nessuna'}</p>
            <p style="color: #92400e; font-size: 11px;">📈 Forex: +2 (BC alza tassi)</p>
        </div>
        """, unsafe_allow_html=True)
        
        # Stagflazione (PMI ↓, Inflazione ↑)
        stag_currencies = regime_counts.get("stagflazione", [])
        st.markdown(f"""
        <div style="background-color: #fee2e2; border-radius: 10px; padding: 15px; margin: 5px; min-height: 120px;">
            <h4 style="color: #dc2626; margin: 0;">🔴 Stagflazione</h4>
            <p style="color: #991b1b; font-size: 12px; margin: 5px 0;">PMI ↓ + Inflazione ↑</p>
            <p style="color: #b91c1c; font-weight: bold; font-size: 18px;">{', '.join(stag_currencies) if stag_currencies else 'Nessuna'}</p>
            <p style="color: #991b1b; font-size: 11px;">📉 Forex: -2 (BC paralizzata)</p>
        </div>
        """, unsafe_allow_html=True)
    
    st.markdown("---")
    
    # === TABELLA DETTAGLI PER VALUTA ===
    st.markdown("#### 📋 Dettagli per Valuta")
    
    table_rows = []
    divergence_alerts = []
    
    currency_order = ["USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD"]
    
    for currency in currency_order:
        data = regimes_data.get(currency, {})
        if not data or data.get("error"):
            table_rows.append({
                "Valuta": currency,
                "Regime": "⚠️ N/A",
                "PMI Comp.": "N/A",
                "PMI Avg 3m": "N/A",
                "Δ PMI": "N/A",
                "CPI Head": "N/A",
                "Infl Avg 3m": "N/A",
                "Δ Infl.": "N/A",
                "Mom. PMI": "-",
                "Mom. Infl.": "-"
            })
            continue
        
        regime = data.get("regime", "N/A")
        regime_info = data.get("regime_info", {})
        regime_emoji = regime_info.get("emoji", "❓") if regime_info else "❓"
        regime_name = regime_info.get("name", regime) if regime_info else regime
        
        row = {
            "Valuta": currency,
            "Regime": f"{regime_emoji} {regime_name}",
            "PMI Comp.": f"{data.get('pmi_composite', 'N/A'):.1f}" if data.get('pmi_composite') else "N/A",
            "PMI Avg 3m": f"{data.get('pmi_avg_3m', 'N/A'):.1f}" if data.get('pmi_avg_3m') else "N/A",
            "Δ PMI": f"{data.get('delta_pmi', 0):+.1f}" if data.get('delta_pmi') is not None else "N/A",
            "CPI Head": f"{data.get('cpi_headline', 'N/A'):.1f}%" if data.get('cpi_headline') else "N/A",
            "Infl Avg 3m": f"{data.get('inflation_avg_3m', 'N/A'):.1f}%" if data.get('inflation_avg_3m') else "N/A",
            "Δ Infl.": f"{data.get('delta_inflation', 0):+.1f}" if data.get('delta_inflation') is not None else "N/A",
            "Mom. PMI": data.get("momentum_pmi", "-"),
            "Mom. Infl.": data.get("momentum_inflation", "-")
        }
        table_rows.append(row)
        
        # Raccogli alert divergenze
        if data.get("divergence"):
            div = data["divergence"]
            divergence_alerts.append(f"**{currency}**: {div.get('emoji', '⚠️')} {div.get('message', '')}")
    
    # Mostra tabella
    if table_rows:
        df = pd.DataFrame(table_rows)
        
        # Funzione per colorare in base al regime
        def style_regime_table(row):
            styles = [''] * len(row)
            regime_cell = row["Regime"]
            
            if "Espansione" in regime_cell:
                styles[1] = 'background-color: #d1fae5; color: #059669; font-weight: bold'
            elif "Reflazione" in regime_cell:
                styles[1] = 'background-color: #fef3c7; color: #d97706; font-weight: bold'
            elif "Stagflazione" in regime_cell:
                styles[1] = 'background-color: #fee2e2; color: #dc2626; font-weight: bold'
            elif "Deflazione" in regime_cell:
                styles[1] = 'background-color: #e0e7ff; color: #4f46e5; font-weight: bold'
            
            return styles
        
        styled_df = df.style.apply(style_regime_table, axis=1)
        st.dataframe(styled_df, use_container_width=True, hide_index=True)
    
    # === ALERT DIVERGENZE ===
    if divergence_alerts:
        st.markdown("#### ⚠️ Alert Divergenze CPI")
        for alert in divergence_alerts:
            st.warning(alert)
    
    # Legenda
    st.caption("""
    **Legenda:** PMI Comp. = PMI Composito (ponderato Manufacturing + Services) | 
    Δ = variazione vs media 3 mesi | Mom. = Momentum (⬆️⬆️ forte aumento, ⬆️ aumento, ↗️ leggero, ➡️ stabile, ↘️ leggero calo, ⬇️ calo, ⬇️⬇️ forte calo)
    """)
    
    # === EXPANDER VERIFICA DATI ===
    with st.expander("🔍 Verifica Calcoli (dettagli)", expanded=False):
        st.markdown("#### Formula Calcolo Regimi")
        st.markdown("""
        ```
        Δ PMI = PMI Composite attuale - Media PMI ultimi 3 mesi
        Δ Inflazione = Indice Inflazione attuale - Media Inflazione ultimi 3 mesi
        
        Indice Inflazione = (CPI Core × 0.7) + (CPI Headline × 0.3)
        (se Core non disponibile, usa solo Headline)
        
        Regimi e Punteggi Forex:
        - Espansione:   Δ PMI > 0  E  Δ Inflazione < 0  → +1 (economia cresce)
        - Reflazione:   Δ PMI > 0  E  Δ Inflazione > 0  → +2 (BC alza tassi)
        - Stagflazione: Δ PMI < 0  E  Δ Inflazione > 0  → -2 (BC paralizzata)
        - Deflazione:   Δ PMI < 0  E  Δ Inflazione < 0  → -1 (BC taglia tassi)
        ```
        """)
        
        st.markdown("#### Dettagli per Valuta")
        
        for currency in currency_order:
            data = regimes_data.get(currency, {})
            if not data or data.get("error"):
                continue
            
            with st.container():
                st.markdown(f"**{currency}**")
                
                col_a, col_b = st.columns(2)
                
                with col_a:
                    st.markdown("**PMI:**")
                    pmi_manuf = data.get("pmi_manufacturing", "N/A")
                    pmi_serv = data.get("pmi_services", "N/A")
                    pmi_comp = data.get("pmi_composite", "N/A")
                    pmi_avg = data.get("pmi_avg_3m", "N/A")
                    delta_pmi = data.get("delta_pmi", "N/A")
                    
                    # Pesi PMI
                    weights = {"USD": "30/70", "EUR": "50/50", "GBP": "20/80", 
                              "JPY": "60/40", "CHF": "100/0", "AUD": "50/50", "CAD": "100/0"}
                    
                    st.text(f"  Manuf: {pmi_manuf}")
                    st.text(f"  Services: {pmi_serv if pmi_serv else '-'}")
                    st.text(f"  Pesi M/S: {weights.get(currency, '50/50')}")
                    st.text(f"  Composite: {pmi_comp}")
                    st.text(f"  Media 3m: {pmi_avg}")
                    st.text(f"  Δ PMI: {delta_pmi:+.2f}" if isinstance(delta_pmi, (int, float)) else f"  Δ PMI: {delta_pmi}")
                
                with col_b:
                    st.markdown("**Inflazione:**")
                    cpi_head = data.get("cpi_headline", "N/A")
                    cpi_core = data.get("cpi_core", "N/A")
                    infl_idx = data.get("inflation_index", "N/A")
                    infl_avg = data.get("inflation_avg_3m", "N/A")
                    delta_infl = data.get("delta_inflation", "N/A")
                    
                    st.text(f"  CPI Headline: {cpi_head}%")
                    st.text(f"  CPI Core: {cpi_core}%" if cpi_core else "  CPI Core: -")
                    st.text(f"  Indice (0.7C+0.3H): {infl_idx:.2f}%" if isinstance(infl_idx, (int, float)) else f"  Indice: {infl_idx}")
                    st.text(f"  Media 3m: {infl_avg}%")
                    st.text(f"  Δ Infl: {delta_infl:+.2f}" if isinstance(delta_infl, (int, float)) else f"  Δ Infl: {delta_infl}")
                
                st.markdown("---")


def display_central_bank_history(history_data: dict = None):
    """
    Mostra la tabella storico decisioni delle banche centrali.
    Con colori: verde = hike, rosso = cut
    
    Args:
        history_data: Dati storico già recuperati (opzionale). Se None, usa sessione o recupera.
    """
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


# ============================================================================
# FUNZIONI LAYOUT NUOVO (Dati Input + Calendario)
# ============================================================================

def render_data_section(
    title: str,
    icon: str,
    data_type: str,
    data: dict | list | None,
    timestamp: datetime | None,
    user_id: str,
    display_func: callable,
    fetch_func: callable,
    extra_content: callable = None
) -> bool:
    """
    Renderizza una sezione dati con header, stato freshness e bottone aggiorna.
    
    Returns:
        True se l'utente ha cliccato "Aggiorna"
    """
    # Calcola stato freshness
    freshness = check_data_freshness(data_type, timestamp)
    
    # Header con titolo e bottone
    col_title, col_status, col_btn = st.columns([3, 2, 1])
    
    with col_title:
        st.markdown(f"### {icon} {title}")
    
    with col_status:
        if timestamp:
            ts_str = timestamp.strftime("%d/%m %H:%M") if hasattr(timestamp, 'strftime') else str(timestamp)
            st.caption(f"📅 {ts_str} - {freshness['status']} {freshness['message']}")
        else:
            st.caption(f"📅 Mai aggiornato - {freshness['status']}")
    
    with col_btn:
        update_clicked = st.button(f"🔄", key=f"update_{data_type}", help=f"Aggiorna {title}")
    
    # Se cliccato aggiorna, esegui il fetch
    if update_clicked:
        with st.spinner(f"Aggiornamento {title}..."):
            try:
                new_data = fetch_func()
                st.session_state[f'last_{data_type}_data'] = new_data
                save_data_timestamp(data_type, user_id)
                st.success(f"✅ {title} aggiornati!")
                st.rerun()
            except Exception as e:
                st.error(f"❌ Errore: {str(e)[:100]}")
        return True
    
    # Mostra contenuto
    if data:
        display_func(data)
    else:
        st.info(f"ℹ️ Nessun dato disponibile. Clicca 🔄 per aggiornare.")
    
    # Contenuto extra (es. link aggiuntivi per la sezione news)
    if extra_content:
        extra_content()
    
    st.markdown("---")
    return False


def render_calendar_sidebar(user_id: str, analyses_list: list) -> dict | None:
    """
    Renderizza il calendario nella sidebar con le date delle analisi evidenziate.
    
    Returns:
        L'analisi selezionata se l'utente clicca su una data, None altrimenti
    """
    st.markdown("### 📂 Storico Analisi")
    
    # Costruisci mappa date -> analisi
    analyses_by_date = {}
    for analysis in analyses_list:
        dt_str = analysis.get("analysis_datetime", "")
        if not dt_str:
            data_obj = analysis.get("data", {})
            if isinstance(data_obj, dict):
                dt_str = data_obj.get("analysis_datetime", "")
        
        if dt_str:
            try:
                # Formato: 2025-01-21_14-30-00
                date_part = dt_str.split("_")[0]
                if date_part not in analyses_by_date:
                    analyses_by_date[date_part] = []
                analyses_by_date[date_part].append(analysis)
            except:
                pass
    
    # Ottieni mese/anno corrente o selezionato
    now = get_italy_now()
    
    if 'calendar_year' not in st.session_state:
        st.session_state['calendar_year'] = now.year
    if 'calendar_month' not in st.session_state:
        st.session_state['calendar_month'] = now.month
    
    year = st.session_state['calendar_year']
    month = st.session_state['calendar_month']
    
    # Navigazione mese
    col_prev, col_month, col_next = st.columns([1, 2, 1])
    
    with col_prev:
        if st.button("◀", key="cal_prev"):
            if month == 1:
                st.session_state['calendar_month'] = 12
                st.session_state['calendar_year'] = year - 1
            else:
                st.session_state['calendar_month'] = month - 1
            st.rerun()
    
    with col_month:
        month_names = ["", "Gen", "Feb", "Mar", "Apr", "Mag", "Giu", 
                       "Lug", "Ago", "Set", "Ott", "Nov", "Dic"]
        st.markdown(f"**{month_names[month]} {year}**")
    
    with col_next:
        if st.button("▶", key="cal_next"):
            if month == 12:
                st.session_state['calendar_month'] = 1
                st.session_state['calendar_year'] = year + 1
            else:
                st.session_state['calendar_month'] = month + 1
            st.rerun()
    
    # Genera calendario
    cal = calendar.Calendar(firstweekday=0)  # Lunedì = 0
    month_days = cal.monthdayscalendar(year, month)
    
    # Costruisci calendario come HTML per evitare deformazioni
    calendar_html = """
    <style>
    .cal-table { width: 100%; border-collapse: collapse; font-size: 14px; }
    .cal-table th { padding: 4px; text-align: center; color: #6b7280; font-weight: normal; }
    .cal-table td { padding: 6px 4px; text-align: center; }
    .cal-day { color: #9ca3af; }
    .cal-day-analysis { color: #10b981; font-weight: bold; }
    .cal-day-today { color: #3b82f6; font-weight: bold; }
    .cal-day-today-analysis { color: #10b981; font-weight: bold; text-decoration: underline; }
    </style>
    <table class="cal-table">
    <tr><th>Lu</th><th>Ma</th><th>Me</th><th>Gi</th><th>Ve</th><th>Sa</th><th>Do</th></tr>
    """
    
    dates_with_analysis = []
    
    for week in month_days:
        calendar_html += "<tr>"
        for day in week:
            if day == 0:
                calendar_html += "<td></td>"
            else:
                date_str = f"{year}-{month:02d}-{day:02d}"
                is_today = (day == now.day and month == now.month and year == now.year)
                has_analysis = date_str in analyses_by_date
                
                if has_analysis:
                    dates_with_analysis.append(date_str)
                
                if has_analysis and is_today:
                    css_class = "cal-day-today-analysis"
                elif has_analysis:
                    css_class = "cal-day-analysis"
                elif is_today:
                    css_class = "cal-day-today"
                else:
                    css_class = "cal-day"
                
                calendar_html += f'<td class="{css_class}">{day}</td>'
        calendar_html += "</tr>"
    
    calendar_html += "</table>"
    
    st.markdown(calendar_html, unsafe_allow_html=True)
    
    # Legenda
    st.caption("🟢 Analisi salvata | 🔵 Oggi")
    
    st.markdown("---")
    
    # Selectbox per scegliere data con analisi
    selected_analysis = None
    
    if dates_with_analysis:
        # Ordina date in ordine decrescente (più recenti prima)
        dates_with_analysis.sort(reverse=True)
        
        # Crea opzioni leggibili
        date_options = ["-- Seleziona data --"] + [
            datetime.strptime(d, "%Y-%m-%d").strftime("%d/%m/%Y") 
            for d in dates_with_analysis
        ]
        
        selected_date_display = st.selectbox(
            "📅 Carica analisi:",
            date_options,
            key="calendar_date_select"
        )
        
        if selected_date_display != "-- Seleziona data --":
            # Converti in formato originale
            selected_date = datetime.strptime(selected_date_display, "%d/%m/%Y").strftime("%Y-%m-%d")
            
            if st.button("📂 Carica", use_container_width=True, key="load_analysis_btn"):
                if selected_date in analyses_by_date:
                    selected_analysis = analyses_by_date[selected_date][0]
    else:
        st.caption("Nessuna analisi in questo mese")
    
    # Pulsante per tornare a oggi
    st.markdown("---")
    if st.button("📅 Vai a Oggi", use_container_width=True):
        st.session_state['calendar_year'] = now.year
        st.session_state['calendar_month'] = now.month
        st.rerun()
    
    return selected_analysis


def render_additional_links_section(user_id: str) -> tuple[str, list]:
    """
    Renderizza la sezione link aggiuntivi dentro la sezione news.
    
    Returns:
        (additional_text, links_structured)
    """
    additional_text = ""
    links_structured = []
    
    with st.expander("📎 Link Aggiuntivi (opzionale)", expanded=False):
        urls = st.text_area(
            "Inserisci URL (uno per riga)",
            height=80,
            placeholder="https://federalreserve.gov/...\nhttps://reuters.com/...",
            help="Max 10 URL",
            key="additional_urls_input"
        )
        
        if urls.strip():
            url_list = [u.strip() for u in urls.split('\n') if u.strip().startswith('http')]
            st.info(f"📌 {len(url_list)} URL inseriti")
            
            if st.button("🔄 Processa Link", key="process_links"):
                with st.spinner("Elaborazione link..."):
                    try:
                        additional_text, links_structured = fetch_additional_resources(url_list)
                        st.session_state['last_links_text'] = additional_text
                        st.session_state['last_links_structured'] = links_structured
                        st.success(f"✅ {len(links_structured)} link processati")
                    except Exception as e:
                        st.error(f"❌ Errore: {str(e)[:100]}")
        
        # Mostra link già processati
        if 'last_links_structured' in st.session_state and st.session_state['last_links_structured']:
            st.caption(f"📎 {len(st.session_state['last_links_structured'])} link già processati")
            links_structured = st.session_state['last_links_structured']
            additional_text = st.session_state.get('last_links_text', '')
    
    return additional_text, links_structured


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
            summary = data.get("summary", "")  # Non troncare più
            
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
        
        # Mostra tabella con column_config per espandere la sintesi
        import pandas as pd
        df_currencies = pd.DataFrame(currency_rows)
        
        currency_column_config = {
            "Valuta": st.column_config.TextColumn("Valuta", width="small"),
            "Score": st.column_config.TextColumn("Score", width="small"),
            "Forza": st.column_config.TextColumn("Forza", width="small"),
            "Sintesi": st.column_config.TextColumn("Sintesi", width="large"),
        }
        
        st.dataframe(
            df_currencies, 
            use_container_width=True, 
            hide_index=True,
            column_config=currency_column_config
        )
        
        # Expander per vedere lo storico punteggi per ogni valuta
        with st.expander("📈 Storico punteggi per valuta"):
            # Recupera storico (usa user_id da session_state)
            user_id_for_history = st.session_state.get('user_id', 'default')
            scores_history = get_currency_scores_history(user_id_for_history, limit=100)
            
            # Verifica se ci sono dati
            max_data_points = max(len(h) for h in scores_history.values()) if scores_history else 0
            
            if max_data_points > 1:
                # Trova range date disponibili
                all_dates = []
                for curr_hist in scores_history.values():
                    for h in curr_hist:
                        if h.get("date_obj"):
                            all_dates.append(h["date_obj"])
                
                if all_dates:
                    min_date = min(all_dates).date()
                    max_date = max(all_dates).date()
                else:
                    min_date = datetime.now().date() - timedelta(days=30)
                    max_date = datetime.now().date()
                
                # --- CONTROLLI ---
                col_curr1, col_curr2 = st.columns([1, 1])
                
                currencies_list = ["USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD"]
                
                with col_curr1:
                    primary_currency = st.selectbox(
                        "📊 Valuta principale",
                        currencies_list,
                        index=0,
                        key="hist_primary_curr"
                    )
                
                with col_curr2:
                    compare_enabled = st.checkbox("Confronta con", key="hist_compare_enabled")
                    if compare_enabled:
                        compare_options = [c for c in currencies_list if c != primary_currency]
                        secondary_currency = st.selectbox(
                            "Seconda valuta",
                            compare_options,
                            index=0,
                            key="hist_secondary_curr",
                            label_visibility="collapsed"
                        )
                    else:
                        secondary_currency = None
                
                # Date picker
                col_date1, col_date2 = st.columns(2)
                with col_date1:
                    date_from = st.date_input(
                        "📅 Da",
                        value=min_date,
                        min_value=min_date,
                        max_value=max_date,
                        key="hist_date_from"
                    )
                with col_date2:
                    date_to = st.date_input(
                        "📅 A",
                        value=max_date,
                        min_value=min_date,
                        max_value=max_date,
                        key="hist_date_to"
                    )
                
                # --- FILTRA DATI PER DATE ---
                def filter_by_date(history_list, from_date, to_date):
                    filtered = []
                    for h in history_list:
                        if h.get("date_obj"):
                            h_date = h["date_obj"].date()
                            if from_date <= h_date <= to_date:
                                filtered.append(h)
                    return filtered
                
                primary_history = filter_by_date(scores_history.get(primary_currency, []), date_from, date_to)
                
                if len(primary_history) > 0:
                    # Crea DataFrame per grafico
                    chart_data = []
                    all_scores = []
                    
                    for h in primary_history:
                        chart_data.append({
                            "Data": h["date"],
                            "Punteggio": h["score"],
                            "Valuta": primary_currency
                        })
                        all_scores.append(h["score"])
                    
                    # Aggiungi seconda valuta se abilitata
                    if compare_enabled and secondary_currency:
                        secondary_history = filter_by_date(scores_history.get(secondary_currency, []), date_from, date_to)
                        for h in secondary_history:
                            chart_data.append({
                                "Data": h["date"],
                                "Punteggio": h["score"],
                                "Valuta": secondary_currency
                            })
                            all_scores.append(h["score"])
                    
                    chart_df = pd.DataFrame(chart_data)
                    
                    # Calcola scala Y dinamica con margine
                    if all_scores:
                        y_min = min(all_scores) - 2
                        y_max = max(all_scores) + 2
                        # Assicurati che zero sia sempre visibile se i dati lo attraversano
                        if min(all_scores) <= 0 <= max(all_scores):
                            y_min = min(y_min, -1)
                            y_max = max(y_max, 1)
                    else:
                        y_min, y_max = -5, 5
                    
                    # --- GRAFICO CON ALTAIR ---
                    try:
                        import altair as alt
                        
                        # Definisci colori
                        if compare_enabled and secondary_currency:
                            color_scale = alt.Scale(
                                domain=[primary_currency, secondary_currency],
                                range=['#1f77b4', '#ff7f0e']
                            )
                        else:
                            color_scale = alt.Scale(
                                domain=[primary_currency],
                                range=['#1f77b4']
                            )
                        
                        # Grafico principale con scala Y dinamica
                        line_chart = alt.Chart(chart_df).mark_line(
                            point=alt.OverlayMarkDef(size=60),
                            strokeWidth=2.5
                        ).encode(
                            x=alt.X('Data:N', title='Data', sort=None),
                            y=alt.Y('Punteggio:Q', title='Punteggio', 
                                   scale=alt.Scale(domain=[y_min, y_max])),
                            color=alt.Color('Valuta:N', scale=color_scale, legend=alt.Legend(title="Valuta")),
                            tooltip=['Data', 'Valuta', 'Punteggio']
                        ).properties(
                            height=400
                        )
                        
                        # Linea zero di riferimento
                        zero_df = pd.DataFrame({'zero': [0]})
                        zero_line = alt.Chart(zero_df).mark_rule(
                            strokeDash=[5, 5],
                            color='gray',
                            strokeWidth=1
                        ).encode(
                            y='zero:Q'
                        )
                        
                        # Combina i grafici
                        final_chart = alt.layer(zero_line, line_chart).configure_axis(
                            labelFontSize=12,
                            titleFontSize=14
                        )
                        
                        st.altair_chart(final_chart, use_container_width=True)
                        
                    except ImportError:
                        st.line_chart(
                            chart_df.pivot(index="Data", columns="Valuta", values="Punteggio"),
                            use_container_width=True,
                            height=400
                        )
                    
                    # --- STATISTICHE IN TOGGLE COMPATTO ---
                    with st.expander("📊 Statistiche", expanded=False):
                        if compare_enabled and secondary_currency:
                            secondary_history_stats = filter_by_date(scores_history.get(secondary_currency, []), date_from, date_to)
                            
                            # Riga unica con tutte le statistiche
                            scores_1 = [h["score"] for h in primary_history]
                            scores_2 = [h["score"] for h in secondary_history_stats] if secondary_history_stats else []
                            
                            col1, col2 = st.columns(2)
                            with col1:
                                if scores_1:
                                    avg_1 = sum(scores_1) / len(scores_1)
                                    trend_1 = scores_1[-1] - scores_1[0] if len(scores_1) > 1 else 0
                                    st.caption(f"**{primary_currency}** 🔵: Media {avg_1:.1f} | Min {min(scores_1):+d} | Max {max(scores_1):+d} | Trend {trend_1:+d}")
                            with col2:
                                if scores_2:
                                    avg_2 = sum(scores_2) / len(scores_2)
                                    trend_2 = scores_2[-1] - scores_2[0] if len(scores_2) > 1 else 0
                                    st.caption(f"**{secondary_currency}** 🟠: Media {avg_2:.1f} | Min {min(scores_2):+d} | Max {max(scores_2):+d} | Trend {trend_2:+d}")
                        else:
                            scores_1 = [h["score"] for h in primary_history]
                            if scores_1:
                                avg_1 = sum(scores_1) / len(scores_1)
                                trend_1 = scores_1[-1] - scores_1[0] if len(scores_1) > 1 else 0
                                st.caption(f"**{primary_currency}**: Media {avg_1:.1f} | Min {min(scores_1):+d} | Max {max(scores_1):+d} | Trend {trend_1:+d}")
                else:
                    st.info(f"Nessun dato nel periodo selezionato per {primary_currency}.")
            else:
                st.info("Storico non disponibile. Esegui più analisi per vedere i grafici.")
        
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
        
        # ===== MOSTRA CORREZIONI PUNTEGGI =====
        if "score_corrections" in analysis:
            corrections = analysis["score_corrections"]
            with st.expander(f"🔧 Correzioni punteggi automatiche ({len(corrections)})", expanded=False):
                st.warning("I seguenti punteggi sono stati corretti automaticamente per violazione delle regole:")
                for correction in corrections:
                    st.markdown(f"- {correction}")
        
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
            
            # Mappa nomi parametri con range (ORDINE IMPORTANTE!)
            param_names = {
                "tassi_attuali": "🏦 Tassi Attuali [-1/+1]",
                "regime_economico": "🎯 Regime Economico [-2/+2]",
                "aspettative_tassi": "📈 Aspettative Tassi [-1/+1]",
                "inflazione": "💰 Inflazione [-1/+1]",
                "crescita_pil": "📊 Crescita/PIL [-1/+1]",
                "risk_sentiment": "⚠️ Risk Sentiment [-1/+1]",
                "cot_score": "📊 COT Score [-2/+2]",
                "news_bonus": "📰 News Bonus [-1/+1]"
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
            current_price = pair_data.get("current_price", "")
            key_drivers = pair_data.get("key_drivers", [])
            
            # Mostra solo se abbiamo dati validi
            has_valid_price = current_price and current_price not in ["", "N/A", "None"]
            has_scenarios = price_scenarios and any(price_scenarios.values())
            
            if has_valid_price or has_scenarios:
                st.markdown("### 📊 Scenari di Prezzo")
                
                # Box prezzo attuale (solo se valido)
                if has_valid_price:
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
    
    # ===== CARICA DATI E TIMESTAMPS =====
    
    # Carica dati esistenti dalla sessione o dall'ultima analisi
    cached_data = {}
    
    # Se non abbiamo dati in sessione, carica dall'ultima analisi
    if not st.session_state.get('last_macro_data'):
        cached_data = get_latest_analysis_data(user_id)
        
        # Se abbiamo trovato dati in cache, salvali in session_state
        if cached_data:
            if cached_data.get('macro_data'):
                st.session_state['last_macro_data'] = cached_data['macro_data']
            if cached_data.get('pmi_data'):
                st.session_state['last_pmi_data'] = cached_data['pmi_data']
            if cached_data.get('cb_history_data'):
                st.session_state['last_cb_history'] = cached_data['cb_history_data']
            if cached_data.get('forex_prices'):
                st.session_state['last_forex_prices'] = cached_data['forex_prices']
            if cached_data.get('news_structured'):
                st.session_state['last_news_structured'] = cached_data['news_structured']
            if cached_data.get('regimes_data'):
                st.session_state['last_regimes_data'] = cached_data['regimes_data']
            
            # Imposta anche i timestamps dalla data dell'analisi
            if cached_data.get('cached_datetime'):
                try:
                    cached_dt = datetime.strptime(cached_data['cached_datetime'], "%Y-%m-%d_%H-%M-%S")
                    if ITALY_TZ:
                        cached_dt = cached_dt.replace(tzinfo=ITALY_TZ)
                    
                    # Imposta timestamp per ogni tipo di dato presente
                    if cached_data.get('macro_data'):
                        st.session_state['timestamp_macro'] = cached_dt
                    if cached_data.get('cb_history_data'):
                        st.session_state['timestamp_cb_history'] = cached_dt
                    if cached_data.get('pmi_data'):
                        st.session_state['timestamp_pmi'] = cached_dt
                    if cached_data.get('forex_prices'):
                        st.session_state['timestamp_prices'] = cached_dt
                    if cached_data.get('news_structured'):
                        st.session_state['timestamp_news'] = cached_dt
                    if cached_data.get('regimes_data'):
                        st.session_state['timestamp_regimes'] = cached_dt
                except:
                    pass
    
    # Ora recupera i dati dalla sessione
    macro_data = st.session_state.get('last_macro_data')
    pmi_data = st.session_state.get('last_pmi_data')
    cb_history_data = st.session_state.get('last_cb_history')
    forex_prices = st.session_state.get('last_forex_prices')
    news_structured = st.session_state.get('last_news_structured', {})
    
    # Carica timestamps e calcola freshness
    timestamps = load_data_timestamps(user_id)
    all_fresh, freshness_details = get_all_data_freshness(timestamps)
    
    # --- SIDEBAR (Solo calendario) ---
    with st.sidebar:
        # Status compatto
        st.markdown(f"### 👤 {username}")
        
        if API_KEY_LOADED:
            st.caption("✅ API Claude OK")
        else:
            st.caption("❌ API Key mancante")
        
        # Status moduli
        modules_status = []
        if REGIMES_MODULE_LOADED:
            modules_status.append("Regimi ✅")
        else:
            modules_status.append("Regimi ❌")
        if COT_MODULE_LOADED:
            modules_status.append("COT ✅")
        else:
            modules_status.append("COT ❌")
        st.caption(" | ".join(modules_status))
        
        st.markdown("---")
        
        # Calendario analisi
        user_analyses = get_user_analyses(user_id, limit=60)
        selected_from_calendar = render_calendar_sidebar(user_id, user_analyses)
        
        # Se selezionata un'analisi dal calendario, caricala
        if selected_from_calendar:
            st.session_state['current_analysis'] = selected_from_calendar
            st.session_state['analysis_source'] = 'loaded'
            st.session_state['viewing_historical'] = True
            st.rerun()
    
    # ===== BANNER SE VISUALIZZANDO ANALISI STORICA =====
    if st.session_state.get('viewing_historical'):
        analysis = st.session_state.get('current_analysis', {})
        dt_str = analysis.get('analysis_datetime', analysis.get('data', {}).get('analysis_datetime', ''))
        
        col_banner, col_close = st.columns([5, 1])
        with col_banner:
            st.warning(f"📂 **Visualizzando analisi storica del {format_datetime_display(dt_str)}**")
        with col_close:
            if st.button("✕ Chiudi", type="secondary"):
                st.session_state['viewing_historical'] = False
                if 'current_analysis' in st.session_state:
                    del st.session_state['current_analysis']
                st.rerun()
        
        # Mostra i dati dell'analisi storica
        data_container = analysis.get('data', analysis)
        
        st.markdown("## 📊 Dati dell'analisi storica")
        
        # --- SEZIONE 1: Macro ---
        if data_container.get('macro_data'):
            st.markdown("### 📊 Dati Macro")
            display_macro_data(data_container['macro_data'])
            st.markdown("---")
        
        # --- SEZIONE 2: Regimi Economici (con PMI in toggle) ---
        regimes_data = data_container.get('regimes_data')
        pmi_data = data_container.get('pmi_data')
        
        if regimes_data or pmi_data:
            st.markdown("### 🎯 Regimi Economici")
            
            if regimes_data and REGIMES_MODULE_LOADED:
                display_economic_regimes(regimes_data)
                
                # Toggle per vedere i dati PMI grezzi
                if pmi_data:
                    with st.expander("📈 Visualizza Dati PMI Grezzi", expanded=False):
                        display_pmi_table(pmi_data)
            elif pmi_data:
                # Solo PMI se non ci sono regimi
                st.info("ℹ️ Regimi non calcolati in questa analisi.")
                with st.expander("📈 Visualizza Dati PMI", expanded=True):
                    display_pmi_table(pmi_data)
            
            st.markdown("---")
        
        # --- SEZIONE 3: Storico BC ---
        if data_container.get('cb_history_data'):
            st.markdown("### 🏦 Storico Banche Centrali")
            display_central_bank_history(data_container['cb_history_data'])
            st.markdown("---")
        
        # --- SEZIONE 3.5: COT Data ---
        if data_container.get('cot_data') and COT_MODULE_LOADED:
            st.markdown("### 📊 COT Non-Commercial (Speculatori)")
            display_cot_data(data_container['cot_data'])
            st.markdown("---")
        
        # --- SEZIONE 4: Prezzi Forex ---
        if data_container.get('forex_prices'):
            st.markdown("### 💱 Prezzi Forex")
            display_forex_prices(data_container['forex_prices'])
            st.markdown("---")
        
        # --- SEZIONE 5: Notizie ---
        if data_container.get('news_structured'):
            st.markdown("### 📰 Notizie")
            display_news_summary(data_container['news_structured'], data_container.get('links_structured'))
            st.markdown("---")
        
        # --- Analisi Claude storica ---
        if data_container.get('claude_analysis'):
            display_analysis_matrix(data_container['claude_analysis'])
        
        return  # Stop qui se visualizzando analisi storica
    
    # ===== MAIN AREA - DATI INPUT =====
    col_main_title, col_main_btn = st.columns([6, 1])
    with col_main_title:
        st.markdown("## 📊 Dati di Input")
    with col_main_btn:
        if st.button("🔄 Tutto", key="upd_all", help="Aggiorna tutti i dati"):
            with st.spinner("Aggiornamento di tutti i dati..."):
                progress_all = st.progress(0, text="Aggiornamento Macro...")
                
                # 1. Macro
                new_macro = fetch_macro_data()
                st.session_state['last_macro_data'] = new_macro
                st.session_state['timestamp_macro'] = get_italy_now()
                save_data_timestamp('macro', user_id)
                progress_all.progress(15, text="Aggiornamento PMI e Regimi...")
                
                # 2. PMI + Regimi
                new_pmi_data = fetch_all_pmi_data()
                st.session_state['last_pmi_data'] = new_pmi_data
                st.session_state['timestamp_pmi'] = get_italy_now()
                save_data_timestamp('pmi', user_id)
                
                if REGIMES_MODULE_LOADED:
                    pmi_for_regimes = {}
                    for curr, data in new_pmi_data.items():
                        pmi_for_regimes[curr] = {
                            "manufacturing": data.get("manufacturing", {}).get("current"),
                            "services": data.get("services", {}).get("current")
                        }
                    regimes_result = analyze_all_regimes(pmi_for_regimes)
                    if SUPABASE_ENABLED:
                        for currency, regime_data in regimes_result.items():
                            if not regime_data.get("error"):
                                save_regime_to_supabase(supabase_request, currency, regime_data)
                    st.session_state['last_regimes_data'] = regimes_result
                    st.session_state['timestamp_regimes'] = get_italy_now()
                progress_all.progress(40, text="Aggiornamento Storico BC...")
                
                # 3. Storico BC
                new_cb = get_central_bank_history_summary()
                st.session_state['last_cb_history'] = new_cb
                st.session_state['timestamp_cb_history'] = get_italy_now()
                save_data_timestamp('cb_history', user_id)
                progress_all.progress(50, text="Aggiornamento COT Data...")
                
                # 3.5 COT Data
                if COT_MODULE_LOADED:
                    try:
                        cot_manager = COTDataManager(supabase_request if SUPABASE_ENABLED else None)
                        cot_result = cot_manager.fetch_and_update()
                        st.session_state['last_cot_data'] = cot_result
                        st.session_state['timestamp_cot'] = get_italy_now()
                        save_data_timestamp('cot', user_id)
                    except Exception as e:
                        st.session_state['last_cot_data'] = {'status': 'error', 'message': str(e)}
                progress_all.progress(65, text="Aggiornamento Prezzi...")
                
                # 4. Prezzi Forex
                new_prices = fetch_forex_prices()
                st.session_state['last_forex_prices'] = new_prices
                st.session_state['timestamp_prices'] = get_italy_now()
                save_data_timestamp('prices', user_id)
                progress_all.progress(85, text="Aggiornamento Notizie...")
                
                # 5. Notizie
                new_news, new_structured = search_web_news()
                
                # Aggiungi ForexFactory news
                ff_news = fetch_forexfactory_news()
                if ff_news.get("success") and ff_news.get("news"):
                    new_structured["forexfactory_direct"] = ff_news["news"]
                
                st.session_state['last_news_text'] = new_news
                st.session_state['last_news_structured'] = new_structured
                st.session_state['timestamp_news'] = get_italy_now()
                save_data_timestamp('news', user_id)
                
                progress_all.progress(100, text="✅ Tutti i dati aggiornati!")
                time.sleep(0.5)
                st.rerun()
    
    # --- SEZIONE 1: DATI MACRO ---
    col_title1, col_status1, col_btn1 = st.columns([3, 3, 1])
    with col_title1:
        st.markdown("### 📊 Dati Macro")
    with col_status1:
        f = freshness_details.get('macro', {})
        ts = timestamps.get('macro')
        ts_str = ts.strftime("%d/%m %H:%M") if ts else "Mai"
        st.caption(f"📅 {ts_str} - {f.get('status', '🟠')} {f.get('message', 'N/A')}")
    with col_btn1:
        if st.button("🔄", key="upd_macro", help="Aggiorna Dati Macro"):
            with st.spinner("Aggiornamento..."):
                new_data = fetch_macro_data()
                st.session_state['last_macro_data'] = new_data
                st.session_state['timestamp_macro'] = get_italy_now()
                save_data_timestamp('macro', user_id)
                st.rerun()
    
    if macro_data:
        display_macro_data(macro_data)
    else:
        st.info("ℹ️ Nessun dato. Clicca 🔄 per aggiornare.")
    st.markdown("---")
    
    # --- SEZIONE 2: REGIMI ECONOMICI (con PMI integrato) ---
    if REGIMES_MODULE_LOADED:
        # Carica regimi da Supabase se non presenti in session_state
        if 'last_regimes_data' not in st.session_state and SUPABASE_ENABLED:
            try:
                cached_regimes, cached_ts = get_all_current_regimes(supabase_request)
                if cached_regimes:
                    st.session_state['last_regimes_data'] = cached_regimes
                    if cached_ts:
                        # Converti a timezone Italy se necessario
                        if cached_ts.tzinfo is not None and ITALY_TZ:
                            cached_ts = cached_ts.astimezone(ITALY_TZ)
                        st.session_state['timestamp_regimes'] = cached_ts
            except:
                pass
        
        # Calcola freshness regimi
        ts_regime = st.session_state.get('timestamp_regimes')
        regimes_freshness = check_data_freshness("regimes", ts_regime)
        
        # Header con status e bottone aggiorna
        col_title_reg, col_status_reg, col_btn_reg = st.columns([3, 3, 1])
        with col_title_reg:
            st.markdown("### 🎯 Regimi Economici")
        with col_status_reg:
            ts_str = ts_regime.strftime("%d/%m %H:%M") if ts_regime else "Mai"
            st.caption(f"📅 {ts_str} - {regimes_freshness.get('status', '🟠')} {regimes_freshness.get('message', 'N/A')}")
        with col_btn_reg:
            if st.button("🔄", key="upd_regimes", help="Aggiorna Regimi Economici (recupera PMI e CPI)"):
                with st.spinner("Analisi regimi economici..."):
                    # Prima aggiorna i PMI
                    new_pmi_data = fetch_all_pmi_data()
                    st.session_state['last_pmi_data'] = new_pmi_data
                    st.session_state['timestamp_pmi'] = get_italy_now()
                    save_data_timestamp('pmi', user_id)
                    
                    # Prepara dati PMI per l'analisi regimi
                    pmi_for_regimes = {}
                    for curr, data in new_pmi_data.items():
                        pmi_for_regimes[curr] = {
                            "manufacturing": data.get("manufacturing", {}).get("current"),
                            "services": data.get("services", {}).get("current")
                        }
                    
                    # Analizza regimi
                    regimes_result = analyze_all_regimes(pmi_for_regimes)
                    
                    # Salva su Supabase se disponibile
                    if SUPABASE_ENABLED:
                        for currency, regime_data in regimes_result.items():
                            if not regime_data.get("error"):
                                save_regime_to_supabase(supabase_request, currency, regime_data)
                    
                    st.session_state['last_regimes_data'] = regimes_result
                    st.session_state['timestamp_regimes'] = get_italy_now()
                    st.rerun()
        
        # Mostra regimi
        regimes_data = st.session_state.get('last_regimes_data')
        if regimes_data:
            display_economic_regimes(regimes_data)
            
            # Toggle per vedere i dati PMI grezzi
            with st.expander("📈 Visualizza Dati PMI Grezzi", expanded=False):
                pmi_data_display = st.session_state.get('last_pmi_data')
                if pmi_data_display:
                    display_pmi_table(pmi_data_display)
                else:
                    st.info("ℹ️ Nessun dato PMI. Aggiorna i regimi per recuperare i dati.")
        else:
            st.info("ℹ️ Nessun dato regime. Clicca 🔄 per analizzare.")
        
        st.markdown("---")
    
    # --- SEZIONE 3: STORICO BC ---
    col_title2, col_status2, col_btn2 = st.columns([3, 3, 1])
    with col_title2:
        st.markdown("### 🏦 Storico Banche Centrali")
    with col_status2:
        f = freshness_details.get('cb_history', {})
        ts = timestamps.get('cb_history')
        ts_str = ts.strftime("%d/%m %H:%M") if ts else "Mai"
        st.caption(f"📅 {ts_str} - {f.get('status', '🟠')} {f.get('message', 'N/A')}")
    with col_btn2:
        if st.button("🔄", key="upd_cb", help="Aggiorna Storico BC"):
            with st.spinner("Aggiornamento..."):
                new_data = get_central_bank_history_summary()
                st.session_state['last_cb_history'] = new_data
                st.session_state['timestamp_cb_history'] = get_italy_now()
                save_data_timestamp('cb_history', user_id)
                st.rerun()
    
    if cb_history_data:
        display_central_bank_history(cb_history_data)
    else:
        st.info("ℹ️ Nessun dato. Clicca 🔄 per aggiornare.")
    st.markdown("---")
    
    # --- SEZIONE 3.5: COT DATA ---
    if COT_MODULE_LOADED:
        cot_data = st.session_state.get('last_cot_data')
        ts_cot = st.session_state.get('timestamp_cot')
        cot_freshness = check_data_freshness("cot", ts_cot)
        
        col_title_cot, col_status_cot, col_btn_cot = st.columns([3, 3, 1])
        with col_title_cot:
            st.markdown("### 📊 COT Non-Commercial (Speculatori)")
        with col_status_cot:
            ts_str = ts_cot.strftime("%d/%m %H:%M") if ts_cot else "Mai"
            st.caption(f"📅 {ts_str} - {cot_freshness.get('status', '🟠')} {cot_freshness.get('message', 'N/A')}")
        with col_btn_cot:
            if st.button("🔄", key="upd_cot", help="Aggiorna dati COT"):
                with st.spinner("Aggiornamento dati COT..."):
                    try:
                        cot_manager = COTDataManager(supabase_request if SUPABASE_ENABLED else None)
                        cot_result = cot_manager.fetch_and_update()
                        st.session_state['last_cot_data'] = cot_result
                        st.session_state['timestamp_cot'] = get_italy_now()
                        save_data_timestamp('cot', user_id)
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ Errore: {e}")
        
        if cot_data and cot_data.get('status') == 'ok':
            display_cot_data(cot_data)
        elif cot_data and cot_data.get('status') == 'error':
            st.warning(f"⚠️ Errore COT: {cot_data.get('message', 'Errore sconosciuto')}")
            if cot_data.get('debug'):
                with st.expander("🔍 Debug Log"):
                    for msg in cot_data.get('debug', [])[-5:]:
                        st.text(msg)
        else:
            st.info("ℹ️ Nessun dato COT. Clicca 🔄 per aggiornare.")
        st.markdown("---")
    
    # --- SEZIONE 3.6: RISK SENTIMENT (VIX + S&P 500) ---
    risk_sentiment_data = st.session_state.get('last_risk_sentiment')
    ts_risk = st.session_state.get('timestamp_risk_sentiment')
    risk_freshness = check_data_freshness("risk_sentiment", ts_risk)
    
    col_title_risk, col_status_risk, col_btn_risk = st.columns([3, 3, 1])
    with col_title_risk:
        st.markdown("### 📊 Risk Sentiment")
    with col_status_risk:
        ts_str = ts_risk.strftime("%d/%m %H:%M") if ts_risk else "Mai"
        st.caption(f"📅 {ts_str} - {risk_freshness.get('status', '🟠')} {risk_freshness.get('message', 'N/A')}")
    with col_btn_risk:
        if st.button("🔄", key="upd_risk", help="Aggiorna Risk Sentiment (VIX + S&P 500)"):
            with st.spinner("Recupero dati VIX e S&P 500..."):
                new_data = fetch_risk_sentiment_data()
                st.session_state['last_risk_sentiment'] = new_data
                st.session_state['timestamp_risk_sentiment'] = get_italy_now()
                st.rerun()
    
    # Mostra dati Risk Sentiment
    if risk_sentiment_data and risk_sentiment_data.get('status') == 'ok':
        # Mostra regime
        regime = risk_sentiment_data.get('regime', 'neutral')
        interpretation = risk_sentiment_data.get('interpretation', '')
        vix = risk_sentiment_data.get('vix')
        sp_change = risk_sentiment_data.get('sp500_change_pct')
        
        # Colore regime
        if regime == 'risk-on':
            regime_color = "🟢"
        elif regime == 'risk-off':
            regime_color = "🔴"
        else:
            regime_color = "⚪"
        
        st.markdown(f"**Regime: {regime_color} {regime.upper()}**")
        
        col_vix, col_sp = st.columns(2)
        with col_vix:
            if vix is not None:
                vix_color = "🟢" if vix < 15 else "🟠" if vix <= 20 else "🔴" if vix <= 25 else "🔴🔴"
                st.metric("VIX", f"{vix:.1f}", help="< 15 = basso, 15-20 = normale, 20-25 = elevato, > 25 = alto")
        with col_sp:
            if sp_change is not None:
                sp_color = "🟢" if sp_change > 1 else "🔴" if sp_change < -1 else "⚪"
                st.metric("S&P 500 Δ%", f"{sp_change:+.2f}%", help="> +1% = rally, < -1% = sell-off")
        
        # Tabella punteggi per valuta
        with st.expander("📋 Punteggi Risk Sentiment per valuta"):
            currency_scores = risk_sentiment_data.get('currency_scores', {})
            rows = []
            for curr in ['USD', 'EUR', 'GBP', 'JPY', 'CHF', 'AUD', 'CAD']:
                score_data = currency_scores.get(curr, {})
                score = score_data.get('score', 0)
                reason = score_data.get('reason', '')
                score_display = f"🟢 +{score}" if score > 0 else f"🔴 {score}" if score < 0 else f"⚪ {score}"
                rows.append({'Valuta': curr, 'Score': score_display, 'Motivo': reason})
            df_risk = pd.DataFrame(rows)
            st.dataframe(df_risk, use_container_width=True, hide_index=True)
    else:
        st.info("ℹ️ Nessun dato. Clicca 🔄 per aggiornare.")
    st.markdown("---")
    
    # --- SEZIONE 4: PREZZI FOREX ---
    col_title4, col_status4, col_btn4 = st.columns([3, 3, 1])
    with col_title4:
        st.markdown("### 💱 Prezzi Forex")
    with col_status4:
        f = freshness_details.get('prices', {})
        ts = timestamps.get('prices')
        ts_str = ts.strftime("%d/%m %H:%M") if ts else "Mai"
        st.caption(f"📅 {ts_str} - {f.get('status', '🟠')} {f.get('message', 'N/A')}")
    with col_btn4:
        if st.button("🔄", key="upd_prices", help="Aggiorna Prezzi"):
            with st.spinner("Aggiornamento..."):
                new_data = fetch_forex_prices()
                st.session_state['last_forex_prices'] = new_data
                st.session_state['timestamp_prices'] = get_italy_now()
                save_data_timestamp('prices', user_id)
                st.rerun()
    
    if forex_prices:
        display_forex_prices(forex_prices)
    else:
        st.info("ℹ️ Nessun dato. Clicca 🔄 per aggiornare.")
    st.markdown("---")
    
    # --- SEZIONE 5: NOTIZIE ---
    col_title5, col_status5, col_btn5 = st.columns([3, 3, 1])
    with col_title5:
        st.markdown("### 📰 Notizie")
    with col_status5:
        f = freshness_details.get('news', {})
        ts = timestamps.get('news')
        ts_str = ts.strftime("%d/%m %H:%M") if ts else "Mai"
        st.caption(f"📅 {ts_str} - {f.get('status', '🟠')} {f.get('message', 'N/A')}")
    with col_btn5:
        if st.button("🔄", key="upd_news", help="Aggiorna Notizie"):
            with st.spinner("Aggiornamento notizie..."):
                news_text, new_structured = search_web_news()
                
                # Aggiungi ForexFactory news
                ff_news = fetch_forexfactory_news()
                if ff_news.get("success") and ff_news.get("news"):
                    new_structured["forexfactory_direct"] = ff_news["news"]
                
                st.session_state['last_news_text'] = news_text
                st.session_state['last_news_structured'] = new_structured
                st.session_state['timestamp_news'] = get_italy_now()
                save_data_timestamp('news', user_id)
                st.rerun()
    
    if news_structured:
        display_news_summary(news_structured, st.session_state.get('last_links_structured'))
    else:
        st.info("ℹ️ Nessuna notizia. Clicca 🔄 per aggiornare.")
    
    # Link aggiuntivi (dentro sezione news)
    additional_text, links_structured = render_additional_links_section(user_id)
    
    st.markdown("---")
    
    # ===== SEZIONE ANALISI CLAUDE =====
    st.markdown("## 🤖 Analisi Claude AI")
    
    # Ricalcola freshness (potrebbe essere cambiata dopo aggiornamenti)
    timestamps = load_data_timestamps(user_id)
    all_fresh, freshness_details = get_all_data_freshness(timestamps)
    
    # Conta dati mancanti o non freschi
    not_fresh = [k for k, v in freshness_details.items() if not v.get('is_fresh', False)]
    
    if not_fresh:
        st.warning(f"⚠️ **Alcuni dati non sono aggiornati:** {', '.join(not_fresh).upper()}")
        st.caption("Aggiorna tutti i dati prima di lanciare una nuova analisi, oppure carica una vecchia analisi dal calendario.")
        can_analyze = False
    else:
        st.success("✅ Tutti i dati sono aggiornati!")
        can_analyze = API_KEY_LOADED
    
    # Bottone analisi
    if st.button(
        "🤖 AVVIA ANALISI CLAUDE",
        disabled=not can_analyze,
        use_container_width=True,
        type="primary"
    ):
        progress = st.progress(0, text="Inizializzazione...")
        
        try:
            # Recupera dati economici per News Catalyst
            progress.progress(10, text="📊 Recupero dati economici...")
            economic_events = {}
            try:
                economic_events = fetch_all_economic_events()
                st.session_state['last_economic_events'] = economic_events
            except Exception as e:
                st.warning(f"⚠️ Errore dati economici: {str(e)[:50]}")
                economic_events = st.session_state.get('last_economic_events', {})
            
            # Recupera news text per Claude
            news_text = st.session_state.get('last_news_text', '')
            if not news_text and news_structured:
                # Ricostruisci news_text dalle news structured
                news_text = ""
                for source, items in news_structured.items():
                    if isinstance(items, list):
                        for item in items[:10]:
                            if isinstance(item, dict):
                                news_text += f"• {item.get('title', '')}\n"
            
            # Link aggiuntivi
            add_text = st.session_state.get('last_links_text', '')
            
            # Dati COT
            cot_data = st.session_state.get('last_cot_data')
            
            # Dati Risk Sentiment
            risk_sentiment_data = st.session_state.get('last_risk_sentiment')
            
            # Analisi Claude
            progress.progress(30, text="🤖 Claude sta analizzando...")
            
            claude_analysis = analyze_with_claude(
                ANTHROPIC_API_KEY,
                macro_data,
                news_text,
                add_text,
                pmi_data,
                forex_prices,
                economic_events,
                cb_history_data,
                cot_data,
                risk_sentiment_data
            )
            
            # ===== INTEGRA REGIMI ECONOMICI NEI PUNTEGGI =====
            if REGIMES_MODULE_LOADED and "currency_analysis" in claude_analysis:
                regimes_data = st.session_state.get('last_regimes_data', {})
                
                if regimes_data:
                    claude_analysis["currency_analysis"] = add_regime_scores_to_analysis(
                        claude_analysis["currency_analysis"],
                        regimes_data
                    )
                else:
                    # Nessun dato regime disponibile - aggiungi score 0 per tutte le valute
                    for currency, data in claude_analysis["currency_analysis"].items():
                        if isinstance(data, dict) and "scores" in data:
                            data["scores"]["regime_economico"] = {
                                "score": 0,
                                "motivation": "Dati regime non disponibili"
                            }
                
                # Ricalcola pair_analysis con i nuovi punteggi (preserva prezzi)
                existing_pair_analysis = claude_analysis.get("pair_analysis", {})
                claude_analysis["pair_analysis"] = calculate_pair_from_currencies(
                    claude_analysis["currency_analysis"],
                    forex_prices,
                    existing_pair_analysis
                )
            
            # Salva risultato
            progress.progress(80, text="💾 Salvataggio...")
            
            # Includi regimi e COT se disponibili
            regimes_for_save = st.session_state.get('last_regimes_data', {})
            cot_for_save = st.session_state.get('last_cot_data', {})
            
            analysis_result = {
                "macro_data": macro_data,
                "pmi_data": pmi_data,
                "cb_history_data": cb_history_data,
                "forex_prices": forex_prices,
                "economic_events": economic_events,
                "news_structured": news_structured,
                "links_structured": links_structured,
                "regimes_data": regimes_for_save,  # Aggiungi regimi
                "cot_data": cot_for_save,  # Aggiungi COT
                "claude_analysis": claude_analysis,
                "options_selected": {"full": True}
            }
            
            if save_analysis(analysis_result, user_id, "full", {"full": True}):
                st.session_state['current_analysis'] = analysis_result
                st.session_state['analysis_source'] = 'new'
                progress.progress(100, text="✅ Completato!")
                st.rerun()
            else:
                progress.progress(100, text="❌ Errore salvataggio")
                
        except Exception as e:
            st.error(f"❌ Errore analisi: {str(e)}")
    
    # ===== MOSTRA ULTIMA ANALISI (se dati freschi) =====
    if all_fresh and 'current_analysis' in st.session_state:
        analysis = st.session_state['current_analysis']
        source = st.session_state.get('analysis_source', 'unknown')
        
        # Estrai claude_analysis dal container
        data_container = analysis.get('data', analysis)
        claude_analysis = data_container.get('claude_analysis')
        
        if claude_analysis:
            if source == 'new':
                st.success("✅ Nuova analisi completata!")
            
            # Mostra data analisi
            dt_str = analysis.get('analysis_datetime', data_container.get('analysis_datetime', ''))
            if dt_str:
                st.caption(f"📅 Analisi del {format_datetime_display(dt_str)}")
            
            display_analysis_matrix(claude_analysis)
    
    elif not all_fresh:
        # Messaggio quando dati non freschi
        st.info("💡 Aggiorna i dati per visualizzare/creare un'analisi, oppure carica un'analisi storica dal calendario nella sidebar.")
    
    # ===== FOOTER =====
    st.markdown("---")
    st.markdown("""
    <div style="text-align: center; color: #6b7280; font-size: 0.8rem;">
        📊 Forex Macro Analyst v4.0 | Powered by Claude AI<br>
        ⚠️ Non costituisce consiglio di investimento
    </div>
    """, unsafe_allow_html=True)

# ============================================================================
# RUN
# ============================================================================

if __name__ == "__main__":
    main()
