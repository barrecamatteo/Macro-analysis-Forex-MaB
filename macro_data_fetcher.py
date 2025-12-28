"""
Macro Data Fetcher v4 - Web Scraping Pagine Ufficiali Banche Centrali
======================================================================
Recupera i tassi direttamente dalle pagine ufficiali delle banche centrali.
NO API complesse, solo scraping HTML delle pagine pubbliche.

Fonti:
- USD: FRED API (Federal Reserve)
- EUR: ECB Data Portal  
- GBP: Bank of England Database
- JPY: Bank of Japan (via web scraping)
- CHF: SNB Website
- AUD: RBA Statistics
- CAD: Bank of Canada Valet API
"""

import requests
from datetime import datetime
from typing import Dict, Optional, List
import time
import re


class MacroDataFetcher:
    """
    Fetcher per dati macroeconomici.
    Usa web scraping delle pagine ufficiali delle banche centrali.
    """
    
    def __init__(self, fred_api_key: str):
        self.fred_api_key = fred_api_key
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        })
        self.timeout = 25
        
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
        
        # FRED codes per altri indicatori (inflazione, PIL, disoccupazione, BCI)
        self.fred_codes = {
            'inflation': {
                'USD': 'CPALTT01USM657N',
                'EUR': 'EA19CPALTT01GYM',
                'GBP': 'CPALTT01GBM657N',
                'JPY': 'CPALTT01JPM657N',
                'CHF': 'CPALTT01CHM657N',
                'AUD': 'CPALTT01AUQ657N',
                'CAD': 'CPALTT01CAM657N',
            },
            'gdp_growth': {
                'USD': 'A191RL1Q225SBEA',
                'EUR': 'NAEXKP01EZQ657S',
                'GBP': 'NAEXKP01GBQ657S',
                'JPY': 'NAEXKP01JPQ657S',
                'CHF': 'NAEXKP01CHQ657S',
                'AUD': 'NAEXKP01AUQ657S',
                'CAD': 'NAEXKP01CAQ657S',
            },
            'unemployment': {
                'USD': 'UNRATE',
                'EUR': 'LRHUTTTTEZM156S',
                'GBP': 'LRHUTTTTGBM156S',
                'JPY': 'LRHUTTTTJPM156S',
                'CHF': 'LRHUTTTTCHM156S',
                'AUD': 'LRHUTTTTAUM156S',
                'CAD': 'LRHUTTTTCAM156S',
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
    # FRED API (per indicatori diversi dai tassi)
    # =========================================================================
    
    def _fetch_fred(self, series_id: str) -> Optional[Dict]:
        """Recupera dati da FRED API."""
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            'series_id': series_id,
            'api_key': self.fred_api_key,
            'file_type': 'json',
            'sort_order': 'desc',
            'limit': 5
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
            print(f"    [FRED Error] {series_id}: {e}")
        return None

    # =========================================================================
    # TASSI DI INTERESSE - Web Scraping Banche Centrali
    # =========================================================================

    def _fetch_fed_rate(self) -> Optional[Dict]:
        """Federal Reserve - Fed Funds Target Rate (upper bound)."""
        # Usa FRED per la Fed (è la fonte ufficiale)
        try:
            result = self._fetch_fred('DFEDTARU')  # Upper bound
            if result:
                result['source'] = 'Federal Reserve (FRED)'
            return result
        except Exception as e:
            print(f"    [Fed Error] {e}")
        return None

    def _fetch_ecb_rate(self) -> Optional[Dict]:
        """ECB - Deposit Facility Rate."""
        # Scraping dalla pagina ECB
        url = "https://www.ecb.europa.eu/stats/policy_and_exchange_rates/key_ecb_interest_rates/html/index.en.html"
        
        try:
            response = self.session.get(url, timeout=self.timeout)
            if response.status_code == 200:
                text = response.text
                
                # Cerca il Deposit facility rate
                # Pattern: cerca numeri dopo "Deposit facility"
                patterns = [
                    r'Deposit\s+facility[^0-9]*?(\d+\.?\d*)\s*%',
                    r'deposit\s+facility[^0-9]*?(\d+\.?\d*)',
                    r'(\d+\.\d+)\s*</td>\s*</tr>\s*</tbody>',  # Ultima riga tabella
                ]
                
                for pattern in patterns:
                    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
                    if match:
                        rate = float(match.group(1))
                        if 0 <= rate <= 10:  # Sanity check
                            return {
                                'value': rate,
                                'date': datetime.now().strftime('%Y-%m-%d'),
                                'source': 'ECB'
                            }
        except Exception as e:
            print(f"    [ECB Error] {e}")
        
        # Fallback: usa FRED
        try:
            result = self._fetch_fred('ECBDFR')
            if result:
                result['source'] = 'ECB (via FRED)'
            return result
        except:
            pass
        
        return None

    def _fetch_boe_rate(self) -> Optional[Dict]:
        """Bank of England - Bank Rate."""
        url = "https://www.bankofengland.co.uk/boeapps/database/Bank-Rate.asp"
        
        try:
            response = self.session.get(url, timeout=self.timeout)
            if response.status_code == 200:
                text = response.text
                
                # Cerca "Current official Bank Rate" seguito dal valore
                # Pattern nella pagina: <div>Current official Bank Rate</div><div>3.75%</div>
                patterns = [
                    r'Current\s+official\s+Bank\s+Rate[^0-9]*?(\d+\.?\d*)\s*%',
                    r'>(\d+\.\d+)%?</div>\s*</div>\s*<h3',  # Prima del titolo
                    r'Bank Rate.*?(\d+\.\d+)\s*%',
                ]
                
                for pattern in patterns:
                    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
                    if match:
                        rate = float(match.group(1))
                        if 0 <= rate <= 15:
                            return {
                                'value': rate,
                                'date': datetime.now().strftime('%Y-%m-%d'),
                                'source': 'Bank of England'
                            }
                
                # Cerca nella tabella - prima riga dopo header
                table_pattern = r'<tr[^>]*>\s*<td[^>]*>(\d{1,2}\s+\w+\s+\d{2,4})</td>\s*<td[^>]*>([^<]+)</td>'
                matches = re.findall(table_pattern, text)
                if matches:
                    date_str, rate_str = matches[0]
                    rate = float(rate_str.strip())
                    if 0 <= rate <= 15:
                        return {
                            'value': rate,
                            'date': date_str,
                            'source': 'Bank of England'
                        }
                        
        except Exception as e:
            print(f"    [BoE Error] {e}")
        
        return None

    def _fetch_boj_rate(self) -> Optional[Dict]:
        """Bank of Japan - Policy Rate."""
        # Il BoJ non ha un'API semplice, proviamo la pagina delle statistiche
        url = "https://www.boj.or.jp/en/statistics/boj/other/coredata/index.htm"
        
        try:
            response = self.session.get(url, timeout=self.timeout)
            if response.status_code == 200:
                text = response.text
                
                # Cerca pattern per policy rate
                patterns = [
                    r'Policy.?Rate[^0-9]*?(\d+\.?\d*)\s*%',
                    r'Uncollateralized\s+Overnight\s+Call\s+Rate[^0-9]*?(\d+\.?\d*)',
                    r'(\d+\.\d+)\s*percent',
                ]
                
                for pattern in patterns:
                    match = re.search(pattern, text, re.IGNORECASE)
                    if match:
                        rate = float(match.group(1))
                        if 0 <= rate <= 5:
                            return {
                                'value': rate,
                                'date': datetime.now().strftime('%Y-%m-%d'),
                                'source': 'Bank of Japan'
                            }
        except Exception as e:
            print(f"    [BoJ Error] {e}")
        
        # Fallback: FRED
        try:
            result = self._fetch_fred('IRSTCI01JPM156N')
            if result:
                result['source'] = 'BoJ (via FRED)'
            return result
        except:
            pass
        
        return None

    def _fetch_snb_rate(self) -> Optional[Dict]:
        """Swiss National Bank - Policy Rate."""
        url = "https://www.snb.ch/en/the-snb/mandates-goals/monetary-policy/decisions"
        
        try:
            response = self.session.get(url, timeout=self.timeout)
            if response.status_code == 200:
                text = response.text
                
                # Cerca il policy rate
                patterns = [
                    r'policy\s+rate[^0-9]*?(-?\d+\.?\d*)\s*%',
                    r'SNB\s+policy\s+rate[^0-9]*?(-?\d+\.?\d*)',
                    r'interest\s+rate[^0-9]*?(-?\d+\.?\d*)\s*%',
                ]
                
                for pattern in patterns:
                    match = re.search(pattern, text, re.IGNORECASE)
                    if match:
                        rate = float(match.group(1))
                        if -2 <= rate <= 5:
                            return {
                                'value': rate,
                                'date': datetime.now().strftime('%Y-%m-%d'),
                                'source': 'SNB'
                            }
        except Exception as e:
            print(f"    [SNB Error] {e}")
        
        # Fallback: FRED
        try:
            result = self._fetch_fred('IRSTCI01CHM156N')
            if result:
                result['source'] = 'SNB (via FRED)'
            return result
        except:
            pass
        
        return None

    def _fetch_rba_rate(self) -> Optional[Dict]:
        """Reserve Bank of Australia - Cash Rate Target."""
        url = "https://www.rba.gov.au/statistics/cash-rate/"
        
        try:
            response = self.session.get(url, timeout=self.timeout)
            if response.status_code == 200:
                text = response.text
                
                # Cerca nella tabella delle decisioni
                # La tabella ha: Date | Change | Rate | Documents
                patterns = [
                    r'Cash\s+rate\s+target[^0-9]*?(\d+\.?\d*)\s*%',
                    r'<td[^>]*>(\d+\.\d+)</td>\s*<td[^>]*>\s*<a[^>]*>Statement',
                    r'>(\d+\.\d+)</td>\s*</tr>',  # Rate nella tabella
                ]
                
                for pattern in patterns:
                    matches = re.findall(pattern, text, re.IGNORECASE | re.DOTALL)
                    if matches:
                        # Prendi il primo valore (più recente)
                        rate = float(matches[0]) if isinstance(matches[0], str) else float(matches[0])
                        if 0 <= rate <= 15:
                            return {
                                'value': rate,
                                'date': datetime.now().strftime('%Y-%m-%d'),
                                'source': 'RBA'
                            }
        except Exception as e:
            print(f"    [RBA Error] {e}")
        
        # Fallback: FRED
        try:
            result = self._fetch_fred('IRSTCI01AUM156N')
            if result:
                result['source'] = 'RBA (via FRED)'
            return result
        except:
            pass
        
        return None

    def _fetch_boc_rate(self) -> Optional[Dict]:
        """Bank of Canada - Overnight Rate Target."""
        url = "https://www.bankofcanada.ca/valet/observations/STATIC_ATABLE_V39079/json?recent=1"
        
        try:
            response = self.session.get(url, timeout=self.timeout)
            if response.status_code == 200:
                data = response.json()
                observations = data.get('observations', [])
                if observations:
                    obs = observations[-1]
                    date_str = obs.get('d', '')
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
            print(f"    [BoC Error] {e}")
        
        return None

    # =========================================================================
    # METODI PUBBLICI
    # =========================================================================
    
    def _validate_rate(self, value: float) -> bool:
        return value is not None and -2 <= value <= 25
    
    def _validate_inflation(self, value: float) -> bool:
        return value is not None and -10 <= value <= 50
    
    def _validate_unemployment(self, value: float) -> bool:
        return value is not None and 0 <= value <= 35
    
    def _validate_gdp(self, value: float) -> bool:
        return value is not None and -20 <= value <= 25
    
    def _validate_bci(self, value: float) -> bool:
        return value is not None and 70 <= value <= 130

    def get_interest_rate(self, currency: str) -> Optional[Dict]:
        """Recupera tasso di interesse dalla banca centrale."""
        result = None
        
        print(f"  Fetching {currency} interest rate...")
        
        if currency == 'USD':
            result = self._fetch_fed_rate()
        elif currency == 'EUR':
            result = self._fetch_ecb_rate()
        elif currency == 'GBP':
            result = self._fetch_boe_rate()
        elif currency == 'JPY':
            result = self._fetch_boj_rate()
        elif currency == 'CHF':
            result = self._fetch_snb_rate()
        elif currency == 'AUD':
            result = self._fetch_rba_rate()
        elif currency == 'CAD':
            result = self._fetch_boc_rate()
        
        if result and self._validate_rate(result.get('value')):
            print(f"    ✓ {currency}: {result['value']}% from {result['source']}")
            return result
        
        print(f"    ✗ {currency}: Failed to fetch rate")
        return None

    def get_inflation(self, currency: str) -> Optional[Dict]:
        """Recupera inflazione YoY da FRED."""
        code = self.fred_codes['inflation'].get(currency)
        if code:
            result = self._fetch_fred(code)
            if result and self._validate_inflation(result.get('value')):
                return result
        return None

    def get_gdp_growth(self, currency: str) -> Optional[Dict]:
        """Recupera crescita PIL da FRED."""
        code = self.fred_codes['gdp_growth'].get(currency)
        if code:
            result = self._fetch_fred(code)
            if result and self._validate_gdp(result.get('value')):
                return result
        return None

    def get_unemployment(self, currency: str) -> Optional[Dict]:
        """Recupera disoccupazione da FRED."""
        code = self.fred_codes['unemployment'].get(currency)
        if code:
            result = self._fetch_fred(code)
            if result and self._validate_unemployment(result.get('value')):
                return result
        return None

    def get_business_confidence(self, currency: str) -> Optional[Dict]:
        """Recupera Business Confidence Index da FRED."""
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
            'source': 'Banche Centrali + FRED',
            'data': {}
        }
        
        for currency in currencies:
            print(f"\n[{currency}] Fetching data...")
            
            results['data'][currency] = {
                'country': self.currency_names.get(currency, currency),
                'indicators': {}
            }
            
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
                    print(f"    [Error] {ind_key}: {e}")
                    results['data'][currency]['indicators'][ind_key] = {
                        'name': self.indicator_names[ind_key],
                        'value': None,
                        'date': None,
                        'source': f'Error'
                    }
                
                time.sleep(0.5)  # Rate limiting
        
        return results

    def format_for_display(self, data: Dict = None) -> str:
        """Formatta i dati per visualizzazione."""
        if data is None:
            data = self.get_all_data()
            
        lines = []
        lines.append("=" * 100)
        lines.append("📊 DATI MACROECONOMICI - Banche Centrali Ufficiali")
        lines.append("=" * 100)
        lines.append(f"Timestamp: {data['timestamp'][:19]}")
        lines.append("")
        
        header = f"{'Valuta':<6} {'Paese':<12} {'Tasso%':<10} {'Infl%':<10} {'PIL%':<10} {'Disocc%':<10} {'BCI':<10}"
        lines.append(header)
        lines.append("-" * 100)
        
        for currency, info in data['data'].items():
            ind = info['indicators']
            
            def fmt(key):
                val = ind.get(key, {}).get('value')
                return f"{val:.2f}" if val is not None else 'N/A'
            
            line = f"{currency:<6} {info['country']:<12} {fmt('interest_rate'):<10} {fmt('inflation'):<10} {fmt('gdp_growth'):<10} {fmt('unemployment'):<10} {fmt('business_confidence'):<10}"
            lines.append(line)
        
        lines.append("-" * 100)
        lines.append("\n📌 Fonti Tassi: Fed, ECB, BoE, BoJ, SNB, RBA, BoC")
        lines.append("📌 Altri indicatori: FRED (Federal Reserve Economic Data)")
        
        return "\n".join(lines)


# =============================================================================
# TEST
# =============================================================================

if __name__ == "__main__":
    import os
    
    api_key = os.environ.get('FRED_API_KEY')
    
    if not api_key:
        print("=" * 60)
        print("TEST MacroDataFetcher v4.0")
        print("Web Scraping Banche Centrali")
        print("=" * 60)
        api_key = input("\nFRED API Key: ").strip()
    
    if api_key:
        fetcher = MacroDataFetcher(api_key)
        
        print("\n" + "=" * 60)
        print("TEST TASSI DI INTERESSE")
        print("=" * 60)
        
        for curr in ['USD', 'EUR', 'GBP', 'JPY', 'CHF', 'AUD', 'CAD']:
            result = fetcher.get_interest_rate(curr)
            if result:
                print(f"  {curr}: {result['value']}% ({result['source']})")
            else:
                print(f"  {curr}: FAILED")
        
        print("\n" + "=" * 60)
        print("TEST COMPLETO")
        print("=" * 60)
        
        data = fetcher.get_all_data()
        print(fetcher.format_for_display(data))
