# Centrale delle Bolle – Webapp Gestionale (Django)

![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Django](https://img.shields.io/badge/Django-092E20?style=for-the-badge&logo=django&logoColor=white)
![Bootstrap](https://img.shields.io/badge/Bootstrap-7952B3?style=for-the-badge&logo=bootstrap&logoColor=white)
![Plotly](https://img.shields.io/badge/Plotly-3F4F75?style=for-the-badge&logo=plotly&logoColor=white)

**Centrale delle Bolle** è una webapp gestionale sviluppata in Django per supportare le operazioni aziendali legate a:

- Documenti di Trasporto (DDT)
- Fatture elettroniche (FatturaPA)
- Reportistica interattiva
- Gestione articoli e clienti
- Statistiche operative

Il progetto è stato realizzato per un ambiente produttivo reale ed è in uso quotidiano in Va.Lat.

---

## Funzionalità principali

### Gestione DDT e documenti
- Creazione, modifica e archiviazione DDT
- Ricerca e filtraggio documenti
- Esportazione dei dati

### Fatture elettroniche (FatturaPA)
- Generazione XML conforme allo standard FatturaPA
- Archiviazione e gestione dello storico

### Reportistica con grafici interattivi
- Dashboard con grafici interattivi realizzati con **Plotly**
- Filtri dinamici (cliente, intervallo temporale, categorie)
- Visualizzazione vendite, distribuzione articoli e trend operativi

### Interfaccia utente
- UI responsive basata su **Bootstrap 5**
- Template Django personalizzati

## Deploy in produzione

La webapp gira su:

- Raspberry Pi 4 con Linux  
- Reverse proxy tramite **Nginx**
- Application server **Gunicorn**
- Servizio raggiungibile tramite hostname DuckDNS: https://centralebolle.duckdns.org/
