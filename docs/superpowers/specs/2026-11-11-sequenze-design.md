# Telecomando, pezzo E1: sequenze (intervallometro e timelapse) — Design

**Data:** 2026-11-11
**Stato:** approvato in sessione; cancello superato il 2026-11-11, esiti e decisioni nella sezione «Esito della prova»
**Obiettivo:** dal pannello del telecomando, far scattare la fotocamera a intervalli regolari per una durata scelta, e alla fine scaricare i JPEG della sequenza in una cartella dedicata e montarli in un video.

Segue le specifiche del telecomando (`2026-11-06-telecomando-design.md`, pezzi A–C) e delle impostazioni dal PC (`2026-11-08-impostazioni-dal-pc-design.md`, pezzo D), fatti e verificati sul campo. Il pezzo E è diviso in due: **E1, le sequenze** (questa specifica) ed **E2, la posa B** (specifica propria, dopo).

## Ambito

**Dentro:** una sezione «Sequenza» nel pannello del telecomando con intervallo e durata; il regista che fa partire gli scatti; l'avanzamento e l'arresto; a fine sequenza l'uscita dal telecomando, la domanda all'utente, lo scarico dei soli JPEG in una cartella dedicata e il montaggio di un video MP4 con `ffmpeg`.

**Usi da reggere** (scelti in sessione): timelapse di giorno (uno scatto ogni 2–30 s, qualche centinaio di foto); cielo notturno (pose da 15" a 60" una dopo l'altra, per ore); cambiamenti lenti (uno scatto ogni qualche minuto, per molte ore).

**Fuori, di proposito:** la posa B e le pose Live (pezzo E2); l'avvio a un'ora precisa o ritardato; la rampa di esposizione (tramonti); il montaggio dai RAW; lo scarico dei RAW della sequenza (restano sulla scheda, si scaricano dalla griglia); una sequenza che sopravvive alla chiusura della finestra; più sequenze in coda.

## Fatti accertati

Dalle prove sul campo dei pezzi A–D (`docs/field-report-2026-09-24.md`) e da `tests/fixtures/commandlist.xml`:

- La fotocamera non ha comandi di intervallometro: `exec_takemotion` offre `starttake`/`stoptake` e nient'altro per le foto. La sequenza la fa l'app.
- `exec_takemotion` funziona solo con il live acceso (altrimenti errorcode 1004).
- `starttake` risponde quando la fotocamera è di nuovo libera: circa 2 s per una posa da 2", 16,8 s per 8", 129 s per 60" (posa più fotogramma scuro della riduzione rumore). In quel tempo la fotocamera non risponde a nessun'altra richiesta e il live si ferma; il Wi-Fi resta in piedi.
- Dopo una posa da 60" il live non riparte da solo: l'app rientra in rec alla fine dell'anteprima. Il rientro (play→rec) rimette la modalità della ghiera fisica, e l'app rimette quella scelta dal PC.
- `core/camprops.py` stima l'occupazione di uno scatto (`shot_busy_seconds`) e l'attesa massima (`shot_timeout`).
- `get_imglist` restituisce i file nell'ordine della scheda, con nome, dimensione, data e ora; uscendo dal telecomando l'app torna in riproduzione e rilegge l'elenco.
- `set_timeout.cgi` risponde 520: il tempo di spegnimento automatico non si imposta dal PC.
- Sul PC dell'utente ci sono `ffmpeg` (con `libx264`) e `systemd-inhibit`.

**Non noto alla stesura**, e deciso dalla prova (vedi «Esito della prova»): il tempo fra due scatti rapidi; se `get_imglist` risponde in modalità scatto; se il live si può spegnere e riaccendere senza rientrare in rec; se la fotocamera si addormenta negli intervalli lunghi.

## Prova sul campo (cancello)

Script `tools/prova_sequenza.py` (nel repository), che gira **senza Internet** (PC collegato alla fotocamera) e scrive tutto in `diag-downloads/prova_sequenza.txt` (ignorato da git). Non cambia nessuna impostazione; scatta circa 15 foto; alla fine torna in riproduzione, anche dopo un errore. Con un ricevitore UDP che svuota il live (come l'app), in modalità scatto:

1. **Ritmo.** Dieci scatti uno dopo l'altro (`starttake`, `stoptake`, `getrecview`), poi cinque senza `getrecview`. Annota per ognuno i tempi di ogni richiesta e il tempo fra un `starttake` e il successivo, più tempo e modalità di scatto letti prima di cominciare.
2. **Elenco in modalità scatto.** `get_imglist` di `/DCIM` e dell'ultima cartella: stato HTTP, numero di righe, tempo di risposta, e se l'ultimo file elencato è l'ultimo scattato.
3. **Live spento fra gli scatti.** `stopliveview`; uno `starttake` (atteso errorcode 1004); `startliveview` senza play→rec; tempo fino al primo pacchetto del live; uno scatto; rilettura di `takemode` (deve essere quello di prima).
4. **Intervalli lunghi.** Cinque minuti ferma con il live acceso, poi uno scatto; cinque minuti ferma con il live spento (se il punto 3 è riuscito), poi `startliveview` e uno scatto. Durante le attese nessuna richiesta. Annota se la fotocamera risponde ancora e in quanto tempo.

**Cosa decide ogni esito:**

| Punto | Esito | Conseguenza nel progetto |
|---|---|---|
| 1 | tempo fra due scatti rapidi `T` | l'intervallo minimo offerto è il primo valore dell'elenco ≥ `T` + 1 s; se saltare `getrecview` fa guadagnare più di 0,5 s, sotto i 5 s di intervallo l'anteprima non si chiede |
| 2 | l'elenco risponde in rec | all'avvio della sequenza l'app fotografa i nomi dei file; le foto della sequenza sono quelle nuove rispetto a quel momento |
| 2 | non risponde | le foto della sequenza sono quelle nuove rispetto all'elenco letto prima di entrare nel telecomando; la finestra finale lo dice («foto nuove dall'ingresso nel telecomando») |
| 3 | riesce, modalità invariata | con intervallo ≥ 60 s il live si spegne fra gli scatti e si riaccende prima del successivo, con un anticipo pari al tempo misurato più 3 s (minimo 5 s) |
| 3 | non riesce | il live resta acceso per tutta la sequenza; con durata ≥ 1 h il riepilogo avvisa «il live resta acceso: controlla la batteria» |
| 4 | la fotocamera risponde dopo 5 minuti | nessuna richiesta fra gli scatti |
| 4 | non risponde | fra gli scatti il regista manda una lettura leggera (`get_camprop takemode`) ogni 30 s; la prova finale verifica che basti, e se non basta l'intervallo massimo offerto scende a 2 minuti |

Gli esiti si annotano in `docs/field-report-2026-09-24.md` e in questa specifica, in una nuova sezione «Esito della prova» subito dopo questa, prima di scrivere il piano della parte 2.

## Esito della prova

Eseguita il 2026-11-11 (ghiera su A, 1/250, scatto singolo, RAW+JPEG); misure in `docs/field-report-2026-09-24.md`, sezione «Sequenze: prova sul campo».

| Punto | Esito | Decisione |
|---|---|---|
| 1 | ciclo mediano 1,0 s con anteprima (massimo 1,2 s), 0,7 s senza: l'anteprima costa 0,3 s | **intervallo minimo 2 s**; l'anteprima si chiede sempre |
| 2 | l'elenco risponde in rec in 0,14–0,28 s ed è uguale a quello in riproduzione, ma letto subito dopo uno scatto non contiene ancora quella foto | **istantanea dei nomi all'avvio della sequenza**, presa non prima di 3 s dalla fine dell'ultimo scatto (se serve, l'avvio aspetta); le foto della sequenza sono i JPEG nuovi rispetto all'istantanea, letti in riproduzione a fine sequenza. Se l'istantanea fallisce, vale la regola di riserva: foto nuove dall'ingresso nel telecomando, e la finestra finale lo dice |
| 3 | a live spento lo scatto è rifiutato (1004); `startliveview` da solo fa ripartire il live in 0,3 s, alla stessa qualità | **con intervallo ≥ 60 s il live si spegne fra gli scatti** (`stopliveview` alla fine dell'anteprima) e si riaccende **5 s prima** dello scatto successivo (0,3 s misurati più 3 s, minimo 5 s). Se alla riaccensione il live non arriva entro 3 s, vale il rientro in rec esistente, con la modalità rimessa |
| 4 | dopo 5 minuti ferma la fotocamera risponde subito, con il live acceso e con il live spento | **nessuna richiesta fra gli scatti**. Gli intervalli di 10, 15 e 30 minuti restano offerti: la prova finale ne verifica uno da 15 minuti, e se la fotocamera non risponde vale la riga «non risponde» della tabella del cancello |

Due cose restano da verificare nella prova finale con l'app, perché la prova sul campo non cambia impostazioni: che una modalità scelta dal PC resti quella dopo `stopliveview`/`startliveview`, e un'attesa di 15 minuti a live spento.

Ogni scatto produce due file (JPG e ORF) con la fotocamera in RAW+JPEG.

## Comportamento

### Il pannello

Sotto «Impostazioni», la sezione **Sequenza**:

- **Intervallo** e **Durata**: due selettori come quelli delle impostazioni (freccette, rotella, elenco al clic), con valori pronti.
  - Intervallo: 2, 3, 5, 8, 10, 15, 20, 30, 45 s; 1, 2, 3, 5, 10, 15, 30 min (il minimo della prova è 2 s: l'elenco è tutto offerto). Valore iniziale 5 s.
  - Durata: 5, 10, 15, 20, 30, 45 min; 1 h, 1 h 30, 2, 3, 4, 6, 8, 12 h. Valore iniziale 30 min.
- **Riepilogo**, una riga che si aggiorna a ogni cambio di intervallo, durata o impostazioni: «360 scatti · video di 14 s a 25 fps» (i fotogrammi al secondo sono gli ultimi scelti). Se l'occupazione stimata dello scatto supera l'intervallo: «la posa dura più dell'intervallo: scatti uno dopo l'altro, circa 15 in 30 min» (60" con riduzione rumore: un ciclo di 121 s).
- **Avvia sequenza**, attivo quando lo è Scatta.

Intervallo, durata, fotogrammi al secondo e risoluzione si ricordano fra una sessione e l'altra (`QSettings`, chiavi `sequence/…`).

Mentre la sequenza gira la sezione mostra «Scatto 42 di 360 · prossimo fra 3 s», «Mancano 26 min» e il pulsante **Ferma sequenza**. Scatta, Registra, i selettori delle impostazioni e i clic di messa a fuoco sono spenti. Il live resta visibile; dopo ogni scatto l'anteprima copre il live per 2 s, o per metà dell'intervallo se è più breve. Con intervallo di almeno 60 s il live si spegne alla fine dell'anteprima (al suo posto il messaggio «Live in pausa fino al prossimo scatto») e si riaccende 5 s prima dello scatto.

### I tempi

- Il primo scatto parte all'avvio (istante `t0`). Lo scatto `k` è dovuto a `t0 + k × intervallo`.
- Uno scatto dovuto parte solo quando il telecomando è pronto: ingresso confermato, nessuno scatto in corso, nessuna uscita in corso. Se non lo è, parte appena lo diventa.
- All'avvio l'app prende l'istantanea dei nomi dei file; `t0` è l'istante in cui l'istantanea è arrivata (o è fallita).
- Con il live in pausa, il telecomando è pronto solo quando il live è stato riacceso; la riaccensione parte 5 s prima dello scatto dovuto.
- Se all'istante in cui il telecomando torna pronto sono già scaduti più scatti, ne parte **uno solo**; quelli scaduti nel frattempo non si recuperano e non contano come saltati.
- Scaduta la durata (`t0 + durata`) non parte più nessuno scatto; quello in corso finisce. La sequenza è finita quando la durata è scaduta e nessuno scatto è in corso.
- Numero di scatti previsto: `ceil(durata / max(intervallo, occupazione stimata + 1 s))`, dove l'occupazione viene da `shot_busy_seconds` (zero se ignota).

### Arresto ed errori

- **Ferma sequenza:** nessun nuovo scatto; quello in corso finisce; poi la fine sequenza.
- **Scatto fallito** (la fotocamera non ha scattato, o non ha risposto): conta come saltato e la sequenza prosegue.
- **Tre scatti saltati di fila:** la sequenza si ferma con «Sequenza interrotta: la fotocamera non risponde», e segue la fine sequenza con le foto fatte.
- **Telecomando non pronto troppo a lungo senza uno scatto in corso** (rientro nel live che non riesce, «Riprova» a schermo): ogni periodo di `max(intervallo, 30 s)` passato oltre lo scatto dovuto senza che il telecomando torni pronto conta come uno scatto saltato. L'attesa di uno scatto in corso (pose lunghe) non conta mai.
- **Live fermo dopo uno scatto:** vale la ripartenza automatica esistente (rientro in rec alla fine dell'anteprima, modalità rimessa).
- **Uscita dal telecomando o chiusura della finestra con la sequenza attiva:** domanda di conferma («La sequenza si fermerà.»). Uscendo, segue la fine sequenza; chiudendo la finestra, no.
- **Sospensione del PC:** per tutta la sequenza un processo `systemd-inhibit --what=sleep:idle` tiene sveglio il PC; se il comando manca, un avviso nella barra di stato e la sequenza parte lo stesso.

### Fine sequenza

1. L'app esce dal telecomando come farebbe l'utente tornando alle miniature: live spento, fotocamera in riproduzione, elenco riletto.
2. Dall'elenco riletto ricava i **JPEG della sequenza**: quelli nuovi rispetto all'istantanea presa all'avvio (vedi «Esito della prova», punto 2), nell'ordine della scheda.
3. Mostra la finestra di fine sequenza: «Sequenza finita: 358 foto (2 saltate).», la domanda «Scaricarle in una cartella dedicata?», la casella **Monta il video** (accesa), **Risoluzione** (Full HD 1920×1080, 4K 3840×2160) e **Fotogrammi al secondo** (24, 25, 30), i pulsanti **Scarica** e **Non ora**.
   - Nessun JPEG fra i file nuovi (fotocamera in solo RAW): la finestra dice «La fotocamera ha salvato solo RAW: niente da scaricare qui, niente video» e offre solo **Chiudi**.
   - `ffmpeg` assente, o meno di due foto: la casella del video è spenta, con il motivo.
4. **Scarica:** i JPEG vanno in `<destinazione>/Sequenza AAAA-MM-GG HH.MM/` (ora di avvio della sequenza; se la cartella esiste, ` (2)`, ` (3)`…), con i nomi della fotocamera, senza sottocartelle per data. Usa lo scarico esistente (verifica della dimensione, pausa, Riprova). Se la fotocamera nel frattempo si è spenta, lo scarico si ferma con il Riprova di sempre: l'elenco della sequenza resta in memoria finché l'app è aperta.
5. **Video**, a scarico finito: `timelapse.mp4` nella stessa cartella. Avanzamento nella barra di stato («Montaggio del video: 120 di 358»), con Annulla. Se fallisce o viene annullato, il file parziale si cancella, le foto restano e la barra di stato dice perché.
6. **Non ora:** nient'altro; le foto restano sulla scheda e si vedono nella griglia.

### Il video

`ffmpeg`, H.264 (`libx264`, `-crf 18`, `-preset medium`, `-pix_fmt yuv420p`, `-movflags +faststart`), contenitore MP4. Le foto, nell'ordine della sequenza, entrano come collegamenti simbolici numerati (`000001.jpg`…) in una cartella temporanea, lette con `-framerate <fps> -i %06d.jpg`. Filtro: `scale=L:A:force_original_aspect_ratio=increase,crop=L:A` (ritaglio al centro in 16:9). L'uscita si scrive in `timelapse.mp4.part` (`-f mp4`) e si rinomina a montaggio riuscito. L'avanzamento si legge da `-progress pipe:1` (riga `frame=N`).

## Architettura

### Core (nessun import di Qt)

**`core/sequence.py`** (nuovo):
- `INTERVALS`, `DURATIONS` (secondi) e `format_span(seconds)` («5 s», «1 min», «1 h 30»).
- `SequencePlan(interval, duration)` con `expected_shots(busy)`, `due_at(k)` e `summary(busy, fps)` (il testo del riepilogo).
- `SequenceState`: l'automa della sequenza, guidato da un orologio passato da fuori. Metodi `start(now)`, `tick(now, ready) -> bool` (vero se deve partire uno scatto adesso), `shot_done(ok, now)`, `stop()`, e le letture `taken`, `skipped`, `index`, `next_in(now)`, `remaining(now)`, `finished`, `aborted`. Contiene tutte le regole di «I tempi» e di «Arresto ed errori» che non toccano la GUI.
- `sequence_files(before: set[str], after: list[MediaFile]) -> list[MediaFile]`: i JPEG nuovi, nell'ordine della scheda.
- `folder_name(started: datetime, existing: Callable[[str], bool]) -> str`.

**`core/timelapse.py`** (nuovo): `find_ffmpeg()`, `RESOLUTIONS`, `FRAME_RATES`, `link_frames(files, folder)`, `build_command(ffmpeg, frames_dir, fps, size, out)`, `parse_progress(line) -> int | None`.

**`core/power.py`** (nuovo): `SleepInhibitor` con `start() -> bool` e `stop()`; un processo figlio `systemd-inhibit … sleep infinity`, terminato in `stop()` e alla distruzione.

**`core/downloader.py`**: `plan_flat(files, folder) -> list[Job]`, come `plan` ma con destinazione `folder / media.name`.

**`core/protocol.py`**: nessun comando nuovo. `list_media` serve anche in modalità scatto (verificato); `stop_liveview`/`start_liveview` esistono già.

### GUI

**`gui/sequence.py`** (nuovo):
- `SpanStepper(QWidget)`: selettore di un valore fra quelli di un elenco (freccette, rotella, elenco al clic), con gli stessi stili di `PropStepper` (`#propValue`, `#propArrow`) ma senza fotocamera: nessuna pausa, nessuno stato «in attesa», segnale `changed(int)` immediato. `PropStepper` resta com'è.
- `SequenceSection(QWidget)`: due `SpanStepper`, il riepilogo, Avvia/Ferma, le due righe di avanzamento. Segnali `start_requested(interval, duration)`, `stop_requested()`. Metodi `set_summary(text)`, `set_running(bool)`, `set_progress(text, text)`, `set_enabled(bool)`.
- `SequenceController(QObject)`: possiede `SequenceState`, un `QTimer` da 200 ms e il `SleepInhibitor`. Segnali `shoot_requested()`, `progress(str, str)`, `ended(taken, skipped, aborted, started_at)`. Metodi `start(plan, busy)`, `stop()`, `set_ready(bool)`, `shot_done(ok)`, `is_running()`. Non conosce la finestra.
- `SequenceEndDialog(QDialog)`: la finestra di fine sequenza; restituisce scarico sì/no, video sì/no, risoluzione, fotogrammi al secondo.

**`gui/workers.py`**: `TimelapseWorker(QObject)` su un suo thread, con `progress(int, int)`, `done(path)`, `failed(str)`, `cancel()`. `RemoteWorker` riceve tre comandi: `snapshot_names()` (istantanea dei nomi, segnale `names_read(object)`), `pause_live()` e `resume_live(port)` (segnale `live_resumed(bool)`).

**`gui/remote.py`**: `RemotePanel` aggiunge `self.sequence = SequenceSection()`; `_refresh` la spegne quando lo è Scatta, tranne il pulsante Ferma durante una sequenza.

**`gui/main_window.py`**: solo collegamenti. Lo scatto della sequenza passa da un ingresso che non dipende dal pulsante Scatta (spento durante la sequenza) ma dalle stesse condizioni; `on_remote_shot` e `on_remote_failed` avvisano il regista; i cambi di stato del telecomando chiamano `set_ready`; `ended` avvia la fine sequenza (uscita, rilettura, finestra, scarico con `plan_flat`, video). Le conferme di uscita e chiusura. La logica resta nel regista e nel core: `main_window.py` ha già 1471 righe.

## Test

- **Core:** `SequenceState` con un orologio finto: cadenza regolare; scatto più lungo dell'intervallo (uno solo alla ripresa, nessun recupero); fine alla durata con uno scatto in corso; arresto; tre saltati di fila; telecomando non pronto a lungo; conteggi e testi. `expected_shots` e `summary` sui casi del riepilogo. `sequence_files` (solo JPEG, ordine, nessun file vecchio). `folder_name` con cartella esistente. `timelapse`: comando costruito, collegamenti numerati nell'ordine, lettura dell'avanzamento. `SleepInhibitor` con un comando finto, e con il comando assente. `plan_flat`.
- **GUI:** `SequenceSection` (riepilogo, stati, minimo dell'intervallo); `SequenceController` con orologio e timer guidati dal test; finestra di fine sequenza nei tre casi (normale, solo RAW, senza `ffmpeg`); nella finestra principale: comandi spenti durante la sequenza, conferma all'uscita, fine sequenza che esce dal telecomando e mostra la finestra.
- **Dall'inizio alla fine**, contro la fotocamera finta: tre scatti a 0,3 s, elenco con tre JPEG e tre ORF nuovi, scarico nella cartella della sequenza (solo i JPEG), e un video vero di tre fotogrammi con `ffmpeg` (test saltato se `ffmpeg` manca). Con la verifica al contrario: scollegato il segnale di scatto del regista, il test fallisce.
- **Script della prova:** le funzioni pure (letture, riassunto) come per `prova_impostazioni.py`.

## Prova finale con la fotocamera

Con l'app, senza Internet, registro in `diag-downloads/`:
1. Timelapse di giorno: intervallo minimo offerto, 5 minuti; scarico e video in Full HD a 25.
2. Pose lunghe: M, 15", intervallo 5 s, 5 minuti: scatti uno dopo l'altro, riepilogo coerente, live che riparte.
3. Intervallo lungo: 2 minuti per 15 minuti in M (modalità scelta dal PC, ghiera su A): live in pausa fra gli scatti, nessuno scatto saltato, e la modalità resta M dopo ogni riaccensione.
4. Attesa lunga: intervallo di 15 minuti per 30 minuti (tre scatti): la fotocamera risponde ancora dopo 15 minuti a live spento.
5. Ferma a metà; uscita dal telecomando a metà (conferma); «Non ora» alla fine.
6. Uno scatto a mano subito prima di Avvia: quella foto non deve finire fra quelle della sequenza.

L'esito va in `docs/field-report-2026-09-24.md`.

## Documentazione

`README.md`: riga nella tabella «Cosa funziona» (verificata dopo la prova finale), un paragrafo nella sezione Telecomando, `ffmpeg` fra i requisiti facoltativi, i file nuovi nell'albero. `DESIGN.md`: la sezione Sequenza nel pannello del telecomando e la finestra di fine sequenza.
