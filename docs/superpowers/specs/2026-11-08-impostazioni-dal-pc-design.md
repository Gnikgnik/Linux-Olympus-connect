# Telecomando, pezzo D: impostazioni dal PC — Design

**Data:** 2026-11-08
**Stato:** approvato in sessione; il formato del comando di modifica e le regole per modalità si fissano con la prova sul campo descritta più avanti, che fa da cancello
**Obiettivo:** dal pannello del telecomando, cambiare modalità, tempo, diaframma, ISO, compensazione, bilanciamento del bianco, modalità di scatto, filtri artistici e scene della fotocamera, vedendo l'effetto nel live.

Segue la specifica del telecomando (`2026-11-06-telecomando-design.md`, pezzi A–C, fatti e verificati sul campo il 2026-11-07). Il pezzo E (automatismi) avrà una specifica sua.

## Ambito

**Dentro:** le impostazioni che la fotocamera dichiara modificabili (`attribute` = `getset`) dei gruppi scelti in sessione: esposizione (`takemode`, `shutspeedvalue`, `focalvalue`, `isospeedvalue`, `expcomp`), bilanciamento (`wbvalue`), modalità di scatto (`drivemode`), creativi (`artfilter`, `ArtEffectType<Filtro>`, `SceneSub`).

**Fuori, di proposito:** l'esposizione dei video (`exposemovie`) e qualunque modifica durante una registrazione; `colorphase` (colore del filtro Partial Color); le proprietà in sola lettura (`touchactiveframe`, `lowvibtime`, `noisereduction`, …); qualunque impostazione della fotocamera fuori da `get_camprop`/`set_camprop`.

## Fatti accertati

Dall'elenco `get_camprop.cgi?com=desc&propname=desclist` registrato il 2026-11-06 (`tests/fixtures/remote/camprop_desclist.xml`, ghiera su S, obiettivo LUMIX G VARIO 12-32): ogni `<desc>` ha `propname`, `attribute` (`get` o `getset`), `value` ed `enum` (valori ammessi separati da spazi).

| Proprietà | `getset` | Valori dichiarati |
|---|---|---|
| `takemode` | sì | `iAuto P A Scene2 S M ART movie` |
| `shutspeedvalue` | sì | `60" … 1"` (secondi, con le virgolette) e `1.3 … 4000` (denominatori: 250 = 1/250 s) |
| `focalvalue` | sì | `1.0 … 91`: l'intera scala, **non** quella dell'obiettivo montato (f/3,5–22 per il 12-32) |
| `isospeedvalue` | sì | `Auto Low 200 … 25600` |
| `expcomp` | sì | `-5.0 … +5.0` a passi di 0,3/0,4 |
| `wbvalue` | sì | `0 18 16 17 20 35 23 256 257 258 259 512` |
| `drivemode` | sì | `normal lowvib-normal silent-normal continuous-H silent-continuous-H continuous-L lowvib-continuous-L silent-continuous-L lowvib-selftimer customselftimer lowvib-customselftimer silent-customselftimer` |
| `artfilter` | sì | `popart fantasic_focus daydream light_tone rough_monochrome toy_photo miniature cross_process gentle_sepia dramatic_tone ligne_clair pastel vintage partcolor bleach_bypass placame` |
| `ArtEffectType<Filtro>` | sì | `Type1 Type2` (alcuni anche `Type3`) |
| `SceneSub` | sì | `Portrait ePortrait LandscapeAndPerson NightViewPerson NightView NightBlastOffFirework NightLiveComposit Landscape Sunset BeachAndSnow HDRShooting Candle SilentShooting` |

La prova del 2026-11-08 (P, A, S, M) ha mostrato che la fotocamera tiene valori separati per ogni modalità, che in P, A, M l'elenco del tempo comincia con `livecomp livetime livebulb` (58 valori; 55 in S) e che in M `expcomp` ha il solo valore `0.0`. La scala del diaframma (`1.0 … 91`) non è quella dell'obiettivo e i valori fuori obiettivo (per esempio `focalvalue=1.0` col 12-32) vengono accettati senza errore: la parte 2 limita il diaframma alla gamma dell'obiettivo, che il live dichiara (vedi sotto).

Dalla lista dei comandi: `set_camprop.cgi` usa **POST** con `com=set` e `propname`; il corpo non è dichiarato. Il formato del protocollo OPC, `<?xml version="1.0"?><set><value>VALORE</value></set>`, è stato **verificato** dalla prova del 2026-11-08 (vedi «Cancello»).

**Limiti dell'obiettivo nel live (prova dello zoom del 2026-11-08, LUMIX G VARIO 12-32 f/3,5–5,6).** Nell'estensione RTP del primo pacchetto di ogni fotogramma (stesso formato a TLV del pezzo C):
- **TLV 9** = tre uint32 big endian, in decimi di f-stop: `[diaframma più chiuso, diaframma più aperto alla focale attuale, idem]`. Misurato: `[220, 35, 35]` a 12 mm, `[220, 46, 46]` a 20 mm, `[220, 56, 56]` a 32 mm: f/22 e f/3,5 → f/5,6, e il secondo valore segue lo zoom in tempo reale;
- **TLV 18** = tre uint32, ciascuno due uint16 (alto, basso): la seconda parola è `(focale attuale, focale massima)` in mm (`(12, 32)` … `(32, 32)`); la prima vale `(8, 12)`, con 12 = focale minima.
Il selettore del diaframma usa quindi solo i valori dell'elenco compresi fra `TLV9[1]/10` e `TLV9[0]/10` (tolleranza 0,05), aggiornati mentre si zooma. **Finché i limiti non sono noti** (nessun fotogramma del live ancora, o un live che non li dichiara) la riga del diaframma offre il **solo valore confermato**: niente passi, e l'elenco al clic contiene solo quello. Ha l'aspetto di una riga normale, non «automatico». Appena arrivano i limiti vale la gamma dell'obiettivo; se tornano ignoti, di nuovo il solo valore attuale. Motivo: la fotocamera accetta senza errore anche `f/1,0` su un obiettivo f/3,5.

Dal pezzo C: i comandi della modalità scatto si mandano con il live acceso; la fotocamera risponde agli errori con HTTP 520 e `<errorcode>`.

## Prova sul campo (cancello)

Script `tools/prova_impostazioni.py` (nel repository), che gira **senza Internet** (PC collegato alla fotocamera) e scrive tutto in `diag-downloads/prova_impostazioni.txt` (ignorato da git), con gli elenchi grezzi in `diag-downloads/impostazioni/`. Con un ricevitore UDP che svuota il live (datagramma di apertura alla porta con i byte scambiati, come l'app), in modalità scatto e con il live acceso:

1. legge `desclist` e salva i valori originali di ogni proprietà che toccherà;
2. **formato del comando**: porta `isospeedvalue` a un altro valore ammesso con `POST set_camprop.cgi?com=set&propname=isospeedvalue` e corpo `<?xml version="1.0"?><set><value>…</value></set>`, poi lo rilegge con `get`; se non cambia, prova le varianti `<set><value>…</value></set>` senza dichiarazione, `value=` come parametro della query, e `Content-Type: text/xml`; annota stato, corpo della risposta e quale variante funziona;
3. per `P`, `A`, `S`, `M`: imposta `takemode`, rilegge `desclist` e annota per `shutspeedvalue`, `focalvalue`, `isospeedvalue`, `expcomp`: `attribute`, `value` ed `enum` (lunghezza e estremi);
4. in `S`, prova a impostare `focalvalue`; in `A`, `shutspeedvalue`; con l'obiettivo montato, prova `focalvalue=1.0` in `A`: annota stato, `errorcode` e il valore riletto (rifiuto, adattamento o errore);
5. imposta e rilegge `wbvalue` (un valore diverso) e `drivemode` (un valore diverso, mai l'autoscatto), annotando la risposta;
6. in `finally`: rimette **ogni** proprietà toccata al valore originale, ferma il live, torna in riproduzione, e annota l'esito di ogni ripristino.

Non scatta e non registra. La ghiera fisica non viene toccata.

**Cancello: passa (prova sul campo del 2026-11-08).** Formato funzionante: `POST set_camprop.cgi?com=set&propname=<nome>` con corpo `<?xml version="1.0"?><set><value>VALORE</value></set>` (nessun Content-Type necessario), per esempio `POST set_camprop.cgi?com=set&propname=isospeedvalue` con `<?xml version="1.0"?><set><value>250</value></set>`. Risposta HTTP 200 con corpo `<?xml version="1.0"?><response><drivemode>…</drivemode><cameradrivemode>…</cameradrivemode></response>`, uguale qualunque proprietà si imposti; l'effetto si verifica rileggendo la proprietà (ISO 200 → 250 → 200). Nessuna richiesta è stata rifiutata: `focalvalue` in S, `shutspeedvalue` in A e `focalvalue=1.0` in A (obiettivo LUMIX G VARIO 12-32 f/3,5–5,6) hanno dato HTTP 200 e il valore riletto era quello impostato; quindi nessun `errorcode` da gestire per questi casi. Anche `wbvalue=18` e `drivemode=silent-normal` sono stati accettati e riletti. Ripristino completo di tutti i valori originali. Dettagli nel resoconto sul campo.

## Architettura

### Core (nessun import di Qt)

**`core/camprops.py`, nuovo.**

- `CameraProp` (dataclass frozen): `name: str`, `value: str`, `values: tuple[str, ...]`, `settable: bool`.
- `parse_desclist(body: bytes) -> dict[str, CameraProp]`: legge ogni `<desc>`; `settable` = `"set" in attribute`; un `desc` malformato si salta; corpo illeggibile → dizionario vuoto. Non solleva.
- `format_value(name: str, value: str) -> str`, in italiano:
  - `shutspeedvalue`: `60"` → `60 s`, `1.3"` → `1,3 s`, `250` → `1/250`, `1.3` → `1/1,3`;
    in P, A, M l'elenco comincia con `livecomp`, `livetime`, `livebulb`, che si mostrano «Live Composite», «Live Time», «Live Bulb»;
  - `focalvalue`: `5.6` → `f/5,6`, `10` → `f/10`;
  - `isospeedvalue`: `Auto` → `Auto`, `Low` → `Low`, numeri invariati;
  - `expcomp`: `0.0` → `0 EV`, `+0.3` → `+0,3 EV`, `-0.7` → `−0,7 EV` (segno meno U+2212);
  - `takemode`: `iAuto` → `iAuto`, `Scene2` → `Scene`, `ART` → `ART`, `movie` → `Video`, gli altri invariati;
  - `wbvalue`: `0` Auto, `18` Luce diurna, `16` Ombra, `17` Nuvoloso, `20` Tungsteno, `35` Fluorescente, `23` Subacqueo, `256`–`259` Personalizzato 1–4, `512` Temperatura personalizzata; i nomi vanno confermati nella prova finale guardando il monitor della fotocamera;
  - `drivemode`: `normal` Singolo, `lowvib-normal` Singolo anti-vibrazione, `silent-normal` Singolo silenzioso, `continuous-H` Raffica H, `silent-continuous-H` Raffica H silenziosa, `continuous-L` Raffica L, `lowvib-continuous-L` Raffica L anti-vibrazione, `silent-continuous-L` Raffica L silenziosa, `lowvib-selftimer` Autoscatto anti-vibrazione, `customselftimer` Autoscatto personalizzato, `lowvib-customselftimer` Autoscatto pers. anti-vibrazione, `silent-customselftimer` Autoscatto pers. silenzioso;
  - `artfilter`: Pop Art, Soft Focus (`fantasic_focus`), Pale & Light Color (`daydream`), Light Tone, Grainy Film (`rough_monochrome`), Pin Hole (`toy_photo`), Diorama (`miniature`), Cross Process, Gentle Sepia, Dramatic Tone, Key Line (`ligne_clair`), Watercolor (`pastel`), Vintage, Partial Color (`partcolor`), Bleach Bypass, Instant Film (`placame`): i nomi della fotocamera, confermati nella prova finale;
  - `ArtEffectType*`: `Type1` → I, `Type2` → II, `Type3` → III;
  - `SceneSub`: Ritratto, e-Ritratto, Paesaggio e ritratto, Ritratto notturno, Notturno, Fuochi d'artificio, Live Composite notturno, Paesaggio, Tramonto, Spiaggia e neve, HDR, Lume di candela, Silenzioso;
  - un valore senza traduzione si mostra così com'è.
- `visible_props(props: dict[str, CameraProp]) -> list[str]`: i nomi delle righe da mostrare, in quest'ordine: `takemode`, `shutspeedvalue`, `focalvalue`, `isospeedvalue`, `expcomp`, `wbvalue`, `drivemode`; poi `artfilter` e `ArtEffectType<filtro corrente>` solo se `takemode` è `ART`; `SceneSub` solo se è `Scene2`. Solo le proprietà presenti nell'elenco.
- `is_automatic(name: str, props) -> bool`: le righe che la modalità attuale lascia alla fotocamera; regola verificata dalla prova, per modalità, perché l'`attribute` dell'elenco non la distingue (in P, A, S, M tempo, diaframma, ISO e compensazione sono tutti `getset` e la fotocamera accetta ogni richiesta): in `iAuto` tutte quelle di esposizione tranne `takemode`; in `P` tempo e diaframma; in `A` il tempo; in `S` il diaframma; in `M` nessuna; più ogni proprietà non `settable`; più ogni proprietà il cui `enum` ha al massimo un valore (bloccata: in M `expcomp` dichiara il solo `0.0`).
- `step(prop: CameraProp, current: str, delta: int) -> str`: il valore a `delta` passi nell'elenco dichiarato, fermo agli estremi (niente giro).
- `HIDDEN` / `is_hidden(name, value)`: i valori per cui il percorso di scatto non è stato costruito e che la prova non ha mai usato apposta: in `shutspeedvalue` `livecomp`, `livetime`, `livebulb`; in `drivemode` ogni valore che contiene `selftimer`. Non compaiono nell'elenco al clic e i passi li saltano. Se la fotocamera è già su uno di questi (scelto sul corpo), la riga lo mostra come valore attuale (formattato) e il primo passo porta al valore selezionabile più vicino. I tempi lunghi come `60"` restano selezionabili (da verificare sul campo).

**`core/protocol.py`.**

- `camera_props() -> dict[str, CameraProp]`: `get_camprop.cgi?com=desc&propname=desclist`, con `parse_desclist`.
- `set_prop(name: str, value: str) -> None`: `POST set_camprop.cgi?com=set&propname=<name>` con il corpo del formato verificato dalla prova; un HTTP diverso da 200 diventa `ProtocolError` con `code` (come gli altri comandi). Le virgolette dei tempi lunghi partono così come sono (`<value>60"</value>`: XML valido nel testo di un elemento, e il corpo verificato non conteneva mai entità); solo `&`, `<`, `>` diventerebbero entità, perché il corpo resti ben formato. Quale forma accetti la fotocamera per `60"` è da verificare sul campo.

### GUI

**`RemoteWorker`** (`gui/workers.py`): due slot nuovi, in fila con gli altri comandi:
- `read_props()` → emette `props_read(object)` (il dizionario) oppure `props_failed(str)`;
- `set_prop(str, str)` → `set_prop` sul client, poi rilegge sempre l'elenco ed emette `props_read`; se la modifica fallisce emette `failed(messaggio)` e rilegge comunque, così il pannello torna al valore vero.
- Dopo `entered`, la finestra chiede `read_props`.

**`gui/props.py`, nuovo.**
- `PropStepper(QWidget)`: etichetta a sinistra; a destra `‹ valore ›`. Le frecce e la rotella sopra il valore spostano di un passo nell'elenco dichiarato; sulla riga della **modalità** la rotella non fa nulla (scorrendo il pannello si cambierebbe modalità per sbaglio) e passa al pannello che scorre: solo frecce ed elenco; un clic sul valore apre un `QMenu` con tutti i valori formattati. Mentre il valore mostrato è diverso da quello confermato, è attenuato. Dopo **250 ms** dall'ultimo passo emette `changed(name, value)` con il solo valore finale. Stato `automatic`: valore attenuato, scritta «automatico», frecce nascoste, nessuna interazione. `set_confirmed(value)` riallinea al valore della fotocamera.
- `PropsSection(QWidget)`: intestazione «Impostazioni» (`SectionLabel`); costruisce e aggiorna una riga per ogni nome di `visible_props`, con l'etichetta italiana (Modalità, Tempo, Diaframma, ISO, Compensazione, Bilanciamento, Scatto, Filtro, Variante, Scena); `set_props(props)` aggiunge, toglie e aggiorna le righe senza ricostruire quelle che restano; `set_enabled(bool)`; segnale `prop_changed(name, value)`; quando parte un cambio di modalità le altre righe si spengono (i loro comandi in attesa cadono) fino alla rilettura, perché la fotocamera tiene i valori per modalità; un valore in attesa che il nuovo elenco o i nuovi limiti non ammettono cade; le righe tolte non inviano più nulla; messaggio «Impostazioni non disponibili» quando la lettura fallisce.

**`RemotePanel`** (`gui/remote.py`): la sezione «Fotocamera» con la modalità in sola lettura è sostituita da `PropsSection`, sotto «Scatto».

**`MainWindow`**: segnali `remote_read_props()` e `remote_set_prop(str, str)` verso il `RemoteWorker` (in coda, connessi e scollegati con gli altri della sessione); `on_remote_entered` chiede la lettura; `prop_changed` → `remote_set_prop`; `props_read` → `PropsSection.set_props`; i selettori sono spenti finché non c'è `entered`, durante uno scatto, durante la registrazione e durante l'uscita.

## Pose lunghe

Misurato sul campo il 2026-11-08 (sonda offline, modalità M, riduzione rumore `noisereduction=auto`, soglia `NoiseReductionExposureTime=1"`): con 8" `starttake` risponde dopo 16,8 s, con 60" dopo 129,0 s, con 2" dopo circa 2 s. Oltre la soglia la fotocamera aggiunge un fotogramma scuro lungo quanto la posa, quindi resta occupata circa il doppio dell'esposizione più qualche secondo. In quel tempo non risponde a nessuna altra richiesta HTTP e il live non manda pacchetti; il Wi-Fi resta su. Quando `starttake` torna, `stoptake` risponde subito, `getrecview` funziona (circa 0,1 s) e il live riparte. Prima di questo lavoro l'attesa fissa di 15 s scadeva con 60", la finestra mostrava «Live interrotto» con Riprova, e un Riprova accodava `enter_rec_mode` dietro lo scatto: la sessione si rompeva.

- **`camprops`:** `exposure_seconds` legge i tempi (`60"` e `2.5"` secondi, `1.3` e `250` frazioni; None per le pose Live e i valori illeggibili); `shot_busy_seconds` stima l'occupazione quando il tempo non è automatico nella modalità, raddoppiandola se la riduzione rumore non è `off` e la posa arriva alla soglia (soglia assente: 1 s; `noisereduction` assente: attiva); `shot_timeout` è l'occupazione più il 10 % e 15 s (60" con riduzione rumore: 147 s contro 129 s misurati), o 150 s senza stima (un 60" scelto dalla fotocamera più il fotogramma scuro, più margine).
- **Protocollo:** `take_picture(read_timeout)` aspetta `starttake` per quel tempo; `stoptake` segue sempre, anche dopo uno `starttake` fallito, e in quel caso un suo errore non copre l'originale.
- **`RemoteWorker`:** ricorda l'ultimo elenco letto; prima di `starttake` emette `exposing(secondi)` se l'occupazione stimata è di almeno 2 s, e passa `shot_timeout` allo scatto.
- **Finestra:** durante uno scatto il live fermo non mostra Riprova e Riprova non fa nulla; con `exposing` il live mostra «Posa in corso: ancora circa N s», aggiornato ogni secondo, poi «Quasi finito…» (anche nella barra di stato). A scatto finito, riuscito o no, se il live è ancora fermo compare il solito «Live interrotto.» con Riprova; se è ripartito (`resumed`) il conto alla rovescia sparisce. Il conto si ferma anche uscendo dal telecomando e chiudendo.

Limite noto: la stima viene dall'ultimo elenco letto (all'ingresso e dopo ogni cambio dal PC). Un tempo cambiato sul corpo della fotocamera non è visto finché l'elenco non si rilegge.

## Errori

- valore rifiutato (HTTP 520, `errorcode`): il selettore torna al valore confermato (la rilettura lo garantisce) e la barra di stato mostra il messaggio di `describe_error`;
- lettura dell'elenco fallita, o elenco illeggibile (HTTP 200 con un corpo da cui non esce nessuna proprietà): selettori spenti con «Impostazioni non disponibili»; scatto e video continuano;
- valore sconosciuto: mostrato grezzo;
- nessuna eccezione esce da worker o slot.

## Test

Tutti senza fotocamera.

- **`tests/test_camprops.py`:** `parse_desclist` sull'elenco vero e su quelli registrati dalla prova per P, A, S, M (controllati per i seriali); `format_value` un caso per tipo, tempi in secondi e in denominatori, segno meno, ogni bilanciamento, ogni modalità di scatto, filtri, varianti, scene, valore sconosciuto; `visible_props` e `is_automatic` per ogni modalità registrata; `step` agli estremi.
- **Protocollo:** `camera_props` sull'elenco vero; `set_prop` invia il metodo, la query e il corpo verificati (requests_mock); un 520 con `errorcode` diventa `ProtocolError` con il codice.
- **`RemoteWorker`:** `set_prop` rilegge sempre, anche dopo un rifiuto; nessuna eccezione esce.
- **`PropStepper`/`PropsSection`:** frecce, rotella, elenco al clic, un solo `changed` con il valore finale dopo 250 ms, ritorno al confermato, righe automatiche intoccabili, righe aggiunte e tolte al cambio di modalità.
- **Finestra:** entrando si leggono le impostazioni; un cambio arriva al worker; durante il video i selettori sono spenti.
- **Dall'inizio alla fine:** la fotocamera finta del test del telecomando accetta `POST set_camprop` e risponde con l'elenco aggiornato; la finestra vera mostra il nuovo valore. Il test va verificato **fallire senza la modifica**.

## Prova finale con la fotocamera

Con l'app (`OLYMPUS_DEBUG=1`, uscita registrata in un file): cambiare modalità, tempo, ISO, compensazione e bilanciamento dal PC e verificarli sul monitor della fotocamera e nel live; controllare i nomi di bilanciamento, filtri e scene contro quelli mostrati dalla fotocamera; uscire dal telecomando e verificare se la fotocamera torna a seguire la ghiera fisica.

## Documentazione

README: la riga nella tabella «Cosa funziona», il paragrafo nel «Telecomando», il file nuovo nello schema. DESIGN.md: la sezione Impostazioni del pannello e il selettore. Resoconto sul campo: gli esiti delle due prove.
