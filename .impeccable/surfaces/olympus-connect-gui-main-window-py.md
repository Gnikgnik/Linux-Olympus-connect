---
version: 1
slug: "olympus-connect-gui-main-window-py"
primary_target: "olympus_connect/gui/main_window.py"
related_targets: ["olympus_connect/gui/grid.py","olympus_connect/gui/workers.py"]
---

## Scope

Finestra principale di Olympus Connect (`olympus_connect/gui/`). Modalità: **Operate** — l'utente deve portare a termine un lavoro, scegliere e scaricare file dalla fotocamera. Non è una vetrina.

Pubblico: il fotografo dell'utente primario in PRODUCT.md, a casa, la sera, con 1141 RAW sulla scheda e una sessione dedicata davanti. Schermo di riferimento **1366×768**: l'altezza è il vincolo dominante.

Da non toccare: il core non importa Qt, i download restano seriali, nessuna cancellazione, la password non viene memorizzata. Le due viste (miniature, elenco dettagliato), i filtri e la destinazione per data restano funzioni esistenti da preservare.

## Direction contract

**THESIS.** Una camera oscura digitale: lo schermo è scuro perché le foto devono essere l'unica cosa luminosa, e ogni pannello si ritira quando non serve. Rifiuta l'arrangiamento di default della categoria — una barra strumenti sopra una griglia nuda con una barra di stato sotto — perché tratta 1141 file come una lista invece che come un archivio da esplorare.

**OWN-WORLD.** Il mondo è Lightroom, fissato dal brief, e presta esattamente quattro cose: palette, tipografia, densità, una mossa. Palette: tre livelli di neutro freddo (`#1b1e21` shell, `#232629` pannelli, `#2b2f33` superfici attive) con le miniature come unica fonte di luce; ciano `#4db8d4` solo per selezione, azione primaria e stato attivo; ambra e rosso solo dove portano significato. Tipografia: una sola famiglia di sistema, scala fissa 11/12/13/16 px, rapporto stretto, etichette in maiuscoletto spaziato a 10 px. Densità: righe da 24 px, padding da 8/12 px, nessuno spazio decorativo. Non presta né layout né controlli: i controlli restano quelli standard di Qt.

**STORY.** L'utente apre, vede subito se la fotocamera risponde e quanto c'è sulla scheda, capisce in un colpo d'occhio quanto è già suo e quanto manca, sceglie guardando le foto invece dei nomi, avvia e sa quando avrà finito.

**FIRST VIEWPORT.** Tre zone orizzontali sopra una barra di stato. A sinistra un rail da 232 px: stato della connessione con il modello della fotocamera, conteggi per tipo come righe-filtro cliccabili con il peso in GB, destinazione. Al centro la griglia di miniature o l'elenco, con il selettore di vista e l'azione primaria in una barra sottile sopra. A destra un pannello da 300 px: anteprima grande del file selezionato più i suoi dati. Entrambi i laterali si richiudono a 36 px con un click. In basso, su una riga sola: file in corso, avanzamento, velocità, tempo stimato. L'azione primaria «Scarica selezionati» sta in alto al centro, in ciano pieno, unico elemento saturo a riposo.

**FORM.** Direzione fissata dal brief (stile Lightroom): batte il sorteggio, quindi nessun concept-seed. Prima in lista perché è l'unica che il brief ammette. seed: none (direzione fissata dal brief, nessun sorteggio)

**FINISH.** unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Mossa caratteristica

L'anteprima su `get_screennail`: la fotocamera genera un JPEG da circa 1 MB anche per i RAW, quindi si può giudicare uno scatto **prima** di scaricarlo. È ciò che distingue questo da un elenco di file, e nessuna miniatura da 9 KB lo sostituisce. Si sospende durante i download, come le miniature.

## Motion

150–250 ms, solo per comunicare stato: richiusura dei pannelli, comparsa in dissolvenza delle miniature all'arrivo, transizione fra anteprima e segnaposto, avanzamento interpolato invece che a scatti. Nessuna coreografia all'avvio: il prodotto si apre dentro un compito.

## Decisioni aperte

Lingua dell'interfaccia (vedi PRODUCT.md): questo lavoro aggiunge stringhe italiane, aumentando il costo di un'eventuale conversione a `tr()`.
