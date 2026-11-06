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
3. Visione dal vivo, per `0640x0480`, `1024x0768` e `1280x0960` (ogni volta `mode=play` e poi `mode=rec&lvqty=<qualità>`; dimensione letta dal TLV 26): il ricevitore è **già in ascolto** su un socket UDP con porta locale fissa **40000** e buffer di ricezione richiesto da 4 MB; prima di `exec_takemisc.cgi?com=startliveview&port=40000` invia **un datagramma** da quella porta a 192.168.0.10:16540 (apre il passaggio nel firewall); registra i pacchetti per circa 3 secondi, poi chiama `stopliveview`. Misura: pacchetti e fotogrammi completi (marcatori RTP), dimensioni dei pacchetti, salti di sequenza a regime (dopo 0,2 s dal ritorno di `startliveview`) e arretrato dei primi 0,2 s, campi dell'intestazione RTP, indirizzo e porta di origine. Se non arriva nessun pacchetto lo dichiara come probabile blocco del firewall e stampa la regola temporanea `sudo ufw allow in from 192.168.0.10 proto udp to any port 40000` (e quella per toglierla), da usare solo come ripiego.
4. Con il **live acceso**: `exec_takemotion.cgi?com=assignafframe&point=<centro>` poi `releaseafframe`: stato e corpo (dice se la fotocamera comunica l'esito della messa a fuoco).
5. Sempre con il **live acceso**: `exec_takemotion.cgi?com=starttake` poi `stoptake`, poi `exec_takemisc.cgi?com=getrecview` e `getlastjpg`: stati, corpi, tempi, dimensioni.
6. `exec_takemotion.cgi?com=startmovietake&liveview=on`, 3 secondi, `stopmovietake`, con il live attivo e già in ascolto: il live continua durante la registrazione? E dopo lo stop (3 s di ascolto, poi `startliveview` di nuovo)?
7. `stopliveview`, `switch_cammode.cgi?mode=play`, `get_imglist.cgi` della cartella: la foto e il video nuovi compaiono?

In un blocco `finally`: `stopmovietake`, `stopliveview` e ritorno in riproduzione, qualunque cosa sia andata storta.

**Cosa chiede all'utente:** ghiera su P, A, S, M o iAuto; un soggetto davanti all'obiettivo. Sulla scheda restano una foto e un video di prova; l'app non li cancella.

**Riservatezza.** Fotogrammi del live e immagine di revisione mostrano ciò che la fotocamera inquadra. I pacchetti grezzi e le immagini restano in `diag-downloads/` (ignorata da git). Nelle fixture entra solo la **struttura**: per ogni pacchetto le intestazioni RTP vere e la lunghezza; il payload JPEG viene sostituito da un JPEG sintetico spezzato allo stesso modo. Le risposte di `get_camprop` possono contenere seriali e vanno controllate come le fixture EXIF prima del commit.

**Esito della prova sul campo (terza esecuzione, 2026-11-06 22:21), che chiude tutti i punti «da verificare»:** passa. Eseguita **senza alcuna regola ufw**. Buffer di ricezione richiesto 4 MB, effettivo 8 MB.
- **Firewall: nessuna regola necessaria.** Il datagramma dalla porta locale 40000 a 192.168.0.10:16540 inviato prima di `startliveview` basta a far arrivare il live. Supera la conclusione della seconda esecuzione («regola necessaria»): la prima esecuzione era fallita solo perché nessun datagramma era stato inviato prima. L'app invia un datagramma dalla propria porta di ricezione a 192.168.0.10:16540 prima di ogni `startliveview`. Origine dei pacchetti sempre 192.168.0.10:16540.
- **Qualità:** il cambio funziona con `switch_cammode mode=play` e poi `mode=rec&lvqty=<qualità>`; il TLV 26 conferma 640×480, 1024×768 e 1280×960.
- **Frequenza.** Fotogrammi completi (marcatori RTP) in catture di circa 3,1 s: 640×480 96, 1024×768 95, quindi 30 fotogrammi/s; 1280×960 46 in 3,05 s, cioè circa **15 fotogrammi/s**. La riga «30.00 fotogrammi/s» della sonda per 1280×960 è sbagliata: conta i timestamp distinti e non i fotogrammi completi.
- **Regime stabile** (dopo 0,2 s dal ritorno di `startliveview`): 0 salti di sequenza a tutte le qualità. Arretrato nei primi 0,2 s: 189 / 466 / 288 pacchetti (640 / 1024 / 1280). Il buffer da 4 MB e il ricevitore già attivo hanno risolto l'overflow visto in precedenza.
- Dimensioni dei pacchetti: 640×480 226–1472, 1024×768 15–1472, 1280×960 32–1472 byte. Estensione di 228 byte, TLV [1, 4, 5, 3, 2, 6, 8, 9, 10, 12, 11, 14, 16, 17, 18, 19, 20, 26].
- Formato: RTP versione 2, payload type 96, CSRC 0, padding 0. L'estensione (profilo 0x0001, lunghezza in parole da 32 bit) è un **elenco di TLV**: ogni TLV = id (2 byte, big endian), lunghezza in parole da 32 bit (2 byte), valore di 4×lunghezza byte. Id noti: **1** contatore; **2** riquadro di messa a fuoco live (zero prima della messa a fuoco, coincide con la risposta di `starttake` dopo); **21** presente solo durante la registrazione; **26** dimensione del fotogramma (due uint32 big endian: larghezza, altezza). Estensione di 228 byte (intestazione 244) nel live normale, 240 byte (intestazione 256) in registrazione; la lunghezza varia, quindi il parser non deve supporre una lunghezza fissa.
- Fine del fotogramma = **solo il marcatore RTP**; `FF D9` si verifica sul fotogramma assemblato, mai per pacchetto (nel video un pacchetto di marcatore ha payload di 1 byte, D9, mentre FF chiudeva il pacchetto precedente). Inizio = pacchetto con l'estensione.
- Durante la registrazione la dimensione del fotogramma è **640×360** (16:9), con un arresto di circa 0,4 s all'avvio della registrazione. La cattura del video inizia con ~46 pacchetti vecchi della sessione di scatto (nessuno leggeva il socket durante messa a fuoco e scatto, nessuno svuotamento dopo `stopliveview (scatto)`), poi un salto di sequenza di 1814.
- **Dopo `stopmovietake` il live continua da solo** (1896 pacchetti nei 3 s successivi); la dimensione del fotogramma torna a 640×480. La conclusione della seconda esecuzione («il live si è fermato dopo il video») era un artefatto della sonda: la finestra di ascolto era troppo corta. Durante la registrazione il fotogramma è 640×360, quindi `RemoteView` deve seguire la dimensione letta dal TLV 26.
- **Qualità predefinita del live: `1024x0768`**, la più alta a 30 fotogrammi/s (1280×960 scende a circa 15).
- Messa a fuoco: assignafframe point=0320x0240 risponde HTTP 200 in 174 ms con `affocus ok`, `afframepoint 0284x0204`, `afframesize 0072x0072`; il punto restituito è l'angolo in alto a sinistra del riquadro (centro meno metà di 72, in coordinate del live). `camprop_desclist` riporta `touchactiveframe 0096-0072_0448x0336`: l'area toccabile per la messa a fuoco è 448×336 con origine (96, 72) dentro il fotogramma 640×480. I comandi `exec_takemotion` richiedono il live acceso: con il live fermo rispondono HTTP 520, errorcode 1004.
- Scatto: starttake HTTP 200 in 502 ms (`take ok`, `affocus ok`, afframepoint 0296x0224, afframesize 0046x0032), stoptake 200; getrecview con il live acceso: JPEG 1024×768, 50 587 byte, 44 ms; getlastjpg con il live acceso: JPEG 5184×3888, 1 759 136 byte, 1184 ms.
- Video: startmovietake con liveview=on HTTP 200 in 536 ms, stopmovietake 200 in 1606 ms; il live continua durante la registrazione (a 640×360).
- Modalità della ghiera: get_camprop takemode risponde `<get><value>S</value></get>`.

## Architettura

### Core (nessun import di Qt)

**`core/liveview.py`, nuovo.**

- `parse_rtp(packet: bytes) -> RtpPacket | None`: intestazione RTP (RFC 3550): versione 2, marcatore, tipo di payload, sequenza, timestamp, estensione opzionale (profilo e dati), payload. Pacchetti più corti dell'intestazione, versione diversa da 2 o estensione che sfora: `None`. L'estensione è un elenco di TLV (vedi l'esito della prova) di lunghezza variabile (228 e 240 byte osservate); il risultato espone almeno il TLV 26 (dimensione del fotogramma) e il TLV 2 (riquadro di messa a fuoco).
- `FrameAssembler` con `feed(packet: bytes) -> bytes | None`: accumula i payload di un fotogramma e restituisce il JPEG completo quando il fotogramma si chiude (regola fissata dalla prova: inizio = pacchetto con l'estensione, fine = marcatore RTP, con `FF D9` verificato sull'intero buffer assemblato e mai per pacchetto).
  - pacchetto mancante (salto di sequenza) a metà fotogramma: il fotogramma si scarta e si aspetta l'inizio del successivo; mai un JPEG incompleto. L'arretrato vecchio di una sessione precedente si presenta come un salto e viene scartato allo stesso modo;
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
- **sentinella**: nessun fotogramma per 2 s dopo il primo emette `stalled()`; nessun fotogramma entro 3 s dall'avvio emette `no_signal()` (ripiego, nel caso il firewall blocchi comunque il live);
- `start()` apre il socket su una porta locale scelta dal sistema (nessuna regola del firewall è necessaria) ed emette `listening(port: int)`; **prima di ogni `startliveview` invia un datagramma dalla porta su cui è in ascolto a 192.168.0.10:16540**, che apre il passaggio per le risposte della fotocamera; `stop()` chiude il ciclo e il socket; nessuna eccezione esce dal ciclo né dagli slot. La finestra chiede al `RemoteWorker` di entrare solo dopo `listening`, passandogli quella porta.

**`RemoteWorker`** (`gui/workers.py`), su un thread proprio con un client proprio, separato dal live perché un comando che aspetta la fotocamera non deve fermare l'immagine:

- slot `enter(quality, port)` (rec mode + avvio del live + lettura della modalità), `focus(x, y)`, `shoot()`, `start_movie()`, `stop_movie()`, `leave()` (ferma video se in corso, ferma il live, torna in play; ogni passo tentato anche se il precedente fallisce);
- segnali: `entered(take_mode: str)`, `focused(esito: object)` (il `bool | None` di `assign_af`), `shot(rec_view: bytes)` (immagine di revisione, o vuoto se non disponibile), `movie_started()`, `movie_stopped()`, `left()`, `failed(messaggio: str)`;
- comandi in fila, uno alla volta; tutto dentro `try`.

**Finestra** (`gui/main_window.py`) e vista nuova (`gui/remote.py`, nuovo: `RemoteView` per il live e `RemotePanel` per i comandi):

- **terzo pulsante nella barra in alto**, accanto a Miniature ed Elenco: **Telecomando**, con icona disegnata nello stile di `gui/icons.py`. Attivo solo con la fotocamera collegata e nessun download in corso. Non viene ricordato come vista d'avvio: all'avvio la fotocamera non è collegata;
- **entrando:** sospende miniature e anteprima (i loro client lavorano in riproduzione), disattiva "Scarica selezionati" e i filtri, mostra `RemoteView` al centro e `RemotePanel` al posto di anteprima e dettagli, chiede al `RemoteWorker` di entrare e al `LiveViewWorker` di ascoltare;
- **`RemoteView`:** il fotogramma più recente scalato alla **dimensione reale letta dal TLV 26** (non un 4:3 fisso: in registrazione è 16:9, 640×360) con bande ai lati, sfondo `sunken` del sistema di design; un clic converte la posizione nelle coordinate del live (tenendo conto delle bande e della dimensione corrente del fotogramma) e le limita al `touchactiveframe` (96,72 448×336) e chiede la messa a fuoco; il riquadro di messa a fuoco si disegna nel punto, con l'esito se la fotocamera lo comunica; sopra l'immagine, quando serve, i messaggi «Live interrotto» con Riprova e «Nessuna immagine dalla fotocamera» con la spiegazione del firewall;
- **`RemotePanel`:** sezione «Scatto» con **Scatta** (pulsante primario, anche con la barra spaziatrice) e **Registra / Ferma** (pallino rosso, tempo trascorso `mm:ss` con cifre tabulari, anche con il tasto R); sezione «Fotocamera» con la modalità della ghiera in sola lettura;
- **dopo lo scatto:** l'immagine di revisione copre il live per 2 s, con la dissolvenza del sistema (180 ms, OutCubic, da 0,35), poi il live riprende; dopo un video il live riprende subito;
- **uscendo** (Miniature, Elenco, o chiusura dell'app): `RemoteWorker.leave()` (che ferma anche una registrazione in corso), poi il live si ferma, poi la finestra rifà l'elenco con `on_refresh()`, che crea un `ListWorker` con un client **nuovo**: nessuna modalità ricordata, quindi `ensure_play_mode` rimette davvero la fotocamera in riproduzione. Alla chiusura dell'app l'uscita ha un limite di 3 s;
- durante il telecomando la barra di stato mostra lo stato (collegamento, registrazione) e gli errori tradotti.

## Errori

- live assente entro 3 s: messaggio di ripiego sopra il live: nessun segnale, con il suggerimento di controllare il firewall (`sudo ufw allow in from 192.168.0.10 proto udp`). Nell'uso normale non serve nessuna regola;
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
