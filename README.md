# 📊 Forex Macro Analyst - Claude AI

[![Streamlit](https://img.shields.io/badge/Streamlit-FF4B4B?style=for-the-badge&logo=Streamlit&logoColor=white)](https://streamlit.io/)
[![Python](https://img.shields.io/badge/Python-3.9+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org/)
[![Anthropic](https://img.shields.io/badge/Claude_AI-Sonnet_4-orange?style=for-the-badge)](https://anthropic.com/)

**Analizzatore Forex Macroeconomico con IA** - Genera analisi fondamentali complete su 19 coppie forex usando Claude AI, dati macroeconomici in tempo reale e ricerche web automatiche.

![Demo](https://img.shields.io/badge/Status-Production_Ready-green?style=flat-square)

---

## 🎯 Cosa Fa

1. **Raccoglie dati macro** (tassi, inflazione, PIL, disoccupazione) da fonti ufficiali
2. **Cerca notizie e outlook** con ricerche web automatiche (DuckDuckGo)
3. **Analizza 19 coppie forex** usando Claude AI (claude-sonnet-4-20250514)
4. **Genera punteggi e bias** per ogni coppia con spiegazioni dettagliate
5. **Proiezioni tassi BC** - Date meeting, probabilità mercato, outlook 12 mesi

---

## ⭐ Features Principali

### 📈 Dati Macroeconomici (100% Gratuiti)
| Indicatore | Fonte | Copertura |
|------------|-------|-----------|
| Tassi di interesse | global-rates.com | 7 valute |
| Inflazione CPI | global-rates.com + ABS | 7 valute |
| PIL (GDP Growth) | API Ninjas | 7 valute |
| Disoccupazione | API Ninjas | 7 valute |

### 🏦 Proiezioni Tassi Banche Centrali (NUOVO!)
Per ogni BC (Fed, ECB, BoE, BoJ, SNB, RBA, BoC):
- **Prossimo Meeting**: Data esatta
- **Probabilità Mercato**: % hold/cut/hike (CME FedWatch, ASX, etc.)
- **Storico Recente**: N tagli/rialzi negli ultimi 12 mesi
- **Outlook 12M**: Previsioni analisti
- **Stance**: Hawkish/Neutrale/Dovish

### 🔍 Ricerche Web Automatiche
- Query dinamiche per ogni banca centrale
- Aggiornate automaticamente con anno corrente
- Fonti: Reuters, Bloomberg, CME, ASX, ING, Goldman Sachs, etc.

### 📊 Analisi per Coppia
Per ognuna delle 19 coppie:
- Punteggi su 6 parametri (-2 a +2)
- Bias finale (bullish/bearish/neutral)
- Sintesi in italiano
- Scenari prezzo (base/bullish/bearish)
- Key drivers

---

## 🛠️ Installazione

### Prerequisiti
- Python 3.9+
- API Key Anthropic ([console.anthropic.com](https://console.anthropic.com/))
- API Key API Ninjas (opzionale, gratuita: [api-ninjas.com](https://api-ninjas.com/))

### Setup Locale

```bash
# 1. Clona il repository
git clone https://github.com/tuousername/forex-macro-analyst.git
cd forex-macro-analyst

# 2. Installa dipendenze
pip install -r requirements.txt

# 3. Configura API Keys
# Crea file config.py:
echo 'ANTHROPIC_API_KEY = "sk-ant-..."' > config.py
echo 'API_NINJAS_KEY = "tua_api_key"' >> config.py

# 4. Avvia
streamlit run forex_analyzer_claude.py
```

### Setup Streamlit Cloud

1. Fork questo repository
2. Vai su [share.streamlit.io](https://share.streamlit.io/)
3. Connetti il tuo repo GitHub
4. Aggiungi i secrets in Settings > Secrets:

```toml
ANTHROPIC_API_KEY = "sk-ant-..."
API_NINJAS_KEY = "tua_api_key"
```

---

## 📁 Struttura File

```
forex-macro-analyst/
├── forex_analyzer_claude.py   # App principale Streamlit
├── macro_data_fetcher.py      # Fetcher dati macro (v7)
├── config.py                  # API Keys (non committare!)
├── requirements.txt           # Dipendenze Python
├── README.md                  # Questo file
├── .gitignore                 # Esclude config.py
└── data/                      # Cache analisi (auto-generata)
```

---

## 📋 Requirements

```txt
streamlit>=1.28.0
anthropic>=0.49.0
duckduckgo-search>=4.0.0
pandas>=2.0.0
requests>=2.31.0
```

---

## 🎮 Utilizzo

### 1. Avvia l'app
```bash
streamlit run forex_analyzer_claude.py
```

### 2. Seleziona le coppie
- Default: tutte le 19 coppie
- Puoi deselezionare quelle non interessanti

### 3. Genera analisi
- Click su "🚀 Genera Analisi Completa"
- Attendi ~2-3 minuti (raccolta dati + analisi AI)

### 4. Esplora i risultati
- **Top Bullish/Bearish**: Le migliori opportunità
- **Tabella Proiezioni Tassi**: Outlook per ogni BC
- **Dettaglio Coppia**: Click su una riga per i dettagli

### 5. Esporta
- JSON completo con tutti i dati
- Salvataggio automatico in `data/`

---

## 🔄 Frequenza Aggiornamento Consigliata

| Evento | Quando aggiornare |
|--------|-------------------|
| **Routine** | Domenica sera (prima settimana trading) |
| **Post-NFP** | Venerdì dopo Non-Farm Payrolls |
| **Post-CPI** | Dopo rilascio inflazione USA/EU |
| **Post-FOMC** | Dopo meeting Fed |
| **Post-ECB/BoE** | Dopo decisioni tassi |

---

## 📊 Coppie Forex Analizzate

### JPY Crosses
USD/JPY, GBP/JPY, AUD/JPY, EUR/JPY, CAD/JPY

### AUD Crosses
AUD/USD, AUD/CAD, GBP/AUD, EUR/AUD

### CAD Crosses
EUR/CAD, GBP/CAD

### CHF Crosses
USD/CHF, EUR/CHF, GBP/CHF, CAD/CHF, AUD/CHF

### Majors
EUR/USD, EUR/GBP, GBP/USD

---

## ⚙️ Configurazione Avanzata

### Variabili d'ambiente supportate

```bash
# Obbligatoria
ANTHROPIC_API_KEY=sk-ant-...

# Opzionali
API_NINJAS_KEY=...        # Per PIL e disoccupazione
SUPABASE_URL=...          # Per storage cloud
SUPABASE_KEY=...          # Per storage cloud
```

### Personalizzare le ricerche

Le query di ricerca sono in `search_qualitative_data()` nel file `forex_analyzer_claude.py`. Puoi aggiungere query personalizzate per fonti specifiche.

---

## 🤝 Contributing

1. Fork il repository
2. Crea un branch (`git checkout -b feature/nuova-feature`)
3. Commit le modifiche (`git commit -am 'Aggiunta nuova feature'`)
4. Push al branch (`git push origin feature/nuova-feature`)
5. Apri una Pull Request

---

## ⚠️ Disclaimer

**Questo tool è solo per scopi educativi e informativi.**

- Non costituisce consulenza finanziaria
- Le analisi sono generate da IA e possono contenere errori
- Fai sempre le tue ricerche prima di tradare
- Il trading forex comporta rischi significativi

---

## 📝 Changelog

### v2.1.0 (Dicembre 2025)
- ✨ **Nuova sezione rate_outlook**: Proiezioni tassi per ogni BC
- 🔍 **Query dinamiche**: Ricerche aggiornate automaticamente
- 📊 **Tabella Meeting BC**: Date, probabilità, outlook
- 🏦 **7 Banche Centrali**: Fed, ECB, BoE, BoJ, SNB, RBA, BoC

### v2.0.0 (Dicembre 2025)
- 🚀 **MacroDataFetcher v7**: 100% gratuito
- 📈 **API Ninjas**: PIL e disoccupazione
- 🌐 **Scraping migliorato**: global-rates.com + ABS

### v1.0.0 (Novembre 2025)
- 🎉 Release iniziale
- 🤖 Integrazione Claude AI
- 📊 19 coppie forex

---

## 📄 License

MIT License - Vedi [LICENSE](LICENSE) per dettagli.

---

## 🙏 Credits

- **Claude AI** by [Anthropic](https://anthropic.com/)
- **Streamlit** - Framework UI
- **DuckDuckGo Search** - Ricerche web
- **API Ninjas** - Dati economici
- **global-rates.com** - Tassi e inflazione

---

<p align="center">
  Made with ❤️ for Forex Traders
</p>
