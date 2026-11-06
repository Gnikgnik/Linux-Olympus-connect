import pytest
import requests

from olympus_connect.core.errors import CameraNotReachable, ProtocolError
from olympus_connect.core.protocol import BASE_URL, HEADERS, OlympusClient

CAMINFO = b'<?xml version="1.0"?><caminfo><model>E-M10MarkIV</model></caminfo>'

# Il root reale è <oishare>, non <oimp>: verificato sulla fotocamera.
COMMANDLIST = (
    b'<?xml version="1.0"?><oishare>'
    b'<version>4.50</version>'
    b'<cgi name="get_imglist"><http_method type="get"/></cgi>'
    b'<cgi name="switch_cammode"><http_method type="get"/></cgi>'
    b'<cgi name="get_thumbnail"><http_method type="get"/></cgi>'
    b'</oishare>'
)
COMMANDLIST_WITH_SCREENNAIL = COMMANDLIST.replace(
    b'</oishare>',
    b'<cgi name="get_screennail"><http_method type="get"/></cgi></oishare>',
)
COMMANDLIST_WITH_TIMEOUT = COMMANDLIST.replace(
    b'</oishare>',
    b'<cgi name="set_timeout"><http_method type="get"/></cgi></oishare>',
)


def test_headers_include_oishare_user_agent():
    # La fotocamera non lo richiede (verificato sul campo: risponde HTTP 200
    # anche senza), ma replichiamo il comportamento di OI.Share perché non
    # costa niente e altri firmware potrebbero pretenderlo.
    assert HEADERS["User-Agent"] == "OI.Share v2"
    assert HEADERS["Host"] == "192.168.0.10"


def test_connect_returns_model_and_populates_commands(requests_mock):
    requests_mock.get(f"{BASE_URL}/get_commandlist.cgi", content=COMMANDLIST)
    requests_mock.get(f"{BASE_URL}/get_caminfo.cgi", content=CAMINFO)

    client = OlympusClient()
    assert client.connect() == "E-M10MarkIV"
    assert "get_imglist" in client.commands
    assert "switch_cammode" in client.commands


def test_connect_sends_user_agent(requests_mock):
    requests_mock.get(f"{BASE_URL}/get_commandlist.cgi", content=COMMANDLIST)
    requests_mock.get(f"{BASE_URL}/get_caminfo.cgi", content=CAMINFO)

    OlympusClient().connect()
    assert requests_mock.request_history[0].headers["User-Agent"] == "OI.Share v2"


def test_connect_raises_camera_not_reachable_on_connection_error(requests_mock):
    requests_mock.get(f"{BASE_URL}/get_commandlist.cgi",
                      exc=requests.exceptions.ConnectionError)
    with pytest.raises(CameraNotReachable):
        OlympusClient().connect()


def test_connect_raises_protocol_error_on_garbage(requests_mock):
    requests_mock.get(f"{BASE_URL}/get_commandlist.cgi", content=b"not xml at all")
    with pytest.raises(ProtocolError):
        OlympusClient().connect()


def test_ensure_play_mode_issues_switch_cammode(requests_mock):
    requests_mock.get(f"{BASE_URL}/switch_cammode.cgi", content=b"")
    client = OlympusClient()
    client.ensure_play_mode()
    assert requests_mock.last_request.qs["mode"] == ["play"]


def test_ensure_play_mode_is_idempotent(requests_mock):
    requests_mock.get(f"{BASE_URL}/switch_cammode.cgi", content=b"")
    client = OlympusClient()
    client.ensure_play_mode()
    client.ensure_play_mode()
    assert len(requests_mock.request_history) == 1


def test_keep_awake_sends_timeoutsec(requests_mock):
    # Nota dal Task 0: su questo firmware set_timeout risponde HTTP 520
    # errorcode 1006 per ogni valore, quindi keep_awake() restituirà False
    # sulla fotocamera reale. Il metodo resta perché altri modelli possono
    # supportarlo, e perché il suo valore è evitare che l'assenza del
    # comando diventi un errore visibile all'utente.
    requests_mock.get(f"{BASE_URL}/get_commandlist.cgi", content=COMMANDLIST_WITH_TIMEOUT)
    requests_mock.get(f"{BASE_URL}/get_caminfo.cgi", content=CAMINFO)
    requests_mock.get(f"{BASE_URL}/set_timeout.cgi", content=b"")

    client = OlympusClient()
    client.connect()
    assert client.keep_awake(1800) is True
    assert requests_mock.last_request.qs["timeoutsec"] == ["1800"]


def test_keep_awake_returns_false_when_camera_lacks_the_command():
    # Altri modelli non dichiarano set_timeout: non è un errore, si rinuncia.
    client = OlympusClient()
    client.commands = {"get_imglist"}
    assert client.keep_awake() is False


def test_keep_awake_swallows_failures(requests_mock):
    # Il fallimento reale di questa fotocamera: HTTP 520 errorcode 1006.
    # Un keep-alive fallito non deve impedire di scaricare le foto.
    requests_mock.get(f"{BASE_URL}/set_timeout.cgi", status_code=520,
                      content=b'<?xml version="1.0"?>\n<errorcode>1006</errorcode>')
    client = OlympusClient()
    client.commands = {"set_timeout"}
    assert client.keep_awake() is False


def test_list_media_recurses_into_subdirectories(requests_mock):
    def body(request, context):
        directory = request.qs["dir"][0]
        if directory == "/dcim":
            return "/DCIM,100OLYMP,0,16,19594,45820\r\n"
        return "/DCIM/100OLYMP,P9240123.JPG,5000000,0,19594,45820\r\n"

    requests_mock.get(f"{BASE_URL}/get_imglist.cgi", text=body)
    requests_mock.get(f"{BASE_URL}/switch_cammode.cgi", content=b"")

    files = OlympusClient().list_media()
    assert [f.name for f in files] == ["P9240123.JPG"]


def test_list_media_treats_404_as_empty_directory(requests_mock):
    # La fotocamera risponde 404 per una directory vuota: non è un errore.
    requests_mock.get(f"{BASE_URL}/get_imglist.cgi", status_code=404)
    requests_mock.get(f"{BASE_URL}/switch_cammode.cgi", content=b"")
    assert OlympusClient().list_media() == []


def test_list_media_raises_protocol_error_on_unexpected_status(requests_mock):
    requests_mock.get(f"{BASE_URL}/get_imglist.cgi", status_code=500, content=b"boom")
    requests_mock.get(f"{BASE_URL}/switch_cammode.cgi", content=b"")
    with pytest.raises(ProtocolError):
        OlympusClient().list_media()


def test_thumbnail_returns_bytes(requests_mock):
    requests_mock.get(f"{BASE_URL}/get_thumbnail.cgi", content=b"\xff\xd8jpeg")
    assert OlympusClient().thumbnail("/DCIM/100OLYMP/P9240123.JPG") == b"\xff\xd8jpeg"


def test_screennail_returns_bytes(requests_mock):
    # Anteprima grande: circa 1 MB anche per i RAW (misurato sul campo).
    # È ciò che permette di giudicare uno scatto prima di scaricarlo.
    requests_mock.get(f"{BASE_URL}/get_screennail.cgi", content=b"\xff\xd8grande")
    assert OlympusClient().screennail("/DCIM/100OLYMP/A.ORF") == b"\xff\xd8grande"
    assert requests_mock.last_request.qs["dir"] == ["/dcim/100olymp/a.orf"]


def test_has_screennail_reads_the_commandlist(requests_mock):
    requests_mock.get(f"{BASE_URL}/get_commandlist.cgi", content=COMMANDLIST_WITH_SCREENNAIL)
    requests_mock.get(f"{BASE_URL}/get_caminfo.cgi", content=CAMINFO)
    client = OlympusClient()
    client.connect()
    assert client.has_screennail() is True


def test_has_screennail_false_when_camera_lacks_it():
    # Altri modelli potrebbero non dichiararlo: l'anteprima si disattiva
    # invece di produrre errori a ogni selezione.
    client = OlympusClient()
    client.commands = {"get_thumbnail"}
    assert client.has_screennail() is False


def test_exif_returns_the_raw_bytes(requests_mock):
    requests_mock.get(f"{BASE_URL}/get_exif.cgi", content=b"II*\x00dati")
    assert OlympusClient().exif("/DCIM/100OLYMP/A.ORF") == b"II*\x00dati"
    assert requests_mock.last_request.qs["dir"] == ["/dcim/100olymp/a.orf"]


def test_exif_raises_on_http_error(requests_mock):
    requests_mock.get(f"{BASE_URL}/get_exif.cgi", status_code=404)
    with pytest.raises(ProtocolError):
        OlympusClient().exif("/DCIM/100OLYMP/A.ORF")


def test_has_exif_reads_the_real_commandlist(fixture_bytes):
    import xml.etree.ElementTree as ElementTree
    client = OlympusClient()
    radice = ElementTree.fromstring(fixture_bytes("commandlist.xml"))
    client.commands = {e.attrib["name"].strip() for e in radice.iter("cgi")}
    assert client.has_exif() is True


def test_has_exif_false_when_camera_lacks_it():
    client = OlympusClient()
    client.commands = {"get_thumbnail"}
    assert client.has_exif() is False


def test_open_stream_reports_total_from_content_length(requests_mock):
    requests_mock.get(f"{BASE_URL}/DCIM/100OLYMP/A.JPG",
                      content=b"x" * 1000,
                      headers={"Content-Length": "1000"})
    with OlympusClient().open_stream("/DCIM/100OLYMP/A.JPG") as stream:
        assert stream.total == 1000
        assert stream.resumed is False
        assert b"".join(stream.chunks) == b"x" * 1000


def test_open_stream_with_offset_sends_range_header(requests_mock):
    requests_mock.get(f"{BASE_URL}/DCIM/100OLYMP/A.JPG", content=b"y" * 100)
    with OlympusClient().open_stream("/DCIM/100OLYMP/A.JPG", offset=500):
        pass
    assert requests_mock.last_request.headers["Range"] == "bytes=500-"


def test_open_stream_detects_honoured_range(requests_mock):
    # 206 + Content-Range: la ripresa funziona, total viene dal denominatore.
    # Questo firmware non lo fa, ma altri modelli sì e il ramo va protetto.
    requests_mock.get(f"{BASE_URL}/DCIM/100OLYMP/A.JPG",
                      status_code=206, content=b"z" * 500,
                      headers={"Content-Range": "bytes 500-999/1000"})
    with OlympusClient().open_stream("/DCIM/100OLYMP/A.JPG", offset=500) as stream:
        assert stream.resumed is True
        assert stream.total == 1000


def test_open_stream_detects_ignored_range(requests_mock):
    # Il comportamento reale della E-M10 Mark IV: 200 nonostante il Range,
    # nessun Content-Range, file intero. Si riparte da zero.
    requests_mock.get(f"{BASE_URL}/DCIM/100OLYMP/A.JPG",
                      status_code=200, content=b"w" * 1000,
                      headers={"Content-Length": "1000"})
    with OlympusClient().open_stream("/DCIM/100OLYMP/A.JPG", offset=500) as stream:
        assert stream.resumed is False
        assert stream.total == 1000


def test_open_stream_survives_missing_content_length(requests_mock):
    requests_mock.get(f"{BASE_URL}/DCIM/100OLYMP/A.JPG", content=b"q" * 10,
                      headers={"Content-Length": ""})
    with OlympusClient().open_stream("/DCIM/100OLYMP/A.JPG") as stream:
        assert stream.total == 0        # sconosciuto, non un errore
        assert b"".join(stream.chunks) == b"q" * 10


def test_open_stream_raises_protocol_error_on_404(requests_mock):
    requests_mock.get(f"{BASE_URL}/DCIM/100OLYMP/A.JPG", status_code=404)
    with pytest.raises(ProtocolError):
        with OlympusClient().open_stream("/DCIM/100OLYMP/A.JPG"):
            pass


def test_connect_parses_real_camera_fixtures(requests_mock, fixture_bytes):
    # Il test che conta: output registrato dalla fotocamera reale (Task 0).
    requests_mock.get(f"{BASE_URL}/get_commandlist.cgi",
                      content=fixture_bytes("commandlist.xml"))
    requests_mock.get(f"{BASE_URL}/get_caminfo.cgi",
                      content=fixture_bytes("caminfo.xml"))

    client = OlympusClient()
    assert client.connect() == "E-M10MarkIV"
    assert "get_imglist" in client.commands
    assert "get_thumbnail" in client.commands
    assert "set_timeout" in client.commands   # dichiarato, anche se non funziona


def test_list_media_parses_real_camera_fixtures(requests_mock, fixture_bytes):
    """L'elenco completo della scheda reale: 1161 file su due livelli."""
    dcim = fixture_bytes("imglist_dcim.csv")
    olymp = fixture_bytes("imglist_dcim_100olymp.csv")

    def body(request, context):
        return dcim if request.qs["dir"][0] == "/dcim" else olymp

    requests_mock.get(f"{BASE_URL}/get_imglist.cgi", content=body)
    requests_mock.get(f"{BASE_URL}/switch_cammode.cgi", content=b"")

    files = OlympusClient().list_media()
    assert len(files) == 1161

    from olympus_connect.core.model import MediaKind
    kinds = {}
    for f in files:
        kinds[f.kind] = kinds.get(f.kind, 0) + 1
    assert kinds[MediaKind.RAW] == 1141
    assert kinds[MediaKind.MOVIE] == 18
    assert kinds[MediaKind.JPEG] == 2
