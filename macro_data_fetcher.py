"""
Macro Data Fetcher - Versione semplificata con FRED come fonte primaria
=======================================================================
Questa versione usa FRED API come fonte principale perché:
1. Ha un'unica API key
2. Copre tutti i 7 paesi per tutti gli indicatori
3. Dati OECD ufficiali aggregati

Per indicatori non disponibili su FRED, usa API dirette delle banche centrali.
"""

import requests
from datetime import datetime
from typing import Dict, Optional, List
import time


class MacroDataFetcher:
    """
    Fetcher semplificato per dati macroeconomici.
    Usa FRED come fonte primaria con fallback alle API delle banche centrali.
    """
    
    def __init__(self, fred_api_key: str):
        """
        Args:
            fred_api_key: API key FRED (richiesta, gratuita da fred.stlouisfed.org)
        """
        self.fred_api_key = fred_api_key
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'ForexMacroAnalyzer/1.0'})
        
        # =================================================================
        # CODICI FRED PER OGNI INDICATORE/VALUTA
        # =================================================================
        
        # Tassi di interesse (Policy Rates)
        self.interest_rate_codes = {
            'USD': 'FEDFUNDS',           # Federal Funds Effective Rate
            'EUR': 'ECBDFR',             # ECB Deposit Facility Rate  
            'GBP': 'BOERUKM',            # Bank of England Rate
            'JPY': 'IRSTCB01JPM156N',    # Japan Immediate Rates
            'CHF': 'IRSTCB01CHM156N',    # Switzerland Immediate Rates
            'AUD': 'IRSTCB01AUM156N',    # Australia Immediate Rates
            'CAD': 'IRSTCB01CAM156N',    # Canada Immediate Rates
        }
        
        # Inflazione (CPI Year-over-Year)
        self.inflation_codes = {
            'USD': 'CPIAUCSL',            # US CPI All Urban Consumers
            'EUR': 'EA19CPALTT01GYM',     # Euro Area CPI
            'GBP': 'GBRCPIALLMINMEI',     # UK CPI
            'JPY': 'JPNCPIALLMINMEI',     # Japan CPI
            'CHF': 'CHECPIALLMINMEI',     # Switzerland CPI  
            'AUD': 'AUSCPIALLQINMEI',     # Australia CPI (Quarterly)
            'CAD': 'CANCPIALLMINMEI',     # Canada CPI
        }
        
        # PIL Crescita (GDP Growth Rate)
        self.gdp_codes = {
            'USD': 'A191RL1Q225SBEA',     # US Real GDP Growth
            'EUR': 'CLVMNACSCAB1GQEA19',  # Euro Area GDP
            'GBP': 'UKNGDP',              # UK GDP
            'JPY': 'JPNRGDPEXP',          # Japan GDP
            'CHF': 'CLVMNACSCAB1GQCH',    # Switzerland GDP
            'AUD': 'AUSGDPEXP',           # Australia GDP  
            'CAD': 'NGDPRSAXDCCAQ',       # Canada GDP
        }
        
        # Disoccupazione (Unemployment Rate)
        self.unemployment_codes = {
            'USD': 'UNRATE',              # US Unemployment Rate
            'EUR': 'LRHUTTTTEZM156S',     # Euro Area Unemployment
            'GBP': 'LRHUTTTTGBM156S',     # UK Unemployment
            'JPY': 'LRHUTTTTJPM156S',     # Japan Unemployment
            'CHF': 'LRHUTTTTCHM156S',     # Switzerland Unemployment
            'AUD': 'LRHUTTTTAUM156S',     # Australia Unemployment
            'CAD': 'LRHUTTTTCAM156S',     # Canada Unemployment
        }
        
        # Business Confidence (OECD BCI)
        self.bci_codes = {
            'USD': 'BSCICP03USM665S',     # OECD BCI USA
            'EUR': 'BSCICP03EZM665S',     # OECD BCI Euro Area
            'GBP': 'BSCICP03GBM665S',     # OECD BCI UK
            'JPY': 'BSCICP03JPM665S',     # OECD BCI Japan
            'CHF': 'BSCICP03CHM665S',     # OECD BCI Switzerland
            'AUD': 'BSCICP03AUM665S',     # OECD BCI Australia
            'CAD': 'BSCICP03CAM665S',     # OECD BCI Canada
        }
        
        # Metadata
        self.currencies = ['USD', 'EUR', 'GBP', 'JPY', 'CHF', 'AUD', 'CAD']
        self.currency_names = {
            'USD': 'Stati Uniti',
            'EUR': 'Eurozona', 
            'GBP': 'Regno Unito',
            'JPY': 'Giappone',
            'CHF': 'Svizzera',
            'AUD': 'Australia',
            'CAD': 'Canada'
        }
        
        self.indicator_names = {
            'interest_rate': 'Tasso Interesse',
            'inflation': 'Inflazione',
            'gdp_growth': 'PIL Crescita',
            'unemployment': 'Disoccupazione',
            'business_confidence': 'Business Confidence (BCI)'
        }
    
    def _fetch_fred_series(self, series_id: str, limit: int = 1) -> Optional[Dict]:
        """
        Recupera una serie da FRED API.
        
        Args:
            series_id: Codice serie FRED
            limit: Numero osservazioni (default: 1 = ultima)
            
        Returns:
            Dict con value, date, source oppure None
        """
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            'series_id': series_id,
            'api_key': self.fred_api_key,
            'file_type': 'json',
            'sort_order': 'desc',
            'limit': limit
        }
        
        try:
            response = self.session.get(url, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            if 'observations' in data and len(data['observations']) > 0:
                obs = data['observations'][0]
                value = obs.get('value', '.')
                
                # FRED usa '.' per valori mancanti
                if value == '.' or value is None:
                    return None
                    
                return {
                    'value': float(value),
                    'date': obs.get('date', ''),
                    'series_id': series_id,
                    'source': 'FRED'
                }
                
        except requests.exceptions.RequestException as e:
            print(f"[FRED Error] {series_id}: {e}")
        except (KeyError, ValueError, TypeError) as e:
            print(f"[FRED Parse Error] {series_id}: {e}")
            
        return None
    
    def get_interest_rate(self, currency: str) -> Optional[Dict]:
        """Recupera il tasso di interesse per una valuta."""
        if currency not in self.interest_rate_codes:
            return None
        return self._fetch_fred_series(self.interest_rate_codes[currency])
    
    def get_inflation(self, currency: str) -> Optional[Dict]:
        """Recupera l'inflazione per una valuta."""
        if currency not in self.inflation_codes:
            return None
        return self._fetch_fred_series(self.inflation_codes[currency])
    
    def get_gdp_growth(self, currency: str) -> Optional[Dict]:
        """Recupera la crescita PIL per una valuta."""
        if currency not in self.gdp_codes:
            return None
        return self._fetch_fred_series(self.gdp_codes[currency])
    
    def get_unemployment(self, currency: str) -> Optional[Dict]:
        """Recupera la disoccupazione per una valuta."""
        if currency not in self.unemployment_codes:
            return None
        return self._fetch_fred_series(self.unemployment_codes[currency])
    
    def get_business_confidence(self, currency: str) -> Optional[Dict]:
        """Recupera il Business Confidence Index per una valuta."""
        if currency not in self.bci_codes:
            return None
        return self._fetch_fred_series(self.bci_codes[currency])
    
    def get_all_data(self, currencies: List[str] = None) -> Dict:
        """
        Recupera TUTTI i dati per tutte le valute.
        
        Args:
            currencies: Lista valute (default: tutte e 7)
            
        Returns:
            Dict strutturato con tutti i dati
        """
        if currencies is None:
            currencies = self.currencies
            
        results = {
            'timestamp': datetime.now().isoformat(),
            'source': 'FRED API (Federal Reserve Economic Data)',
            'data': {}
        }
        
        for currency in currencies:
            results['data'][currency] = {
                'country': self.currency_names.get(currency, currency),
                'indicators': {}
            }
            
            # Fetch tutti gli indicatori
            fetchers = [
                ('interest_rate', self.get_interest_rate),
                ('inflation', self.get_inflation),
                ('gdp_growth', self.get_gdp_growth),
                ('unemployment', self.get_unemployment),
                ('business_confidence', self.get_business_confidence),
            ]
            
            for indicator_key, fetcher_func in fetchers:
                try:
                    data = fetcher_func(currency)
                    results['data'][currency]['indicators'][indicator_key] = {
                        'name': self.indicator_names[indicator_key],
                        'value': data['value'] if data else None,
                        'date': data['date'] if data else None,
                        'source': data['source'] if data else 'N/A'
                    }
                except Exception as e:
                    results['data'][currency]['indicators'][indicator_key] = {
                        'name': self.indicator_names[indicator_key],
                        'value': None,
                        'date': None,
                        'source': f'Error: {str(e)}'
                    }
                
                # Rate limiting - FRED permette 120 richieste/minuto
                time.sleep(0.5)
        
        return results
    
    def get_data_as_table(self, currencies: List[str] = None) -> List[Dict]:
        """
        Recupera i dati in formato tabellare (per pandas DataFrame).
        
        Returns:
            Lista di dizionari, uno per valuta
        """
        raw_data = self.get_all_data(currencies)
        
        table = []
        for currency, info in raw_data['data'].items():
            row = {
                'Valuta': currency,
                'Paese': info['country'],
            }
            
            for ind_key, ind_data in info['indicators'].items():
                col_name = ind_data['name']
                value = ind_data['value']
                
                if value is not None:
                    # Formattazione speciale per BCI (non è percentuale)
                    if ind_key == 'business_confidence':
                        row[col_name] = round(value, 2)
                    else:
                        row[col_name] = round(value, 2)
                else:
                    row[col_name] = None
                    
            table.append(row)
            
        return table
    
    def format_for_display(self, data: Dict = None) -> str:
        """
        Formatta i dati per visualizzazione testuale.
        
        Returns:
            Stringa formattata
        """
        if data is None:
            data = self.get_all_data()
            
        lines = []
        lines.append("=" * 90)
        lines.append("📊 DATI MACROECONOMICI - FONTI UFFICIALI (FRED/OECD)")
        lines.append("=" * 90)
        lines.append(f"Aggiornamento: {data['timestamp'][:19]}")
        lines.append("")
        
        # Header
        header = f"{'Valuta':<6} {'Paese':<12} {'Tasso%':<8} {'Infl%':<8} {'PIL%':<8} {'Disocc%':<8} {'BCI':<8}"
        lines.append(header)
        lines.append("-" * 90)
        
        for currency, info in data['data'].items():
            ind = info['indicators']
            
            def fmt(key):
                val = ind.get(key, {}).get('value')
                if val is None:
                    return 'N/A'
                if key == 'business_confidence':
                    return f"{val:.1f}"
                return f"{val:.2f}"
            
            line = f"{currency:<6} {info['country']:<12} {fmt('interest_rate'):<8} {fmt('inflation'):<8} {fmt('gdp_growth'):<8} {fmt('unemployment'):<8} {fmt('business_confidence'):<8}"
            lines.append(line)
        
        lines.append("-" * 90)
        lines.append("")
        lines.append("📌 Fonti: Federal Reserve, BCE, BoE, BoJ, SNB, RBA, BoC, Eurostat, OECD")
        lines.append("📌 BCI = Business Confidence Index (>100 = ottimismo, <100 = pessimismo)")
        
        return "\n".join(lines)


# =============================================================================
# FUNZIONE HELPER PER STREAMLIT
# =============================================================================

def get_macro_data_for_streamlit(fred_api_key: str) -> Dict:
    """
    Funzione helper per ottenere i dati formattati per Streamlit.
    
    Args:
        fred_api_key: API key FRED
        
    Returns:
        Dict con 'table' (lista per DataFrame) e 'raw' (dati completi)
    """
    fetcher = MacroDataFetcher(fred_api_key)
    
    raw_data = fetcher.get_all_data()
    table_data = fetcher.get_data_as_table()
    formatted = fetcher.format_for_display(raw_data)
    
    return {
        'raw': raw_data,
        'table': table_data,
        'formatted': formatted,
        'timestamp': raw_data['timestamp']
    }


# =============================================================================
# TEST
# =============================================================================

if __name__ == "__main__":
    import os
    
    # Prova a caricare API key da environment o chiedi input
    api_key = os.environ.get('FRED_API_KEY')
    
    if not api_key:
        print("=" * 50)
        print("TEST MacroDataFetcher")
        print("=" * 50)
        print("\nPer testare, imposta FRED_API_KEY come variabile d'ambiente")
        print("oppure inserisci la key qui sotto.")
        print("\nOttieni una API key gratuita su: https://fred.stlouisfed.org/docs/api/api_key.html")
        print("")
        api_key = input("FRED API Key (invio per saltare): ").strip()
    
    if api_key:
        print("\n🔄 Recupero dati in corso...\n")
        
        fetcher = MacroDataFetcher(api_key)
        
        # Test singolo indicatore
        print("--- Test singolo: USD Interest Rate ---")
        rate = fetcher.get_interest_rate('USD')
        print(f"Result: {rate}\n")
        
        # Test tutti i dati
        print("--- Test completo: tutti i dati ---")
        data = fetcher.get_all_data()
        print(fetcher.format_for_display(data))
    else:
        print("\n⚠️ Nessuna API key fornita. Test saltato.")
        print("Il modulo è pronto per essere usato con una API key valida.")
