"""Le funzioni dello strumento di prova delle sequenze che non toccano la fotocamera."""
from pathlib import Path

import pytest

from tools import prova_sequenza as ps
from tools.prova_sequenza import (
    Esiti, Scatto, autoscatto, codice_errore, ha_scattato, intervallo_minimo, mediana,
    nuovi, per_estensione, porta_sorgente, posa_lunga, riassunto, voci,
)

FIXTURES = Path(__file__).parent / "fixtures"
OK = (b'<?xml version="1.0"?><response><take>ok</take><affocus>ok</affocus>'
      b'<afframepoint>0296x0224</afframepoint><afframesize>0046x0032</afframesize></response>')
RIFIUTO = b'<?xml version="1.0"?><result>error</result><errorcode>1004</errorcode>'


def test_reads_the_real_listings():
    assert voci((FIXTURES / "imglist_dcim.csv").read_text()) == [("/DCIM", "100OLYMP")]
    file = voci((FIXTURES / "imglist_dcim_100olymp.csv").read_text())
    assert file[0] == ("/DCIM/100OLYMP", "P2100001.ORF")
    assert len(file) == 1161


def test_garbage_listing_is_empty():
    assert voci("VER_100\n\nnon una riga\n") == []


def test_new_files_keep_the_card_order():
    assert nuovi(["a.JPG", "a.ORF"], ["a.JPG", "a.ORF", "b.JPG", "b.ORF"]) == ["b.JPG", "b.ORF"]
    assert nuovi([], []) == []


def test_extension_summary():
    assert per_estensione(["b.JPG", "b.ORF", "c.jpg"]) == "JPG 2, ORF 1"
    assert per_estensione([]) == "nessuno"


def test_slow_shutters_and_self_timers_are_recognised():
    assert posa_lunga('60"') and posa_lunga('1.3"') and posa_lunga("livebulb")
    assert not posa_lunga("250") and not posa_lunga("1.3")
    assert autoscatto("lowvib-customselftimer") and not autoscatto("lowvib-normal")


def test_reads_the_shot_response():
    assert ha_scattato(OK)
    assert not ha_scattato(b"<response><take>ng</take></response>")
    assert not ha_scattato(b"") and not ha_scattato(b"non xml")
    assert codice_errore(RIFIUTO) == "1004"
    assert codice_errore(OK) is None


def test_median_and_minimum_interval():
    assert mediana([3.0, 1.0, 2.0]) == 2.0 and mediana([]) is None
    assert intervallo_minimo(0.6) == 2      # 0,6 + 1 di margine → 2
    assert intervallo_minimo(1.0) == 2
    assert intervallo_minimo(1.2) == 3
    assert intervallo_minimo(4.5) == 8
    assert intervallo_minimo(5000) == 1800


def test_byte_swapped_port():
    assert porta_sorgente(40000) == 16540


def test_shot_cycle_adds_the_requests():
    assert Scatto(True, "200", OK, 0.5, 0.02, 0.1, 300000).ciclo == pytest.approx(0.62)
    assert Scatto(True, "200", OK, 0.5, 0.02, None, 0).ciclo == pytest.approx(0.52)


def test_summary_of_a_complete_run():
    e = Esiti(con_anteprima=[1.8, 1.6, 2.3], senza=[1.5, 1.4, 1.6],
              stato_elenco_rec="200", nuovi_in_rec=["a.JPG", "a.ORF", "b.JPG", "b.ORF"],
              scatto_a_live_spento="rifiutato (HTTP 520, errorcode 1004)",
              riacceso=True, tempo_riaccensione=0.84, scatto_dopo_riaccensione=True,
              modo_invariato=True, lungo_acceso="scatto ok in 0.4 s", live_dopo_attesa=True,
              lungo_spento="live riacceso in 0.9 s, scatto ok in 0.5 s",
              foto=19, nuovi_sulla_scheda=["a.JPG", "a.ORF"])
    assert riassunto(e) == [
        "== ESITO ==",
        "1. ritmo: ciclo mediano 1.8 s con anteprima (min 1.6, max 2.3), 1.5 s senza; "
        "intervallo minimo proposto 3 s; l'anteprima si chiede sempre (guadagno 0.3 s)",
        "2. elenco in modalità scatto: risponde, 4 file nuovi dopo gli scatti (JPG 2, ORF 2)",
        "3. scatto a live spento: rifiutato (HTTP 520, errorcode 1004); "
        "live riacceso senza rientro: sì in 0.8 s; scatto dopo: sì; modalità invariata: sì",
        "4. dopo l'attesa con il live acceso: scatto ok in 0.4 s (live ancora attivo: sì); "
        "con il live spento: live riacceso in 0.9 s, scatto ok in 0.5 s",
        "foto scattate dalla prova: 19; file nuovi sulla scheda: 2 (JPG 1, ORF 1)",
    ]


def test_summary_drops_the_review_when_it_costs_more_than_half_a_second():
    riga = riassunto(Esiti(con_anteprima=[2.4, 2.4], senza=[1.6, 1.6]))[1]
    assert "intervallo minimo proposto 3 s" in riga          # da 1,6 s, non da 2,4
    assert "sotto i 5 s l'anteprima non si chiede (guadagno 0.8 s)" in riga


def test_summary_of_a_run_that_stopped_early():
    assert riassunto(Esiti()) == [
        "== ESITO ==",
        "1. ritmo: non misurato",
        "2. elenco in modalità scatto: non provato",
        "3. live spento fra gli scatti: non provato",
        "4. dopo l'attesa con il live acceso: non provato; con il live spento: non provato",
        "foto scattate dalla prova: 0; file nuovi sulla scheda: non letti",
    ]


def test_summary_of_the_unlucky_branches():
    righe = riassunto(Esiti(stato_elenco_rec="520", scatto_a_live_spento="riuscito",
                            riacceso=False, scatto_dopo_riaccensione=True, modo_invariato=False))
    assert righe[2] == "2. elenco in modalità scatto: non risponde (HTTP 520)"
    assert righe[3] == ("3. scatto a live spento: riuscito; live riacceso senza rientro: "
                        "no, serve il rientro; scatto dopo: sì; modalità invariata: no")
    assert riassunto(Esiti(stato_elenco_rec="200"))[2] == \
        "2. elenco in modalità scatto: risponde ma non vede le foto nuove"


def test_a_shot_sends_start_stop_and_review(requests_mock):
    requests_mock.get(f"{ps.BASE}/exec_takemotion.cgi", content=OK)
    requests_mock.get(f"{ps.BASE}/exec_takemisc.cgi", content=b"\xff\xd8jpeg")
    s = ps.scatta(True)
    assert s.ok and s.stato == "200" and s.byte == 6 and s.anteprima is not None
    storia = requests_mock.request_history
    assert [r.qs["com"][0] for r in storia] == ["starttake", "stoptake", "getrecview"]
    assert storia[0].timeout == ps.TIMEOUT_SCATTO and storia[1].timeout == ps.TIMEOUT


def test_a_refused_shot_keeps_its_code_and_skips_the_review(requests_mock):
    requests_mock.get(f"{ps.BASE}/exec_takemotion.cgi", status_code=520, content=RIFIUTO)
    s = ps.scatta(True)
    assert not s.ok and s.stato == "520" and codice_errore(s.corpo) == "1004"
    assert s.anteprima is None
    assert [r.qs["com"][0] for r in requests_mock.request_history] == ["starttake", "stoptake"]


def test_listing_reads_the_last_folder(requests_mock):
    requests_mock.get(f"{ps.BASE}/get_imglist.cgi?DIR=%2FDCIM",
                      text="VER_100\n/DCIM,100OLYMP,0,16,23714,17744\n"
                           "/DCIM,101OLYMP,0,16,23714,17744\n")
    requests_mock.get(f"{ps.BASE}/get_imglist.cgi?DIR=%2FDCIM%2F101OLYMP",
                      text="VER_100\n/DCIM/101OLYMP,PB110001.JPG,5,0,1,1\n"
                           "/DCIM/101OLYMP,PB110001.ORF,9,0,1,1\n")
    stato, nomi, secondi = ps.elenco_file()
    assert stato == "200" and nomi == ["PB110001.JPG", "PB110001.ORF"] and secondi >= 0


def test_listing_reports_a_refusal(requests_mock):
    requests_mock.get(f"{ps.BASE}/get_imglist.cgi", status_code=520)
    assert ps.elenco_file()[:2] == ("520", [])


# -- la prova intera contro una fotocamera finta ---------------------------------

class _Finta:
    """Una fotocamera finta: risponde alle richieste e tiene lo stato del live."""

    def __init__(self, tempo="250", modo="A", riaccende_senza_rientro=True):
        self.tempo, self.modo = tempo, modo
        self.riaccende_senza_rientro = riaccende_senza_rientro
        self.live = False
        self.appena_rientrata = False
        self.file = ["P0000001.JPG", "P0000001.ORF"]
        self.richieste: list[str] = []

    def rispondi(self, request, context):
        q = {k: v[0] for k, v in request.qs.items()}
        nome = request.path.rsplit("/", 1)[-1]
        self.richieste.append(nome + ":" + (q.get("com") or q.get("mode") or q.get("dir") or ""))
        if nome == "switch_cammode.cgi":
            self.live = False
            self.appena_rientrata = q["mode"] == "rec"
            return b""
        if nome == "get_imglist.cgi":
            if q["dir"] == "/dcim":
                return b"VER_100\n/DCIM,100OLYMP,0,16,1,1\n"
            return ("VER_100\n" + "".join(f"/DCIM/100OLYMP,{n},5,0,1,1\n" for n in self.file)).encode()
        if nome == "get_camprop.cgi":
            valore = {"takemode": self.modo, "shutspeedvalue": self.tempo,
                      "drivemode": "lowvib-normal", "noisereduction": "auto"}[q["propname"]]
            return f'<?xml version="1.0"?><get><value>{valore}</value></get>'.encode()
        if nome == "exec_takemisc.cgi":
            if q["com"] == "startliveview":
                self.live = self.riaccende_senza_rientro or self.appena_rientrata
                self.appena_rientrata = False
            elif q["com"] == "stopliveview":
                self.live = False
            elif q["com"] == "getrecview":
                return b"\xff\xd8anteprima"
            return b""
        if q["com"] == "starttake":
            if not self.live:
                context.status_code = 520
                return RIFIUTO
            n = len(self.file) // 2 + 1
            self.file += [f"P{n:07d}.JPG", f"P{n:07d}.ORF"]
            return OK
        return b""


@pytest.fixture
def finta(requests_mock, monkeypatch, tmp_path):
    camera = _Finta()

    class Svuota:
        porta = 40000

        def __init__(self):
            self._n = 0

        @property
        def pacchetti(self):
            if camera.live:
                self._n += 1
            return self._n

        def apri(self):
            pass

        def chiudi(self):
            pass

    import re
    import time
    requests_mock.get(re.compile(re.escape(ps.BASE) + "/.*"), content=camera.rispondi)
    vero = time.sleep
    monkeypatch.setattr(ps.time, "sleep", lambda s: vero(min(s, 0.002)))
    monkeypatch.setattr(ps, "Svuota", Svuota)
    monkeypatch.setattr(ps, "ATTESA_LUNGA", 0.01)
    monkeypatch.setattr(ps, "ATTESA_LIVE", 0.05)
    monkeypatch.setattr(ps, "CARTELLA", tmp_path)
    monkeypatch.setattr(ps, "REFERTO", tmp_path / "prova_sequenza.txt")
    monkeypatch.setattr(ps, "_righe", [])
    return camera


def test_a_whole_run_against_a_fake_camera(finta, tmp_path):
    assert ps.main() == 0
    referto = (tmp_path / "prova_sequenza.txt").read_text()
    assert "2. elenco in modalità scatto: risponde, 30 file nuovi dopo gli scatti (JPG 15, ORF 15)" in referto
    assert ("3. scatto a live spento: rifiutato (HTTP 520, errorcode 1004); "
            "live riacceso senza rientro: sì in ") in referto
    assert "scatto dopo: sì; modalità invariata: sì" in referto
    assert "(live ancora attivo: sì); con il live spento: live riacceso in " in referto
    assert "foto scattate dalla prova: 18; file nuovi sulla scheda: 36 (JPG 18, ORF 18)" in referto
    assert "FERMO" not in referto and "ERRORE" not in referto
    # alla fine: live spento, riproduzione, elenco
    assert finta.richieste[-4:] == ["exec_takemisc.cgi:stopliveview", "switch_cammode.cgi:play",
                                    "get_imglist.cgi:/dcim", "get_imglist.cgi:/dcim/100olymp"]
    assert not finta.live


def test_a_camera_that_needs_the_full_reentry(finta, tmp_path):
    finta.riaccende_senza_rientro = False
    assert ps.main() == 0
    referto = (tmp_path / "prova_sequenza.txt").read_text()
    assert "live riacceso senza rientro: no, serve il rientro; scatto dopo: sì" in referto
    assert "attesa con il live spento saltata" in referto
    assert "con il live spento: non provato" in referto
    assert "foto scattate dalla prova: 17;" in referto
    assert finta.richieste[-3] == "switch_cammode.cgi:play"


def test_a_slow_shutter_stops_before_any_shot(finta, tmp_path):
    finta.modo, finta.tempo = "M", '60"'
    assert ps.main() == 1
    referto = (tmp_path / "prova_sequenza.txt").read_text()
    assert 'FERMO: il tempo 60" è troppo lungo' in referto
    assert "foto scattate dalla prova: 0; file nuovi sulla scheda: 0 (nessuno)" in referto
    assert "exec_takemotion.cgi:starttake" not in finta.richieste
    assert "switch_cammode.cgi:play" in finta.richieste[-3:]


def test_an_unreachable_camera_stops_at_once(requests_mock, monkeypatch, tmp_path):
    import re

    import requests
    requests_mock.get(re.compile(re.escape(ps.BASE) + "/.*"), exc=requests.ConnectTimeout)
    monkeypatch.setattr(ps, "CARTELLA", tmp_path)
    monkeypatch.setattr(ps, "REFERTO", tmp_path / "prova_sequenza.txt")
    monkeypatch.setattr(ps, "_righe", [])
    assert ps.main() == 1
    referto = (tmp_path / "prova_sequenza.txt").read_text()
    assert "FERMO: la fotocamera non risponde; nessuna foto scattata" in referto
    assert "1. ritmo: non misurato" in referto
