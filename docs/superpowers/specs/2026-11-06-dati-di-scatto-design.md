# Dati di scatto nel pannello — Design

**Data:** 2026-11-06
**Stato:** approvato in sessione; la fonte dei dati (A o B) si decide con la prova sul campo descritta più avanti
**Obiettivo:** mostrare tempo, diaframma, ISO, focale, obiettivo e gli altri dati di scatto del file selezionato, letti dalla fotocamera via Wi-Fi, prima di scaricarlo.

È la prima di due funzionalità decise insieme; la seconda, il telecomando con visione dal vivo, avrà una specifica sua e una prova sul campo sua.

## Perché

L'anteprima grande permette già di giudicare la composizione di uno scatto senza scaricarlo. Mancano i numeri: a ISO 6400 e 1/60 s la stessa foto si giudica in un altro modo, e scegliere fra due scatti simili spesso si fa sull'obiettivo o sul diaframma. Con i dati di scatto accanto all'anteprima il pannello basta a decidere.

## Ambito

**Dentro:** i dati del solo file selezionato, letti al momento della selezione, mostrati nel pannello di destra.

**Fuori, di proposito:**
- filtri e ordinamento per dati di scatto: richiederebbero la lettura di tutti i 1161 file in anticipo e una cache su disco. Il design non lo impedisce in seguito;
- la sezione proprietaria Olympus (MakerNote), e con essa il preset del bilanciamento del bianco;
- i video: nessuna richiesta, sezione nascosta, salvo che la prova sul campo mostri che `get_exif` restituisce dati utili anche per i MOV.

## Fatti accertati

Dalle foto già scaricate (`P9061098.ORF`, letto con exiv2): la fotocamera scrive **nell'EXIF standard** tutti i campi scelti.

| Campo | Tag | Esempio |
|---|---|---|
| Tempo | `Exif.Photo.ExposureTime` 0x829A | 1/60 s |
| Diaframma | `Exif.Photo.FNumber` 0x829D | F3.5 |
| ISO | `Exif.Photo.ISOSpeedRatings` 0x8827 | 6400 |
| Focale | `Exif.Photo.FocalLength` 0x920A | 14.0 mm |
| Obiettivo | `Exif.Photo.LensModel` 0xA434 | LUMIX G VARIO 14-42/F3.5-5.6 II |
| Compensazione | `Exif.Photo.ExposureBiasValue` 0x9204 | 0 EV |
| Modalità | `Exif.Photo.ExposureProgram` 0x8822 | 4, priorità dei tempi |
| Bilanciamento | `Exif.Photo.WhiteBalance` 0xA403 | 0, Auto |
| Flash | `Exif.Photo.Flash` 0x9209 | bit 0 = scattato |
| Risoluzione | `Exif.Image.ImageWidth` / `ImageLength`, oppure `PixelXDimension` / `PixelYDimension` | 5200 × 3904 |

Il campo standard del bilanciamento distingue solo Auto e Manuale; il preset (Luce diurna, Nuvoloso…) sta nel MakerNote, fuori ambito.

Dalla lista dei comandi della fotocamera: `get_exif.cgi` esiste e prende un solo parametro, `DIR`. **Non è noto cosa restituisca**: lo stabilisce la prova sul campo.

## Fonte dei dati

- **A, consigliata:** `get_exif.cgi?DIR=<percorso>`, una richiesta dedicata.
- **B, ripiego:** l'EXIF contenuto nel JPEG dell'anteprima, se la fotocamera ve lo copia. Nessuna richiesta in più.

**Esito della prova sul campo (2026-11-06):** fonte **A**. `get_exif.cgi` risponde HTTP 200 in 57 ms con 66 560 byte per un ORF; l'intestazione è `IIRO`, la variante ORF dell'intestazione TIFF, e i byte sono i primi 66 560 del file ORF stesso. Per JPG e MOV la prova non è stata possibile, perché la scheda conteneva solo file ORF: restano da verificare. L'anteprima non contiene un blocco Exif, quindi la fonte B è esclusa.

## Architettura

### Core (nessun import di Qt)

**`core/exif.py`, nuovo.**

- `parse_exif(data: bytes) -> ShotInfo | None` accetta tre forme di input:
  - EXIF grezzo che inizia con l'intestazione TIFF (`II*\0` o `MM\0*`);
  - un blocco che inizia con `Exif\0\0` seguito dall'intestazione TIFF;
  - un JPEG intero (`FF D8`): scorre i segmenti fino all'APP1 `Exif\0\0`.

  Accetta come intestazione TIFF anche quelle degli ORF Olympus: `IIRO` e `IIRS` (little-endian) e `MMOR` (big-endian), con la stessa struttura di `II*\0` e `MM\0*`; `get_exif.cgi` restituisce un ORF in questa forma.

  Legge IFD0 e la sotto-IFD EXIF (puntatore 0x8769), nient'altro. Gestisce entrambi gli ordini dei byte. **Non solleva mai eccezioni**: input vuoto, troncato, puntatori fuori dai limiti, IFD che rimandano a se stesse o tipi inattesi producono `None` oppure un `ShotInfo` con i soli campi leggibili. Un `ShotInfo` con tutti i campi a `None` diventa `None`.
- `ShotInfo`, dataclass immutabile, tutti i campi facoltativi: `exposure_time: Fraction | None`, `f_number: Fraction | None`, `iso: int | None`, `focal_length: Fraction | None`, `lens: str | None`, `exposure_bias: Fraction | None`, `exposure_program: int | None`, `white_balance: int | None`, `flash_fired: bool | None`, `width: int | None`, `height: int | None`.
- `format_shot(info: ShotInfo) -> FormattedShot`, funzione pura, in italiano:
  - `headline`: i valori presenti fra tempo, diaframma, ISO e focale, separati da spazi larghi: `1/60 s   f/3,5   ISO 6400   14 mm`;
  - `rows`: lista ordinata di coppie (etichetta, valore) per Obiettivo, Modalità, Compensazione, Bilanciamento, Flash, Risoluzione, **solo per i campi presenti**.

  Regole di formattazione:
  - tempo (convenzione di exiftool): fino a 1/4 di secondo `1/N s`, con N = 1/tempo arrotondato per eccesso a metà (non all'intero pari); sopra 1/4 di secondo la forma decimale con la virgola, `0,3 s`, `0,5 s`, `0,8 s`, `1 s`, `2,5 s`, `30 s`;
  - diaframma: `f/3,5`, `f/8`, una cifra decimale al massimo, virgola italiana;
  - focale: `14 mm`, decimali solo se non nulli; 0 equivale ad assente;
  - compensazione: `0 EV`, `+0,3 EV`, `−0,7 EV` con il vero segno meno U+2212, una cifra decimale;
  - modalità: 1 Manuale, 2 Programma, 3 Priorità dei diaframmi, 4 Priorità dei tempi; altri valori: riga omessa;
  - bilanciamento: 0 Auto, 1 Manuale;
  - flash: `Scattato` o `Non scattato`;
  - risoluzione: `5200 × 3904` con il segno di moltiplicazione U+00D7. Precedenza a `PixelXDimension`/`PixelYDimension` della sotto-IFD EXIF, che descrivono l'immagine effettiva; `ImageWidth`/`ImageLength` di IFD0 solo se mancano.

**`core/protocol.py`.**

- `has_exif() -> bool`: `get_exif` è nella lista dei comandi, stesso schema di `has_screennail()`.
- `exif(camera_path: str) -> bytes`: corpo della risposta di `get_exif.cgi?DIR=<percorso>`, tramite `_get_ok`.

Se vince la fonte B, `exif()` e `has_exif()` non vengono aggiunti e il worker passa a `parse_exif` i byte dell'anteprima.

### GUI

**`PreviewWorker`** (`gui/workers.py`):

- nuovo segnale `shot_ready = pyqtSignal(str, object)`, con il percorso e un `ShotInfo` oppure `None`;
- in `request()`, dopo la connessione pigra già presente e prima dell'anteprima: se il file non è un video e la fotocamera dichiara `get_exif`, chiede i dati, li legge con `parse_exif` ed emette `shot_ready`. Con la fonte B l'emissione avviene dopo l'anteprima, dagli stessi byte;
- la lettura dei dati e quella dell'anteprima hanno ciascuna il proprio `try`: il fallimento della prima emette `shot_ready(percorso, None)` e l'anteprima viene chiesta comunque;
- tutto resta dentro blocchi `try`: un'eccezione che sfugge da uno slot invocato in coda fa abortire il processo;
- con il worker sospeso (download in corso) non parte nessuna delle due richieste, come oggi.

Lo stesso worker invece di uno nuovo: le richieste alla fotocamera restano in fila una alla volta, che è ciò che il suo server sopporta meglio, e la sospensione vale per entrambe senza codice aggiunto.

**Finestra** (`gui/main_window.py`):

- `on_shot_ready(percorso, info)`: scartato se `percorso` non è più il file selezionato, come per l'anteprima;
- una nuova sezione nel pannello, `SectionLabel("Scatto")`, **sotto "Dettagli"**: i dettagli sono disponibili subito, i dati di scatto arrivano dopo, e aggiunti in fondo non spostano nulla;
- contenuto: la riga di testa in stile valore con peso 600, poi una griglia etichetta/valore costruita come quella dei dettagli. Le righe di campi assenti non vengono mostrate;
- la sezione compare con la dissolvenza esistente, da opacità 0,35, 180 ms, OutCubic;
- casi:
  - selezione multipla, nessuna selezione, video, download in corso: sezione nascosta;
  - in attesa della risposta: sezione nascosta (l'attesa è breve, un indicatore lampeggerebbe e basta);
  - `info` è `None`: sezione visibile con la sola riga attenuata «Dati di scatto non disponibili».

Altezza: anteprima circa 250 px, dettagli circa 130 px, scatto circa 170 px; il totale sta nei circa 660 px disponibili su uno schermo alto 768.

## Prova sul campo

Prima del lettore, `diag.py` riceve un passaggio `probe_exif(files)`, eseguito dopo quelli esistenti:

1. `get_exif.cgi` su un JPG, un ORF e un MOV presi dall'elenco: stato HTTP, `Content-Type`, dimensione, tempo di risposta, primi 16 byte in esadecimale;
2. un'anteprima (`get_screennail`) di un ORF: contiene un APP1 `Exif\0\0`? Se sì, dimensione del blocco;
3. le risposte grezze salvate in `tests/fixtures/exif/`: `jpg.bin`, `orf.bin`, `mov.bin`, `screennail_orf.jpg`.

**Dati personali.** L'EXIF contiene `BodySerialNumber` (0xA431) e può contenere altri seriali, nel MakerNote o nel numero di serie dell'obiettivo (0xA435). Prima di qualunque commit:
- i seriali nei file salvati vengono sovrascritti **sul posto**, a parità di lunghezza, con un valore fittizio, così offset e puntatori restano validi;
- i file di `tests/fixtures/` che `diag.py` riscrive con i seriali veri (`caminfo.xml` e gli altri) vengono riportati alla versione anonimizzata già in git;
- prima del commit, una ricerca dei seriali veri su tutto ciò che è in stage deve risultare vuota.

L'esito della prova (fonte scelta, tempi, formato) viene aggiunto a questa specifica e al resoconto sul campo.

## Test

Tutti senza fotocamera collegata.

- **Lettore (`tests/test_exif.py`):** i byte veri della prova, anonimizzati, con i valori attesi presi da exiv2. In più, input costruiti: ordine Motorola e Intel, campo mancante, puntatore fuori dai limiti, IFD ciclica, input vuoto, JPEG senza APP1, blocco `Exif\0\0`, intestazione TIFF nuda.
- **Formattazione:** `1/4000 s`, `1/60 s`, `2,5 s`, `30 s`; `f/1,8`, `f/8`; `0 EV`, `+0,3 EV`, `−0,7 EV`; focale 0 come assente; modalità sconosciuta omessa; `ShotInfo` con un solo campo.
- **Protocollo:** `has_exif()` contro la lista dei comandi vera; `exif()` contro il server di prova.
- **Worker:** ordine delle richieste (dati prima dell'anteprima, con la fonte A); fallimento dei dati con anteprima comunque richiesta; fallimento dell'anteprima con dati comunque emessi; nessuna richiesta di dati per un MOV; nessuna richiesta durante la sospensione; nessuna eccezione che esca dallo slot.
- **Finestra:** sezione nascosta con selezione multipla, video e download in corso; risposta di un file non più selezionato scartata; righe assenti non mostrate; riga «non disponibili» con `info` nullo.
- **Dall'inizio alla fine:** server HTTP di prova che restituisce l'EXIF vero, client vero, finestra vera; la sezione compare con i valori attesi. Il test va verificato **fallire senza la modifica**, come quello introdotto dopo la regressione delle miniature.

## Documentazione

README: la riga dei dati di scatto nella tabella «Cosa funziona», `exif.py` nello schema della struttura. DESIGN.md: la sezione Scatto nella descrizione dell'ispettore.
