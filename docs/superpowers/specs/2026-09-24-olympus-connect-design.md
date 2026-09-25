# Olympus Connect — Design

**Data:** 2026-09-24
**Stato:** approvato; sezioni *Problema*, *Protocollo* e *Fatti accertati* riscritte il 2026-09-24 dopo la verifica sul campo
**Obiettivo:** applicazione desktop Linux per collegarsi via Wi-Fi a una Olympus OM-D E-M10 Mark IV e scaricare foto e video.

## Problema

Su Windows il trasferimento funziona. Su CachyOS, collegandosi all'access point della fotocamera, non si riesce a scaricare nulla.

**Causa accertata: manca il client.** La fotocamera espone un server HTTP perfettamente funzionante, ma non serve un indice di directory: puntare un browser su `192.168.0.10` non mostra nulla, e senza conoscere gli endpoint CGI non c'è modo di arrivare ai file. Su Windows il lavoro lo fa OI.Share; su Linux non esiste un equivalente installabile.

La prima stesura di questa spec attribuiva il problema al fatto che la fotocamera scartasse le richieste prive dell'header `User-Agent: OI.Share v2`. **La verifica sul campo ha smentito l'ipotesi:** `get_caminfo.cgi` risponde `HTTP 200` con 436 byte identici sia con l'header sia senza. L'header viene comunque inviato — è quello che manda OI.Share, non costa niente e altri firmware potrebbero pretenderlo — ma non è la causa di nulla.

Resta valido il secondo problema, di natura diversa: **NetworkManager tratta l'AP della fotocamera come una rete normale**, le assegna una default route ed esegue il connectivity check, potendo riagganciarsi a un'altra rete a metà trasferimento. Il profilo dedicato descritto più avanti è la risposta a questo.

## Protocollo

La fotocamera in modalità AP espone un server HTTP su `http://192.168.0.10/`, documentato come *OPC Communication Protocol 1.0a*. L'esemplare di riferimento dichiara `version 4.50`, `oitrackversion 3.10`, modello `E-M10MarkIV`.

| Endpoint | Uso |
|---|---|
| `get_commandlist.cgi` | XML dei comandi supportati dal modello specifico |
| `get_caminfo.cgi` | modello, numeri di serie, capacità dichiarate |
| `switch_cammode.cgi?mode=play` | passaggio a modalità riproduzione, richiesto prima di elencare |
| `get_imglist.cgi?DIR=/DCIM` | elenco file in formato CSV |
| `get_thumbnail.cgi?DIR=<path>` | miniatura JPEG |
| `get_screennail.cgi?DIR=<path>` | anteprima più grande della miniatura |
| `set_timeout.cgi?timeoutsec=<n>` | timeout di inattività della sessione Wi-Fi |
| `GET /DCIM/100OLYMP/<file>` | download del file |

Header inviati su ogni richiesta: `User-Agent: OI.Share v2`, `Host: 192.168.0.10`. Non sono obbligatori (vedi *Problema*), ma replicano il comportamento di OI.Share.

`get_caminfo.cgi` dichiara `GetRawImageSupport: available` e `GetImageScreennailSupport: available`. L'unica funzione Bluetooth dichiarata è `BleShutter`: **nessun handshake BLE è richiesto** per elencare o scaricare.

Formato di una riga di `get_imglist.cgi`:

```
/DCIM/100OLYMP,P2100001.ORF,19578383,0,23626,28090
 directory      nome         size     attr date  time
```

La prima riga della risposta è un'intestazione di versione (`VER_100`) che non ha 6 campi e va ignorata come qualunque riga malformata.

`date` e `time` sono in formato FAT a campi di bit:

- anno = `1980 + (date >> 9)`, mese = `(date >> 5) & 15`, giorno = `date & 31`
- ora = `time >> 11`, minuti = `(time >> 5) & 63`, secondi = `2 * (time & 31)`

Decodifica verificata contro l'EXIF reale: `23626/28090` → `2026-02-10 13:45:52`, contro `13:45:53` letto da `exiv2`. **Lo scarto di un secondo è la granularità intrinseca del formato FAT, non un difetto:** i secondi sono memorizzati in unità da 2. Non "correggere" questa formula.

Bit di `attr`: 1 = read-only, 2 = hidden, 4 = system, 8 = volume, 16 = directory. Solo hidden, system e volume vanno scartati; **read-only è un file normale** e compare nei dati reali. Il bit directory richiede ricorsione. Una directory vuota risponde `404`, che va trattato come lista vuota e non come errore.
## Decisioni prese

| Decisione | Scelta | Motivo |
|---|---|---|
| Interfaccia | GUI desktop PyQt6 | scelta per poter selezionare visivamente quali file prendere |
| Formati | JPEG + ORF (RAW) + MOV | tutti trattati in modo uniforme: `GET` sul path del file |
| Gestione rete | completa via `nmcli`, con degradazione a sola diagnostica | è la metà del problema da risolvere; senza, il routing resta rotto |
| Organizzazione file | per data di scatto: `<dest>/2026/2026-09-24/P9240123.JPG` | la data arriva dalla `get_imglist`, non serve leggere l'EXIF |
| Duplicati | salta se esiste lo stesso nome **con la stessa dimensione**; riscarica se la dimensione differisce | non richiede rilettura del file; reso sicuro dai file `.part` (vedi sotto) |
| Cancellazione dalla scheda | **non implementata** | unica operazione irreversibile del software; non serve all'obiettivo |
| Client di protocollo | scritto da zero | vedi *Perché non `olympuswifi`* |
| Concorrenza | `QThread` + signal Qt, download **seriali** | il server della camera è un microcontrollore: richieste parallele lo degradano |

### Perché non `olympuswifi`

La libreria [`joergmlpts/olympus-wifi`](https://github.com/joergmlpts/olympus-wifi) implementa correttamente il protocollo e ne resta il riferimento documentale, ma non è riusabile come dipendenza per un motivo strutturale:

```python
def download_image(self, dir: str) -> bytes:
    return requests.get(self.URL_PREFIX + dir[1:], headers=self.HEADERS).content
```

`.content` carica l'intero file in memoria e la firma restituisce `bytes`. Sulla scheda di riferimento il MOV più grande è di **944 MB**: significa quasi un gigabyte di RAM per un singolo file, e nessuna possibilità di scrivere su disco progressivamente. Non è correggibile dall'esterno — la firma del metodo *è* il vincolo. Si aggiungono l'assenza di `timeout` (una camera in standby appende la GUI) e i `print()` su stdout dentro `list_images`, invisibili in una GUI.

Contropartita accettata: `olympuswifi` è collaudata su molti modelli, questo client parte da uno. Mitigazione: `get_commandlist.cgi` fa dichiarare alla fotocamera stessa i comandi che supporta, quindi il client si adatta invece di assumere.

## Architettura

Core sincrono che non conosce Qt; GUI che lo avvolge in worker thread. La separazione rende il protocollo testabile senza schermo e permette la diagnosi da terminale.

```
olympus_connect/
├── core/
│   ├── model.py        # MediaFile, MediaKind — dati puri, zero I/O
│   ├── protocol.py     # OlympusClient — HTTP + protocollo OPC
│   ├── network.py      # CameraNetwork — nmcli + diagnostica
│   ├── downloader.py   # streaming, Range/resume, layout destinazione
│   └── errors.py       # eccezioni tipizzate
├── gui/
│   ├── main_window.py  # toolbar, status bar, pannello progressi
│   ├── grid.py         # QListView IconMode + modello sui MediaFile
│   └── workers.py      # ListWorker, ThumbWorker, DownloadWorker
├── diag.py             # strumento diagnostico da terminale
└── tests/
```

Dipendenze: `requests`, `PyQt6` (entrambe già presenti nel sistema). Per i test: `requests-mock`, `pytest`.

### `core/model.py`

`MediaKind` enum (JPEG / RAW / MOVIE) e `MediaFile(path, size, shot_at, kind)`, dove `path` è il percorso sulla camera. Proprietà derivate: `name`, `dest_relpath` (`2026/2026-09-24/P9240123.JPG`).

Nessun I/O. La decodifica della data FAT è una funzione pura isolata qui: è il punto più facile da sbagliare del progetto.

### `core/protocol.py` — `OlympusClient`

| Metodo | Comportamento |
|---|---|
| `connect()` | `get_commandlist.cgi` → set di comandi supportati; `get_caminfo.cgi` → modello |
| `ensure_play_mode()` | `switch_cammode.cgi?mode=play` |
| `keep_awake(seconds)` | `set_timeout.cgi?timeoutsec=…`, solo se il comando è dichiarato; fallimento non bloccante |
| `list_media(dir="/DCIM")` | ricorsiva; salta hidden/system/volume; `404` = vuoto; ritorna `list[MediaFile]` |
| `thumbnail(path)` | `get_thumbnail.cgi?DIR=…` → `bytes` |
| `open_stream(path, offset=0)` | iteratore di chunk + dimensione totale + **flag: il server ha onorato il `Range`?** (`206` contro `200`) |

Ogni metodo verifica che il comando sia presente nel `get_commandlist` della fotocamera prima di invocarlo: i comandi variano da modello a modello, e `keep_awake` in particolare deve degradare silenziosamente dove `set_timeout` non esiste.

Ogni richiesta ha `timeout` esplicito: 3 s per il connect, 15 s per il read. Lo streaming legge in chunk da 64 KiB. Solleva eccezioni tipizzate, non stampa mai.

### `core/network.py` — `CameraNetwork`

**Scala diagnostica.** Un gradino per ogni causa distinta, perché "non funziona" non è un messaggio utile:

1. esiste un dispositivo Wi-Fi? (`nmcli -t -f DEVICE,TYPE,STATE device`)
2. è associato all'SSID della fotocamera?
3. esiste una route verso `192.168.0.10`? (`ip route get`)
4. la porta TCP 80 accetta? (socket, timeout breve)
5. `get_caminfo.cgi` risponde? — interrogato **due volte, con e senza** lo `User-Agent` di OI.Share, così la causa #1 del problema diventa un fatto verificato e mostrato a schermo

**Profilo NetworkManager.** Il modulo crea e gestisce solo il profilo `olympus-connect-<ssid>`, quindi non può danneggiare le connessioni esistenti. Impostazioni:

- `ipv4.never-default yes` — la connessione alla fotocamera non diventa la default route
- `ipv6.method disabled`
- `ipv4.route-metric 900`
- `connection.autoconnect no`

**Cosa `never-default` fa e cosa non fa.** Impedisce che la fotocamera diventi la default route, quindi il traffico verso Internet continua a essere instradato su un'altra interfaccia — *se esiste*. Sulla macchina di riferimento c'è una sola interfaccia attiva (`wlan0`; `enp0s31f6` è senza cavo), quindi collegarsi alla fotocamera significa comunque restare senza Internet: nessuna configurazione di routing può creare un secondo percorso che non c'è. Il vantaggio reale in quel caso è impedire a NetworkManager di stravolgere la tabella di routing e di riagganciare automaticamente altre reti a metà trasferimento.

Con ethernet collegata, un dongle Wi-Fi USB o il telefono in tethering USB, `never-default` fa esattamente ciò che promette: Internet resta attivo su quell'interfaccia e la fotocamera vive su `wlan0`.

**Conseguenza operativa sullo sviluppo:** durante i test sul campo la macchina è offline. Per questo `diag.py` è uno script autonomo con referto su file (`diag-report.txt`), eseguibile senza assistenza e senza rete.

La password del Wi-Fi resta dentro il profilo NetworkManager: **questo software non la memorizza mai**.

Tutte le invocazioni di `nmcli` passano da un unico runner con timeout, iniettabile per i test. Se `nmcli` manca o fallisce, si solleva `NetworkUnavailable` e la GUI degrada a sola diagnostica senza rompersi.

### `core/downloader.py`

Due fasi separate.

`plan(files, dest_root) -> list[Job]` confronta la lista della camera col disco e classifica ogni file in nuovo / già presente / da riscaricare. Permette alla GUI di mostrare "12 nuovi, 40 già presenti" prima di trasferire un byte.

`run(job, on_progress, should_cancel)`:

1. scrive su `<dest>.part`
2. se il `.part` esiste **e** il server supporta `Range`, riprende dalla sua dimensione; altrimenti tronca e riparte. **Sul firmware 4.50 della E-M10 IV il `Range` non è supportato** (vedi *Fatti accertati*), quindi in pratica si riparte sempre da zero; il ramo di ripresa resta nel codice perché il rilevamento è a runtime e altri modelli o firmware potrebbero supportarlo
3. a trasferimento completo, verifica la dimensione e fa `os.replace()` al nome finale
4. imposta l'mtime del file a `shot_at`
5. controlla `should_cancel` tra i chunk

Il punto 3 è ciò che rende sicura la regola dei duplicati: un file col nome definitivo è per costruzione integro, quindi "esiste con la stessa dimensione → salta" non può lasciare un file troncato considerato completo.

Seriale per costruzione: accetta un job alla volta, la coda vive nel worker.

### `gui/`

- `main_window.py` — toolbar (Connetti / Aggiorna / Scarica selezionati / cartella destinazione), status bar col modello della fotocamera, pannello progressi con avanzamento per file e totale più i pulsanti **Annulla** e **Riprova** (quest'ultimo attivo quando la coda è in pausa per `CameraAsleep`). Filtri per tipo (JPEG/RAW/MOV) e "solo non scaricati". `QSettings` per cartella di destinazione, SSID e filtri; destinazione predefinita `~/Immagini/Olympus`.
- `grid.py` — `QListView` in `IconMode` con modello custom sulla lista di `MediaFile`. Virtualizza: 2000 foto non creano 2000 widget; le miniature si richiedono solo per le righe visibili, in coda seriale.
- `workers.py` — `ListWorker`, `ThumbWorker`, `DownloadWorker`: `QObject` spostati su `QThread`, che avvolgono le chiamate al core ed emettono signal. Il core non importa Qt.

## Flusso

1. avvio → `probe()` → stato in barra
2. "Connetti" → `ensure_profile()` + `connect()` → nuovo `probe()`
3. `OlympusClient.connect()` → `ensure_play_mode()` → `list_media()`
4. griglia popolata; `plan()` marca i già scaricati
5. miniature caricate pigramente per le righe visibili
6. "Scarica selezionati" → job accodati al `DownloadWorker`, seriali
7. annullamento in qualsiasi momento; il `.part` interrotto viene scartato al tentativo successivo, perché senza `Range` non è riutilizzabile

## Gestione errori

Regola trasversale: **la GUI non si blocca e non muore**. Ogni worker ha un canale d'errore; un'eccezione diventa una riga marcata "fallita", non un traceback su un terminale che nessuno guarda.

| Stato | Causa | Messaggio / comportamento |
|---|---|---|
| `CameraNotReachable` | nessuna route, o TCP rifiutato | "Attiva il Wi-Fi sulla fotocamera: menu → Connessione a smartphone" |
| `WrongNetwork` | associato ad altra rete | nomina la rete a cui sei effettivamente collegato |
| `CameraAsleep` | AP caduto per inattività | pausa della coda: "riconnetti e premi Riprova" |
| `RangeUnsupported` | il firmware ignora `Range` | degrada in silenzio a ripartenza da zero; annotato una volta nel log. **È il caso normale su questo firmware** |
| `ProtocolError` | XML/CSV inattesi | messaggio + risposta grezza in pannello dettagli |
| disco pieno / permessi | I/O locale | fallimento del singolo job, la coda continua |

`CameraAsleep` è l'errore che si incontrerà davvero: la E-M10 IV spegne l'AP dopo inattività, e su un file grande il sintomo è un read timeout a metà. Gestione: 3 tentativi con backoff 2 s / 4 s / 8 s, poi pausa della coda con messaggio esplicito.

**Senza supporto `Range` questo errore è caro:** il tentativo riparte da zero, e sulla scheda di riferimento ci sono video da 944 MB, 672 MB e 528 MB.

Il keep-alive via `set_timeout.cgi` sembrava la mitigazione ovvia, ma **sul firmware 4.50 non funziona**: risponde `HTTP 520` con `errorcode 1006` per ogni valore provato. `keep_awake()` resta nel client e degrada a `False` senza sollevare, ma non ottiene nulla qui. Restano quindi tre difese reali:

1. **L'ordine della coda mette i file piccoli per primi.** Un'interruzione costa il file in corso, e con questo ordine il file in corso è quasi sempre piccolo: i 1141 ORF da 20 MB si chiudono prima che i MOV grandi entrino in coda.
2. **Il trasferimento stesso è attività.** Il rischio di standby si concentra nei periodi di inattività — mentre si scorrono le miniature o si sceglie cosa scaricare — non durante un `GET` continuo. Questo non elimina le cadute di Wi-Fi, per cui i tentativi con backoff restano necessari.
3. **`.part` + rename atomico**, descritti sopra.

Il file `.part` con rename atomico resta comunque essenziale, e in assenza di `Range` lo è di più: garantisce che un trasferimento interrotto non lasci mai un file col nome definitivo, che è ciò che rende sicura la regola dei duplicati.

## Test

| Cosa | Come |
|---|---|
| `model.py` — data FAT, path destinazione | unit test puri, zero rete |
| `plan()` — regola di salto | file finti su `tmp_path`: stessa dimensione, dimensione diversa, assente |
| `protocol.py` | `requests-mock` su output reale registrato dalla fotocamera |
| `downloader.py` | server HTTP locale istruibile: normale / ignora `Range` / **chiude la connessione al 50 %** |
| `network.py` | runner `nmcli` iniettabile: si verificano gli argv emessi, senza toccare la rete |
| GUI | smoke test di costruzione; verifica manuale con la fotocamera |

Il test più importante è la connessione chiusa al 50 %: è l'unico modo per sapere che il recupero funziona prima di scoprirlo su un video da 944 MB. Il server di test deve coprire entrambi i comportamenti — onora il `Range` e lo ignora — perché il secondo è quello reale su questo firmware e il primo protegge il ramo di codice che serve ad altri modelli.

## Test sul campo (prima azione dell'implementazione)

`diag.py`, eseguito con la fotocamera accesa e collegata, **prima di scrivere codice GUI**. Esegue la scala diagnostica, interroga `get_caminfo.cgi` con e senza `User-Agent`, salva l'output grezzo di `get_commandlist.cgi` e `get_imglist.cgi`, scarica un JPEG, un ORF e un MOV, verifica il `Range`, prova `get_thumbnail`/`get_screennail` su ciascun tipo e sonda `set_timeout`.

Produce le fixture di test e resta nel repo come strumento di diagnosi permanente. Duplica l'output in `diag-report.txt`, perché viene eseguito mentre la macchina è offline e il referto è l'unico risultato.

## Fatti accertati sul campo

Verificati il 2026-09-24 su E-M10 Mark IV, firmware OI.Share `version 4.50`, seriale `XXXXXXXXX`.

| Domanda | Esito |
|---|---|
| Risponde su `192.168.0.10:80`? | **Sì**, con rotta on-link diretta (`src 192.168.0.3`) |
| Serve un handshake Bluetooth? | **No.** `caminfo` dichiara solo `BleShutter` |
| Serve l'header `User-Agent: OI.Share v2`? | **No.** `HTTP 200` identico con e senza |
| ORF scaricabile via `GET`? | **Sì.** 19,6 MB integri; `caminfo` dichiara `GetRawImageSupport: available` |
| MOV scaricabile via `GET`? | **Sì.** 129,6 MB, QuickTime valido |
| `Range` supportato? | **No.** `HTTP 200`, nessun `Content-Range`, file intero rimandato su tutti e tre i formati |
| Decodifica data FAT corretta? | **Sì**, con granularità 2 s intrinseca al formato |
| `set_timeout` utilizzabile? | **No.** Dichiarato nel commandlist, ma risponde `HTTP 520` / `errorcode 1006` per 300, 600, 1800 e 3600 s |
| `get_thumbnail` copre ORF e MOV? | **Sì.** JPEG validi di 8–13 KB per tutti e tre i formati: la griglia funziona sul 100 % della scheda |
| `get_screennail` disponibile? | **Sì**, ma oltre 1 MB su ORF: adatto a un'anteprima singola, non a una griglia. Fuori scope |
| `get_unusedcapacity` affidabile? | **No.** Restituisce `0xFFFFFFFF`, sentinella di "non disponibile" |

Contenuto della scheda di riferimento — rilevante perché definisce la scala del problema:

| Tipo | File | Totale | Media |
|---|---|---|---|
| `.ORF` | 1141 | 23,48 GB | 20,6 MB |
| `.MOV` | 18 | 4,39 GB | 243,8 MB (massimo 944 MB) |
| `.JPG` | 2 | 0,02 GB | 9,0 MB |

**Questo dato giustifica il progetto meglio di qualunque argomento:** OI.Share importa solo JPEG, quindi l'applicazione ufficiale vede 2 file su 1161. Comporta anche che la griglia debba essere virtualizzata per davvero e che il salto dei duplicati non sia un ottimizzazione ma un requisito.

## Fuori scope

Cancellazione dalla scheda (`exec_erase.cgi`), live view, scatto remoto, sincronizzazione orologio, log GPS. Tutto raggiungibile con lo stesso client, nessuno necessario per scaricare foto.
