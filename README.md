# Olympus Connect

Scarica foto e video da una fotocamera Olympus via Wi-Fi, su Linux.

Applicazione desktop PyQt6: griglia di miniature o elenco dettagliato, selezione dei file, download con ripresa dopo le disconnessioni, e gestione del profilo NetworkManager per l'access point della fotocamera.

Sviluppato e verificato su **Olympus OM-D E-M10 Mark IV**, firmware OI.Share `4.50`, su CachyOS con NetworkManager 1.58. Il client interroga la fotocamera sui comandi che supporta (`get_commandlist.cgi`), quindi dovrebbe adattarsi ad altri modelli OM-D — ma nessun altro è stato provato.

## Perché serve

**Su Linux non esiste un client.** Questa è la spiegazione completa, e vale la pena scriverla perché in rete circolano ipotesi più complicate e sbagliate.

La fotocamera in modalità Wi-Fi espone un server HTTP perfettamente funzionante su `http://192.168.0.10/`, che parla il protocollo *OPC Communication Protocol 1.0a* di Olympus. Il problema è che **non serve un indice di directory**: puntandoci un browser non si vede nulla, e senza conoscere gli endpoint CGI non c'è modo di arrivare ai file. Su Windows e Android quel lavoro lo fa OI.Share; su Linux non c'è un equivalente installabile.

Due cose che si leggono spesso e che qui sono state **verificate false** sulla E-M10 Mark IV:

- *«La fotocamera rifiuta le richieste senza l'header `User-Agent: OI.Share v2`.»* Falso: `get_caminfo.cgi` risponde `HTTP 200` con byte identici con e senza l'header. Questo software lo invia comunque, perché è ciò che fa OI.Share e non costa niente, ma non è necessario.
- *«Serve un accoppiamento Bluetooth preliminare.»* Falso: `get_caminfo.cgi` dichiara come unica funzione BLE `BleShutter`, e nessun handshake è richiesto per elencare o scaricare.

C'è invece un problema reale, di natura diversa: NetworkManager tratta l'access point della fotocamera come una rete normale, le assegna la default route ed esegue il connectivity check, potendo riagganciarsi a un'altra rete a metà trasferimento. Il profilo dedicato che questo software crea risolve quello.

**E poi c'è il motivo che rende il progetto necessario invece che comodo.** OI.Share importa solo JPEG. Sulla scheda di riferimento:

| Tipo | File | Totale |
|---|---|---|
| `.ORF` (RAW) | 1141 | 23,5 GB |
| `.MOV` | 18 | 4,4 GB |
| `.JPG` | 2 | 0,02 GB |

L'applicazione ufficiale vede **2 file su 1161**. Chi scatta in RAW non ha nessuno strumento.

## Cosa funziona, verificato sul campo

| | Esito |
|---|---|
| Download JPEG | ✅ |
| Download RAW `.ORF` | ✅ `caminfo` dichiara `GetRawImageSupport: available` |
| Download video `.MOV` | ✅ provato fino a 129 MB; sulla scheda ci sono file da 944 MB |
| Miniature per JPEG, RAW e video | ✅ `get_thumbnail.cgi` restituisce JPEG validi di 8–13 KB per tutti i tipi |
| Dati di scatto prima di scaricare | ✅ tempo, diaframma, ISO, focale, obiettivo e altri, da `get_exif.cgi` (verificato sui RAW; JPEG e video da verificare) |
| Telecomando: live, messa a fuoco col tocco, scatto, video | ✅ live a 1024×768 e 30 fotogrammi/s, nessuna regola del firewall |
| Impostazioni dal PC: modalità, tempo, diaframma, ISO, compensazione, bilanciamento, scatto, filtri | ✅ diaframma limitato all'obiettivo e allo zoom; pose lunghe fino a 60" con conto alla rovescia |
| Sequenze di scatti a intervalli (intervallometro), scarico dei JPEG in una cartella dedicata e video timelapse | da verificare sul campo: i comandi sono misurati (ciclo di scatto 1 s, live che si spegne e si riaccende in 0,3 s), la funzione nell'app non ancora |
| Richieste `Range` (ripresa) | ❌ **non supportate**: risponde `200` senza `Content-Range` |
| `set_timeout.cgi` (keep-alive) | ❌ dichiarato ma risponde `520` / `errorcode 1006` |
| `get_unusedcapacity.cgi` | ❌ restituisce `0xFFFFFFFF`, sentinella di «non disponibile» |

Il referto completo delle misure è in [`docs/field-report-2026-09-24.md`](docs/field-report-2026-09-24.md).

### Conseguenza dell'assenza di `Range`

Un trasferimento interrotto **riparte da zero**: la fotocamera non supporta la ripresa da un offset, e su un video da centinaia di MB il costo è alto. Tre scelte di progetto lo attenuano:

1. **La coda parte dai file più piccoli.** Un'interruzione costa il file in corso, e così è quasi sempre il più piccolo.
2. **Un trasferimento attivo è esso stesso attività**, quindi il rischio di standby si concentra nei momenti di inattività, non durante un download.
3. **Scrittura su `.part` e rinomina atomica.** Un file col nome definitivo è per costruzione integro, quindi la regola «esiste con la stessa dimensione → salta» è sicura e non può lasciare un file troncato considerato completo.

Per questo il pulsante si chiama **Riprova** e non «Riprendi»: non riprende, ricomincia. I file già completati non vengono ritrasferiti.

## Installazione

Servono Python 3.12 o successivo, PyQt6 e `requests`. Su Arch e derivate:

```bash
sudo pacman -S --needed python-pyqt6 python-requests
```

Facoltativo: `ffmpeg`, che serve solo per montare il video di una sequenza (`sudo pacman -S ffmpeg`). Senza, il resto funziona e le foto della sequenza si scaricano lo stesso.

Poi, nella cartella del progetto:

```bash
python3 -m venv --system-site-packages .venv && .venv/bin/pip install -e '.[dev]'
```

`--system-site-packages` riusa PyQt6 di sistema invece di ricompilarlo. Conseguenza: nel venv non viene creato lo script `pytest`, quindi i test si lanciano con `.venv/bin/python -m pytest`.

### Voce di menu e icona

```bash
./install-desktop.sh
```

Installa la voce nel menu (*Grafica → Fotografia*), l'icona nel tema `hicolor` e il comando `olympus-connect` in `~/.local/bin`. Solo nella home dell'utente: nessun `sudo`, nessuna modifica di sistema. Per rimuovere tutto, `./install-desktop.sh --rimuovi`.

Va rieseguito se sposti la cartella del progetto, perché il file `.desktop` contiene il percorso assoluto del venv.

L'icona è una OM-D vista di fronte: calotta argento con la gobba del pentaprisma, corpo nero, obiettivo, su fondo blu. Esiste in due varianti. Sotto i 48 px ghiere, zigrinatura e riflessi dell'obiettivo sparirebbero sotto il pixel, quindi per 16, 22, 24 e 32 px viene installata una variante semplificata che tiene solo la sagoma, che resta riconoscibile anche nel pannello.

Il riferimento a Olympus sta nel disegno del corpo, non nel marchio: né il logo né la scritta OLYMPUS compaiono nell'applicazione, e questo progetto non è affiliato a OM Digital Solutions né a Olympus.

## Uso

```bash
olympus-connect            # se hai eseguito install-desktop.sh
.venv/bin/python -m olympus_connect   # altrimenti
```

1. Sulla fotocamera, attiva la connessione Wi-Fi verso smartphone
2. **Connetti** — la prima volta chiede l'SSID, scegliendolo da un elenco delle reti visibili con quelle che sembrano fotocamere per prime, e poi la password. La password va direttamente a NetworkManager: **questa applicazione non la memorizza mai**
3. **Aggiorna** popola l'elenco e indica quanti file sono nuovi e quanti già presenti
4. Scegli la vista: **Miniature** oppure **Elenco dettagliato**, ordinabile per colonna e inizialmente per data di scatto
5. Selezionando un file, il pannello di destra mostra l'anteprima grande e i dati di scatto: tempo, diaframma, ISO, focale, obiettivo, modalità, compensazione, bilanciamento, flash e risoluzione
6. Filtra per tipo (JPEG / RAW / Video) o mostra solo i file non ancora scaricati
7. Seleziona e premi **Scarica selezionati**

I file finiscono in `~/Immagini/Olympus/ANNO/ANNO-MM-GG/NOME.EXT`, con la data di modifica impostata alla data di scatto. La cartella di destinazione è configurabile e viene ricordata, insieme alla vista, ai filtri e all'SSID.

### Telecomando

Con la fotocamera collegata, il pulsante **Telecomando** nella barra in alto mostra il live al centro della finestra. Un clic sull'immagine mette a fuoco in quel punto; **Scatta** (o la barra spaziatrice) scatta e mostra la foto per due secondi; **Registra** (o il tasto R) avvia e ferma un video. Tornando a Miniature o a Elenco la fotocamera torna in riproduzione e l'elenco si aggiorna con i file nuovi. Solo se il live non arriva (il messaggio «Nessuna immagine dalla fotocamera») il firewall sta bloccando i pacchetti UDP della fotocamera: consentili con `sudo ufw allow in from 192.168.0.10 proto udp`. Per la diagnostica sul campo, avviando con `OLYMPUS_DEBUG=1` il programma scrive su stderr il punto di messa a fuoco e le risposte della fotocamera.

Sotto Scatta e Registra, la sezione **Impostazioni** mostra modalità, tempo, diaframma, ISO, compensazione, bilanciamento e modalità di scatto (in ART anche filtro e variante, in Scene la scena). Le frecce, la rotella del mouse o un clic sul valore li cambiano; il comando parte un attimo dopo l'ultimo passo. Le righe che la modalità lascia alla fotocamera sono attenuate («automatico»). Il diaframma offre solo i valori possibili con l'obiettivo e lo zoom del momento. Con le pose lunghe l'app aspetta la posa più la riduzione rumore (con 60" circa due minuti) e mostra il conto alla rovescia; nel frattempo il live si ferma.

Sotto le impostazioni, la sezione **Sequenza** fa da intervallometro: si scelgono l'intervallo (da 2 secondi a 30 minuti) e la durata (da 5 minuti a 12 ore) e si preme **Avvia sequenza**. Durante la sequenza gli altri comandi del telecomando sono spenti, e **Ferma sequenza** la interrompe dopo lo scatto in corso. Con intervalli da un minuto in su il live si spegne fra uno scatto e l'altro, per risparmiare la batteria, e si riaccende poco prima dello scatto successivo. Finché la sequenza è in corso il PC non va in sospensione. Alla fine il programma esce dal telecomando e chiede se scaricare i JPEG della sequenza in una cartella `Sequenza AAAA-MM-GG HH.MM` dentro la destinazione, e se montarli in un video `timelapse.mp4` (Full HD o 4K; 24, 25 o 30 fotogrammi al secondo). I RAW restano sulla scheda.

Non serve aprire porte nel firewall: prima di avviare il live il PC manda un pacchetto alla fotocamera, e il firewall accetta le risposte. Se il live non arriva comunque, l'app lo dice e suggerisce la regola da aggiungere.

### Il profilo di rete

Alla prima connessione viene creato un profilo NetworkManager chiamato `olympus-connect-<ssid>`, con:

| Impostazione | Perché |
|---|---|
| `ipv4.never-default yes` | la fotocamera non diventa la default route |
| `ipv4.route-metric 900` | resta dietro alle reti vere |
| `ipv6.method disabled` | elimina il rumore dei router advertisement |
| `connection.autoconnect no` | nessun aggancio a sorpresa |

Il software gestisce **solo** quel profilo e non tocca nessun'altra connessione.

Una precisazione onesta su `never-default`: impedisce alla fotocamera di prendere la default route, quindi il traffico continua su un'altra interfaccia — *se esiste*. **Con una sola scheda Wi-Fi resti comunque senza Internet mentre sei collegato alla fotocamera**, perché nessuna configurazione di routing può creare un percorso che non c'è. Con ethernet, un dongle USB o il telefono in tethering USB, invece, Internet resta attivo.

## Quando qualcosa non funziona

```bash
./diag.py
```

Esegue una scala diagnostica con un gradino per ogni causa distinta, così «non funziona» diventa un'indicazione precisa:

```
[OK  ] dispositivo Wi-Fi presente — wlan0
[OK  ] rete Wi-Fi attiva — wlan0→E-M10MKIV-P-XXXXXXXXX
[OK  ] 192.168.0.10 raggiungibile direttamente (on-link)
[OK  ] porta 80 della fotocamera — accetta
```

Poi registra l'output grezzo della fotocamera in `tests/fixtures/`, scarica un campione di ogni tipo e verifica il supporto `Range`, le miniature e `set_timeout`. Tutto l'output viene duplicato in `diag-report.txt`, perché lo strumento gira mentre la macchina è offline.

Un dettaglio che vale la pena conoscere: `ip route get 192.168.0.10` **riesce sempre**, perché la default route del router di casa copre qualunque indirizzo. Non dice nulla sul fatto che tu sia collegato alla fotocamera. La verifica giusta è che la rotta sia *on-link*, cioè senza gateway intermedio.

## Cosa non fa, di proposito

**Non cancella niente dalla fotocamera.** Il protocollo ha `exec_erase.cgi`, ma non viene mai invocato: è l'unica operazione irreversibile disponibile, non serve a scaricare foto, e tenerla fuori significa che nessun difetto del programma potrà mai mangiarti gli scatti.

Fuori scope anche la sincronizzazione dell'orologio e il log GPS, entrambi raggiungibili con lo stesso client ma non necessari.

## Test

```bash
.venv/bin/python -m pytest
```

777 test, nessuno dei quali richiede la fotocamera collegata. Alcuni girano contro l'output **reale** registrato dalla fotocamera in `tests/fixtures/`, incluso uno che fa passare tutti i 1161 file attraverso il client e ne verifica la ripartizione per tipo.

Il test più importante è `test_download_recovers_from_drop_at_half_without_range`: un server locale chiude la connessione a metà del corpo **e** ignora il `Range`, cioè riproduce esattamente il comportamento di questa fotocamera. Verifica che il recupero produca un file corretto invece di un file corrotto.

## Struttura

```
olympus_connect/
├── core/            nessun import di Qt, testabile senza schermo
│   ├── model.py        MediaFile, decodifica data FAT, parsing imglist
│   ├── protocol.py     OlympusClient: HTTP e protocollo OPC
│   ├── network.py      diagnostica e profilo NetworkManager
│   ├── downloader.py   pianificazione, .part, rinomina atomica
│   ├── stats.py        velocità, tempo stimato, formattazione delle misure
│   ├── exif.py         dati di scatto dall'EXIF standard, in italiano
│   ├── liveview.py     pacchetti RTP del live, ricomposizione dei fotogrammi
│   ├── camprops.py     impostazioni della fotocamera, testo italiano e regole
│   ├── sequence.py     piano e automa delle sequenze, foto della sequenza, nome della cartella
│   ├── timelapse.py    montaggio del video di una sequenza con ffmpeg
│   ├── power.py        tiene sveglio il PC durante una sequenza (systemd-inhibit)
│   └── errors.py
├── gui/             worker su QThread, il core non sa che Qt esista
│   ├── workers.py      ListWorker, ThumbWorker, PreviewWorker, DownloadWorker
│   ├── grid.py         modello a colonne, griglia ed elenco
│   ├── preview.py      anteprima grande da get_screennail
│   ├── shot.py         sezione Scatto del pannello
│   ├── liveview.py     ricezione del live su un thread
│   ├── remote.py       vista del live e comandi del telecomando
│   ├── props.py        sezione Impostazioni del telecomando
│   ├── sequence.py     sezione Sequenza, finestra di fine sequenza, regista degli scatti
│   ├── panels.py       pannelli laterali richiudibili e animati
│   ├── theme.py        palette scura e foglio di stile
│   ├── icons.py        icone disegnate a tracciato, un solo peso
│   └── main_window.py
└── resources/       icona dell'applicazione, dettagliata e semplificata
diag.py              strumento diagnostico, eseguibile da solo
tools/prova_sequenza.py  prova sul campo delle sequenze, scrive un referto
install-desktop.sh   voce di menu, icone e comando nella home
```

I download sono **seriali** per scelta: il server della fotocamera è un microcontrollore e più richieste in volo insieme lo degradano. Per lo stesso motivo le miniature si sospendono durante un trasferimento.

## Licenza

**GNU General Public License v3.0 o successiva** — testo completo in [`LICENSE`](LICENSE).

In breve: puoi usare, studiare, modificare e redistribuire questo software, anche commercialmente, a condizione che le versioni derivate restino sotto la stessa licenza e che ne distribuisci il codice sorgente. Nessuna garanzia.
