# Telecomando: visione dal vivo, scatto e video — Design

**Data:** 2026-11-06
**Stato:** approvato in sessione; i formati della visione dal vivo e le risposte della modalità scatto si fissano con la prova sul campo descritta più avanti, che fa da cancello
**Obiettivo:** inquadrare e scattare da lontano: vedere sul PC l'inquadratura della fotocamera in tempo reale, mettere a fuoco con un clic, scattare foto e girare video.

## Perché

Con la fotocamera su un treppiede e il fotografo al PC (autoritratti, animali, still life, riprese video con la fotocamera fuori portata) servono tre cose insieme: vedere l'inquadratura, decidere dove mettere a fuoco, scattare o registrare. Prese da sole non bastano: una visione dal vivo senza scatto, o uno scatto alla cieca, non rispondono al bisogno. Per questo questa specifica le copre tutte e tre.

## Ambito

Il telecomando si divide in cinque pezzi. Questa specifica copre **A, B e C**:

| Pezzo | Contenuto | Qui |
|---|---|---|
| A | prova sul campo della modalità scatto | sì |
| B | visione dal vivo | sì |
| C | scatto, video, messa a fuoco col tocco, revisione dopo lo scatto | sì |
| D | impostazioni dal PC: ISO, tempi, diaframmi, compensazione, bilanciamento, filtri artistici | no, specifica propria |
| E | automatismi: intervallometro, timelapse, posa B | no, specifica propria |

**Fuori, di proposito:** cambiare le impostazioni di scatto dal PC (pezzo D); scaricare automaticamente il file appena scattato (si scarica con la funzione esistente, dopo); zoom motorizzato e messa a fuoco manuale (`ctrlzoom`, `supermacromfinaflock`); qualunque cancellazione dalla scheda.

## Fatti accertati

Dalla lista dei comandi della E-M10 Mark IV (`tests/fixtures/commandlist.xml`):

| Comando | Parametri dichiarati |
|---|---|
| `switch_cammode.cgi` | `mode=rec` con `lvqty` fra `0320x0240`, `0640x0480`, `0800x0600`, `1024x0768`, `1280x0960`; `mode=play` |
| `exec_takemisc.cgi` | `com=startliveview` con `port`; `stopliveview`; `getrecview`; `getlastjpg`; altri fuori ambito |
| `exec_takemotion.cgi` | `com=assignafframe` con `point`; `releaseafframe`; `takeready` con `point`; `starttake` con `point`, `exposuremin`, `upperlimit`; `stoptake`; `startmovietake` con `limitter` e `liveview=on`; `stopmovietake` |
| `get_camprop.cgi` | `com=desc` / `com=get` con `propname`, fra cui `takemode`, `desclist` |

Dalla prova sul campo del 2026-11-06: `switch_cammode=rec` cambia modalità (in rec, `set_timeout` risponde `errorcode 1001` invece del `1006` della riproduzione). Nient'altro della modalità scatto è stato misurato.

Dal sistema: sulla macchina di riferimento il firewall **ufw è attivo**; le sue regole non sono leggibili senza privilegi.

La fotocamera sta **o in modalità scatto o in riproduzione**: elenco, miniature, anteprime, dati di scatto e download funzionano solo in riproduzione.

## Prova sul campo (pezzo A)

Un passaggio nuovo di `diag.py`, `probe_remote()`, eseguito **solo con l'opzione `--telecomando`** e per ultimo: scatta davvero una foto e gira un video, quindi non deve partire in una diagnosi qualunque.

In ordine:

1. `switch_cammode.cgi?mode=rec&lvqty=0640x0480`: stato, corpo, tempo.
2. `get_camprop.cgi?com=get&propname=takemode` e `get_camprop.cgi?com=desc&propname=desclist`: risposte salvate; dicono la modalità della ghiera e l'elenco delle proprietà (base del pezzo D).
3. Visione dal vivo, per `0640x0480` e `1024x0768` (rientrando in rec con la qualità nuova): apre un socket UDP su una porta locale libera, chiama `exec_takemisc.cgi?com=startliveview&port=<porta>`, registra i pacchetti per 5 secondi, chiama `stopliveview`. Misura: pacchetti e fotogrammi al secondo, dimensioni dei pacchetti, campi dell'intestazione RTP (versione, tipo di payload, marcatore, sequenza, timestamp, presenza e lunghezza dell'estensione), indirizzo e porta di origine. Se in 5 secondi non arriva **nessun** pacchetto lo dichiara come probabile blocco del firewall e stampa la regola temporanea da applicare a mano per riprovare, `sudo ufw allow in from 192.168.0.10 proto udp to any port <porta>`, e quella per toglierla, `sudo ufw delete allow in from 192.168.0.10 proto udp to any port <porta>`, con la porta usata dalla prova (fissa per l'opzione di riprova, così la regola vale).
4. `exec_takemotion.cgi?com=assignafframe&point=<centro>` poi `releaseafframe`: stato e corpo (dice se la fotocamera comunica l'esito della messa a fuoco).
5. `exec_takemotion.cgi?com=starttake` poi `stoptake`, poi `exec_takemisc.cgi?com=getrecview` e `getlastjpg`: stati, corpi, tempi, dimensioni.
6. `exec_takemotion.cgi?com=startmovietake&liveview=on`, 3 secondi, `stopmovietake`, con il live attivo: il live continua durante la registrazione?
7. `stopliveview`, `switch_cammode.cgi?mode=play`, `get_imglist.cgi` della cartella: la foto e il video nuovi compaiono?

In un blocco `finally`: `stopliveview` e ritorno in riproduzione, qualunque cosa sia andata storta.

**Cosa chiede all'utente:** ghiera su P, A, S, M o iAuto; un soggetto davanti all'obiettivo. Sulla scheda restano una foto e un video di prova; l'app non li cancella.

**Riservatezza.** Fotogrammi del live e immagine di revisione mostrano ciò che la fotocamera inquadra. I pacchetti grezzi e le immagini restano in `diag-downloads/` (ignorata da git). Nelle fixture entra solo la **struttura**: per ogni pacchetto le intestazioni RTP vere e la lunghezza; il payload JPEG viene sostituito da un JPEG sintetico spezzato allo stesso modo. Le risposte di `get_camprop` possono contenere seriali e vanno controllate come le fixture EXIF prima del commit.

**Cancello.** Se il live non arriva nemmeno con la regola temporanea, o se lo scatto non parte, il lavoro si ferma qui, prima di scrivere l'interfaccia. L'esito viene annotato in questa specifica e nel resoconto sul campo, insieme alle decisioni che ne derivano: qualità predefinita del live (la più alta che mantiene almeno 15 fotogrammi al secondo), formato dell'estensione RTP, fine del fotogramma (marcatore o fine JPEG), esito della messa a fuoco, se serve una regola del firewall.

## Architettura

### Core (nessun import di Qt)

**`core/liveview.py`, nuovo.**

- `parse_rtp(packet: bytes) -> RtpPacket | None`: intestazione RTP (RFC 3550): versione 2, marcatore, tipo di payload, sequenza, timestamp, estensione opzionale (profilo e dati), payload. Pacchetti più corti dell'intestazione, versione diversa da 2 o estensione che sfora: `None`.
- `FrameAssembler` con `feed(packet: bytes) -> bytes | None`: accumula i payload di un fotogramma e restituisce il JPEG completo quando il fotogramma si chiude (regola fissata dalla prova: marcatore RTP oppure `FF D9` finale). Il primo pacchetto di un fotogramma si riconosce dalla regola fissata dalla prova (estensione presente, oppure payload che inizia con `FF D8`).
  - pacchetto mancante (salto di sequenza) a metà fotogramma: il fotogramma si scarta e si aspetta l'inizio del successivo; mai un JPEG incompleto;
  - pacchetti duplicati o fuori ordine: il fotogramma corrente si scarta;
  - un fotogramma che supera 4 MB si scarta;
  - non solleva mai eccezioni, con nessun input.

**`core/protocol.py`.**

- `enter_rec_mode(quality: str)`: `switch_cammode.cgi?mode=rec&lvqty=<quality>`; imposta la modalità ricordata a `rec`.
- `start_liveview(port: int)`, `stop_liveview()`: `exec_takemisc.cgi?com=startliveview&port=` / `com=stopliveview`.
- `assign_af(x: int, y: int) -> bool | None`, `release_af()`: `exec_takemotion.cgi?com=assignafframe&point=<XXXX>x<YYYY>` / `releaseafframe`. Restituisce `True` a fuoco, `False` messa a fuoco fallita, `None` se la fotocamera non comunica l'esito. Il formato di `point` e il modo in cui l'esito arriva si fissano con la prova.
- `take_picture()`: `starttake` seguito da `stoptake`.
- `start_movie()`, `stop_movie()`: `startmovietake` con `liveview=on`, `stopmovietake`.
- `rec_view() -> bytes`: `exec_takemisc.cgi?com=getrecview`.
- `take_mode() -> str`: `get_camprop.cgi?com=get&propname=takemode`, ridotto a una sigla (P, A, S, M, iAuto…).
- `ensure_play_mode()` continua a funzionare come oggi; un client che è passato in rec lo sa e ritorna in play quando serve.
- Ogni risposta d'errore della fotocamera diventa un `ProtocolError` con il codice (`errorcode`) leggibile, così `describe_error` può tradurlo.

### GUI

**`LiveViewWorker`** (`gui/liveview.py`, nuovo), su un thread proprio:

- possiede il socket UDP (modulo `socket` di Python, timeout di ricezione 0,25 s per poter uscire dal ciclo), un `FrameAssembler` e la decodifica: trasforma ogni JPEG completo in una `QImage` sul proprio thread;
- **vince l'ultimo fotogramma**: tiene un solo fotogramma in attesa (protetto da un lock); emette `frame_ready()` solo se il precedente è già stato preso; la finestra prende l'ultimo con `take_frame() -> QImage | None`. Il ritardo non si accumula mai;
- **sentinella**: nessun fotogramma per 2 s dopo il primo emette `stalled()`; nessun fotogramma entro 3 s dall'avvio emette `no_signal()` (probabile firewall);
- `start()` apre il socket su una porta locale (quella scelta dal sistema, oppure una porta fissa se la prova stabilisce che serve una regola del firewall) ed emette `listening(port: int)`; `stop()` chiude il ciclo e il socket; nessuna eccezione esce dal ciclo né dagli slot. La finestra chiede al `RemoteWorker` di entrare solo dopo `listening`, passandogli quella porta.

**`RemoteWorker`** (`gui/workers.py`), su un thread proprio con un client proprio, separato dal live perché un comando che aspetta la fotocamera non deve fermare l'immagine:

- slot `enter(quality, port)` (rec mode + avvio del live + lettura della modalità), `focus(x, y)`, `shoot()`, `start_movie()`, `stop_movie()`, `leave()` (ferma video se in corso, ferma il live, torna in play; ogni passo tentato anche se il precedente fallisce);
- segnali: `entered(take_mode: str)`, `focused(esito: object)` (il `bool | None` di `assign_af`), `shot(rec_view: bytes)` (immagine di revisione, o vuoto se non disponibile), `movie_started()`, `movie_stopped()`, `left()`, `failed(messaggio: str)`;
- comandi in fila, uno alla volta; tutto dentro `try`.

**Finestra** (`gui/main_window.py`) e vista nuova (`gui/remote.py`, nuovo: `RemoteView` per il live e `RemotePanel` per i comandi):

- **terzo pulsante nella barra in alto**, accanto a Miniature ed Elenco: **Telecomando**, con icona disegnata nello stile di `gui/icons.py`. Attivo solo con la fotocamera collegata e nessun download in corso. Non viene ricordato come vista d'avvio: all'avvio la fotocamera non è collegata;
- **entrando:** sospende miniature e anteprima (i loro client lavorano in riproduzione), disattiva "Scarica selezionati" e i filtri, mostra `RemoteView` al centro e `RemotePanel` al posto di anteprima e dettagli, chiede al `RemoteWorker` di entrare e al `LiveViewWorker` di ascoltare;
- **`RemoteView`:** il fotogramma più recente scalato in 4:3 con bande ai lati, sfondo `sunken` del sistema di design; un clic converte la posizione nelle coordinate del live (tenendo conto delle bande) e chiede la messa a fuoco; il riquadro di messa a fuoco si disegna nel punto, con l'esito se la fotocamera lo comunica; sopra l'immagine, quando serve, i messaggi «Live interrotto» con Riprova e «Nessuna immagine dalla fotocamera» con la spiegazione del firewall;
- **`RemotePanel`:** sezione «Scatto» con **Scatta** (pulsante primario, anche con la barra spaziatrice) e **Registra / Ferma** (pallino rosso, tempo trascorso `mm:ss` con cifre tabulari, anche con il tasto R); sezione «Fotocamera» con la modalità della ghiera in sola lettura;
- **dopo lo scatto:** l'immagine di revisione copre il live per 2 s, con la dissolvenza del sistema (180 ms, OutCubic, da 0,35), poi il live riprende; dopo un video il live riprende subito;
- **uscendo** (Miniature, Elenco, o chiusura dell'app): `RemoteWorker.leave()` (che ferma anche una registrazione in corso), poi il live si ferma, poi la finestra rifà l'elenco con `on_refresh()`, che crea un `ListWorker` con un client **nuovo**: nessuna modalità ricordata, quindi `ensure_play_mode` rimette davvero la fotocamera in riproduzione. Alla chiusura dell'app l'uscita ha un limite di 3 s;
- durante il telecomando la barra di stato mostra lo stato (collegamento, registrazione) e gli errori tradotti.

## Errori

- live assente entro 3 s: messaggio sopra il live che indica il probabile blocco del firewall e la regola da applicare a mano (la prova stabilisce quale); è l'unico caso in cui all'utente si chiede di toccare il sistema;
- live interrotto per 2 s: «Live interrotto» con Riprova, che rientra in rec e riavvia il live;
- scatto o video rifiutati (scheda piena, ghiera in una modalità che non lo consente, `errorcode` della fotocamera): messaggio in italiano nella barra di stato tramite `describe_error`; il live continua;
- fotocamera addormentata o Wi-Fi perso durante una registrazione: il video lo chiude la fotocamera; l'app lo dice e torna allo stato iniziale del telecomando;
- nessuna eccezione esce dai worker; il ritorno in riproduzione all'uscita viene tentato anche se tutto il resto è fallito.

## Test

Tutti senza fotocamera collegata.

- **`tests/test_liveview.py`:** `parse_rtp` su intestazioni vere della prova e costruite; `FrameAssembler` sui pacchetti veri con payload sintetico (restituisce JPEG decodificabili, quanti fotogrammi attesi), più: pacchetto perso a metà, fuori ordine, duplicato, fotogramma oltre 4 MB, spazzatura, ogni troncamento di ogni pacchetto (nessuna eccezione).
- **Protocollo:** ogni comando nuovo contro `requests_mock` con i parametri giusti; risposte d'errore con `errorcode` diventano `ProtocolError` leggibili.
- **`LiveViewWorker`:** un mittente UDP su `127.0.0.1` invia i pacchetti; consegna l'ultimo fotogramma; con la finestra lenta scarta i fotogrammi in eccesso invece di accumularli; `stalled` dopo 2 s di silenzio, `no_signal` senza nessun pacchetto entro 3 s; `stop()` termina il ciclo.
- **`RemoteWorker`:** ordine dei comandi; `leave()` tenta ogni passo anche se il precedente fallisce; nessuna eccezione esce dagli slot.
- **Finestra:** il pulsante Telecomando è disattivato senza fotocamera e durante un download; entrare sospende miniature e anteprima e disattiva download e filtri; uscire con una registrazione in corso la ferma; uscire rifà l'elenco con un client nuovo; la revisione copre il live per 2 s; un clic sull'immagine diventa le coordinate giuste del live anche con le bande; barra spaziatrice e R.
- **Dall'inizio alla fine:** fotocamera finta HTTP più un mittente UDP che invia un live sintetico alla porta richiesta da `startliveview`; la finestra vera entra nel telecomando, mostra i fotogrammi, scatta, esce e rifà l'elenco. Il test va verificato **fallire senza la modifica**.

## Documentazione

README: la riga del telecomando nella tabella «Cosa funziona», un paragrafo nell'«Uso», la nota sul firewall se la prova la rende necessaria, i file nuovi nello schema della struttura. DESIGN.md: il modo Telecomando, la vista del live e il pannello dei comandi. Resoconto sul campo: l'esito della prova.
