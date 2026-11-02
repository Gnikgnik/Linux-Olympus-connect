# Product

<!-- impeccable:product-schema 1 -->

## Platform

desktop

<!-- Lo schema di Impeccable prevede web | ios | android | adaptive: nessuno
     descrive questo prodotto, che è un'applicazione desktop Linux scritta in
     PyQt6. Il valore corretto è "desktop"; scrivere "web" avrebbe fatto
     caricare al lavoro futuro linee guida sbagliate. Ambiente di riferimento:
     KDE Plasma su Wayland, installazione a livello utente. -->

## Users

**Utente primario:** Antonio, fotografo che scatta in RAW con una Olympus
OM-D E-M10 Mark IV e lavora su Linux (CachyOS). Il suo problema concreto:
sulla scheda ci sono 1141 file ORF, 18 video MOV e 2 JPEG, e nessuno
strumento su Linux sa portarli sul computer.

**Situazione d'uso:** a casa, dopo aver scattato. Collega il PC
all'access point della fotocamera e, avendo una sola scheda Wi-Fi, resta
senza Internet per tutta la durata del trasferimento. Non è un flusso
"sempre connesso": è una sessione dedicata, che l'utente avvia e segue.

**Pubblico futuro possibile:** utenti Olympus/OM System su Linux, che oggi
non hanno alternative. Le decisioni prioritarie restano quelle dell'utente
primario, ma le scelte costose da invertire — lingua dell'interfaccia,
assunzioni sul modello di fotocamera — vanno prese senza chiudere la porta
a questo pubblico.

## Product Purpose

Scaricare foto e video da una fotocamera Olympus via Wi-Fi su Linux,
scegliendo visivamente quali file prendere.

Esiste perché su Linux manca il client, non il protocollo: la fotocamera
espone un server HTTP perfettamente funzionante, ma non serve un indice di
directory, quindi senza conoscere gli endpoint CGI non c'è modo di
raggiungere i file. Su Windows e Android quel lavoro lo fa OI.Share.

**Successo:** l'utente apre l'applicazione, vede le miniature di tutto ciò
che c'è sulla scheda, seleziona e scarica, e i file arrivano integri e
organizzati per data senza che debba pensare al protocollo, al routing o
alla ripresa dei trasferimenti.

## Positioning

**OI.Share importa solo JPEG.** Sulla scheda di riferimento l'applicazione
ufficiale vede 2 file su 1161: il 98% degli scatti è RAW e le resta
invisibile. Questo prodotto tratta ORF e MOV come cittadini di prima classe,
non come estensione.

È la differenza che un prodotto vicino non potrebbe copiare senza cambiare
politica: l'esclusione del RAW in OI.Share è deliberata, non un limite
tecnico. `get_caminfo.cgi` della fotocamera dichiara
`GetRawImageSupport: available`.

## Operating Context

- **Rete:** la fotocamera crea un access point su `192.168.0.10`. Con una
  sola scheda Wi-Fi il PC resta offline durante l'uso; nessuna
  configurazione di routing può evitarlo. NetworkManager tratterebbe quell'AP
  come una rete normale, assegnandogli la default route e potendo
  riagganciarsi altrove a metà trasferimento: serve un profilo dedicato.
- **Scala:** 27,9 GB sulla scheda di riferimento. Il video più grande è di
  944 MB, la media degli ORF 20,6 MB. Un trasferimento completo dura ore su
  Wi-Fi 2,4 GHz.
- **Destinazione:** `~/Immagini/Olympus/ANNO/ANNO-MM-GG/`, con la data di
  modifica del file impostata alla data di scatto.
- **Avvio:** voce nel menu KDE (Grafica → Fotografia) e comando
  `olympus-connect`, installati nella home senza `sudo`.
- **Diagnosi:** `diag.py` gira in autonomia mentre la macchina è offline e
  scrive un referto su file, perché in quel momento nessuno può assistere.

## Capabilities and Constraints

**Verificato sul campo** (E-M10 Mark IV, firmware OI.Share 4.50):

| | |
|---|---|
| Download JPEG, ORF, MOV | funziona |
| Miniature per tutti e tre i tipi | funziona, JPEG di 8–13 KB |
| Richieste `Range` | **non supportate**: un trasferimento interrotto riparte da zero |
| `set_timeout` (keep-alive) | dichiarato ma risponde `520` / `errorcode 1006` |
| `get_unusedcapacity` | restituisce `0xFFFFFFFF`, valore sentinella inutilizzabile |
| Handshake Bluetooth | non richiesto |
| Header `User-Agent: OI.Share v2` | non richiesto; inviato comunque per aderenza a OI.Share |

**Vincoli di progetto:**

- **Download seriali.** Il server della fotocamera è un microcontrollore:
  più trasferimenti in volo lo degradano. Per lo stesso motivo le miniature
  si sospendono durante un download.
- **Nessuna cancellazione.** `exec_erase.cgi` esiste nel protocollo e non
  viene mai invocato. È l'unica operazione irreversibile disponibile e non
  serve all'obiettivo.
- **La password Wi-Fi non viene mai memorizzata** dall'applicazione: va a
  NetworkManager, che la conserva nel profilo.
- **Il core non importa Qt.** La separazione rende il protocollo testabile
  senza schermo e permette la diagnosi da terminale.

**Supporto fotocamere:** solo la E-M10 Mark IV è verificata. Nessun impegno
sugli altri modelli, ma il codice evita assunzioni legate a questo
esemplare: interroga `get_commandlist.cgi` invece di dare per scontati i
comandi disponibili.

**Decisioni esplicitamente aperte:**

- **Lingua dell'interfaccia.** Oggi è interamente in italiano e non esiste
  alcun meccanismo di traduzione (`QTranslator`, `tr()`). Per un progetto
  pubblico su GitHub la scelta fra solo italiano, solo inglese e bilingue
  con traduzioni Qt è rimandata. Va decisa prima che le stringhe si
  moltiplichino: più tardi si decide, più costa.
- **Keep-alive.** `set_timeout` fallisce in modalità `play`. Resta da
  provare in modalità `rec` e con valori limite; `diag.py` ha già la sonda
  pronta. Se un valore fosse accettato, si recupererebbe una difesa reale
  sui video grandi.

## Brand Commitments

- **Nome:** Olympus Connect. Repository GitHub `Linux-Olympus-connect`,
  account `Gnikgnik`.
- **Licenza:** GPL-3.0-or-later, file `LICENSE` nel repository.
- **Icona:** `olympus_connect/resources/`, in due varianti — dettagliata per
  48 px e oltre, semplificata per 16–32 px, dove le tre onde Wi-Fi della
  versione dettagliata si fondono in una macchia. Fotocamera chiara su fondo
  scuro arrotondato, onde Wi-Fi ciano dal mirino.
- **Voce:** messaggi d'errore che dicono all'utente *cosa fare*, non cosa è
  andato storto. Nessuna etichetta che promette ciò che il firmware non fa:
  il pulsante si chiama "Riprova", non "Riprendi", perché senza supporto
  `Range` il file riparte da zero.
- **Dati personali:** seriali della fotocamera e SSID reali sono stati
  sostituiti con valori fittizi in tutto il repository. `diag.py` riscrive le
  fixture a ogni esecuzione, quindi l'anonimizzazione va rifatta dopo ogni
  diagnostica e prima di ogni pubblicazione.

## Evidence on Hand

- **Referto di verifica sul campo:** `docs/field-report-2026-09-24.md`. Due
  passate con la fotocamera reale, con i valori misurati di ogni endpoint.
- **Verifica incrociata di un download:** `P9061187.ORF`, 21 453 224 byte,
  dimensione e timestamp coincidenti fra `get_imglist` ed `exiv2` sul file
  arrivato.
- **Fixture reali** in `tests/fixtures/`: output registrato dalla fotocamera,
  con i seriali anonimizzati. Cinque test girano su questi dati, incluso uno
  che fa passare tutti i 1161 file attraverso il client.
- **189 test**, nessuno dei quali richiede la fotocamera collegata.
- **Spec e piano** in `docs/superpowers/`, che registrano anche le diagnosi
  sbagliate e la loro correzione.

**Assenze da non colmare inventando:** nessun utente oltre all'autore,
nessun modello di fotocamera diverso dalla E-M10 Mark IV è stato provato,
nessuna misura di prestazioni su hardware diverso da questo. Non esistono
testimonianze, numeri di adozione né confronti con altri strumenti oltre
alla differenza documentata con OI.Share.

## Product Principles

1. **Il RAW è il caso principale.** Il 98% degli scatti dell'utente è ORF.
   Qualunque funzione che tratti il RAW come un extra ha sbagliato bersaglio.
2. **Mai distruggere.** Nessuna operazione irreversibile sulla fotocamera.
   Nessun file interrotto può prendere il nome definitivo: il rename è
   atomico e avviene solo a dimensione verificata.
3. **Interrogare, non assumere.** La fotocamera dichiara cosa supporta. Il
   software si adatta a quella risposta invece di codificare il
   comportamento di un esemplare.
4. **"Non funziona" non è un messaggio d'errore.** Ogni stato ha un nome e
   un'azione: quale gradino della connessione è fallito, e cosa fare.
5. **Misurare invece di dedurre.** Le due diagnosi sbagliate di questo
   progetto — l'header obbligatorio, la sessione HTTP condivisa — sono state
   entrambe smentite da una misura. Le decisioni future si verificano sulla
   fotocamera, non si argomentano.
