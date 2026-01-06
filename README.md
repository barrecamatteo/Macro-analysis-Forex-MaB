# 📊 Forex Macro Analyst v3.0

Analizzatore forex macroeconomico powered by **Claude AI** con sistema di autenticazione e analisi modulare.

## ✨ Novità v3.0

### 🔐 Sistema di Autenticazione
- Login con username/password
- Multi-utente con Supabase
- Ogni utente vede solo le proprie analisi
- Gestione utenti con script utility

### 🎛️ Analisi Modulare
Risparmia sui costi scegliendo cosa analizzare:

| Opzione | Descrizione | Costo |
|---------|-------------|-------|
| 📊 Dati Macro | Tassi, inflazione, PIL, disoccupazione | **GRATIS** |
| 📰 Notizie Web | Forex Factory, outlook BC, geopolitica | **GRATIS** |
| 📎 Link Aggiuntivi | Analizza URL personalizzati | **GRATIS** |
| 🤖 Claude AI | Analisi completa forex | **$$$** |

### 📰 Riepilogo Notizie
Visualizza cosa ha trovato la ricerca web PRIMA di chiamare Claude!

### 📜 Storico Completo
Ogni analisi viene salvata con:
- Timestamp
- Tipo di analisi
- Opzioni selezionate
- Tutti i dati raccolti

---

## 🚀 Installazione

### 1. Requisiti
```bash
pip install streamlit anthropic duckduckgo-search pandas requests
```

### 2. Configurazione API Keys
Crea `config.py`:
```python
ANTHROPIC_API_KEY = "sk-ant-..."
SUPABASE_URL = "https://xxx.supabase.co"
SUPABASE_KEY = "eyJ..."
API_NINJAS_KEY = "xxx"  # Opzionale
```

Oppure usa `st.secrets` su Streamlit Cloud.

### 3. Setup Database Supabase

1. Vai su [Supabase](https://supabase.com) e crea un progetto
2. Vai su **SQL Editor**
3. Esegui lo script `supabase_setup_v3.sql`
4. Copia URL e anon key nelle impostazioni

### 4. Crea Utente Admin
L'utente viene creato automaticamente dallo script SQL:
- **Username:** MBARRECA
- **Password:** mbarreca

### 5. Avvia
```bash
streamlit run forex_analyzer_claude.py
```

---

## 👥 Gestione Utenti

Usa lo script `user_manager.py`:

```bash
# Lista utenti
python user_manager.py list

# Aggiungi utente
python user_manager.py add mario password123 mario@email.com

# Cambia password
python user_manager.py password mario nuova_password

# Elimina utente
python user_manager.py delete mario

# Genera hash password
python user_manager.py hash mia_password
```

---

## 💡 Scenari d'Uso

### Scenario 1: Analisi Completa
Seleziona tutte le opzioni → Costa token Claude ma hai tutto

### Scenario 2: Solo Aggiornamento Dati
- ✅ Dati Macro
- ✅ Notizie Web
- ❌ Claude

→ **GRATIS!** Vedi i dati aggiornati senza spendere

### Scenario 3: Breaking News
- ❌ Dati Macro (già aggiornati prima)
- ❌ Notizie Web
- ✅ Link Aggiuntivi (inserisci URL news)
- ✅ Claude

→ Analisi veloce su notizie specifiche

### Scenario 4: Riepilogo Notizie
- ❌ Dati Macro
- ✅ Notizie Web
- ❌ Claude

→ **GRATIS!** Vedi cosa dice il mercato senza analisi

---

## 📁 Struttura File

```
forex_analyzer_claude.py   # App principale
macro_data_fetcher.py      # Modulo dati macro
user_manager.py            # Utility gestione utenti
config.py                  # Configurazione (non committare!)
supabase_setup_v3.sql      # Script setup database
requirements.txt           # Dipendenze Python
```

---

## 🗄️ Struttura Database

### Tabella `users`
| Campo | Tipo | Descrizione |
|-------|------|-------------|
| id | UUID | Chiave primaria |
| username | VARCHAR | Unico |
| password_hash | VARCHAR | SHA-256 |
| email | VARCHAR | Opzionale |
| is_active | BOOLEAN | Se può accedere |
| created_at | TIMESTAMP | Data creazione |

### Tabella `analyses`
| Campo | Tipo | Descrizione |
|-------|------|-------------|
| id | UUID | Chiave primaria |
| analysis_datetime | VARCHAR | Timestamp analisi |
| user_id | UUID | Foreign key → users |
| analysis_type | VARCHAR | full/macro_only/news_only/etc |
| options_selected | JSONB | Opzioni selezionate |
| data | JSONB | Tutti i dati dell'analisi |

---

## 📝 Changelog

### v3.0.0 (Gennaio 2026)
- 🔐 **Sistema Autenticazione**: Login multi-utente con Supabase
- 🎛️ **Analisi Modulare**: Scegli cosa includere nell'analisi
- 📰 **Riepilogo Notizie**: Visualizza risultati ricerca web
- 📜 **Storico Completo**: Ogni tipo di analisi viene salvata
- 💾 **Database Multi-utente**: Ogni utente ha le sue analisi
- 🛠️ **User Manager**: Script utility per gestione utenti

### v2.3.0 (Dicembre 2025)
- 📰 **Forex Factory News**: Ricerca automatica breaking news

### v2.2.0 (Dicembre 2025)
- 📎 **Risorse Aggiuntive**: URL custom per Claude

### v2.1.0 (Dicembre 2025)
- 🔍 **Query dinamiche**: Ricerche aggiornate automaticamente
- 📊 **Tabella Meeting BC**: Date, probabilità, outlook

---

## ⚠️ Disclaimer

Questo strumento è solo per scopi informativi e educativi. 
**Non costituisce consiglio di investimento.**

---

## 📄 Licenza

MIT License - Vedi LICENSE per dettagli.
