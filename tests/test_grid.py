from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt

from olympus_connect.core.downloader import Job, JobState
from olympus_connect.core.model import MediaFile, MediaKind
from olympus_connect.gui.grid import JOB_ROLE, MediaFilter, MediaGrid, MediaListModel


def job(name="A.JPG", kind=MediaKind.JPEG, state=JobState.NEW, size=100):
    m = MediaFile(f"/DCIM/100OLYMP/{name}", size,
                  datetime(2026, 9, 24, 12, 0, 0), kind)
    return Job(m, Path("/tmp") / name, state)


# --- modello ---------------------------------------------------------------

def test_model_row_count_matches_jobs(qapp):
    model = MediaListModel()
    model.set_jobs([job("A.JPG"), job("B.JPG")])
    assert model.rowCount() == 2


def test_model_display_role_is_file_name(qapp):
    model = MediaListModel()
    model.set_jobs([job("A.JPG")])
    assert model.data(model.index(0, 0), Qt.ItemDataRole.DisplayRole) == "A.JPG"


def test_model_job_role_returns_job(qapp):
    model = MediaListModel()
    model.set_jobs([job("A.JPG")])
    assert model.data(model.index(0, 0), JOB_ROLE).media.name == "A.JPG"


def test_model_tooltip_mentions_size_and_kind(qapp):
    model = MediaListModel()
    model.set_jobs([job("A.ORF", kind=MediaKind.RAW, size=20_000_000)])
    tip = model.data(model.index(0, 0), Qt.ItemDataRole.ToolTipRole)
    assert "A.ORF" in tip and "19.1 MB" in tip and "raw" in tip


def test_model_tooltip_handles_missing_date(qapp):
    m = MediaFile("/DCIM/100OLYMP/ODD.JPG", 5, None, MediaKind.JPEG)
    model = MediaListModel()
    model.set_jobs([Job(m, Path("/tmp/ODD.JPG"), JobState.NEW)])
    assert "sconosciuta" in model.data(model.index(0, 0),
                                      Qt.ItemDataRole.ToolTipRole)


def test_model_set_thumbnail_emits_data_changed(qapp):
    model = MediaListModel()
    model.set_jobs([job("A.JPG")])
    changed = []
    model.dataChanged.connect(lambda tl, br, roles: changed.append(tl.row()))

    model.set_thumbnail("/DCIM/100OLYMP/A.JPG", _tiny_png())
    assert changed == [0]


def test_model_set_thumbnail_for_unknown_path_is_ignored(qapp):
    model = MediaListModel()
    model.set_jobs([job("A.JPG")])
    model.set_thumbnail("/DCIM/100OLYMP/GHOST.JPG", _tiny_png())  # non deve sollevare


def test_model_set_thumbnail_ignores_undecodable_bytes(qapp):
    model = MediaListModel()
    model.set_jobs([job("A.JPG")])
    changed = []
    model.dataChanged.connect(lambda *a: changed.append(1))
    model.set_thumbnail("/DCIM/100OLYMP/A.JPG", b"non una immagine")
    assert changed == []


def test_model_invalid_index_returns_none(qapp):
    model = MediaListModel()
    model.set_jobs([job("A.JPG")])
    assert model.job_at(model.index(5, 0)) is None


# --- filtri ----------------------------------------------------------------

def test_filter_by_kind(qapp):
    f = MediaFilter(kinds={MediaKind.RAW}, only_new=False)
    assert f.matches(job("A.ORF", kind=MediaKind.RAW))
    assert not f.matches(job("A.JPG", kind=MediaKind.JPEG))


def test_filter_none_kinds_accepts_everything(qapp):
    f = MediaFilter(kinds=None, only_new=False)
    assert f.matches(job("A.ORF", kind=MediaKind.RAW))
    assert f.matches(job("A.MOV", kind=MediaKind.MOVIE))


def test_filter_only_new_excludes_present(qapp):
    f = MediaFilter(kinds=None, only_new=True)
    assert f.matches(job(state=JobState.NEW))
    assert f.matches(job(state=JobState.INCOMPLETE))
    assert not f.matches(job(state=JobState.PRESENT))


def test_model_applies_filter_to_row_count(qapp):
    model = MediaListModel()
    model.set_jobs([
        job("A.JPG", kind=MediaKind.JPEG),
        job("B.ORF", kind=MediaKind.RAW),
    ])
    model.set_filter(MediaFilter(kinds={MediaKind.RAW}, only_new=False))

    assert model.rowCount() == 1
    assert model.data(model.index(0, 0), Qt.ItemDataRole.DisplayRole) == "B.ORF"


def test_model_visible_jobs_respects_filter(qapp):
    model = MediaListModel()
    model.set_jobs([job("A.JPG"), job("B.ORF", kind=MediaKind.RAW)])
    model.set_filter(MediaFilter(kinds={MediaKind.JPEG}, only_new=False))

    assert [j.media.name for j in model.visible_jobs()] == ["A.JPG"]


def test_model_empty_kinds_filter_hides_everything(qapp):
    # Tutte le caselle di tipo deselezionate: la griglia si svuota, non
    # mostra tutto.
    model = MediaListModel()
    model.set_jobs([job("A.JPG"), job("B.ORF", kind=MediaKind.RAW)])
    model.set_filter(MediaFilter(kinds=set(), only_new=False))
    assert model.rowCount() == 0


# --- vista -----------------------------------------------------------------

def test_grid_requests_thumbnail_once_per_file(qapp):
    model = MediaListModel()
    grid = MediaGrid(model)
    model.set_jobs([job("A.JPG")])
    grid.resize(400, 300)

    asked = []
    grid.thumbnail_needed.connect(asked.append)
    grid.request_visible_thumbnails()
    grid.request_visible_thumbnails()   # la seconda volta non deve richiedere

    assert asked == ["/DCIM/100OLYMP/A.JPG"]


def test_grid_forget_thumbnail_allows_a_new_request(qapp):
    # Una miniatura arrivata corrotta o non servita deve poter essere
    # richiesta di nuovo: senza questo resta vuota per sempre.
    model = MediaListModel()
    grid = MediaGrid(model)
    model.set_jobs([job("A.JPG")])
    grid.resize(400, 300)

    asked = []
    grid.thumbnail_needed.connect(asked.append)
    grid.request_visible_thumbnails()
    grid.forget_thumbnail("/DCIM/100OLYMP/A.JPG")
    grid.request_visible_thumbnails()

    assert asked == ["/DCIM/100OLYMP/A.JPG"] * 2


def test_grid_selected_jobs_is_empty_without_selection(qapp):
    model = MediaListModel()
    model.set_jobs([job("A.JPG")])
    assert MediaGrid(model).selected_jobs() == []


# --- modello a colonne per l'elenco dettagliato ----------------------------

def test_model_exposes_five_columns_with_headers(qapp):
    model = MediaListModel()
    assert model.columnCount() == 5
    headers = [model.headerData(c, Qt.Orientation.Horizontal,
                               Qt.ItemDataRole.DisplayRole)
               for c in range(5)]
    assert headers == ["Nome", "Data e ora", "Tipo", "Dimensione", "Stato"]


def test_model_column_values(qapp):
    model = MediaListModel()
    model.set_jobs([job("A.ORF", kind=MediaKind.RAW, size=20_971_520,
                        state=JobState.PRESENT)])
    row = [model.data(model.index(0, c), Qt.ItemDataRole.DisplayRole)
           for c in range(5)]
    assert row[0] == "A.ORF"
    assert "24/09/2026" in row[1]
    assert row[2] == "RAW"
    assert row[3] == "20.0 MB"
    assert row[4] == "già presente"


def test_model_column_values_without_date(qapp):
    m = MediaFile("/DCIM/100OLYMP/ODD.JPG", 5, None, MediaKind.JPEG)
    model = MediaListModel()
    model.set_jobs([Job(m, Path("/tmp/ODD.JPG"), JobState.NEW)])
    assert model.data(model.index(0, 1), Qt.ItemDataRole.DisplayRole) == "—"


def test_model_sorts_by_date(qapp):
    from datetime import datetime as dt

    def dated(name, when):
        m = MediaFile(f"/DCIM/100OLYMP/{name}", 10, when, MediaKind.JPEG)
        return Job(m, Path("/tmp") / name, JobState.NEW)

    model = MediaListModel()
    model.set_jobs([
        dated("NUOVA.JPG", dt(2026, 9, 6, 10, 0)),
        dated("VECCHIA.JPG", dt(2020, 1, 1, 0, 0)),
    ])

    model.sort(1, Qt.SortOrder.AscendingOrder)
    assert [j.media.name for j in model.visible_jobs()] == \
        ["VECCHIA.JPG", "NUOVA.JPG"]

    model.sort(1, Qt.SortOrder.DescendingOrder)
    assert [j.media.name for j in model.visible_jobs()] == \
        ["NUOVA.JPG", "VECCHIA.JPG"]


def test_model_sorts_undated_files_last(qapp):
    from datetime import datetime as dt

    dated = MediaFile("/DCIM/100OLYMP/D.JPG", 10, dt(2026, 1, 1), MediaKind.JPEG)
    undated = MediaFile("/DCIM/100OLYMP/U.JPG", 10, None, MediaKind.JPEG)
    model = MediaListModel()
    model.set_jobs([
        Job(undated, Path("/tmp/U.JPG"), JobState.NEW),
        Job(dated, Path("/tmp/D.JPG"), JobState.NEW),
    ])
    model.sort(1, Qt.SortOrder.AscendingOrder)
    assert [j.media.name for j in model.visible_jobs()] == ["D.JPG", "U.JPG"]


def test_model_sorts_by_size_numerically_not_alphabetically(qapp):
    model = MediaListModel()
    model.set_jobs([
        job("BIG.MOV", size=900_000_000),
        job("SMALL.ORF", size=20_000_000),
    ])
    model.sort(3, Qt.SortOrder.AscendingOrder)
    assert [j.media.name for j in model.visible_jobs()] == ["SMALL.ORF", "BIG.MOV"]


def test_sorting_survives_a_filter_change(qapp):
    model = MediaListModel()
    model.set_jobs([job("B.JPG", size=200), job("A.JPG", size=100)])
    model.sort(3, Qt.SortOrder.AscendingOrder)
    model.set_filter(MediaFilter(kinds={MediaKind.JPEG}, only_new=False))
    assert [j.media.name for j in model.visible_jobs()] == ["A.JPG", "B.JPG"]


# --- elenco dettagliato ----------------------------------------------------

def test_table_is_sorted_by_date_and_selectable(qapp):
    from olympus_connect.gui.grid import MediaTable

    model = MediaListModel()
    table = MediaTable(model)
    model.set_jobs([job("A.JPG")])

    assert table.isSortingEnabled()
    assert table.selected_jobs() == []


def test_table_selected_jobs_does_not_duplicate_rows(qapp):
    from PyQt6.QtCore import QItemSelectionModel
    from olympus_connect.gui.grid import MediaTable

    model = MediaListModel()
    table = MediaTable(model)
    model.set_jobs([job("A.JPG"), job("B.JPG")])

    # Selezionare una riga in una vista a colonne produce un indice per
    # colonna: senza deduplica lo stesso file finirebbe 5 volte in coda.
    table.selectionModel().select(
        model.index(0, 0),
        QItemSelectionModel.SelectionFlag.Select |
        QItemSelectionModel.SelectionFlag.Rows,
    )
    assert [j.media.name for j in table.selected_jobs()] == ["A.JPG"]


def test_grid_handles_large_listing(qapp, fixture_bytes):
    """1161 file dalla scheda reale: la griglia deve costruirsi istantaneamente."""
    from olympus_connect.core.downloader import plan
    from olympus_connect.core.model import parse_imglist

    files, _ = parse_imglist(
        fixture_bytes("imglist_dcim_100olymp.csv").decode("ascii", "replace")
    )
    model = MediaListModel()
    grid = MediaGrid(model)
    model.set_jobs(plan(files, Path("/tmp/dest")))
    grid.resize(800, 600)

    assert model.rowCount() == 1161

    # Virtualizzazione: si chiedono le miniature solo per ciò che si vede,
    # non 1161 richieste alla fotocamera.
    asked = []
    grid.thumbnail_needed.connect(asked.append)
    grid.request_visible_thumbnails()
    assert 0 < len(asked) < 100


def _tiny_png() -> bytes:
    """Immagine PNG valida, generata da Qt stesso.

    Generarla invece di incollare un blob base64 evita il rischio di
    incollarne uno corrotto, che è esattamente quello che è successo.
    Richiede la fixture qapp nel test chiamante.
    """
    from PyQt6.QtCore import QBuffer, QByteArray
    from PyQt6.QtGui import QPixmap

    pixmap = QPixmap(4, 4)
    pixmap.fill()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    assert pixmap.save(buffer, "PNG")
    return bytes(data)
