# 📊 Forex Macro Analyst - Powered by Claude AI

## Panoramica

Applicazione web per l'analisi macroeconomica forex che utilizza Claude AI per generare analisi complete su 19 coppie di valute. L'app integra dati in tempo reale da fonti ufficiali, notizie web e indicatori PMI per fornire una visione completa del mercato forex.

---

## 🔧 Caratteristiche Principali

- **19 coppie forex** analizzate simultaneamente
- **7 valute** monitorate: USD, EUR, GBP, JPY, CHF, AUD, CAD
- **Dati macro** da API ufficiali (tassi, inflazione, PIL, disoccupazione)
- **PMI indicators** da Investing.com (Manufacturing + Services)
- **Ricerca notizie** automatica (Forex Factory, outlook banche centrali, geopolitica)
- **Autenticazione** multi-utente con Supabase
- **Storico analisi** salvato su cloud

---

## 📈 Sistema di Scoring - 7 Parametri

L'analisi si basa su **7 parametri fondamentali**, ciascuno con criteri oggettivi e misurabili.

### Range Punteggi
| Parametro | Range | Note |
|-----------|-------|------|
| Aspettative Tassi | -2 a +2 | **Peso doppio** - driver principale |
| Altri 6 parametri | -1 a +1 | Peso standard |
| **Score totale** | -8 a +8 | Per valuta |
| **Differenziale** | -16 a +16 | Base - Quote |

---

## 📋 Criteri Dettagliati per Parametro

### 1️⃣ Tassi Attuali [-1 a +1]

**Logica:** Il differenziale di tasso (carry) attrae flussi di capitale verso la valuta con rendimento maggiore.

| Spread (Base - Quote) | Score Base | Score Quote |
|-----------------------|------------|-------------|
| ≥ +150 bp | +1 | -1 |
| +50 bp a +149 bp | +1 | 0 |
| -49 bp a +49 bp | 0 | 0 |
| -50 bp a -149 bp | 0 | +1 |
| ≤ -150 bp | -1 | +1 |

**Esempio:** EUR (2.15%) vs USD (3.75%) → Spread = -160bp → EUR: -1, USD: +1

---

### 2️⃣ Aspettative Tassi [-2 a +2] ⭐

**Logica:** Il mercato guarda avanti. Le aspettative sui tassi futuri sono più importanti dei tassi attuali.

| Scenario | Score |
|----------|-------|
| BC hawkish con rialzi attesi O prob. taglio <20% | **+2** |
| BC neutrale/leggermente hawkish O prob. taglio 20-40% | **+1** |
| BC neutrale O incertezza elevata | **0** |
| BC leggermente dovish O prob. taglio 60-80% | **-1** |
| BC molto dovish con tagli attesi O prob. taglio >80% | **-2** |

**Fonte dati:** Notizie web (CME FedWatch, dichiarazioni BC, analisti)

---

### 3️⃣ Inflazione [-1 a +1]

**Logica:** Non conta solo il livello, ma quanto l'inflazione supporta la politica monetaria.

| Scenario | Score |
|----------|-------|
| Inflazione 1.5%-2.5% + trend stabile/discesa | **+1** (ideale) |
| Inflazione 2.5%-3.5% + trend incerto | **0** (gestibile) |
| Inflazione >3.5% + trend in salita | **-1** (BC sotto pressione) |
| Inflazione <1.5% + trend in discesa | **-1** (rischio deflazione) |

**Confronto:** Chi ha situazione inflattiva più favorevole per la propria BC?

---

### 4️⃣ Crescita/PIL [-1 a +1] - LAGGING

**Logica:** Il PIL va contestualizzato con inflazione e sostenibilità. Crescita alta con inflazione fuori controllo NON è positiva.

| Scenario | Score |
|----------|-------|
| PIL >2% + inflazione controllata | **+1** (crescita sana) |
| PIL 1%-2% + situazione bilanciata | **0** (moderata) |
| PIL <1% O trend in decelerazione | **-1** (rischio recessione) |
| PIL alto + inflazione fuori controllo | **0** (non sostenibile) |
| Stagflazione (PIL basso + inflazione alta) | **-1** (peggiore) |

**Confronto diretto:**
- Differenziale > 1.5pp → vantaggio netto
- Differenziale 0.5-1.5pp → vantaggio leggero
- Differenziale < 0.5pp → neutro

---

### 5️⃣ PMI [-1 a +1] - LEADING

**Logica:** PMI anticipa il PIL di 3-6 mesi. Considera livello (>50 = espansione) E direzione (delta).

#### Pesi Settoriali per Valuta

| Valuta | Services | Manufacturing | Economia |
|--------|----------|---------------|----------|
| **USD** | 70% | 30% | Servizi (consumi, finanza) |
| **EUR** | 50% | 50% | Mista |
| **GBP** | 70% | 30% | Servizi (finanza) |
| **JPY** | 40% | 60% | Export/Manifattura |
| **CHF** | 60% | 40% | Finanza + Pharma |
| **AUD** | 50% | 50% | Mining + Servizi |
| **CAD** | 50% | 50% | Energia + Servizi |

#### Criteri di Valutazione

| PMI Ponderato | Delta | Score |
|---------------|-------|-------|
| ≥52 | Positivo | **+1** (forte espansione) |
| 50-52 | Positivo | **+1** (espansione moderata) |
| 50-52 | Negativo | **0** (rallentamento) |
| 48-50 | Positivo | **0** (recupero) |
| 48-50 | Negativo | **-1** (peggioramento) |
| <48 | Qualsiasi | **-1** (contrazione) |

**Fonte dati:** Investing.com (CHF Services: TradingEconomics)

---

### 6️⃣ Risk Sentiment [-1 a +1]

**Logica:** In risk-off, capitali verso safe-haven. In risk-on, verso valute cicliche.

#### Classificazione Valute

| Tipo | Valute |
|------|--------|
| **Safe-haven** | USD, JPY, CHF |
| **Cicliche/Commodity** | AUD, CAD, GBP |
| **Semi-cicliche** | EUR |

#### Determinazione Regime

| Indicatore | Regime |
|------------|--------|
| VIX > 25 O equity in forte calo | **Risk-OFF** |
| VIX < 18 E equity positivo | **Risk-ON** |
| Altrimenti | **Neutro** |

#### Matrice Punteggi

| Tipo Coppia | Risk-OFF | Neutro | Risk-ON |
|-------------|----------|--------|---------|
| Ciclica vs Safe-haven | Cicl: -1, Safe: +1 | 0, 0 | Cicl: +1, Safe: -1 |
| Entrambe stesso tipo | 0, 0 | 0, 0 | 0, 0 |

---

### 7️⃣ Bilancia/Fiscale [-1 a +1]

**Logica:** Importante nel lungo termine. Assegnare peso solo se notizie specifiche.

| Scenario | Score |
|----------|-------|
| Current Account surplus >2% + debito gestibile | **+1** |
| Situazione nella media O nessuna notizia | **0** |
| Deficit gemelli elevati O crisi debito | **-1** |

**Regola pratica:** Se non ci sono notizie rilevanti → 0 per entrambe.

---

## 🎯 Interpretazione Risultati

### Differenziale (score_base - score_quote)

| Range | Interpretazione | Forza Segnale |
|-------|-----------------|---------------|
| +8 a +16 | **Strong Bullish** (long) | 🟢🟢 |
| +3 a +7 | **Bullish** (long) | 🟢 |
| -2 a +2 | **Neutral** | 🟡 |
| -7 a -3 | **Bearish** (short) | 🔴 |
| -16 a -8 | **Strong Bearish** (short) | 🔴🔴 |

---

## 📊 Fonti Dati

| Dato | Fonte | Frequenza |
|------|-------|-----------|
| Tassi BC | API ufficiali | Real-time |
| Inflazione | Trading Economics / API | Mensile |
| PIL | API Ninjas | Trimestrale |
| PMI Manufacturing | Investing.com | Mensile |
| PMI Services | Investing.com (CHF: TradingEconomics) | Mensile |
| Notizie | DuckDuckGo Search | On-demand |
| Aspettative tassi | Forex Factory, Reuters, Bloomberg | On-demand |

---

## 🔐 Autenticazione

L'app supporta autenticazione multi-utente tramite Supabase:
- Login con username/password
- Analisi salvate per utente
- Storico consultabile

---

## ⚙️ Configurazione

### Variabili d'ambiente richieste

```
ANTHROPIC_API_KEY=sk-ant-...
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_KEY=eyJ...
API_NINJAS_KEY=xxx (opzionale, per PIL/disoccupazione)
```

### Installazione

```bash
pip install streamlit anthropic duckduckgo-search pandas requests
```

### Esecuzione

```bash
streamlit run forex_analyzer_claude.py
```

---

## 📁 Struttura File

```
forex_analyzer_claude.py    # App principale
macro_data_fetcher.py       # Modulo fetch dati macro
config.py                   # Configurazione locale (opzionale)
README.md                   # Questa documentazione
data/                       # Cartella analisi locali (fallback)
```

---

## 📝 Note Importanti

1. **I punteggi sono RELATIVI al confronto diretto** tra le due valute della coppia
2. **La stessa valuta può avere punteggi diversi** in coppie diverse
3. **PIL + PMI sono complementari**: PIL conferma il passato, PMI anticipa il futuro
4. **Risk Sentiment dipende dal tipo di coppia**, non solo dal regime di mercato
5. **Le aspettative sui tassi hanno peso doppio** perché il mercato guarda avanti

---

## ⚠️ Disclaimer

Questa applicazione è fornita a scopo informativo e didattico. Non costituisce consulenza finanziaria o raccomandazione di investimento. Il trading forex comporta rischi significativi. Consultare sempre un consulente finanziario qualificato prima di prendere decisioni di investimento.

---

## 📜 Changelog

### v3.1 (Gennaio 2026)
- ✅ Aggiunta tabella PMI con scraping Investing.com
- ✅ PMI come 7° criterio di scoring
- ✅ Pesi PMI differenziati per struttura economica
- ✅ Criteri di scoring oggettivi e documentati
- ✅ Confronto diretto PIL contestualizzato con inflazione

### v3.0
- Autenticazione Supabase multi-utente
- Opzioni analisi modulari (macro, news, link, Claude)
- Storico analisi per utente

### v2.0
- Sistema 6 parametri
- Ricerca notizie automatica
- Outlook tassi di interesse

### v1.0
- Versione iniziale
- Analisi Claude base
