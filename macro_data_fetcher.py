"""
Macro Data Fetcher v6 - Hybrid Solution
========================================
Soluzione ibrida gratuita:
- Tassi interesse: global-rates.com (scraping)
- Inflazione: global-rates.com (scraping) + ABS per Australia
- PIL Growth: API Ninjas (gratuito)
- Disoccupazione: API Ninjas (gratuito)

Fonti:
- global-rates.com: tassi interesse e inflazione (6 paesi)
- abs.gov.au: inflazione Australia (fonte ufficiale ABS)
- api-ninjas.com: PIL e disoccupazione

Requisiti:
- requests
- API Key gratuita da api-ninjas.com
"""

import requests
from datetime import datetime
from typing import Dict, Optional, List
import time
import re


class MacroDataFetcher:
    """
    Fetcher per dati macroeconomici.
    Usa global-rates.com per tassi e inflazione.
    Usa API Ninjas per PIL e disoccupazione.
    """
    
    def __init__(self, api_ninjas_key: str):
        self.api_ninjas_key = api_ninjas_key
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        })
        self.timeout = 30
        
        # Cache
        self._rates_cache = None
        self._inflation_cache = None
        self._cache_time = None
        self._cache_duration = 300  # 5 minuti
        
        # Metadata
        self.currencies = ['USD', 'EUR', 'GBP', 'JPY', 'CHF', 'AUD', 'CAD']
        self.currency_names = {
            'USD': 'Stati Uniti', 'EUR': 'Eurozona', 'GBP': 'Regno Unito',
            'JPY': 'Giappone', 'CHF': 'Svizzera', 'AUD': 'Australia', 'CAD': 'Canada'
        }
        
        # Mapping per global-rates.com
        self.rate_country_mapping = {
            'USD': 'United States',
            'EUR': 'Europe',
            'GBP': 'United Kingdom',
            'JPY': 'Japan',
            'CHF': 'Switzerland',
            'AUD': 'Australia',
            'CAD': 'Canada',
        }
        
        # Mapping per inflazione (CPI)
        self.inflation_country_mapping = {
            'USD': 'United States',
            'EUR': 'Europe',  # HICP
            'GBP': 'United Kingdom',
            'JPY': 'Japan',
            'CHF': 'Switzerland',
            'AUD': 'Australia',
            'CAD': 'Canada',
        }
        
        # Mapping per API Ninjas
        self.country_names_ninjas = {
            'USD': 'United States',
            'EUR': 'Germany',  # Proxy per Eurozona
            'GBP': 'United Kingdom',
            'JPY': 'Japan',
            'CHF': 'Switzerland',
            'AUD': 'Australia',
            'CAD': 'Canada',
        }
        
        self.iso_codes = {
            'USD': 'US',
            'EUR': 'DE',
            'GBP': 'GB',
            'JPY': 'JP',
            'CHF': 'CH',
            'AUD': 'AU',
            'CAD': 'CA',
        }

    # =========================================================================
    # GLOBAL-RATES.COM - Scraping
    # =========================================================================
    
    def _fetch_interest_rates_globalrates(self) -> Dict[str, float]:
        """Scrapa tassi di interesse da global-rates.com"""
        if self._rates_cache and self._cache_time:
            elapsed = (datetime.now() - self._cache_time).total_seconds()
            if elapsed < self._cache_duration:
                return self._rates_cache
        
        url = "https://www.global-rates.com/en/interest-rates/central-banks/"
        rates = {}
        
        try:
            response = self.session.get(url, timeout=self.timeout)
            if response.status_code == 200:
                text = response.text
                
                # Pattern per estrarre dalla tabella:
                # | Country/Region | Current | Direction | Previous | Change |
                # Cerca righe con: Country | X.XX % |
                
                for currency, country in self.rate_country_mapping.items():
                    # Pattern specifico per ogni paese
                    # Cerca: "United States" seguito da valore percentuale
                    pattern = rf'{re.escape(country)}\s*\|\s*([\d.]+)\s*%'
                    match = re.search(pattern, text, re.IGNORECASE)
                    
                    if match:
                        rate = float(match.group(1))
                        rates[currency] = rate
                
                if rates:
                    self._rates_cache = rates
                    self._cache_time = datetime.now()
                    
        except Exception as e:
            print(f"[Error] global-rates.com interest rates: {e}")
        
        return rates
    
    def _fetch_inflation_globalrates(self) -> Dict[str, float]:
        """Scrapa inflazione da global-rates.com"""
        if self._inflation_cache and self._cache_time:
            elapsed = (datetime.now() - self._cache_time).total_seconds()
            if elapsed < self._cache_duration:
                return self._inflation_cache
        
        url = "https://www.global-rates.com/en/inflation/"
        inflation = {}
        
        try:
            response = self.session.get(url, timeout=self.timeout)
            if response.status_code == 200:
                text = response.text
                
                for currency, country in self.inflation_country_mapping.items():
                    # Salta Australia - usiamo ABS
                    if currency == 'AUD':
                        continue
                    
                    # Per EUR usa HICP Europe
                    if currency == 'EUR':
                        # Pattern per HICP Europe
                        pattern = r'HICP Europe.*?\|\s*Europe\s*\|\s*HICP\s*\|[^|]*\|[^|]*\|\s*([\d.-]+)\s*%'
                    else:
                        # Pattern per CPI altri paesi
                        pattern = rf'CPI {re.escape(country)}.*?\|[^|]*\|[^|]*\|[^|]*\|[^|]*\|\s*([\d.-]+)\s*%'
                    
                    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
                    
                    if match:
                        infl = float(match.group(1))
                        inflation[currency] = infl
                
                if inflation:
                    self._inflation_cache = inflation
                    
        except Exception as e:
            print(f"[Error] global-rates.com inflation: {e}")
        
        # Fetch Australia da ABS (fonte ufficiale)
        aus_inflation = self._fetch_inflation_abs()
        if aus_inflation is not None:
            inflation['AUD'] = aus_inflation
        
        return inflation
    
    def _fetch_inflation_abs(self) -> Optional[float]:
        """Scrapa inflazione Australia da ABS (Australian Bureau of Statistics)
        
        Il sito ABS usa sempre la stessa struttura:
        - "The Consumer Price Index (CPI) rose/fell X.X% in the 12 months to [Month Year]"
        - Tabella con colonne: Month | Monthly change | Annual change
        
        Pattern robusti per gestire aggiornamenti futuri.
        """
        url = "https://www.abs.gov.au/statistics/economy/price-indexes-and-inflation/consumer-price-index-australia/latest-release"
        
        try:
            response = self.session.get(url, timeout=self.timeout)
            if response.status_code == 200:
                text = response.text
                
                # Pattern 1: "CPI rose/fell X.X% in the 12 months"
                # Gestisce sia aumenti (rose) che diminuzioni (fell)
                pattern = r'CPI\)?\s+(?:rose|fell)\s+([\d.]+)%\s+in\s+the\s+12\s+months'
                match = re.search(pattern, text, re.IGNORECASE)
                
                if match:
                    value = float(match.group(1))
                    # Se "fell", il valore è negativo
                    if 'fell' in match.group(0).lower():
                        value = -value
                    return value
                
                # Pattern 2: Tabella "All groups CPI" - cerca l'ultima riga
                # Formato: MMM-YY | monthly% | annual%
                # Esempio: "Oct-25 | 0.0 | 3.8" o "Nov-25 | 0.1 | 3.5"
                pattern2 = r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-\d{2}\s*\|\s*[\d.-]+\s*\|\s*([\d.]+)'
                matches = re.findall(pattern2, text)
                if matches:
                    # Prendi l'ultimo valore (dato più recente)
                    return float(matches[-1])
                
                # Pattern 3: Cerca nella sezione "Key statistics"
                # "annual change" o "12 months" seguito da percentuale
                pattern3 = r'(?:annual\s+change|12\s+months)[^\d]*?([\d.]+)\s*%'
                match3 = re.search(pattern3, text, re.IGNORECASE)
                if match3:
                    return float(match3.group(1))
                    
        except Exception as e:
            print(f"[Error] ABS Australia inflation: {e}")
        
        return None

    # =========================================================================
    # API NINJAS
    # =========================================================================
    
    def _fetch_gdp_ninjas(self, currency: str) -> Optional[Dict]:
        """Recupera PIL da API Ninjas."""
        iso = self.iso_codes.get(currency)
        if not iso:
            return None
        
        url = f"https://api.api-ninjas.com/v1/gdp?country={iso}"
        headers = {'X-Api-Key': self.api_ninjas_key}
        
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data and len(data) > 0:
                    latest = max(data, key=lambda x: x.get('year', 0))
                    return {
                        'value': latest.get('gdp_growth'),
                        'year': latest.get('year'),
                        'source': 'API Ninjas'
                    }
        except Exception as e:
            print(f"[Error] API Ninjas GDP {currency}: {e}")
        return None
    
    def _fetch_unemployment_ninjas(self, currency: str) -> Optional[Dict]:
        """Recupera disoccupazione da API Ninjas."""
        country = self.country_names_ninjas.get(currency)
        if not country:
            return None
        
        url = f"https://api.api-ninjas.com/v1/country?name={country}"
        headers = {'X-Api-Key': self.api_ninjas_key}
        
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data and len(data) > 0:
                    unemp = data[0].get('unemployment')
                    if unemp:
                        return {
                            'value': float(unemp),
                            'source': 'API Ninjas'
                        }
        except Exception as e:
            print(f"[Error] API Ninjas Unemployment {currency}: {e}")
        return None

    # =========================================================================
    # METODI PUBBLICI
    # =========================================================================
    
    def get_interest_rate(self, currency: str) -> Optional[Dict]:
        """Recupera tasso di interesse."""
        rates = self._fetch_interest_rates_globalrates()
        if currency in rates:
            return {
                'value': rates[currency],
                'date': datetime.now().strftime('%Y-%m-%d'),
                'source': 'global-rates.com'
            }
        return None
    
    def get_inflation(self, currency: str) -> Optional[Dict]:
        """Recupera inflazione YoY."""
        inflation = self._fetch_inflation_globalrates()
        if currency in inflation:
            # Per AUD la fonte è ABS
            source = 'ABS (abs.gov.au)' if currency == 'AUD' else 'global-rates.com'
            return {
                'value': inflation[currency],
                'date': datetime.now().strftime('%Y-%m-%d'),
                'source': source
            }
        return None
    
    def get_gdp_growth(self, currency: str) -> Optional[Dict]:
        """Recupera crescita PIL."""
        return self._fetch_gdp_ninjas(currency)
    
    def get_unemployment(self, currency: str) -> Optional[Dict]:
        """Recupera disoccupazione."""
        return self._fetch_unemployment_ninjas(currency)

    # =========================================================================
    # METODO PRINCIPALE
    # =========================================================================
    
    def get_all_data(self, currencies: List[str] = None) -> Dict:
        """Recupera tutti i dati per tutte le valute."""
        if currencies is None:
            currencies = self.currencies
        
        results = {
            'timestamp': datetime.now().isoformat(),
            'source': 'global-rates.com + API Ninjas',
            'data': {}
        }
        
        # Fetch batch da global-rates
        print("[1/4] Fetching interest rates from global-rates.com...")
        all_rates = self._fetch_interest_rates_globalrates()
        
        print("[2/4] Fetching inflation from global-rates.com...")
        all_inflation = self._fetch_inflation_globalrates()
        
        for currency in currencies:
            print(f"[3/4] Processing {currency}...")
            
            results['data'][currency] = {
                'country': self.currency_names.get(currency, currency),
                'indicators': {}
            }
            
            # Tasso interesse
            rate = all_rates.get(currency)
            results['data'][currency]['indicators']['interest_rate'] = {
                'value': rate,
                'source': 'global-rates.com' if rate else 'N/A'
            }
            
            # Inflazione
            infl = all_inflation.get(currency)
            # Per AUD la fonte è ABS, per altri global-rates.com
            infl_source = 'ABS' if currency == 'AUD' and infl else ('global-rates.com' if infl else 'N/A')
            results['data'][currency]['indicators']['inflation'] = {
                'value': infl,
                'source': infl_source
            }
            
            # PIL (API Ninjas)
            gdp = self._fetch_gdp_ninjas(currency)
            results['data'][currency]['indicators']['gdp_growth'] = {
                'value': gdp['value'] if gdp else None,
                'source': 'API Ninjas' if gdp else 'N/A'
            }
            
            # Disoccupazione (API Ninjas)
            unemp = self._fetch_unemployment_ninjas(currency)
            results['data'][currency]['indicators']['unemployment'] = {
                'value': unemp['value'] if unemp else None,
                'source': 'API Ninjas' if unemp else 'N/A'
            }
            
            time.sleep(0.3)  # Rate limiting
        
        print("[4/4] Done!")
        return results

    def format_for_display(self, data: Dict = None) -> str:
        """Formatta i dati per visualizzazione."""
        if data is None:
            data = self.get_all_data()
        
        lines = []
        lines.append("=" * 80)
        lines.append("📊 DATI MACROECONOMICI")
        lines.append("=" * 80)
        lines.append(f"Timestamp: {data['timestamp'][:19]}")
        lines.append(f"Fonti: global-rates.com (tassi) | global-rates.com + ABS (inflazione)")
        lines.append(f"       API Ninjas (PIL, disoccupazione)")
        lines.append("")
        lines.append(f"{'Valuta':<8} {'Tasso%':<12} {'Inflaz%':<12} {'PIL%':<12} {'Disocc%':<12}")
        lines.append("-" * 80)
        
        for currency, info in data['data'].items():
            ind = info['indicators']
            
            def fmt(key):
                val = ind.get(key, {}).get('value')
                return f"{val:.2f}" if val is not None else "N/A"
            
            line = f"{currency:<8} {fmt('interest_rate'):<12} {fmt('inflation'):<12} {fmt('gdp_growth'):<12} {fmt('unemployment'):<12}"
            lines.append(line)
        
        lines.append("=" * 80)
        return "\n".join(lines)


# =============================================================================
# TEST
# =============================================================================

if __name__ == "__main__":
    import os
    
    # API Key da variabile d'ambiente o config
    API_KEY = os.environ.get('API_NINJAS_KEY', '')
    
    if not API_KEY:
        print("⚠️  Imposta la variabile d'ambiente API_NINJAS_KEY")
        print("   export API_NINJAS_KEY='tua_api_key'")
        print("   Ottieni una API key gratuita su: https://api-ninjas.com")
        exit(1)
    
    print("=" * 80)
    print("TEST MacroDataFetcher v6.0 - Hybrid Solution")
    print("=" * 80)
    
    fetcher = MacroDataFetcher(API_KEY)
    
    # Test completo
    data = fetcher.get_all_data()
    print(fetcher.format_for_display(data))
