"""
Macro Data Fetcher - API Native Banche Centrali + FRED Fallback
================================================================
Priorità: API ufficiali banche centrali → FRED fallback

Fonti Primarie:
- USD: FRED (è già la fonte primaria per USA)
- EUR: ECB Statistical Data Warehouse
- GBP: Bank of England IADB
- JPY: Bank of Japan Statistics  
- CHF: Swiss National Bank Data Portal
- AUD: Reserve Bank of Australia
- CAD: Bank of Canada Valet API
"""

import requests
from datetime import datetime, timedelta
from typing import Dict, Optional, List
import time
import re


class MacroDataFetcher:
    """
    Fetcher per dati macroeconomici.
    Usa API native delle banche centrali con FRED come fallback.
    """
    
    def __init__(self, fred_api_key: str):
        self.fred_api_key = fred_api_key
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
        })
        self.timeout = 20
        
        # Metadata
        self.currencies = ['USD', 'EUR', 'GBP', 'JPY', 'CHF', 'AUD', 'CAD']
        self.currency_names = {
            'USD': 'Stati Uniti', 'EUR': 'Eurozona', 'GBP': 'Regno Unito',
            'JPY': 'Giappone', 'CHF': 'Svizzera', 'AUD': 'Australia', 'CAD': 'Canada'
        }
        self.indicator_names = {
            'interest_rate': 'Tasso Interesse',
            'inflation': 'Inflazione YoY',
            'gdp_growth': 'PIL Crescita',
            'unemployment': 'Disoccupazione',
            'business_confidence': 'Business Confidence'
        }
        
        # FRED codes (fallback) - TUTTI in percentuale
        self.fred_codes = {
            'interest_rate': {
                'USD': 'DFEDTARU',        # Fed Funds Upper Bound
                'EUR': 'ECBDFR',          # ECB Deposit Rate
                'GBP': 'BOERUKM',         # BoE Bank Rate  
                'JPY': 'IRSTCI01JPM156N', # Japan Short-term
                'CHF': 'IRSTCI01CHM156N', # Switzerland Short-term
                'AUD': 'IRSTCI01AUM156N', # Australia Short-term
                'CAD': 'IRSTCI01CAM156N', # Canada Short-term
            },
            'inflation': {
                'USD': 'CPALTT01USM657N', # USA CPI YoY %
                'EUR': 'EA19CPALTT01GYM', # Euro CPI YoY %
                'GBP': 'CPALTT01GBM657N', # UK CPI YoY %
                'JPY': 'CPALTT01JPM657N', # Japan CPI YoY %
                'CHF': 'CPALTT01CHM657N', # Swiss CPI YoY %
                'AUD': 'CPALTT01AUQ657N', # Australia CPI YoY % (quarterly)
                'CAD': 'CPALTT01CAM657N', # Canada CPI YoY %
            },
            'gdp_growth': {
                'USD': 'A191RL1Q225SBEA', # US Real GDP Growth
                'EUR': 'NAEXKP01EZQ657S', # Euro GDP Growth
                'GBP': 'NAEXKP01GBQ657S', # UK GDP Growth
                'JPY': 'NAEXKP01JPQ657S', # Japan GDP Growth
                'CHF': 'NAEXKP01CHQ657S', # Swiss GDP Growth
                'AUD': 'NAEXKP01AUQ657S', # Australia GDP Growth
                'CAD': 'NAEXKP01CAQ657S', # Canada GDP Growth
            },
            'unemployment': {
                'USD': 'UNRATE',           # US Unemployment
                'EUR': 'LRHUTTTTEZM156S',  # Euro Unemployment
                'GBP': 'LRHUTTTTGBM156S',  # UK Unemployment
                'JPY': 'LRHUTTTTJPM156S',  # Japan Unemployment
                'CHF': 'LRHUTTTTCHM156S',  # Swiss Unemployment
                'AUD': 'LRHUTTTTAUM156S',  # Australia Unemployment
                'CAD': 'LRHUTTTTCAM156S',  # Canada Unemployment
            },
            'business_confidence': {
                'USD': 'BSCICP03USM665S',
                'EUR': 'BSCICP03EZM665S',
                'GBP': 'BSCICP03GBM665S',
                'JPY': 'BSCICP03JPM665S',
                'CHF': 'BSCICP03CHM665S',
                'AUD': 'BSCICP03AUM665S',
                'CAD': 'BSCICP03CAM665S',
            }
        }

    # =========================================================================
    # FRED API (Fallback universale)
    # =========================================================================
    
    def _fetch_fred(self, series_id: str) -> Optional[Dict]:
        """Recupera dati da FRED API."""
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            'series_id': series_id,
            'api_key': self.fred_api_key,
            'file_type': 'json',
            'sort_order': 'desc',
            'limit': 5  # Prendi ultimi 5 per trovare valore valido
        }
        
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
            
            if 'observations' in data:
                for obs in data['observations']:
                    value = obs.get('value', '.')
                    if value != '.' and value is not None:
                        return {
                            'value': float(value),
                            'date': obs.get('date', ''),
                            'source': 'FRED'
                        }
        except Exception as e:
            print(f"[FRED Error] {series_id}: {e}")
        return None

    # =========================================================================
    # BANK OF CANADA - Valet API (CAD)
    # =========================================================================
    
    def _fetch_boc_rate(self) -> Optional[Dict]:
        """Bank of Canada - Overnight Rate Target."""
        # Usa il gruppo ATABLE_POLICY_INSTRUMENT
        url = "https://www.bankofcanada.ca/valet/observations/STATIC_ATABLE_V39079/json?recent=1"
        
        try:
            response = self.session.get(url, timeout=self.timeout)
            if response.status_code == 200:
                data = response.json()
                observations = data.get('observations', [])
                if observations:
                    obs = observations[-1]
                    date_str = obs.get('d', '')
                    # Il valore è nel campo STATIC_ATABLE_V39079
                    value = obs.get('STATIC_ATABLE_V39079', {})
                    if isinstance(value, dict):
                        value = value.get('v')
                    if value:
                        return {
                            'value': float(value),
                            'date': date_str,
                            'source': 'Bank of Canada'
                        }
        except Exception as e:
            print(f"[BoC Error] {e}")
        return None

    # =========================================================================
    # RESERVE BANK OF AUSTRALIA (AUD)
    # =========================================================================
    
    def _fetch_rba_rate(self) -> Optional[Dict]:
        """RBA - Cash Rate Target da pagina statistiche."""
        # Scarica il file Excel/CSV delle statistiche
        url = "https://www.rba.gov.au/statistics/tables/xls/f01d.xlsx"
        
        try:
            # Prima prova a prendere dalla pagina HTML
            html_url = "https://www.rba.gov.au/statistics/cash-rate/"
            response = self.session.get(html_url, timeout=self.timeout)
            
            if response.status_code == 200:
                # Cerca il tasso nella tabella HTML
                # Pattern: cerca "Cash rate target %" nella tabella
                text = response.text
                
                # Cerca pattern come "3.60" vicino a date recenti
                # La tabella ha formato: data | change | rate | documents
                import re
                
                # Cerca righe della tabella con tassi
                # Pattern: data (es. "10 Dec 2025") seguita da valore percentuale
                pattern = r'(\d{1,2}\s+\w+\s+\d{4}).*?([+-]?\d+\.\d+).*?(\d+\.\d+)'
                matches = re.findall(pattern, text)
                
                if matches:
                    # Prendi la prima riga (più recente)
                    date_str, change, rate = matches[0]
                    return {
                        'value': float(rate),
                        'date': date_str,
                        'source': 'RBA'
                    }
                
                # Fallback: cerca solo il numero dopo "Cash rate target"
                rate_match = re.search(r'Cash rate target[^0-9]*(\d+\.\d+)', text, re.IGNORECASE)
                if rate_match:
                    return {
                        'value': float(rate_match.group(1)),
                        'date': datetime.now().strftime('%Y-%m-%d'),
                        'source': 'RBA'
                    }
                    
        except Exception as e:
            print(f"[RBA Error] {e}")
        return None

    # =========================================================================
    # BANK OF ENGLAND (GBP)
    # =========================================================================
    
    def _fetch_boe_rate(self) -> Optional[Dict]:
        """Bank of England - Bank Rate."""
        # BoE Interactive Database
        url = "https://www.bankofengland.co.uk/boeapps/iadb/fromshowcolumns.asp"
        params = {
            'csv.x': 'yes',
            'SeriesCodes': 'IUDBEDR',  # Official Bank Rate
            'CSVF': 'CN',
            'VPD': 'Y'
        }
        
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
            if response.status_code == 200:
                lines = response.text.strip().split('\n')
                # Salta header e trova ultima riga con dati validi
                for line in reversed(lines):
                    if line.strip() and not line.startswith('DATE'):
                        parts = line.split(',')
                        if len(parts) >= 2:
                            try:
                                date_str = parts[0].strip().strip('"')
                                value_str = parts[1].strip().strip('"')
                                if value_str and value_str != '':
                                    return {
                                        'value': float(value_str),
                                        'date': date_str,
                                        'source': 'Bank of England'
                                    }
                            except (ValueError, IndexError):
                                continue
        except Exception as e:
            print(f"[BoE Error] {e}")
        return None

    # =========================================================================
    # EUROPEAN CENTRAL BANK (EUR)
    # =========================================================================
    
    def _fetch_ecb_rate(self) -> Optional[Dict]:
        """ECB - Deposit Facility Rate."""
        # ECB Statistical Data Warehouse - SDMX REST API
        # FM.D.U2.EUR.4F.KR.DFR.LEV = Deposit Facility Rate
        url = "https://data-api.ecb.europa.eu/service/data/FM/D.U2.EUR.4F.KR.DFR.LEV"
        params = {
            'lastNObservations': '1',
            'format': 'jsondata'
        }
        
        try:
            headers = {'Accept': 'application/json'}
            response = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
            
            if response.status_code == 200:
                data = response.json()
                # Parse SDMX-JSON format
                try:
                    datasets = data.get('dataSets', [{}])
                    if datasets:
                        series = datasets[0].get('series', {})
                        for key, ser in series.items():
                            obs = ser.get('observations', {})
                            if obs:
                                # Prendi ultima osservazione
                                last_key = max(obs.keys(), key=int)
                                value = obs[last_key][0]
                                
                                # Estrai data
                                structure = data.get('structure', {})
                                dims = structure.get('dimensions', {}).get('observation', [])
                                time_dim = next((d for d in dims if d.get('id') == 'TIME_PERIOD'), None)
                                date_str = ''
                                if time_dim:
                                    values = time_dim.get('values', [])
                                    if values and int(last_key) < len(values):
                                        date_str = values[int(last_key)].get('id', '')
                                
                                return {
                                    'value': float(value),
                                    'date': date_str,
                                    'source': 'ECB'
                                }
                except Exception as parse_err:
                    print(f"[ECB Parse Error] {parse_err}")
                    
        except Exception as e:
            print(f"[ECB Error] {e}")
        return None

    # =========================================================================
    # SWISS NATIONAL BANK (CHF)
    # =========================================================================
    
    def _fetch_snb_rate(self) -> Optional[Dict]:
        """SNB - Policy Rate."""
        # SNB Data Portal
        url = "https://data.snb.ch/api/cube/snboffzisa/data/csv/en"
        
        try:
            response = self.session.get(url, timeout=self.timeout)
            if response.status_code == 200:
                lines = response.text.strip().split('\n')
                # Trova ultima riga con dati
                for line in reversed(lines[1:]):  # Skip header
                    parts = line.split(';')
                    if len(parts) >= 2:
                        try:
                            date_str = parts[0].strip()
                            value_str = parts[-1].strip().replace(',', '.')
                            if value_str and value_str not in ['', '-']:
                                return {
                                    'value': float(value_str),
                                    'date': date_str,
                                    'source': 'SNB'
                                }
                        except (ValueError, IndexError):
                            continue
        except Exception as e:
            print(f"[SNB Error] {e}")
        return None

    # =========================================================================
    # BANK OF JAPAN (JPY)
    # =========================================================================
    
    def _fetch_boj_rate(self) -> Optional[Dict]:
        """Bank of Japan - Policy Rate."""
        # BoJ non ha un'API REST semplice, usiamo la pagina delle statistiche
        # Prova prima con il loro time series data
        
        try:
            # BoJ Time Series - Call Rate (proxy per policy rate)
            url = "https://www.stat-search.boj.or.jp/ssi/mtshtml/fm08_m_1_en.html"
            response = self.session.get(url, timeout=self.timeout)
            
            if response.status_code == 200:
                # Parse HTML per estrarre il tasso
                text = response.text
                # Cerca pattern di tassi di interesse
                # Il formato BoJ è complesso, cerchiamo valori recenti
                
                # Pattern per trovare valori percentuali piccoli (0.xx o x.xx)
                rate_pattern = r'(\d{4}[-/]\d{2}).*?(\d+\.\d+)'
                matches = re.findall(rate_pattern, text)
                
                if matches:
                    # Filtra per valori sensati (< 5%)
                    for date_str, rate in reversed(matches):
                        rate_val = float(rate)
                        if 0 <= rate_val < 5:
                            return {
                                'value': rate_val,
                                'date': date_str,
                                'source': 'Bank of Japan'
                            }
        except Exception as e:
            print(f"[BoJ Error] {e}")
        
        return None

    # =========================================================================
    # METODI PUBBLICI - Con fallback automatico
    # =========================================================================
    
    def _validate_rate(self, value: float) -> bool:
        """Verifica che il tasso sia sensato."""
        return value is not None and -2 <= value <= 25
    
    def _validate_inflation(self, value: float) -> bool:
        """Verifica che l'inflazione sia sensata."""
        return value is not None and -10 <= value <= 50
    
    def _validate_unemployment(self, value: float) -> bool:
        """Verifica che la disoccupazione sia sensata."""
        return value is not None and 0 <= value <= 35
    
    def _validate_gdp(self, value: float) -> bool:
        """Verifica che il PIL sia sensato."""
        return value is not None and -20 <= value <= 25
    
    def _validate_bci(self, value: float) -> bool:
        """Verifica che il BCI sia sensato."""
        return value is not None and 70 <= value <= 130

    def get_interest_rate(self, currency: str) -> Optional[Dict]:
        """Recupera tasso di interesse SOLO da API nativa (no fallback FRED)."""
        result = None
        
        # API nativa per ogni banca centrale
        if currency == 'USD':
            # Per USD usiamo FRED perché È la fonte primaria (Federal Reserve)
            code = self.fred_codes['interest_rate'].get(currency)
            if code:
                result = self._fetch_fred(code)
        elif currency == 'CAD':
            result = self._fetch_boc_rate()
        elif currency == 'AUD':
            result = self._fetch_rba_rate()
        elif currency == 'GBP':
            result = self._fetch_boe_rate()
        elif currency == 'EUR':
            result = self._fetch_ecb_rate()
        elif currency == 'CHF':
            result = self._fetch_snb_rate()
        elif currency == 'JPY':
            result = self._fetch_boj_rate()
        
        # Valida risultato (NO FALLBACK - così vediamo se l'API funziona)
        if result and self._validate_rate(result.get('value')):
            print(f"  [OK] {currency} rate from {result['source']}: {result['value']}%")
            return result
        
        # Se arriviamo qui, l'API nativa ha fallito
        print(f"  [FAIL] {currency} rate: API nativa non ha restituito dati validi")
        return None

    def get_inflation(self, currency: str) -> Optional[Dict]:
        """Recupera inflazione YoY."""
        # Per inflazione FRED è generalmente affidabile
        code = self.fred_codes['inflation'].get(currency)
        if code:
            result = self._fetch_fred(code)
            if result and self._validate_inflation(result.get('value')):
                return result
        return None

    def get_gdp_growth(self, currency: str) -> Optional[Dict]:
        """Recupera crescita PIL."""
        code = self.fred_codes['gdp_growth'].get(currency)
        if code:
            result = self._fetch_fred(code)
            if result and self._validate_gdp(result.get('value')):
                return result
        return None

    def get_unemployment(self, currency: str) -> Optional[Dict]:
        """Recupera disoccupazione."""
        code = self.fred_codes['unemployment'].get(currency)
        if code:
            result = self._fetch_fred(code)
            if result and self._validate_unemployment(result.get('value')):
                return result
        return None

    def get_business_confidence(self, currency: str) -> Optional[Dict]:
        """Recupera Business Confidence Index."""
        code = self.fred_codes['business_confidence'].get(currency)
        if code:
            result = self._fetch_fred(code)
            if result and self._validate_bci(result.get('value')):
                return result
        return None

    # =========================================================================
    # METODO PRINCIPALE
    # =========================================================================
    
    def get_all_data(self, currencies: List[str] = None) -> Dict:
        """Recupera tutti i dati per tutte le valute."""
        if currencies is None:
            currencies = self.currencies
            
        results = {
            'timestamp': datetime.now().isoformat(),
            'source': 'API Banche Centrali + FRED fallback',
            'data': {}
        }
        
        for currency in currencies:
            print(f"\n[Fetching] {currency}...")
            
            results['data'][currency] = {
                'country': self.currency_names.get(currency, currency),
                'indicators': {}
            }
            
            # Fetch indicatori
            indicators = [
                ('interest_rate', self.get_interest_rate),
                ('inflation', self.get_inflation),
                ('gdp_growth', self.get_gdp_growth),
                ('unemployment', self.get_unemployment),
                ('business_confidence', self.get_business_confidence),
            ]
            
            for ind_key, fetcher in indicators:
                try:
                    data = fetcher(currency)
                    results['data'][currency]['indicators'][ind_key] = {
                        'name': self.indicator_names[ind_key],
                        'value': data['value'] if data else None,
                        'date': data['date'] if data else None,
                        'source': data['source'] if data else 'N/A'
                    }
                except Exception as e:
                    print(f"  [Error] {ind_key}: {e}")
                    results['data'][currency]['indicators'][ind_key] = {
                        'name': self.indicator_names[ind_key],
                        'value': None,
                        'date': None,
                        'source': f'Error: {str(e)}'
                    }
                
                time.sleep(0.3)  # Rate limiting
        
        return results

    def format_for_display(self, data: Dict = None) -> str:
        """Formatta i dati per visualizzazione."""
        if data is None:
            data = self.get_all_data()
            
        lines = []
        lines.append("=" * 90)
        lines.append("📊 DATI MACROECONOMICI")
        lines.append("=" * 90)
        lines.append(f"Aggiornamento: {data['timestamp'][:19]}")
        lines.append("")
        
        header = f"{'Valuta':<6} {'Tasso%':<10} {'Infl%':<10} {'PIL%':<10} {'Disocc%':<10} {'BCI':<10}"
        lines.append(header)
        lines.append("-" * 90)
        
        for currency, info in data['data'].items():
            ind = info['indicators']
            
            def fmt(key):
                val = ind.get(key, {}).get('value')
                return f"{val:.2f}" if val is not None else 'N/A'
            
            line = f"{currency:<6} {fmt('interest_rate'):<10} {fmt('inflation'):<10} {fmt('gdp_growth'):<10} {fmt('unemployment'):<10} {fmt('business_confidence'):<10}"
            lines.append(line)
        
        return "\n".join(lines)


# =============================================================================
# TEST
# =============================================================================

if __name__ == "__main__":
    import os
    
    api_key = os.environ.get('FRED_API_KEY')
    
    if not api_key:
        print("=" * 50)
        print("TEST MacroDataFetcher v3.0")
        print("API Native + FRED Fallback")
        print("=" * 50)
        api_key = input("\nFRED API Key: ").strip()
    
    if api_key:
        fetcher = MacroDataFetcher(api_key)
        
        # Test singole API
        print("\n--- Test API Native ---")
        
        print("\n[CAD] Bank of Canada:")
        boc = fetcher._fetch_boc_rate()
        print(f"  Result: {boc}")
        
        print("\n[AUD] RBA:")
        rba = fetcher._fetch_rba_rate()
        print(f"  Result: {rba}")
        
        print("\n[GBP] Bank of England:")
        boe = fetcher._fetch_boe_rate()
        print(f"  Result: {boe}")
        
        print("\n[EUR] ECB:")
        ecb = fetcher._fetch_ecb_rate()
        print(f"  Result: {ecb}")
        
        print("\n[CHF] SNB:")
        snb = fetcher._fetch_snb_rate()
        print(f"  Result: {snb}")
        
        # Test completo
        print("\n\n--- Test Completo ---")
        data = fetcher.get_all_data()
        print(fetcher.format_for_display(data))
