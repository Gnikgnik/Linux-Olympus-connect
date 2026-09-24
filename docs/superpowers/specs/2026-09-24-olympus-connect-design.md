# Olympus Connect — Design

**Data:** 2026-09-24
**Stato:** approvato
**Obiettivo:** applicazione desktop Linux per collegarsi via Wi-Fi a una Olympus OM-D E-M10 Mark IV e scaricare foto e video.

## Problema

Su Windows il trasferimento funziona. Su CachyOS, collegandosi all'access point della fotocamera, non si riesce a scaricare nulla.

Due cause probabili, entrambe indipendenti dal fatto che l'associazione Wi-Fi riesca:

1. **Il server HTTP della fotocamera scarta le richieste senza l'header `User-Agent: OI.Share v2`.** Un browser o `wget` puntati su `192.168.0.10` non ottengono risposta utile, dando l'impressione che la connessione sia rotta.
2. **NetworkManager tratta l'AP della fotocamera come una rete normale:** le assegna una default route (perdita di Internet) ed esegue il connectivity check, potendo riagganciarsi a un'altra rete a metà trasferimento.

Entrambe le cause sono ipotesi motivate, non fatti verificati. La prima azione dell'implementazione è confermarle o smentirle sul campo (vedi *Test sul campo*).

## Protocollo

La fotocamera in modalità AP espone un server HTTP su `http://192.168.0.10/`, documentato come *OPC Communication Protocol 1.0a*.

| Endpoint | Uso |
|---|---|
| `get_commandlist.cgi` | XML dei comandi supportati dal modello specifico |
| `get_caminfo.cgi` | modello fotocamera |
| `switch_cammode.cgi?mode=play` | passaggio a modalità riproduzione, richiesto prima di elencare |
| `get_imglist.cgi?DIR=/DCIM` | elenco file in formato CSV |
| `get_thumbnail.cgi?DIR=<path>` | miniatura JPEG |
| `GET /DCIM/100OLYMP/<file>` | download del file |

Header obbligatori su ogni richiesta: `User-Agent: OI.Share v2`, `Host: 192.168.0.10`.

Formato di una riga di `get_imglist.cgi`:

```
/DCIM/100OLYMP,P9240123.JPG,17150208,0,19594,45820
 directory      nome         size     attr date  time
```

`date` e `time` sono in formato FAT a campi di bit:

- anno = `1980 + (date >> 9)`, mese = `(date >> 5) & 15`, giorno = `date & 31`
- ora = `time >> 11`, minuti = `(time >> 5) & 63`, secondi = `2 * (time & 31)`

Bit di `attr`: 2 = hidden, 4 = system, 8 = volume, 16 = directory. I primi tre si ignorano, il quarto richiede ricorsione. Una directory vuota risponde `404`, che va trattato come lista vuota e non come errore.

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

`.content` carica l'intero file in memoria e la firma restituisce `bytes`. Per un MOV 4K da 600 MB significa 600 MB di RAM e nessuna possibilità di ripresa: una caduta al 90% costa l'intero trasferimento. Non è correggibile dall'esterno — la firma del metodo *è* il vincolo. Si aggiungono l'assenza di `timeout` (una camera in standby appende la GUI) e i `print()` su stdout dentro `list_images`, invisibili in una GUI.

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
| `list_media(dir="/DCIM")` | ricorsiva; salta hidden/system/volume; `404` = vuoto; ritorna `list[MediaFile]` |
| `thumbnail(path)` | `get_thumbnail.cgi?DIR=…` → `bytes` |
| `open_stream(path, offset=0)` | iteratore di chunk + dimensione totale + **flag: il server ha onorato il `Range`?** (`206` contro `200`) |

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
2. se il `.part` esiste **e** il server supporta `Range`, riprende dalla sua dimensione; altrimenti tronca e riparte
3. a trasferimento completo, verifica la dimensione e fa `os.replace()` al nome finale
4. imposta l'mtime del file a `shot_at`
5. controlla `should_cancel` tra i chunk

Il punto 3 è ciò che rende sicura la regola dei duplicati: un file col nome definitivo è per costruzione integro, quindi "esiste con la stessa dimensione → salta" non può lasciare un file troncato considerato completo.

Seriale per costruzione: accetta un job alla volta, la coda vive nel worker.

### `gui/`

- `main_window.py` — toolbar (Connetti / Aggiorna / Scarica selezionati / cartella destinazione), status bar col modello della fotocamera, pannello progressi con avanzamento per file e totale più i pulsanti **Annulla** e **Riprendi** (quest'ultimo attivo quando la coda è in pausa per `CameraAsleep`). Filtri per tipo (JPEG/RAW/MOV) e "solo non scaricati". `QSettings` per cartella di destinazione, SSID e filtri; destinazione predefinita `~/Immagini/Olympus`.
- `grid.py` — `QListView` in `IconMode` con modello custom sulla lista di `MediaFile`. Virtualizza: 2000 foto non creano 2000 widget; le miniature si richiedono solo per le righe visibili, in coda seriale.
- `workers.py` — `ListWorker`, `ThumbWorker`, `DownloadWorker`: `QObject` spostati su `QThread`, che avvolgono le chiamate al core ed emettono signal. Il core non importa Qt.

## Flusso

1. avvio → `probe()` → stato in barra
2. "Connetti" → `ensure_profile()` + `connect()` → nuovo `probe()`
3. `OlympusClient.connect()` → `ensure_play_mode()` → `list_media()`
4. griglia popolata; `plan()` marca i già scaricati
5. miniature caricate pigramente per le righe visibili
6. "Scarica selezionati" → job accodati al `DownloadWorker`, seriali
7. annullamento in qualsiasi momento; i `.part` restano sul disco per la ripresa

## Gestione errori

Regola trasversale: **la GUI non si blocca e non muore**. Ogni worker ha un canale d'errore; un'eccezione diventa una riga marcata "fallita", non un traceback su un terminale che nessuno guarda.

| Stato | Causa | Messaggio / comportamento |
|---|---|---|
| `CameraNotReachable` | nessuna route, o TCP rifiutato | "Attiva il Wi-Fi sulla fotocamera: menu → Connessione a smartphone" |
| `WrongNetwork` | associato ad altra rete | nomina la rete a cui sei effettivamente collegato |
| `CameraAsleep` | AP caduto per inattività | pausa della coda: "riconnetti e premi Riprendi" |
| `RangeUnsupported` | il firmware ignora `Range` | degrada in silenzio a ripartenza da zero; annotato una volta nel log |
| `ProtocolError` | XML/CSV inattesi | messaggio + risposta grezza in pannello dettagli |
| disco pieno / permessi | I/O locale | fallimento del singolo job, la coda continua |

`CameraAsleep` è l'errore che si incontrerà davvero: la E-M10 IV spegne l'AP dopo inattività, e su un MOV da mezzo giga il sintomo è un read timeout a metà. Gestione: 3 tentativi sul chunk corrente con backoff 2 s / 4 s / 8 s, poi pausa della coda con messaggio esplicito. Grazie a `.part` + `Range` non si perde nulla: alla riconnessione si riprende dal byte raggiunto. È lo scenario per cui è stata scartata la libreria esistente.

## Test

| Cosa | Come |
|---|---|
| `model.py` — data FAT, path destinazione | unit test puri, zero rete |
| `plan()` — regola di salto | file finti su `tmp_path`: stessa dimensione, dimensione diversa, assente |
| `protocol.py` | `requests-mock` su output reale registrato dalla fotocamera |
| `downloader.py` | server HTTP locale istruibile: normale / ignora `Range` / **chiude la connessione al 50 %** |
| `network.py` | runner `nmcli` iniettabile: si verificano gli argv emessi, senza toccare la rete |
| GUI | smoke test di costruzione; verifica manuale con la fotocamera |

Il test più importante è la connessione chiusa al 50 %: è l'unico modo per sapere che la ripresa funziona prima di scoprirlo su un video da 600 MB.

## Test sul campo (prima azione dell'implementazione)

`diag.py`, ~40 righe, eseguito con la fotocamera accesa e collegata, **prima di scrivere codice GUI**:

1. esegue la scala diagnostica completa stampando ogni gradino
2. interroga `get_caminfo.cgi` con e senza `User-Agent: OI.Share v2`, confermando o smentendo la causa #1
3. salva l'output grezzo di `get_commandlist.cgi` e `get_imglist.cgi`
4. scarica un JPEG, un ORF e un MOV; verifica se una richiesta `Range` riceve `206`

Risponde alle tre domande aperte — **RAW scaricabile? MOV scaricabile? `Range` supportato?** — e produce le fixture di test. Resta nel repo come strumento di diagnosi permanente.

## Assunzioni da verificare

- **La E-M10 IV risponde su `192.168.0.10:80`** come i modelli precedenti. Nessuna documentazione fa sospettare il contrario, ma non esistono conferme specifiche per questo modello. Se il firmware avesse cambiato indirizzo o richiedesse un handshake Bluetooth preliminare, `diag.py` lo rivela al primo tentativo e il design della rete va rivisto.
- **ORF e MOV sono scaricabili via `GET` sul path.** OI.Share non li espone, ma è un limite dell'app, non necessariamente del server.

Se una delle due cade, si scopre il primo giorno.

## Fuori scope

Cancellazione dalla scheda (`exec_erase.cgi`), live view, scatto remoto, sincronizzazione orologio, log GPS. Tutto raggiungibile con lo stesso client, nessuno necessario per scaricare foto.
