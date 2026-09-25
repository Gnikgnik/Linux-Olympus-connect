# Referto di verifica sul campo — 2026-09-24

Task 0 del piano di implementazione. Fotocamera **Olympus OM-D E-M10 Mark IV**, firmware OI.Share `version 4.50` / `oitrackversion 3.10`, seriale `BJGA85301`, SSID dell'access point `E-M10MKIV-P-BJGA85301`.

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
2. **`nmcli -t -f ACTIVE,SSID device wifi` riportava `no:` per tutte le reti** pur essendo connessi a `FASTWEB-6J936A`. Sostituito con `nmcli -t -f GENERAL.CONNECTION,GENERAL.STATE device show <dev>`, che restituisce il nome del profilo attivo e lo stato `100 (connected)`.

## Referto grezzo — passata 1

```
Olympus Connect — diagnostica (2026-09-24 21:28:17)

== Scala diagnostica ==
  [OK  ] dispositivo Wi-Fi presente — wlan0
  [OK  ] rete Wi-Fi attiva — wlan0→E-M10MKIV-P-BJGA85301
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
