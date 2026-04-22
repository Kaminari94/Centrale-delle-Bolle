# Centrale delle Bolle – Webapp Gestionale (Django)

![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Django](https://img.shields.io/badge/Django-092E20?style=for-the-badge&logo=django&logoColor=white)
![DjangoREST](https://img.shields.io/badge/DJANGO-REST-ff1709?style=for-the-badge&logo=django&logoColor=white&color=ff1709&labelColor=gray)
![Bootstrap](https://img.shields.io/badge/Bootstrap-7952B3?style=for-the-badge&logo=bootstrap&logoColor=white)
![jQuery](https://img.shields.io/badge/jquery-%230769AD.svg?style=for-the-badge&logo=jquery&logoColor=white)
![Plotly](https://img.shields.io/badge/Plotly-3F4F75?style=for-the-badge&logo=plotly&logoColor=white)

**Centrale delle Bolle** è una webapp gestionale sviluppata in Django per supportare le operazioni aziendali legate a:

- Documenti di Trasporto (DDT)
- Fatture elettroniche (FatturaPA)
- Reportistica interattiva
- Gestione articoli e clienti
- Gestione Utenti, Concessionari e Zone per concessionario e per utente
- Statistiche operative

Il progetto è stato realizzato per un ambiente produttivo reale ed è in uso quotidiano nella mia azienda familiare.

## 📱 Mobile API & Android Integration

Questo progetto include anche una versione con API REST (DRF) utilizzata da un frontend mobile:

- ![Link Branch API](https://github.com/Kaminari94/Centrale-delle-Bolle/tree/mobile-api)
- ![Link App Android](https://github.com/Kaminari94/CDB-Android)

---

## Funzionalità principali

### Gestione DDT e documenti
- Creazione, modifica e archiviazione DDT
- Creazione, modifica e archiviazione Schede di Tentata Vendita
- Creazione, modifica e archiviazione Fatture
- Creazione, modifica e archiviazione movimenti di magazzino (Carico, Scarico prodotti)
- Ricerca e filtraggio documenti
- Export dati DDT conforme all'utilizzo con fornitore Centrale del Latte d'Italia

### Fatture elettroniche (FatturaPA)
- Generazione XML conforme allo standard FatturaPA
- Creazione automatica sfruttando i totali delle Schede Tentata Vendita
- Archiviazione e gestione dello storico

### Reportistica con grafici interattivi
- Dashboard con grafici interattivi realizzati con **Plotly**
- Filtri dinamici (cliente, intervallo temporale, categorie)
- Visualizzazione vendite, distribuzione articoli e trend operativi per cliente
- Conteggio giornaliero articoli venduti

### Interfaccia utente
- UI responsive basata su **Bootstrap 5**
- Template Django personalizzati
