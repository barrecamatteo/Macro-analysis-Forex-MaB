"""
COT Data Module - Gestione dati Commitment of Traders per Forex
================================================================
Questo modulo gestisce:
- Download dati COT dalla CFTC API
- Calcolo COT Index (posizionamento nel range 52 settimane)
- Calcolo COT Momentum (accelerazione vs media mobile)
- Salvataggio/lettura da Supabase
- Calcolo punteggi per l'analisi valute

Autore: Claude AI Assistant
Versione: 1.0
"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import json

# ============================================
# CONFIGURAZIONE
# ============================================

# Mappatura valute -> nomi contratti nel report COT Legacy
CURRENCY_CONTRACTS = {
    'EUR': 'EURO FX - CHICAGO MERCANTILE EXCHANGE',
    'GBP': 'BRITISH POUND STERLING - CHICAGO MERCANTILE EXCHANGE',
    'JPY': 'JAPANESE YEN - CHICAGO MERCANTILE EXCHANGE',
    'CHF': 'SWISS FRANC - CHICAGO MERCANTILE EXCHANGE',
    'AUD': 'AUSTRALIAN DOLLAR - CHICAGO MERCANTILE EXCHANGE',
    'CAD': 'CANADIAN DOLLAR - CHICAGO MERCANTILE EXCHANGE',
    'USD': 'USD INDEX - ICE FUTURES U.S.',  # DXY - Dollar Index
}

# Nomi alternativi (il CFTC a volte usa nomi diversi)
CURRENCY_CONTRACTS_ALT = {
    'EUR': ['EURO FX - CHICAGO MERCANTILE EXCHANGE', 'EURO FX'],
    'GBP': ['BRITISH POUND STERLING - CHICAGO MERCANTILE EXCHANGE', 'BRITISH POUND', 'BRITISH POUND STERLING'],
    'JPY': ['JAPANESE YEN - CHICAGO MERCANTILE EXCHANGE', 'JAPANESE YEN'],
    'CHF': ['SWISS FRANC - CHICAGO MERCANTILE EXCHANGE', 'SWISS FRANC'],
    'AUD': ['AUSTRALIAN DOLLAR - CHICAGO MERCANTILE EXCHANGE', 'AUSTRALIAN DOLLAR'],
    'CAD': ['CANADIAN DOLLAR - CHICAGO MERCANTILE EXCHANGE', 'CANADIAN DOLLAR'],
    'USD': ['USD INDEX - ICE FUTURES U.S.', 'U.S. DOLLAR INDEX - ICE FUTURES U.S.', 'US DOLLAR INDEX'],
}

# URL API CFTC (Socrata Open Data)
CFTC_API_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"

# Parametri per i calcoli
LOOKBACK_WEEKS = 52  # Settimane per calcolare min/max COT Index
MOMENTUM_MA_WEEKS = 4  # Settimane per la media mobile del momentum

# Soglie per i punteggi
COT_INDEX_THRESHOLDS = {
    'extreme_high': 80,  # Sopra = estremo long (score 0)
    'bullish': 60,       # 60-80 = bullish (score +1)
    'neutral_high': 60,  # 40-60 = neutro (score 0)
    'neutral_low': 40,
    'bearish': 20,       # 20-40 = bearish (score -1)
    'extreme_low': 20    # Sotto = estremo short (score 0)
}

# ============================================
# CLASSE PRINCIPALE
# ============================================

class COTDataManager:
    """
    Gestisce il download, calcolo e storage dei dati COT.
    """
    
    def __init__(self, supabase_client=None):
        """
        Inizializza il manager.
        
        Args:
            supabase_client: Client Supabase opzionale per persistenza
        """
        self.supabase = supabase_client
        self.debug_messages = []
        self.last_fetch_status = {}
        
    def _log_debug(self, message: str, level: str = "INFO"):
        """Aggiunge un messaggio di debug."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.debug_messages.append(f"[{timestamp}] [{level}] {message}")
        print(f"[COT {level}] {message}")
    
    def get_debug_log(self) -> List[str]:
        """Restituisce i messaggi di debug."""
        return self.debug_messages
    
    def clear_debug_log(self):
        """Pulisce i messaggi di debug."""
        self.debug_messages = []
    
    # ============================================
    # FETCH DATI DA CFTC
    # ============================================
    
    def fetch_cot_from_cftc(self, weeks: int = 60) -> Dict[str, pd.DataFrame]:
        """
        Scarica i dati COT direttamente dall'API CFTC.
        
        Args:
            weeks: Numero di settimane da scaricare (default 60 per avere margine)
            
        Returns:
            Dict con {currency: DataFrame} per ogni valuta
        """
        self._log_debug(f"Inizio download dati COT da CFTC (ultime {weeks} settimane)...")
        results = {}
        
        for currency, contract_names in CURRENCY_CONTRACTS_ALT.items():
            self._log_debug(f"Scaricando {currency}...")
            
            df = None
            for contract_name in contract_names:
                try:
                    # Query Socrata (SoQL)
                    params = {
                        "$where": f"market_and_exchange_names = '{contract_name}'",
                        "$order": "report_date_as_yyyy_mm_dd DESC",
                        "$limit": weeks,
                        "$select": "report_date_as_yyyy_mm_dd,noncomm_positions_long_all,noncomm_positions_short_all,open_interest_all"
                    }
                    
                    response = requests.get(CFTC_API_URL, params=params, timeout=30)
                    
                    if response.status_code == 200:
                        data = response.json()
                        if data and len(data) > 0:
                            df = pd.DataFrame(data)
                            self._log_debug(f"  ✓ {currency}: trovate {len(df)} settimane con '{contract_name}'")
                            break
                        else:
                            self._log_debug(f"  - {currency}: nessun dato con '{contract_name}', provo alternativa...")
                    else:
                        self._log_debug(f"  ✗ {currency}: HTTP {response.status_code}", "WARNING")
                        
                except requests.exceptions.RequestException as e:
                    self._log_debug(f"  ✗ {currency}: Errore connessione - {str(e)[:100]}", "ERROR")
                    self.last_fetch_status[currency] = f"Errore: {str(e)[:50]}"
                    continue
            
            if df is not None and len(df) > 0:
                # Processa il DataFrame
                df['report_date'] = pd.to_datetime(df['report_date_as_yyyy_mm_dd'])
                df['noncomm_long'] = pd.to_numeric(df['noncomm_positions_long_all'], errors='coerce')
                df['noncomm_short'] = pd.to_numeric(df['noncomm_positions_short_all'], errors='coerce')
                df['open_interest'] = pd.to_numeric(df['open_interest_all'], errors='coerce')
                df['net_position'] = df['noncomm_long'] - df['noncomm_short']
                
                # Ordina per data crescente
                df = df.sort_values('report_date', ascending=True).reset_index(drop=True)
                
                # Seleziona colonne utili
                df = df[['report_date', 'net_position', 'noncomm_long', 'noncomm_short', 'open_interest']]
                
                results[currency] = df
                self.last_fetch_status[currency] = f"OK - {len(df)} settimane"
            else:
                self._log_debug(f"  ✗ {currency}: Nessun dato trovato con nessun nome contratto", "ERROR")
                self.last_fetch_status[currency] = "Nessun dato"
        
        self._log_debug(f"Download completato: {len(results)}/{len(CURRENCY_CONTRACTS)} valute")
        return results
    
    # ============================================
    # SALVATAGGIO SU SUPABASE
    # ============================================
    
    def save_to_supabase(self, data: Dict[str, pd.DataFrame]) -> Tuple[int, int]:
        """
        Salva i dati COT su Supabase.
        
        Args:
            data: Dict con {currency: DataFrame}
            
        Returns:
            Tuple (righe_inserite, righe_aggiornate)
        """
        if not self.supabase:
            self._log_debug("Supabase non configurato, skip salvataggio", "WARNING")
            return 0, 0
        
        inserted = 0
        updated = 0
        
        for currency, df in data.items():
            for _, row in df.iterrows():
                try:
                    record = {
                        'currency': currency,
                        'report_date': row['report_date'].strftime('%Y-%m-%d'),
                        'net_position': int(row['net_position']) if pd.notna(row['net_position']) else 0,
                        'noncomm_long': int(row['noncomm_long']) if pd.notna(row['noncomm_long']) else None,
                        'noncomm_short': int(row['noncomm_short']) if pd.notna(row['noncomm_short']) else None,
                        'open_interest': int(row['open_interest']) if pd.notna(row['open_interest']) else None,
                    }
                    
                    # Upsert (insert or update)
                    result = self.supabase.table('cot_data').upsert(
                        record,
                        on_conflict='currency,report_date'
                    ).execute()
                    
                    if result.data:
                        inserted += 1
                        
                except Exception as e:
                    self._log_debug(f"Errore salvataggio {currency} {row['report_date']}: {e}", "ERROR")
        
        self._log_debug(f"Salvati {inserted} record su Supabase")
        return inserted, updated
    
    def load_from_supabase(self, weeks: int = 52) -> Dict[str, pd.DataFrame]:
        """
        Carica i dati COT da Supabase.
        
        Args:
            weeks: Numero di settimane da caricare
            
        Returns:
            Dict con {currency: DataFrame}
        """
        if not self.supabase:
            self._log_debug("Supabase non configurato", "WARNING")
            return {}
        
        results = {}
        
        try:
            # Calcola data minima
            min_date = (datetime.now() - timedelta(weeks=weeks)).strftime('%Y-%m-%d')
            
            for currency in CURRENCY_CONTRACTS.keys():
                response = self.supabase.table('cot_data')\
                    .select('*')\
                    .eq('currency', currency)\
                    .gte('report_date', min_date)\
                    .order('report_date', desc=False)\
                    .execute()
                
                if response.data:
                    df = pd.DataFrame(response.data)
                    df['report_date'] = pd.to_datetime(df['report_date'])
                    results[currency] = df
                    self._log_debug(f"Caricati {len(df)} record per {currency} da Supabase")
                    
        except Exception as e:
            self._log_debug(f"Errore caricamento da Supabase: {e}", "ERROR")
        
        return results
    
    # ============================================
    # CALCOLI COT INDEX E MOMENTUM
    # ============================================
    
    def calculate_cot_index(self, net_positions: np.ndarray) -> float:
        """
        Calcola il COT Index (0-100%) basato sul range delle ultime 52 settimane.
        
        Formula: (Current - Min) / (Max - Min) * 100
        
        Args:
            net_positions: Array delle net positions (ultime 52+ settimane)
            
        Returns:
            COT Index come percentuale (0-100)
        """
        if len(net_positions) < 2:
            return 50.0  # Default neutro
        
        # Usa solo le ultime 52 settimane per min/max
        positions_52w = net_positions[-LOOKBACK_WEEKS:] if len(net_positions) >= LOOKBACK_WEEKS else net_positions
        
        current = net_positions[-1]
        min_val = positions_52w.min()
        max_val = positions_52w.max()
        
        if max_val == min_val:
            return 50.0  # Evita divisione per zero
        
        cot_index = ((current - min_val) / (max_val - min_val)) * 100
        return round(cot_index, 1)
    
    def calculate_momentum(self, net_positions: np.ndarray) -> Dict:
        """
        Calcola il COT Momentum confrontando il delta attuale con la MA dei delta.
        
        Args:
            net_positions: Array delle net positions
            
        Returns:
            Dict con delta_current, ma4_delta, deviation, percentiles
        """
        if len(net_positions) < MOMENTUM_MA_WEEKS + 2:
            return {
                'delta_current': 0,
                'ma4_delta': 0,
                'deviation': 0,
                'percentile_25': 0,
                'percentile_75': 0,
                'status': 'insufficient_data'
            }
        
        # Calcola i delta settimanali
        deltas = np.diff(net_positions)
        
        if len(deltas) < MOMENTUM_MA_WEEKS:
            return {
                'delta_current': int(deltas[-1]) if len(deltas) > 0 else 0,
                'ma4_delta': 0,
                'deviation': 0,
                'percentile_25': 0,
                'percentile_75': 0,
                'status': 'insufficient_data'
            }
        
        # Delta attuale
        delta_current = deltas[-1]
        
        # Media mobile degli ultimi 4 delta (escludendo quello attuale)
        ma4_delta = np.mean(deltas[-MOMENTUM_MA_WEEKS-1:-1]) if len(deltas) > MOMENTUM_MA_WEEKS else np.mean(deltas[:-1])
        
        # Deviazione
        deviation = delta_current - ma4_delta
        
        # Calcola percentili sui delta storici (ultime 52 settimane)
        deltas_52w = deltas[-LOOKBACK_WEEKS:] if len(deltas) >= LOOKBACK_WEEKS else deltas
        percentile_25 = np.percentile(deltas_52w, 25)
        percentile_75 = np.percentile(deltas_52w, 75)
        
        return {
            'delta_current': int(delta_current),
            'ma4_delta': int(ma4_delta),
            'deviation': int(deviation),
            'percentile_25': int(percentile_25),
            'percentile_75': int(percentile_75),
            'status': 'ok'
        }
    
    def calculate_scores(self, cot_index: float, momentum: Dict) -> Dict:
        """
        Calcola i punteggi per COT Index e Momentum.
        
        Args:
            cot_index: COT Index (0-100)
            momentum: Dict con dati momentum
            
        Returns:
            Dict con index_score, momentum_score, interpretations
        """
        # Score COT Index
        if cot_index > COT_INDEX_THRESHOLDS['extreme_high']:
            index_score = 0
            index_interpretation = "Estremo Long (cautela)"
        elif cot_index >= COT_INDEX_THRESHOLDS['bullish']:
            index_score = 1
            index_interpretation = "Bullish"
        elif cot_index >= COT_INDEX_THRESHOLDS['neutral_low']:
            index_score = 0
            index_interpretation = "Neutrale"
        elif cot_index >= COT_INDEX_THRESHOLDS['extreme_low']:
            index_score = -1
            index_interpretation = "Bearish"
        else:
            index_score = 0
            index_interpretation = "Estremo Short (cautela)"
        
        # Score Momentum
        if momentum['status'] != 'ok':
            momentum_score = 0
            momentum_interpretation = "Dati insufficienti"
        elif momentum['deviation'] > momentum['percentile_75']:
            momentum_score = 1
            momentum_interpretation = "Accelerazione acquisti"
        elif momentum['deviation'] < momentum['percentile_25']:
            momentum_score = -1
            momentum_interpretation = "Accelerazione vendite"
        else:
            momentum_score = 0
            momentum_interpretation = "Stabile"
        
        return {
            'index_score': index_score,
            'index_interpretation': index_interpretation,
            'momentum_score': momentum_score,
            'momentum_interpretation': momentum_interpretation,
            'total_score': index_score + momentum_score
        }
    
    # ============================================
    # ANALISI COMPLETA
    # ============================================
    
    def analyze_all_currencies(self, data: Dict[str, pd.DataFrame] = None) -> Dict:
        """
        Esegue l'analisi completa per tutte le valute.
        
        Args:
            data: Dict con dati COT (se None, li scarica)
            
        Returns:
            Dict con analisi per ogni valuta
        """
        if data is None:
            # Prova prima da Supabase
            data = self.load_from_supabase()
            
            # Se non ci sono dati, scarica dalla CFTC
            if not data:
                data = self.fetch_cot_from_cftc()
                if data and self.supabase:
                    self.save_to_supabase(data)
        
        results = {}
        
        for currency, df in data.items():
            if len(df) < 5:
                self._log_debug(f"{currency}: Dati insufficienti ({len(df)} settimane)", "WARNING")
                results[currency] = {
                    'status': 'insufficient_data',
                    'weeks_available': len(df),
                    'net_position': 0,
                    'cot_index': 50.0,
                    'momentum': {'status': 'insufficient_data'},
                    'scores': {'index_score': 0, 'momentum_score': 0, 'total_score': 0}
                }
                continue
            
            net_positions = df['net_position'].values
            
            # Calcoli
            cot_index = self.calculate_cot_index(net_positions)
            momentum = self.calculate_momentum(net_positions)
            scores = self.calculate_scores(cot_index, momentum)
            
            # Dati più recenti
            latest = df.iloc[-1]
            previous = df.iloc[-2] if len(df) > 1 else latest
            
            results[currency] = {
                'status': 'ok',
                'weeks_available': len(df),
                'report_date': latest['report_date'].strftime('%Y-%m-%d'),
                'net_position': int(latest['net_position']),
                'net_position_prev': int(previous['net_position']),
                'noncomm_long': int(latest['noncomm_long']) if pd.notna(latest.get('noncomm_long')) else None,
                'noncomm_short': int(latest['noncomm_short']) if pd.notna(latest.get('noncomm_short')) else None,
                'cot_index': cot_index,
                'min_52w': int(net_positions[-LOOKBACK_WEEKS:].min()) if len(net_positions) >= LOOKBACK_WEEKS else int(net_positions.min()),
                'max_52w': int(net_positions[-LOOKBACK_WEEKS:].max()) if len(net_positions) >= LOOKBACK_WEEKS else int(net_positions.max()),
                'momentum': momentum,
                'scores': scores
            }
            
            self._log_debug(
                f"{currency}: Net={results[currency]['net_position']:+,} | "
                f"Index={cot_index:.0f}% ({scores['index_interpretation']}) | "
                f"Mom={momentum['delta_current']:+,} ({scores['momentum_interpretation']})"
            )
        
        return results
    
    # ============================================
    # FETCH E AGGIORNA (METODO PRINCIPALE)
    # ============================================
    
    def fetch_and_update(self) -> Dict:
        """
        Metodo principale: scarica dati, salva su Supabase, restituisce analisi.
        
        Returns:
            Dict con analisi completa per tutte le valute
        """
        self.clear_debug_log()
        self._log_debug("=== INIZIO AGGIORNAMENTO DATI COT ===")
        
        # Scarica da CFTC
        data = self.fetch_cot_from_cftc()
        
        if not data:
            self._log_debug("Nessun dato scaricato dalla CFTC!", "ERROR")
            return {
                'status': 'error',
                'message': 'Impossibile scaricare dati dalla CFTC',
                'debug': self.debug_messages
            }
        
        # Salva su Supabase
        if self.supabase:
            inserted, _ = self.save_to_supabase(data)
            self._log_debug(f"Salvati {inserted} record su Supabase")
        
        # Analizza
        analysis = self.analyze_all_currencies(data)
        
        self._log_debug("=== AGGIORNAMENTO COMPLETATO ===")
        
        return {
            'status': 'ok',
            'last_update': datetime.now().isoformat(),
            'currencies': analysis,
            'fetch_status': self.last_fetch_status,
            'debug': self.debug_messages
        }


# ============================================
# FUNZIONI HELPER PER INTEGRAZIONE
# ============================================

def get_cot_analysis(supabase_client=None) -> Dict:
    """
    Funzione helper per ottenere l'analisi COT.
    
    Args:
        supabase_client: Client Supabase opzionale
        
    Returns:
        Dict con analisi COT per tutte le valute
    """
    manager = COTDataManager(supabase_client)
    return manager.fetch_and_update()


def get_cot_scores_for_currency(cot_data: Dict, currency: str) -> Tuple[int, int]:
    """
    Estrae i punteggi COT per una specifica valuta.
    
    Args:
        cot_data: Dict restituito da get_cot_analysis()
        currency: Codice valuta (EUR, GBP, etc.)
        
    Returns:
        Tuple (index_score, momentum_score)
    """
    if not cot_data or cot_data.get('status') != 'ok':
        return 0, 0
    
    currencies = cot_data.get('currencies', {})
    currency_data = currencies.get(currency, {})
    scores = currency_data.get('scores', {})
    
    return scores.get('index_score', 0), scores.get('momentum_score', 0)


def format_cot_for_display(cot_data: Dict) -> List[Dict]:
    """
    Formatta i dati COT per la visualizzazione nella UI.
    
    Args:
        cot_data: Dict restituito da get_cot_analysis()
        
    Returns:
        Lista di dict pronti per st.dataframe()
    """
    if not cot_data or cot_data.get('status') != 'ok':
        return []
    
    rows = []
    currencies = cot_data.get('currencies', {})
    
    for currency in ['USD', 'EUR', 'GBP', 'JPY', 'CHF', 'AUD', 'CAD']:
        data = currencies.get(currency, {})
        
        if data.get('status') != 'ok':
            rows.append({
                'Valuta': currency,
                'Net Position': 'N/A',
                'COT Index': 'N/A',
                'Δ Settimana': 'N/A',
                'MA(4) Δ': 'N/A',
                '75° Perc': 'N/A',
                'Settimane': data.get('weeks_available', 0)
            })
            continue
        
        momentum = data.get('momentum', {})
        
        rows.append({
            'Valuta': currency,
            'Net Position': f"{data['net_position']:+,}",
            'COT Index': f"{data['cot_index']:.0f}%",
            'Δ Settimana': f"{momentum.get('delta_current', 0):+,}",
            'MA(4) Δ': f"{momentum.get('ma4_delta', 0):+,}",
            '75° Perc': f"{momentum.get('percentile_75', 0):+,}",
            'Settimane': data.get('weeks_available', 0)
        })
    
    return rows


# ============================================
# TEST STANDALONE
# ============================================

if __name__ == "__main__":
    print("=" * 70)
    print("TEST MODULO COT DATA")
    print("=" * 70)
    
    # Test senza Supabase
    manager = COTDataManager()
    
    # Scarica e analizza
    result = manager.fetch_and_update()
    
    print("\n" + "=" * 70)
    print("RISULTATI ANALISI")
    print("=" * 70)
    
    if result['status'] == 'ok':
        for currency, data in result['currencies'].items():
            print(f"\n{currency}:")
            print(f"  Net Position: {data.get('net_position', 'N/A'):+,}")
            print(f"  COT Index: {data.get('cot_index', 'N/A'):.1f}%")
            print(f"  Settimane disponibili: {data.get('weeks_available', 0)}")
            
            scores = data.get('scores', {})
            print(f"  Score Index: {scores.get('index_score', 0)} ({scores.get('index_interpretation', 'N/A')})")
            print(f"  Score Momentum: {scores.get('momentum_score', 0)} ({scores.get('momentum_interpretation', 'N/A')})")
    else:
        print(f"\nErrore: {result.get('message', 'Unknown')}")
    
    print("\n" + "=" * 70)
    print("DEBUG LOG")
    print("=" * 70)
    for msg in result.get('debug', []):
        print(msg)
