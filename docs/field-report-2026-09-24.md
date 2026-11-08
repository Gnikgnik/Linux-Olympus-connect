# Referto di verifica sul campo — 2026-09-24

Task 0 del piano di implementazione. Fotocamera **Olympus OM-D E-M10 Mark IV**, firmware OI.Share `version 4.50` / `oitrackversion 3.10`, seriale `XXXXXXXXX`, SSID dell'access point `E-M10MKIV-P-XXXXXXXXX`.

Macchina: CachyOS, kernel 7.2.7, NetworkManager 1.58.1, Python 3.14.7. Interfaccia `wlan0` unica attiva (`enp0s31f6` senza cavo), quindi la macchina è **offline** mentre è collegata alla fotocamera: `diag.py` è progettato per girare senza assistenza e scrivere un referto su file.

## Risposte alle domande aperte

| Domanda | Risposta | Conseguenza |
|---|---|---|
| ORF scaricabile via `GET`? | **Sì.** 19 578 383 byte, `exiv2` lo legge come ORF 5200×3904 | `MediaKind.RAW` pienamente supportato |
| MOV scaricabile via `GET`? | **Sì.** 129 623 040 byte, QuickTime valido | `MediaKind.MOVIE` pienamente supportato |
| `Range` produce `206`? | **No.** `HTTP 200`, nessun `Content-Range`, file intero rimandato su tutti e tre i formati | ripresa impossibile: il recupero riparte da zero. Aggiunto `keep_awake()`, `pending()` ordina dal file più piccolo, pulsante "Riprova" invece di "Riprendi" |
| `get_caminfo.cgi` risponde senza `User-Agent`? | **Sì**, `HTTP 200` con 436 byte identici | **ipotesi della spec smentita:** l'header non è la causa del problema. Sezione *Problema* riscritta |

## Conclusione sulla causa del problema

La spec ipotizzava che la fotocamera scartasse le richieste prive dell'header `User-Agent: OI.Share v2`. È falso: risponde identicamente con e senza.

La causa reale è più semplice. Il server HTTP della fotocamera funziona correttamente ma **non serve un indice di directory**: puntare un browser su `192.168.0.10` non mostra nulla, e senza conoscere gli endpoint CGI non c'è modo di raggiungere i file. Su Windows quel lavoro lo fa OI.Share; su Linux non esiste un equivalente installabile. Mancava il client, non il permesso.

L'header viene comunque inviato: è ciò che manda OI.Share, non costa niente e altri firmware potrebbero pretenderlo.

## Capacità dichiarate dalla fotocamera

Da `get_caminfo.cgi`:

```xml
<model>E-M10MarkIV</model>
<GetImageScreennailSupport>available</GetImageScreennailSupport>
<GetRawImageSupport>available</GetRawImageSupport>
<BleFunctionDetailList><Func1>BleShutter</Func1></BleFunctionDetailList>
```

`GetRawImageSupport: available` conferma a livello di protocollo che il RAW è previsto. L'unica funzione Bluetooth dichiarata è `BleShutter`: **nessun handshake BLE è richiesto** per elencare o scaricare, contrariamente a quanto si potrebbe temere sui modelli recenti.

Il `get_commandlist.cgi` (root `<oishare>`, non `<oimp>`) dichiara 37 comandi. Rilevanti per il progetto: `get_imglist`, `get_thumbnail`, `get_screennail`, `get_exif`, `switch_cammode` (`play` / `rec` con `lvqty` fino a 1280×960), **`set_timeout` con parametro `timeoutsec`**, `get_unusedcapacity`, `exec_pwoff`, `get_movplaytime`, `get_movfileinfo`. Presente anche `exec_erase`, che per scelta di progetto non verrà mai invocato.

## Contenuto della scheda

| Tipo | File | Totale | Media | Massimo |
|---|---|---|---|---|
| `.ORF` | 1141 | 23,48 GB | 20,6 MB | — |
| `.MOV` | 18 | 4,39 GB | 243,8 MB | **944,2 MB** |
| `.JPG` | 2 | 0,02 GB | 9,0 MB | — |
| **totale** | **1161** | **27,9 GB** | | |

Intervallo date: 2020-01-01 → 2026-09-06. I 65 file datati `2020-01-01 00:00:00` corrispondono a scatti con orologio della fotocamera non impostato; la data decodifica correttamente e finiranno in `2020/2020-01-01/`.

**Questo dato giustifica il progetto meglio di qualunque argomento.** OI.Share importa solo JPEG: l'applicazione ufficiale vede **2 file su 1161**. Comporta inoltre che la virtualizzazione della griglia e il salto dei duplicati non siano ottimizzazioni ma requisiti, e che i tre MOV più grandi (944, 672, 528 MB) siano il caso peggiore da proteggere in assenza di ripresa.

## Dettagli del formato `get_imglist.cgi`

Due particolarità trovate nei dati reali che i test iniziali non coprivano, entrambe ora con un test dedicato:

- **La risposta è preceduta da una riga `VER_100`**, che non ha 6 campi e va ignorata come qualunque riga malformata.
- **Compare `attrib=1`** (`P9061187.ORF`), il bit FAT *read-only*. È un file normale: solo hidden (2), system (4) e volume (8) vanno scartati. I test prevedevano solo `attrib=0`.

**Decodifica della data verificata contro l'EXIF.** Riga reale `/DCIM/100OLYMP,P2100001.ORF,19578383,0,23626,28090`:

| Fonte | Valore |
|---|---|
| decodifica FAT | `2026-02-10 13:45:52` |
| EXIF via `exiv2` | `2026-02-10 13:45:53` |

Lo scarto di un secondo è la **granularità intrinseca del formato FAT**, che memorizza i secondi in unità da due. La formula è corretta e non va "aggiustata".

## Verifica della scala diagnostica

Due comandi che il piano dava per buoni si sono rivelati inutilizzabili, corretti prima dell'implementazione:

1. **`ip route get 192.168.0.10` riesce sempre.** Con il PC sul Wi-Fi di casa l'output era `192.168.0.10 via 192.168.1.254 dev wlan0`, identico alla rotta verso `8.8.8.8`: il gradino avrebbe segnalato OK proprio nello stato che deve rilevare. Verifica corretta: l'indirizzo deve essere **on-link**, cioè la rotta non deve contenere ` via `. Con la fotocamera collegata: `192.168.0.10 dev wlan0 src 192.168.0.3`.
2. **`nmcli -t -f ACTIVE,SSID device wifi` riportava `no:` per tutte le reti** pur essendo connessi a `casa-wifi`. Sostituito con `nmcli -t -f GENERAL.CONNECTION,GENERAL.STATE device show <dev>`, che restituisce il nome del profilo attivo e lo stato `100 (connected)`.

## Referto grezzo — passata 1

```
Olympus Connect — diagnostica (2026-09-24 21:28:17)

== Scala diagnostica ==
  [OK  ] dispositivo Wi-Fi presente — wlan0
  [OK  ] rete Wi-Fi attiva — wlan0→E-M10MKIV-P-XXXXXXXXX
  [OK  ] 192.168.0.10 raggiungibile direttamente (on-link) — 192.168.0.10 dev wlan0 src 192.168.0.3 uid 1000 \ cache
  [OK  ] porta 80 della fotocamera — accetta

== Verifica dell'header User-Agent ==
  [OK  ] get_caminfo.cgi con User-Agent OI.Share — HTTP 200, 436 byte
  [OK  ] get_caminfo.cgi senza User-Agent — HTTP 200, 436 byte

== Registrazione fixture ==
  salvato commandlist.xml (7829 byte)
  salvato caminfo.xml (436 byte)
  switch_cammode mode=play OK
  salvato imglist_dcim.csv (42 byte) per /DCIM
  salvato imglist_dcim_100olymp.csv (60097 byte) per /DCIM/100OLYMP

  1161 file trovati sulla scheda

== Download e supporto Range ==
  estensioni sulla scheda: .JPG x2, .MOV x18, .ORF x1141
  [OK  ] download .JPG — /DCIM/100OLYMP/P2170015.JPG: HTTP 200, 9118261 byte
  [FAIL] Range su .JPG — HTTP 200, Content-Range=assente, ricevuti 9118261 byte (attesi 9117237)
  [OK  ] download .ORF — /DCIM/100OLYMP/P2100001.ORF: HTTP 200, 19578383 byte
  [FAIL] Range su .ORF — HTTP 200, Content-Range=assente, ricevuti 19578383 byte (attesi 19577359)
  [OK  ] download .MOV — /DCIM/100OLYMP/P2210118.MOV: HTTP 200, 129623040 byte
  [FAIL] Range su .MOV — HTTP 200, Content-Range=assente, ricevuti 129623040 byte (attesi 129622016)

Fatto. Fixture in tests/fixtures/, campioni in diag-downloads/.
```

## Passata 2 — miniature e keep-alive

Eseguita alle 21:55:08 dello stesso giorno. La scala diagnostica, i download e l'esito su `Range` hanno riprodotto identicamente la passata 1.

### Le miniature funzionano su tutti i formati

```
[OK] get_thumbnail  .JPG —   8 416 byte, JPEG valido
[OK] get_thumbnail  .ORF —   9 093 byte, JPEG valido
[OK] get_thumbnail  .MOV —  12 912 byte, JPEG valido
[OK] get_screennail .JPG — 161 280 byte, JPEG valido
[OK] get_screennail .ORF — 1 076 736 byte, JPEG valido
[OK] get_screennail .MOV — 153 441 byte, JPEG valido
```

**La domanda più importante ha risposta affermativa.** `get_thumbnail` restituisce un JPEG valido anche per ORF e MOV, quindi la griglia di miniature funziona sul 100 % della scheda — compresi i 1141 RAW che sono il motivo del progetto. Il Task 9 procede come progettato, senza riprogettazioni.

Dimensioni utili per il dimensionamento: le miniature stanno fra 8 e 13 KB, quindi popolare la griglia visibile costa pochissimo. `get_screennail` su ORF restituisce **oltre 1 MB**: ottimo per un'anteprima a schermo grande del singolo file selezionato, decisamente troppo pesante per una griglia. Resta fuori scope per ora, ma è la scelta giusta se in futuro si volesse un pannello di anteprima.

### `set_timeout` non è utilizzabile su questo firmware

```
[FAIL] set_timeout timeoutsec=300  — HTTP 520: <errorcode>1006</errorcode>
[FAIL] set_timeout timeoutsec=600  — HTTP 520: <errorcode>1006</errorcode>
[FAIL] set_timeout timeoutsec=1800 — HTTP 520: <errorcode>1006</errorcode>
[FAIL] set_timeout timeoutsec=3600 — HTTP 520: <errorcode>1006</errorcode>
```

Il comando è dichiarato nel `get_commandlist` ma rifiuta tutti i valori provati con `HTTP 520` ed `errorcode 1006`. **La mitigazione che avevo individuato per l'assenza di `Range` non è disponibile.**

Due ipotesi non verificate sul motivo, da provare durante la prova end-to-end del Task 11 invece che in una passata dedicata: il comando potrebbe richiedere la modalità `rec` anziché `play` (la sonda è stata eseguita dopo `switch_cammode mode=play`), oppure accettare solo un insieme ristretto di valori. `diag.py` è stato esteso per coprire entrambe le ipotesi alla prossima esecuzione.

`keep_awake()` resta nel codice: per progetto restituisce `False` e non solleva quando il comando manca o fallisce, quindi il comportamento è già corretto — semplicemente non ottiene nulla su questo firmware.

### `get_unusedcapacity` non è affidabile

```
[OK] get_unusedcapacity — HTTP 200: <unused>4294967295</unused>
```

`4294967295` è `0xFFFFFFFF`, cioè `-1` interpretato come intero senza segno: un valore sentinella che significa "non disponibile", non 4 GB liberi. **Non costruire nulla su questo endpoint.**

## Conseguenze finali sul progetto

Con `Range` assente **e** keep-alive non disponibile, il caso peggiore — il MOV da 944 MB — non ha una protezione diretta. Le difese effettive sono tre, tutte già nel piano:

1. **`pending()` ordina dal file più piccolo.** Un'interruzione costa il file in corso, e con questo ordine il file in corso è quasi sempre piccolo: i 1141 ORF da 20 MB si chiudono prima che i MOV grandi entrino in coda.
2. **Il trasferimento stesso è attività.** Il rischio di standby è concentrato nei periodi di inattività — mentre si scorrono le miniature o si decide cosa scaricare — non durante un `GET` continuo. Questo non elimina le cadute di Wi-Fi, per cui i 3 tentativi con backoff restano.
3. **`.part` + rename atomico.** Nessun file interrotto prende mai il nome definitivo, quindi la regola dei duplicati resta sicura e un secondo tentativo riprende sempre da uno stato coerente.

**Task 0 chiuso.** Nessuna assunzione della spec resta non verificata.

## Prova end-to-end dell'applicazione

Eseguita a implementazione completa, con la fotocamera collegata. Esito: JPEG, RAW e video scaricati correttamente; interruzione e ripresa funzionanti; miniature corrette su tutti i tipi; filtri e doppia vista funzionanti.

### Verifica incrociata di un file scaricato

`P9061187.ORF`, confrontando ciò che la fotocamera dichiara nella `get_imglist` con ciò che `exiv2` legge nel file arrivato sul disco:

| | Fotocamera | File scaricato |
|---|---|---|
| Dimensione | 21 453 224 byte | 21 453 224 byte |
| Cartella di destinazione | `2026/2026-09-06/` | `2026/2026-09-06/` |
| Timestamp | 23:08:32 | 23:08:32 |

Tre conferme indipendenti: il trasferimento è **byte-esatto**, l'organizzazione per data usa la data giusta, e la decodifica FAT coincide con l'EXIF al secondo (in questo caso i secondi erano pari, quindi senza lo scarto di granularità).

Da notare: è proprio il file con `attrib=1`, l'unico read-only sulla scheda. Senza il test scritto sui dati reali il parser lo avrebbe scartato insieme ai file nascosti.

### Profilo NetworkManager

```
ipv4.never-default:     sì
ipv4.route-metric:      900
connection.autoconnect: no
```

Il profilo `olympus-connect-<ssid>` è creato con le impostazioni previste, e la fotocamera non compare come default route.

### Non verificato

La sonda estesa di `set_timeout` (modalità `rec` e valori limite) **non è stata eseguita**: l'ultimo referto contiene solo i tentativi in modalità `play`, tutti falliti con `HTTP 520` / `errorcode 1006`. Se un valore venisse accettato in `rec`, il keep-alive sarebbe recuperabile e con esso una difesa reale sui video da centinaia di MB. Resta aperto come miglioramento, non come difetto.

## Aggiornamento 2026-11-06: dati di scatto

Prova eseguita con `diag.py`, sezione «Dati di scatto»:

```
  [SALTATO] get_exif .JPG: nessun file
  [FAIL] get_exif .ORF — HTTP 200, application/octet-stream, 66560 byte, 57 ms, forma: sconosciuta, inizio: 49 49 52 4f 08 00 00 00 17 00 00 01 04 00 01 00
  [SALTATO] get_exif .MOV: nessun file
  [FAIL] EXIF dentro l'anteprima di un ORF — HTTP 200, 1228288 byte, nessun blocco Exif
```

Fonte scelta: **A**, `get_exif.cgi`. Per un ORF risponde `200`, `application/octet-stream`, 66 560 byte in 57 ms, e comincia con l'intestazione `IIRO`. Il `FAIL` dell'etichetta ORF è solo del rilevatore di forma della sonda, che allora non conosceva l'intestazione ORF `IIRO`; da allora è stato corretto in `diag.py`. Per JPG e MOV la prova è stata saltata perché non c'erano file di quel tipo sulla scheda. L'anteprima di un ORF non contiene un blocco Exif, quindi la fonte B non è utilizzabile per i RAW.

## Aggiornamento 2026-11-06: modalità scatto

Prima esecuzione (21:53), senza regola del firewall: `startliveview` risponde 200 ma non arriva nessun pacchetto a entrambe le qualità; i comandi di messa a fuoco e scatto rispondono HTTP 520, errorcode 1004, perché la prova li mandava con il live fermo (corretto poi); il video parte e si ferma. Seconda esecuzione (21:59), con la regola temporanea `ufw allow in from 192.168.0.10 proto udp to any port 40000`: tutto passa. Referto della seconda:

```
== Telecomando (modalità scatto) ==
  Scatta una foto e gira un video di 3 secondi.
  [OK  ] switch_cammode rec 0640x0480 — HTTP 200, 49 ms: (vuoto)
  [OK  ] get_camprop takemode — HTTP 200, 24 ms: <?xml version="1.0"?><get><value>S</value></get>
  [OK  ] get_camprop desclist — HTTP 200, 360 ms: <?xml version="1.0"?><desclist><desc><propname>shutspeedvalue</propname><attribute>getset</attribute><value>640</value><enum>60" 50" 40" 30" 25" 20" 15" 13" 10"
  [OK  ] startliveview 0640x0480 — HTTP 200, 319 ms: <?xml version="1.0"?><funcidlist><funcid name="framesize"/><funcid name="afframeinfo"/><funcid name="mntmediainfo"/><funcid name="rotation"/><funcid name="maxta
  [OK  ] stopliveview 0640x0480 — HTTP 200, 308 ms: (vuoto)
  [OK  ] live 0640x0480 — 3250 pacchetti (650/s), circa 31.0 fotogrammi/s, dimensioni 14–1472 byte, origine 192.168.0.10:16540
    inizi JPEG 155, marcatori RTP 153, fini JPEG 153, salti di sequenza 1, RTP non leggibili 0
    RTP: versione 2, payload type 96, CSRC 0, padding 0
    pacchetti con estensione: 155
    prima estensione: profilo 0x0001, 228 byte, intestazione 90 60 26 f6 00 14 df c0 00 00 00 00 00 01 00 39 00 01 00 01 00 00 72 c1 00 04 00 01 00 00 00 01 00 05 00 01 00 00 07 8c 00 03 00 01 00 00 00 02
  [OK  ] switch_cammode rec 1024x0768 — HTTP 200, 25 ms: (vuoto)
  [OK  ] startliveview 1024x0768 — HTTP 200, 291 ms: <?xml version="1.0"?><funcidlist><funcid name="framesize"/><funcid name="afframeinfo"/><funcid name="mntmediainfo"/><funcid name="rotation"/><funcid name="maxta
  [OK  ] stopliveview 1024x0768 — HTTP 200, 262 ms: (vuoto)
  [OK  ] live 1024x0768 — 3225 pacchetti (645/s), circa 31.0 fotogrammi/s, dimensioni 16–1472 byte, origine 192.168.0.10:16540
    inizi JPEG 155, marcatori RTP 154, fini JPEG 154, salti di sequenza 1, RTP non leggibili 0
    RTP: versione 2, payload type 96, CSRC 0, padding 0
    pacchetti con estensione: 155
    prima estensione: profilo 0x0001, 228 byte, intestazione 90 60 33 e7 00 1c 0f 98 00 00 00 00 00 01 00 39 00 01 00 01 00 00 71 64 00 04 00 01 00 00 00 01 00 05 00 01 00 00 07 8c 00 03 00 01 00 00 00 02
  [OK  ] switch_cammode rec 0640x0480 (scatto) — HTTP 200, 22 ms: (vuoto)
  [OK  ] startliveview (scatto) — HTTP 200, 325 ms: <?xml version="1.0"?><funcidlist><funcid name="framesize"/><funcid name="afframeinfo"/><funcid name="mntmediainfo"/><funcid name="rotation"/><funcid name="maxta
  [OK  ] assignafframe 0320x0240 — HTTP 200, 174 ms: <?xml version="1.0"?><response><affocus>ok</affocus><afframepoint>0284x0204</afframepoint><afframesize>0072x0072</afframesize></response>
  [OK  ] releaseafframe — HTTP 200, 22 ms: (vuoto)
  [OK  ] starttake — HTTP 200, 502 ms: <?xml version="1.0"?><response><take>ok</take><affocus>ok</affocus><afframepoint>0296x0224</afframepoint><afframesize>0046x0032</afframesize></response>
  [OK  ] stoptake — HTTP 200, 19 ms: (vuoto)
  [OK  ] getrecview (live acceso) — HTTP 200, 44 ms: JPEG 50587 byte
  [OK  ] getlastjpg (live acceso) — HTTP 200, 1184 ms: JPEG 1759136 byte
  [OK  ] stopliveview (scatto) — HTTP 200, 320 ms: (vuoto)
  [OK  ] startliveview (video) — HTTP 200, 329 ms: <?xml version="1.0"?><funcidlist><funcid name="framesize"/><funcid name="afframeinfo"/><funcid name="mntmediainfo"/><funcid name="rotation"/><funcid name="maxta
  [OK  ] startmovietake liveview=on — HTTP 200, 536 ms: (vuoto)
  [OK  ] stopmovietake — HTTP 200, 1606 ms: (vuoto)
  [OK  ] live durante la registrazione — 2166 pacchetti in 5.1 s di registrazione
  ripristinata modalità play
  [OK  ] nuovi file sulla scheda — HTTP 200, 11 file, ultimi: PB061196.MOV, P9061187.ORF

Fatto. Risposte e pacchetti in diag-downloads/telecomando/.
```

### Correzioni dopo la revisione

La revisione finale delle fixture del live ha corretto alcune letture del referto sopra (le righe copiate restano com'erano):

- **Frequenza: 30 fotogrammi/s, non 31.** I timestamp RTP sono costanti dentro il fotogramma e crescono di 3000 per fotogramma (90 kHz). Il «31» e il «salto di sequenza» erano artefatti della prova: i primi ~45 pacchetti, letti a t≈0,002 s, sono l'arretrato accumulato nel buffer del kernel mentre la chiamata HTTP `startliveview` bloccava la lettura; il salto di 13–14 pacchetti è un overflow del buffer di ricezione.
- **La cattura «1024x0768» è in realtà 640×480.** La dimensione, letta dal TLV 26 dell'estensione RTP, è 640×480 in entrambe le catture: `switch_cammode mode=rec&lvqty=1024x0768` dato mentre la fotocamera era già in rec non ha cambiato la qualità. La fixture è stata rinominata `0640x0480-b.json`.
- **Il video comincia con ~46 pacchetti vecchi** della sessione di scatto (nessuno leggeva il socket durante messa a fuoco e scatto, nessuno svuotamento dopo «stopliveview (scatto)»), poi un salto di sequenza di 1814.
- **Dopo `stopmovietake` il live si è fermato** (ultimo pacchetto circa 60 ms dopo lo stop). «Live durante la registrazione: sì» vale solo per la registrazione; la ripresa dopo il video è non verificata.
- Durante la registrazione il fotogramma è 640×360 (16:9) e l'estensione è di 240 byte.

Questi punti si verificano con la terza esecuzione della prova (`diag.py --telecomando`, versione 2).


## Terza esecuzione (2026-11-06 22:21)

Terza esecuzione di `diag.py --telecomando` (versione 2), **senza alcuna regola ufw**. Righe del referto (le più lunghe sono troncate):

```
== Telecomando (modalità scatto, prova v2) ==
  Scatta una foto e gira un video di 3 secondi.
  [OK  ] buffer di ricezione — richiesto 4194304 byte, effettivo 8388608 byte (il kernel lo limita a net.core.rmem_max e lo raddoppia)
  [OK  ] switch_cammode rec 0640x0480 — HTTP 200, 47 ms: (vuoto)
  [OK  ] get_camprop takemode — HTTP 200, 23 ms: <?xml version="1.0"?><get><value>S</value></get>
  [OK  ] get_camprop desclist — HTTP 200, 279 ms: <?xml version="1.0"?><desclist><desc><propname>shutspeedvalue</propname><attribute>getset</attribute><value>640</value><enum>60" 50" 40" 30" 25" 20" 1…
  [OK  ] switch_cammode play — HTTP 200, 37 ms: (vuoto)
  [OK  ] switch_cammode rec 0640x0480 — HTTP 200, 50 ms: (vuoto)
  [OK  ] startliveview 0640x0480 — HTTP 200, 315 ms: <?xml version="1.0"?><funcidlist><funcid name="framesize"/><funcid name="afframeinfo"/><funcid name="mntmediainfo"/><funcid name="rotation"/><funci…
  [OK  ] stopliveview 0640x0480 — HTTP 200, 360 ms: (vuoto)
  [OK  ] live 0640x0480 — live senza regola (pacchetto iniziale verso :16540) — 2016 pacchetti, dimensioni 226–1472 byte, origine 192.168.0.10:16540
    regime stabile (dopo 0,2 s dal ritorno di startliveview): 1764 pacchetti, 84 fotogrammi, 30.00 fotogrammi/s, salti di sequenza 0
    pacchetti nei primi 0,2 s (arretrato): 189
    dimensione dal flusso: 640×480
    marcatori RTP 96, estensioni che sforano 0, RTP non leggibili 0
    prima estensione: profilo 0x0001, 228 byte, TLV [1, 4, 5, 3, 2, 6, 8, 9, 10, 12, 11, 14, 16, 17, 18, 19, 20, 26]
  [OK  ] firewall — nessuna regola del firewall necessaria
  [OK  ] switch_cammode play — HTTP 200, 44 ms: (vuoto)
  [OK  ] switch_cammode rec 1024x0768 — HTTP 200, 47 ms: (vuoto)
  [OK  ] startliveview 1024x0768 — HTTP 200, 355 ms: <?xml version="1.0"?><funcidlist><funcid name="framesize"/><funcid name="afframeinfo"/><funcid name="mntmediainfo"/><funcid name="rotation"/><funci…
  [OK  ] stopliveview 1024x0768 — HTTP 200, 347 ms: (vuoto)
  [OK  ] live 1024x0768 — 5344 pacchetti, dimensioni 15–1472 byte, origine 192.168.0.10:16540
    regime stabile (dopo 0,2 s dal ritorno di startliveview): 4706 pacchetti, 84 fotogrammi, 30.00 fotogrammi/s, salti di sequenza 0
    pacchetti nei primi 0,2 s (arretrato): 466
    dimensione dal flusso: 1024×768
    marcatori RTP 95, estensioni che sforano 0, RTP non leggibili 0
    prima estensione: profilo 0x0001, 228 byte, TLV [1, 4, 5, 3, 2, 6, 8, 9, 10, 12, 11, 14, 16, 17, 18, 19, 20, 26]
  [OK  ] switch_cammode play — HTTP 200, 44 ms: (vuoto)
  [OK  ] switch_cammode rec 1280x0960 — HTTP 200, 49 ms: (vuoto)
  [OK  ] startliveview 1280x0960 — HTTP 200, 227 ms: <?xml version="1.0"?><funcidlist><funcid name="framesize"/><funcid name="afframeinfo"/><funcid name="mntmediainfo"/><funcid name="rotation"/><funci…
  [OK  ] stopliveview 1280x0960 — HTTP 200, 276 ms: (vuoto)
  [OK  ] live 1280x0960 — 4027 pacchetti, dimensioni 32–1472 byte, origine 192.168.0.10:16540
    regime stabile (dopo 0,2 s dal ritorno di startliveview): 3615 pacchetti, 43 fotogrammi, 30.00 fotogrammi/s, salti di sequenza 0
    pacchetti nei primi 0,2 s (arretrato): 288
    dimensione dal flusso: 1280×960
    marcatori RTP 46, estensioni che sforano 0, RTP non leggibili 0
    prima estensione: profilo 0x0001, 228 byte, TLV [1, 4, 5, 3, 2, 6, 8, 9, 10, 12, 11, 14, 16, 17, 18, 19, 20, 26]
  [OK  ] switch_cammode play — HTTP 200, 41 ms: (vuoto)
  [OK  ] switch_cammode rec 0640x0480 (scatto) — HTTP 200, 47 ms: (vuoto)
  [OK  ] startliveview (scatto) — HTTP 200, 273 ms: <?xml version="1.0"?><funcidlist><funcid name="framesize"/><funcid name="afframeinfo"/><funcid name="mntmediainfo"/><funcid name="rotation"/><funcid…
  [OK  ] assignafframe 0320x0240 — HTTP 200, 232 ms: <?xml version="1.0"?><response><affocus>ok</affocus><afframepoint>0284x0204</afframepoint><afframesize>0072x0072</afframesize></response>
  [OK  ] releaseafframe — HTTP 200, 30 ms: (vuoto)
  [OK  ] starttake — HTTP 200, 538 ms: <?xml version="1.0"?><response><take>ok</take><affocus>ok</affocus><afframepoint>0296x0224</afframepoint><afframesize>0046x0032</afframesize></response>
  [OK  ] stoptake — HTTP 200, 27 ms: (vuoto)
  [OK  ] getrecview (live acceso) — HTTP 200, 43 ms: JPEG 38270 byte
  [OK  ] getlastjpg (live acceso) — HTTP 200, 1034 ms: JPEG 1563988 byte
  [OK  ] stopliveview (scatto) — HTTP 200, 256 ms: (vuoto)
  [OK  ] live durante messa a fuoco e scatto — 1595 pacchetti; dimensione dal flusso: 640×480
  [OK  ] startliveview (video) — HTTP 200, 285 ms: <?xml version="1.0"?><funcidlist><funcid name="framesize"/><funcid name="afframeinfo"/><funcid name="mntmediainfo"/><funcid name="rotation"/><funcid …
  [OK  ] startmovietake liveview=on — HTTP 200, 490 ms: (vuoto)
  [OK  ] stopmovietake — HTTP 200, 1625 ms: (vuoto)
  [OK  ] live durante la registrazione — 2004 pacchetti in 3.5 s di registrazione
  [OK  ] live dopo stopmovietake — 1896 pacchetti nei 3 s dopo il ritorno dello stop
    dimensione dal flusso: prima 640×480; durante 640×360, 640×480; dopo 640×360, 640×480
  ripristinata modalità play
  [OK  ] nuovi file sulla scheda — HTTP 200, 14 file, ultimi: PB061198.MOV, P9061187.ORF

Fatto. Risposte e pacchetti in diag-downloads/telecomando/.
```

### Cosa ha chiarito

- **Firewall:** nessuna regola necessaria. Basta un datagramma dalla porta locale 40000 a 192.168.0.10:16540 prima di `startliveview`; la prima esecuzione era fallita perché non ne era stato inviato nessuno. Supera la conclusione della seconda esecuzione.
- **Qualità:** il cambio con `switch_cammode mode=play` e poi `mode=rec&lvqty=<qualità>` funziona; il TLV 26 conferma 640×480, 1024×768, 1280×960.
- **Frequenza:** 30 fotogrammi/s a 640×480 e 1024×768 (96 e 95 fotogrammi completi in circa 3,1 s), circa 15 fotogrammi/s a 1280×960 (46 in 3,05 s). La riga «30.00 fotogrammi/s» per 1280×960 è un errore della sonda: conta i timestamp distinti, non i fotogrammi completi.
- **Regime stabile:** 0 salti di sequenza a tutte le qualità (buffer effettivo 8 MB, ricevitore già attivo); arretrato dei primi 0,2 s: 189 / 466 / 288 pacchetti.
- **Dopo il video:** il live continua da solo (1896 pacchetti nei 3 s dopo lo stop), con dimensione di nuovo 640×480; durante la registrazione 640×360. L'«arresto» visto nella seconda esecuzione era una finestra di ascolto troppo corta.
- **Messa a fuoco e scatto con il live acceso:** stesse risposte della seconda esecuzione; getrecview 38 270 byte in 43 ms, getlastjpg 1 563 988 byte in 1034 ms; live durante l'operazione 1595 pacchetti, 76 fotogrammi, 640×480.
- **Decisioni:** qualità predefinita `1024x0768`; nessuna regola del firewall (l'app invia un datagramma prima di ogni `startliveview`); `RemoteView` segue la dimensione del fotogramma dal TLV 26.

## Telecomando con l'app (2026-11-07), prima prova

- **Live assente con porte effimere:** nell'app (porta di ricezione scelta dal sistema) il live non è mai arrivato.
- **Sonda:** con la porta 40000 il live arriva (OK); con porte effimere 0 pacchetti; il momento in cui si invia il datagramma non cambia nulla.
- **Log di blocco di ufw:** la porta di origine dei pacchetti della fotocamera è la porta di ricezione con i due byte scambiati: destinazione 40000 → origine 16540; 35199 → 32649; 40234 → 10909. La porta 16540 funzionava solo perché la sonda ascoltava sempre su 40000.
- **Messa a fuoco:** `touchactiveframe` a 1024×768 = `0153-0115_0716x0537` (coordinate scalate, quindi quelle della messa a fuoco seguono la dimensione del live).
- **Correzione:** il datagramma di apertura va a 192.168.0.10, porta = byte-swap della porta di ricezione (`camera_source_port`).

## Telecomando con l'app (2026-11-07), prova completa

Dopo la correzione della porta del datagramma di apertura (commit `033cbe9`), sessione completa con l'app, avviata con `OLYMPUS_DEBUG=1`, **senza alcuna regola del firewall**:

| Prova | Esito |
|---|---|
| Live | arriva a 1024×768, fluido |
| Messa a fuoco col tocco | 8 clic, tutti `affocus ok`; il riquadro restituito (`afframepoint`, lato 115 a 1024×768) è centrato sul punto cliccato, per esempio clic (471, 416) → riquadro centrato in (469, 415): le coordinate sono quelle del live corrente, nessuna conversione necessaria; il riquadro a schermo diventa verde e il fuoco cade dove si clicca |
| Scatto e revisione | `starttake` ok, revisione da `getrecview` (circa 290 KB) mostrata per 2 s, poi il live riprende |
| Video | `startmovietake liveview=on` e `stopmovietake` ok; il live passa a 16:9 durante la registrazione e il tempo scorre |
| Ritorno a Miniature | `stopliveview`, riproduzione, nuovo elenco: compaiono `PB081200.JPG`, `PB081200.ORF`, `PB081201.MOV` |
| Chiusura dal live | non provata con la fotocamera (l'app è stata chiusa dall'elenco); coperta dai test |

Esito dichiarato dall'utente: tutto funziona.


## Impostazioni dal PC (2026-11-08)

Esito: passa. Referto della prova:

```
Prova impostazioni 2026-11-08 00:31:14
play 200, rec 200
startliveview porta 49679: 200
live: 1574 pacchetti nel primo secondo
  desclist iniziale: HTTP 200, 5463 byte
originali: {'takemode': 'A', 'isospeedvalue': '200', 'focalvalue': '3.5', 'shutspeedvalue': '250', 'expcomp': '0.0', 'wbvalue': '0', 'drivemode': 'lowvib-normal'}
== formato di set_camprop ==
  xml con dichiarazione: HTTP 200, risposta <?xml version="1.0"?><response><drivemode>lowvib-normal</drivemode><cameradrivemode>normal</cameradrivemode></response>, riletto 250 (bersaglio 250)
  -> formato funzionante: xml con dichiarazione
  ISO rimesso a 200: HTTP 200, riletto 200
== modalità ==
takemode P: riletto P
  desclist P: HTTP 200, 5463 byte
  shutspeedvalue: getset, valore 250, 58 valori (livecomp…4000)
  focalvalue: getset, valore 5.6, 40 valori (1.0…91)
  isospeedvalue: getset, valore 200, 24 valori (Auto…25600)
  expcomp: getset, valore 0.0, 31 valori (-5.0…+5.0)
takemode A: riletto A
  desclist A: HTTP 200, 5463 byte
  shutspeedvalue: getset, valore 250, 58 valori (livecomp…4000)
  focalvalue: getset, valore 3.5, 40 valori (1.0…91)
  isospeedvalue: getset, valore 200, 24 valori (Auto…25600)
  expcomp: getset, valore 0.0, 31 valori (-5.0…+5.0)
takemode S: riletto S
  desclist S: HTTP 200, 5436 byte
  shutspeedvalue: getset, valore 640, 55 valori (60"…4000)
  focalvalue: getset, valore 5.6, 40 valori (1.0…91)
  isospeedvalue: getset, valore 200, 24 valori (Auto…25600)
  expcomp: getset, valore 0.0, 31 valori (-5.0…+5.0)
takemode M: riletto M
  desclist M: HTTP 200, 5313 byte
  shutspeedvalue: getset, valore 640, 58 valori (livecomp…4000)
  focalvalue: getset, valore 3.5, 40 valori (1.0…91)
  isospeedvalue: getset, valore 200, 24 valori (Auto…25600)
  expcomp: getset, valore 0.0, 1 valori (0.0…0.0)
== richieste non consentite ==
takemode S: riletto S
  in S, focalvalue=6.3: HTTP 200 <?xml version="1.0"?><response><drivemode>lowvib-normal</drivemode><cameradrivemode>normal</cameradrivemode></response>, riletto 6.3 (prima 5.6)
takemode A: riletto A
  in A, shutspeedvalue=320: HTTP 200 <?xml version="1.0"?><response><drivemode>lowvib-normal</drivemode><cameradrivemode>normal</cameradrivemode></response>, riletto 320 (prima 250)
takemode A: riletto A
  in A, focalvalue=1.0: HTTP 200 <?xml version="1.0"?><response><drivemode>lowvib-normal</drivemode><cameradrivemode>normal</cameradrivemode></response>, riletto 1.0 (prima 3.5)
== bilanciamento e scatto ==
  wbvalue=18: HTTP 200 <?xml version="1.0"?><response><drivemode>lowvib-normal</drivemode><cameradrivemode>normal</cameradrivemode></response>, riletto 18
  drivemode=silent-normal: HTTP 200 <?xml version="1.0"?><response><drivemode>silent-normal</drivemode><cameradrivemode>normal</cameradrivemode></response>, riletto silent-normal
== ripristino ==
  takemode P: riletto P
  P shutspeedvalue=250: HTTP 200, riletto 250
  P focalvalue=5.6: HTTP 200, riletto 5.6
  P expcomp=0.0: HTTP 200, riletto 0.0
  P isospeedvalue=200: HTTP 200, riletto 200
  takemode S: riletto S
  S shutspeedvalue=640: HTTP 200, riletto 640
  S focalvalue=5.6: HTTP 200, riletto 5.6
  S expcomp=0.0: HTTP 200, riletto 0.0
  S isospeedvalue=200: HTTP 200, riletto 200
  takemode M: riletto M
  M shutspeedvalue=640: HTTP 200, riletto 640
  M focalvalue=3.5: HTTP 200, riletto 3.5
  M expcomp=0.0: HTTP 200, riletto 0.0
  M isospeedvalue=200: HTTP 200, riletto 200
  takemode A: riletto A
  A shutspeedvalue=250: HTTP 200, riletto 250
  A focalvalue=3.5: HTTP 200, riletto 3.5
  A expcomp=0.0: HTTP 200, riletto 0.0
  A isospeedvalue=200: HTTP 200, riletto 200
  A wbvalue=0: HTTP 200, riletto 0
  A drivemode=lowvib-normal: HTTP 200, riletto lowvib-normal
ripristino completo
stopliveview 200
ritorno in riproduzione 200
live ricevuto in tutto: 18460 pacchetti
```

## Impostazioni dal PC con l'app (2026-11-08)

Prova con l'app (`OLYMPUS_DEBUG=1`) e due script offline in `diag-downloads/` (ignorati da git).

**Virgolette dei tempi lunghi** (`prova_virgolette.py`, in M): `<value>2"</value>` e `<value>60"</value>` vengono presi e riletti uguali; con `&quot;` la fotocamera capisce `1"` in entrambi i casi. L'app manda le virgolette così come sono. Ripristino di tempo e modalità riuscito.

**Pannello** (riferito dall'utente): righe presenti all'ingresso; il diaframma resta fermo finché arriva il live, poi l'elenco segue l'obiettivo e lo zoom; le righe «automatico» cambiano con la modalità (P, A, S, M, iAuto, ART, Scene); i valori sul monitor della fotocamera coincidono con il pannello; i nomi di bilanciamento, scatto, filtri e varianti sembrano coincidere. Nel registro tutti i comandi `set_camprop` (modalità, tempo da 1/500 a 60", ISO, diaframma da f/4,5 a f/22) rispondono 200 e la rilettura conferma.

**Pose lunghe**: uno scatto a 60" dall'app ha rotto la sessione. La foto c'è sulla scheda, ma l'app aspettava la risposta allo scatto al massimo 15 s, poi il live fermo ha proposto «Riprova» e la richiesta di rientro, in coda dietro lo scatto, ha trovato la fotocamera ancora occupata. Misura con `prova_posa_lunga.py` (M, riduzione rumore `auto` da `1"`):

| Tempo | Risposta a `starttake` | Durante l'attesa |
|---|---|---|
| 2" (dall'app) | circa 2 s | — |
| 8" | 16,8 s | nessuna risposta HTTP, live fermo |
| 60" | 129,0 s | nessuna risposta HTTP, live fermo; Wi-Fi in piedi |

Dopo la risposta `stoptake` e `getrecview` sono immediati e la fotocamera torna normale. Quindi la fotocamera resta occupata per circa due volte la posa (posa più scatto al buio) e qualche secondo.

**Pose lunghe con l'app** (dopo la correzione dell'attesa): 8" e 60" in M dallo stesso pannello. Lo scatto da 8" risponde in circa 17 s con l'anteprima e il live che riparte da solo. Lo scatto da 60" risponde in circa 130 s (attesa massima dell'app 147 s), la foto è sulla scheda e l'anteprima arriva (210 KB), ma dopo il 60" la fotocamera **non riprende il live da sola**: «Live interrotto», e con Riprova si rientra senza problemi. L'anteprima non si vedeva perché il velo di «Live interrotto» le era disegnato sopra. Correzione: l'anteprima resta sopra messaggio e Riprova; dopo uno scatto riuscito col live fermo l'app lo fa ripartire da sola alla fine dell'anteprima.

**Seconda prova delle pose lunghe** (dopo la correzione): 60" in M, risposta in circa 2 min 10 s, anteprima visibile, e circa 2 s dopo (fine dell'anteprima) il live riparte da solo. Modalità di scatto: singolo, silenzioso, raffica H e L con le varianti, tutte confermate; l'autoscatto non compare. Scoperta: **rientrare in rec (`switch_cammode` play→rec) rimette la modalità della ghiera fisica**: dopo la ripartenza la fotocamera era tornata da M (scelta dal PC) ad A (la ghiera). Correzione: dopo un rientro nel live (automatico o con Riprova) l'app rimette la modalità scelta prima; i valori di quella modalità li tiene la fotocamera. All'ingresso nel telecomando invece si segue la ghiera.
