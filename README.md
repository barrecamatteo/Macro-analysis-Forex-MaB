# 📊 Forex Macro Analyst

Analisi macroeconomica globale delle coppie forex powered by **Claude AI**.

## 🚀 Funzionalità

- Analisi di **19 coppie forex** (7 valute: EUR, USD, GBP, JPY, CHF, AUD, CAD)
- Dati economici in tempo reale da **TradingEconomics**
- Ricerca notizie e outlook mercati
- Sistema di scoring basato su **6 parametri macroeconomici**
- Salvataggio storico delle analisi

## 📊 Parametri Analizzati

| Parametro | Range | Descrizione |
|-----------|-------|-------------|
| Tassi Attuali | -1/+1 | Differenziale tassi corrente |
| **Aspettative Tassi** | **-2/+2** | ⭐ Peso doppio! Tagli vs rialzi attesi |
| Inflazione | -1/+1 | Inflazione alta = BC hawkish = positivo |
| Crescita/PIL | -1/+1 | PIL e PMI |
| Risk Sentiment | -1/+1 | Safe-haven vs cyclical |
| Bilancia/Fiscale | -1/+1 | Current Account e Debito/PIL |

## 🛠️ Installazione Locale

1. Clona il repository
2. Crea un file `config.py`:
   ```python
   ANTHROPIC_API_KEY = "la-tua-api-key"
   ```
3. Installa le dipendenze:
   ```bash
   pip install -r requirements.txt
   ```
4. Avvia l'app:
   ```bash
   streamlit run forex_analyzer_claude.py
   ```

## ☁️ Deploy su Streamlit Cloud

1. Fai fork di questo repository
2. Vai su [share.streamlit.io](https://share.streamlit.io)
3. Connetti il tuo repository GitHub
4. Aggiungi il secret `ANTHROPIC_API_KEY` nelle impostazioni

## 📝 Licenza

MIT License
