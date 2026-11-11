"""Le funzioni dello strumento di prova delle sequenze che non toccano la fotocamera."""
from pathlib import Path

import pytest

from tools import prova_sequenza as ps
from tools.prova_sequenza import (
    Esiti, Scatto, codice_errore, ha_scattato, intervallo_minimo, mediana, nuovi,
    per_estensione, porta_sorgente, posa_lunga, riassunto, scatto_singolo, voci,
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


def test_slow_shutters_are_recognised():
    for tempo in ('60"', '1.3"', "livebulb", "livecomp", "bulb", "time"):
        assert posa_lunga(tempo), tempo
    assert not posa_lunga("250") and not posa_lunga("1.3")


def test_only_single_shot_drive_modes_pass():
    for scatto in ("normal", "lowvib-normal", "silent-normal"):
        assert scatto_singolo(scatto), scatto
    for scatto in ("lowvib-customselftimer", "continuous-H", "silent-continuous-L", "?"):
        assert not scatto_singolo(scatto), scatto


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


def _completa() -> Esiti:
    return Esiti(con_anteprima=[1.8, 1.6, 2.3], senza=[1.5, 1.4, 1.6], start=[0.4, 0.5],
                 scatti_ritmo=2, stato_elenco_rec="200",
                 nuovi_in_rec=["a.JPG", "a.ORF", "b.JPG", "b.ORF"],
                 elenco_rec_coincide=True, elenco_rec_completo=True, live_dopo_elenco=True,
                 live_fermo=True, scatto_a_live_spento="rifiutato (HTTP 520, errorcode 1004)",
                 riacceso=True, tempo_riaccensione=0.84, flusso_prima=812.0, flusso_dopo=805.0,
                 scatto_dopo_riaccensione=True, modo_invariato=True,
                 lungo_acceso="scatto ok in 0.4 s", live_dopo_attesa=True,
                 lungo_spento="startliveview 200 in 0.1 s, live riacceso in 0.9 s, "
                              "scatto ok in 0.5 s (0 pacchetti durante l'attesa)",
                 foto=19, nuovi_sulla_scheda=["a.JPG", "a.ORF"])


def test_summary_of_a_complete_run():
    assert riassunto(_completa()) == [
        "== ESITO ==",
        "1. ritmo: ciclo mediano 1.8 s con anteprima (min 1.6, max 2.3), 1.5 s senza (max 1.6); "
        "intervallo minimo proposto 3 s; l'anteprima si chiede sempre (guadagno 0.3 s)",
        "2. elenco in modalità scatto: risponde, 4 file nuovi dopo gli scatti (JPG 2, ORF 2); "
        "uguale a quello in riproduzione: sì; completo dopo gli scatti: sì; il live continua: sì",
        "3. scatto a live spento: rifiutato (HTTP 520, errorcode 1004); "
        "live riacceso senza rientro: sì in 0.8 s; scatto dopo: sì; "
        "flusso del live prima / dopo: 812 / 805 kB/s; "
        "modalità: invariata (è quella della ghiera: controllo debole)",
        "4. dopo l'attesa con il live acceso: scatto ok in 0.4 s (live ancora attivo: sì); "
        "con il live spento: startliveview 200 in 0.1 s, live riacceso in 0.9 s, "
        "scatto ok in 0.5 s (0 pacchetti durante l'attesa)",
        "foto scattate dalla prova: 19; file nuovi sulla scheda: 2 (JPG 1, ORF 1)",
    ]


def test_summary_drops_the_review_when_it_costs_more_than_half_a_second():
    riga = riassunto(Esiti(con_anteprima=[2.4, 2.4], senza=[1.6, 1.6]))[1]
    assert "intervallo minimo proposto 3 s" in riga          # da 1,6 s, non da 2,4
    assert "sotto i 5 s l'anteprima non si chiede (guadagno 0.8 s)" in riga


def test_summary_keeps_the_shots_without_review_when_the_others_failed():
    assert riassunto(Esiti(senza=[1.2, 1.4]))[1] == (
        "1. ritmo: ciclo mediano 1.3 s senza anteprima (max 1.4); con anteprima non misurato; "
        "intervallo minimo proposto 3 s")


def test_summary_warns_about_slow_shots():
    righe = riassunto(Esiti(con_anteprima=[5.0], start=[4.2, 4.6]))
    assert righe[2].strip().startswith("ATTENZIONE: scatti lenti (starttake mediano 4.4 s)")


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
    assert riassunto(Esiti(stato_elenco_rec="520"))[2] == \
        "2. elenco in modalità scatto: non risponde (HTTP 520)"
    assert riassunto(Esiti(stato_elenco_rec="200"))[2] == \
        "2. elenco in modalità scatto: risponde ma non vede le foto nuove"
    stantio = riassunto(Esiti(stato_elenco_rec="200", nuovi_in_rec=["a.JPG"],
                              elenco_rec_coincide=False, elenco_rec_completo=False,
                              live_dopo_elenco=False))[2]
    assert stantio.endswith("uguale a quello in riproduzione: no; "
                            "completo dopo gli scatti: no; il live continua: no")
    assert riassunto(Esiti(live_fermo=False))[3] == \
        "3. live spento fra gli scatti: NON VALIDO, il live non si è fermato dopo stopliveview"
    rientro = riassunto(Esiti(live_fermo=True, scatto_a_live_spento="riuscito", riacceso=False,
                              rientro_usato=True, scatto_dopo_riaccensione=True,
                              modo_invariato=True))[3]
    assert rientro == ("3. scatto a live spento: riuscito; live riacceso senza rientro: "
                       "no, serve il rientro; scatto dopo: sì; "
                       "flusso del live prima / dopo: non misurato; "
                       "modalità: non significativa (c'è stato il rientro)")
    assert riassunto(Esiti(live_fermo=True, riacceso=True, tempo_riaccensione=1.0,
                           modo_invariato=False))[3].endswith("modalità: CAMBIATA")


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
    assert stato == "200" and nomi == ["PB110001.JPG", "PB110001.ORF"]
    assert [r.qs["dir"][0] for r in requests_mock.request_history] == ["/dcim", "/dcim/101olymp"]


def test_listing_reports_a_refusal(requests_mock):
    requests_mock.get(f"{ps.BASE}/get_imglist.cgi", status_code=520)
    assert ps.elenco_file()[:2] == ("520", [])


# -- la prova intera contro una fotocamera finta ---------------------------------

class _Finta:
    """Una fotocamera finta: risponde alle richieste e tiene lo stato del live."""

    def __init__(self):
        self.tempo, self.modo, self.scatto = "250", "A", "lowvib-normal"
        self.riaccende_senza_rientro = True
        self.ignora_stop = False             # stopliveview non ferma il live
        self.live_non_parte = False
        self.rompi_allo_scatto: int | None = None    # solleva un errore a questo scatto
        self.muta_dopo_attesa = False        # non risponde alla prima richiesta dopo l'attesa
        self.elenco_rec_vecchio = False      # in modalità scatto l'elenco non si aggiorna
        self.elenco_rec_in_ritardo = False   # in modalità scatto manca l'ultima foto
        self.live = False
        self.in_rec = False
        self.appena_rientrata = False
        self.file = ["P0000001.JPG", "P0000001.ORF"]
        self.file_all_ingresso: list[str] = []
        self.scatti = 0
        self.attese = 0
        self.richieste: list[str] = []

    def rispondi(self, request, context):
        import requests
        q = {k: v[0] for k, v in request.qs.items()}
        nome = request.path.rsplit("/", 1)[-1]
        self.richieste.append(nome + ":" + (q.get("com") or q.get("mode") or q.get("dir") or ""))
        if self.muta_dopo_attesa and self.attese >= 2:
            self.muta_dopo_attesa = False
            raise requests.ConnectTimeout
        if nome == "switch_cammode.cgi":
            self.live = False
            self.in_rec = q["mode"] == "rec"
            self.appena_rientrata = self.in_rec
            if self.in_rec:
                self.file_all_ingresso = list(self.file)
            return b""
        if nome == "get_imglist.cgi":
            if q["dir"] == "/dcim":
                return b"VER_100\n/DCIM,100OLYMP,0,16,1,1\n"
            file = (self.file_all_ingresso if self.in_rec and self.elenco_rec_vecchio
                    else self.file)
            if self.in_rec and self.elenco_rec_in_ritardo and self.scatti:
                file = file[:-2]
            return ("VER_100\n" + "".join(f"/DCIM/100OLYMP,{n},5,0,1,1\n" for n in file)).encode()
        if nome == "get_camprop.cgi":
            valore = {"takemode": self.modo, "shutspeedvalue": self.tempo,
                      "drivemode": self.scatto, "noisereduction": "auto"}[q["propname"]]
            return f'<?xml version="1.0"?><get><value>{valore}</value></get>'.encode()
        if nome == "exec_takemisc.cgi":
            if q["com"] == "startliveview":
                self.live = (not self.live_non_parte
                             and (self.riaccende_senza_rientro or self.appena_rientrata))
                self.appena_rientrata = False
            elif q["com"] == "stopliveview" and not self.ignora_stop:
                self.live = False
            elif q["com"] == "getrecview":
                return b"\xff\xd8anteprima"
            return b""
        if q["com"] == "starttake":
            if not self.live:
                context.status_code = 520
                return RIFIUTO
            self.scatti += 1
            if self.scatti == self.rompi_allo_scatto:
                raise RuntimeError("guasto finto a metà dello scatto")
            n = len(self.file) // 2 + 1
            self.file += [f"P{n:07d}.JPG", f"P{n:07d}.ORF"]
            return OK
        return b""


@pytest.fixture
def finta(requests_mock, monkeypatch, tmp_path):
    import re
    import time
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

        @property
        def byte(self):
            return self.pacchetti * 1000

        def apri(self):
            pass

        def chiudi(self):
            pass

    def attesa(svuota, secondi, etichetta):
        camera.attese += 1
        return vera_attesa(svuota, secondi, etichetta)

    requests_mock.get(re.compile(re.escape(ps.BASE) + "/.*"), content=camera.rispondi)
    dormi = time.sleep
    vera_attesa = ps.attesa_lunga
    monkeypatch.setattr(ps.time, "sleep", lambda s: dormi(min(s, 0.002)))
    monkeypatch.setattr(ps, "attesa_lunga", attesa)
    monkeypatch.setattr(ps, "Svuota", Svuota)
    monkeypatch.setattr(ps, "ATTESA_LUNGA", 0.01)
    monkeypatch.setattr(ps, "ATTESA_LIVE", 0.05)
    monkeypatch.setattr(ps, "CARTELLA", tmp_path)
    monkeypatch.setattr(ps, "REFERTO", tmp_path / "prova_sequenza.txt")
    monkeypatch.setattr(ps, "_righe", [])
    return camera


def _referto(tmp_path) -> str:
    return (tmp_path / "prova_sequenza.txt").read_text()


CHIUSURA = ["exec_takemotion.cgi:stoptake", "exec_takemisc.cgi:stopliveview",
            "switch_cammode.cgi:play", "get_imglist.cgi:/dcim", "get_imglist.cgi:/dcim/100olymp"]


def test_a_whole_run_against_a_fake_camera(finta, tmp_path):
    assert ps.main() == 0
    referto = _referto(tmp_path)
    assert ("2. elenco in modalità scatto: risponde, 30 file nuovi dopo gli scatti "
            "(JPG 15, ORF 15); uguale a quello in riproduzione: sì; "
            "completo dopo gli scatti: sì; il live continua: sì") in referto
    assert ("3. scatto a live spento: rifiutato (HTTP 520, errorcode 1004); "
            "live riacceso senza rientro: sì in ") in referto
    assert "scatto dopo: sì; flusso del live prima / dopo: " in referto
    assert "modalità: invariata (è quella della ghiera: controllo debole)" in referto
    assert "(live ancora attivo: sì); con il live spento: startliveview 200 in " in referto
    assert "scatto ok in 0.0 s (0 pacchetti durante l'attesa)" in referto
    assert "foto scattate dalla prova: 18; file nuovi sulla scheda: 36 (JPG 18, ORF 18)" in referto
    for parola in ("FERMO", "ERRORE", "ATTENZIONE", "NON VALIDO", "INTERROTTO"):
        assert parola not in referto, parola
    assert finta.richieste[-5:] == CHIUSURA
    assert not finta.live
    # 10 scatti con anteprima e 5 senza, alternati
    assert finta.richieste.count("exec_takemisc.cgi:getrecview") == 10 + 3


def test_a_camera_that_needs_the_full_reentry(finta, tmp_path):
    finta.riaccende_senza_rientro = False
    assert ps.main() == 0
    referto = _referto(tmp_path)
    assert "live riacceso senza rientro: no, serve il rientro; scatto dopo: sì" in referto
    assert "modalità: non significativa (c'è stato il rientro)" in referto
    assert "attesa con il live spento saltata" in referto
    assert "con il live spento: non provato" in referto
    assert "foto scattate dalla prova: 17;" in referto
    assert finta.richieste[-5:] == CHIUSURA


def test_a_live_that_does_not_stop_invalidates_the_third_point(finta, tmp_path):
    finta.ignora_stop = True
    assert ps.main() == 0
    referto = _referto(tmp_path)
    assert ("3. live spento fra gli scatti: NON VALIDO, "
            "il live non si è fermato dopo stopliveview") in referto
    assert "con il live spento: non provato" in referto
    assert "foto scattate dalla prova: 16;" in referto        # 15 del ritmo e 1 dopo l'attesa
    assert finta.richieste[-5:] == CHIUSURA


def test_a_stale_listing_in_shooting_mode_is_reported(finta, tmp_path):
    finta.elenco_rec_vecchio = True
    assert ps.main() == 0
    assert "2. elenco in modalità scatto: risponde ma non vede le foto nuove" in _referto(tmp_path)


def test_a_listing_one_shot_behind_is_not_complete(finta, tmp_path):
    # sul campo (2026-11-11): letto subito dopo 15 scatti, l'elenco ne mostrava 14
    finta.elenco_rec_in_ritardo = True
    assert ps.main() == 0
    assert ("risponde, 28 file nuovi dopo gli scatti (JPG 14, ORF 14); "
            "uguale a quello in riproduzione: sì; completo dopo gli scatti: no") in _referto(tmp_path)


def test_a_camera_asleep_after_the_wait_is_told_apart(finta, tmp_path):
    finta.muta_dopo_attesa = True       # la prima richiesta dopo l'attesa a live spento cade
    assert ps.main() == 0
    referto = _referto(tmp_path)
    assert ("con il live spento: startliveview ConnectTimeout in 0.0 s, "
            "il live non riparte senza rientro; dopo il rientro, scatto ok") in referto
    assert finta.richieste[-5:] == CHIUSURA


@pytest.mark.parametrize("prepara, messaggio", [
    (lambda c: setattr(c, "scatto", "lowvib-customselftimer"), "FERMO: lo scatto è «lowvib-customselftimer»"),
    (lambda c: setattr(c, "scatto", "continuous-H"), "FERMO: lo scatto è «continuous-H»"),
    (lambda c: (setattr(c, "modo", "M"), setattr(c, "tempo", '60"')), 'FERMO: il tempo 60" è troppo lungo'),
    (lambda c: setattr(c, "tempo", '2"'), 'FERMO: il tempo 2" è troppo lungo'),
    (lambda c: setattr(c, "live_non_parte", True), "FERMO: il live non arriva"),
])
def test_stops_before_any_shot(finta, tmp_path, prepara, messaggio):
    prepara(finta)
    assert ps.main() == 1
    referto = _referto(tmp_path)
    assert messaggio in referto
    assert "foto scattate dalla prova: 0; file nuovi sulla scheda: 0 (nessuno)" in referto
    assert "exec_takemotion.cgi:starttake" not in finta.richieste
    assert finta.richieste[-5:] == CHIUSURA


def test_a_failure_in_the_middle_of_a_shot_still_closes_everything(finta, tmp_path):
    finta.rompi_allo_scatto = 4
    assert ps.main() == 1
    referto = _referto(tmp_path)
    assert "ERRORE IMPREVISTO" in referto and "guasto finto" in referto
    assert "foto scattate dalla prova: 3;" in referto
    # lo scatto interrotto viene chiuso subito, e poi di nuovo alla chiusura
    quarto = [i for i, r in enumerate(finta.richieste) if r == "exec_takemotion.cgi:starttake"][3]
    assert finta.richieste[quarto + 1] == "exec_takemotion.cgi:stoptake"
    assert finta.richieste[-5:] == CHIUSURA
    assert not finta.live


def test_ctrl_c_is_recorded_and_still_closes_everything(finta, tmp_path, monkeypatch):
    def interrompi(svuota, secondi, etichetta):
        raise KeyboardInterrupt
    monkeypatch.setattr(ps, "attesa_lunga", interrompi)
    assert ps.main() == 130
    assert "INTERROTTO dall'utente" in _referto(tmp_path)
    assert finta.richieste[-5:] == CHIUSURA


def test_an_unreachable_camera_stops_at_once(requests_mock, monkeypatch, tmp_path):
    import re

    import requests
    requests_mock.get(re.compile(re.escape(ps.BASE) + "/.*"), exc=requests.ConnectTimeout)
    monkeypatch.setattr(ps, "CARTELLA", tmp_path)
    monkeypatch.setattr(ps, "REFERTO", tmp_path / "prova_sequenza.txt")
    monkeypatch.setattr(ps, "_righe", [])
    assert ps.main() == 1
    referto = _referto(tmp_path)
    assert "FERMO: la fotocamera non risponde; nessuna foto scattata" in referto
    assert "1. ritmo: non misurato" in referto
