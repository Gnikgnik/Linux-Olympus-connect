import subprocess

import pytest

from olympus_connect.core.errors import NetworkUnavailable
from olympus_connect.core.network import CameraNetwork, CommandResult

DEVICES_WITH_WIFI = "wlan0:wifi:connected\nenp0s31f6:ethernet:unavailable\n"
DEVICES_NO_WIFI = "enp0s31f6:ethernet:connected\n"

# Output reale di `nmcli -t -f GENERAL.CONNECTION,GENERAL.STATE device show wlan0`
CONNECTED_TO = "GENERAL.CONNECTION:{name}\nGENERAL.STATE:100 (connected)\n"
DISCONNECTED = "GENERAL.CONNECTION:\nGENERAL.STATE:30 (disconnected)\n"

# Output reale di `ip -o route get 192.168.0.10`, misurato sul sistema.
ONLINK = "192.168.0.10 dev wlan0 src 192.168.0.3 uid 1000 \\    cache\n"
VIA_GATEWAY = ("192.168.0.10 via 192.168.1.254 dev wlan0 "
               "src 192.168.1.74 uid 1000 \\    cache\n")


def fake_runner(responses: dict[str, CommandResult]):
    """Runner che risponde in base a una sottostringa dell'argv."""
    calls: list[list[str]] = []

    def run(argv):
        calls.append(argv)
        joined = " ".join(argv)
        for key, result in responses.items():
            if key in joined:
                return result
        return CommandResult(1, "", f"comando non atteso: {joined}")

    run.calls = calls
    return run


def ok(stdout=""):
    return CommandResult(0, stdout, "")


def runner_for(*, devices=DEVICES_WITH_WIFI, connection="OM-D E-M10 IV",
               route=ONLINK, extra=None):
    responses = {
        "-f DEVICE,TYPE,STATE": ok(devices),
        "device show": ok(CONNECTED_TO.format(name=connection)
                          if connection else DISCONNECTED),
        "route get": ok(route),
    }
    responses.update(extra or {})
    return fake_runner(responses)


# --- lettura dello stato ---------------------------------------------------

def test_active_connection_reads_device_show():
    runner = runner_for(connection="OM-D E-M10 IV")
    assert CameraNetwork(runner=runner).active_connection("wlan0") == "OM-D E-M10 IV"


def test_active_connection_is_none_when_device_disconnected():
    runner = runner_for(connection=None)
    assert CameraNetwork(runner=runner).active_connection("wlan0") is None


def test_wifi_devices_returns_device_names_only():
    runner = runner_for()
    assert CameraNetwork(runner=runner).wifi_devices() == ["wlan0"]


def test_is_onlink_true_for_direct_route():
    net = CameraNetwork(runner=runner_for(route=ONLINK))
    onlink, detail = net.is_onlink()
    assert onlink
    assert "192.168.0.10" in detail


def test_is_onlink_false_when_route_goes_through_gateway():
    # Il difetto che questo test blocca: `ip route get` riesce sempre grazie
    # alla default route, quindi il solo codice di ritorno segnalerebbe OK
    # mentre il PC è ancora sul Wi-Fi di casa.
    net = CameraNetwork(runner=runner_for(route=VIA_GATEWAY))
    onlink, detail = net.is_onlink()
    assert not onlink
    assert "192.168.1.254" in detail


def test_is_onlink_false_when_route_command_fails():
    runner = fake_runner({"route get": CommandResult(2, "", "network unreachable")})
    onlink, detail = CameraNetwork(runner=runner).is_onlink()
    assert not onlink
    assert "unreachable" in detail


# --- scala diagnostica -----------------------------------------------------

def test_diagnose_all_green():
    net = CameraNetwork(ssid="OM-D E-M10 IV", runner=runner_for(),
                        probe_tcp=lambda: True)
    diagnosis = net.diagnose()
    assert diagnosis.ok
    assert diagnosis.problem is None


def test_diagnose_reports_missing_wifi_device_first():
    net = CameraNetwork(runner=runner_for(devices=DEVICES_NO_WIFI),
                        probe_tcp=lambda: False)
    diagnosis = net.diagnose()
    assert not diagnosis.ok
    assert diagnosis.problem == "dispositivo Wi-Fi"


def test_diagnose_reports_wrong_network_and_names_it():
    net = CameraNetwork(ssid="OM-D E-M10 IV",
                        runner=runner_for(connection="casa-wifi",
                                          route=VIA_GATEWAY),
                        probe_tcp=lambda: False)
    diagnosis = net.diagnose()
    assert diagnosis.problem == "rete della fotocamera"
    detail = {r.name: r.detail for r in diagnosis.rungs}["rete della fotocamera"]
    assert "casa-wifi" in detail   # deve dire a quale rete si è collegati


def test_diagnose_accepts_connection_via_our_own_profile():
    # Collegandosi col nostro profilo il nome attivo è olympus-connect-<ssid>,
    # non l'SSID: non deve essere confuso con «rete sbagliata».
    net = CameraNetwork(ssid="CAM",
                        runner=runner_for(connection="olympus-connect-CAM"),
                        probe_tcp=lambda: True)
    assert net.diagnose().ok


def test_diagnose_reports_route_not_onlink_before_tcp():
    net = CameraNetwork(ssid="OM-D E-M10 IV",
                        runner=runner_for(route=VIA_GATEWAY),
                        probe_tcp=lambda: False)
    assert net.diagnose().problem == "fotocamera sulla rete locale"


def test_diagnose_reports_tcp_failure_when_onlink():
    net = CameraNetwork(ssid="OM-D E-M10 IV", runner=runner_for(),
                        probe_tcp=lambda: False)
    assert net.diagnose().problem == "porta 80 della fotocamera"


def test_diagnose_skips_ssid_rung_when_ssid_unknown():
    # Senza SSID configurato non si può verificare di essere sulla rete giusta;
    # il gradino non va inventato, va omesso.
    net = CameraNetwork(ssid="", runner=runner_for(connection="qualcosa"),
                        probe_tcp=lambda: True)
    names = [r.name for r in net.diagnose().rungs]
    assert "rete della fotocamera" not in names


def test_diagnose_stops_at_first_failing_rung():
    # Non deve interrogare TCP se non è nemmeno on-link.
    probed = []
    net = CameraNetwork(ssid="CAM",
                        runner=runner_for(connection="CAM", route=VIA_GATEWAY),
                        probe_tcp=lambda: probed.append(True) or True)
    net.diagnose()
    assert probed == []


def test_diagnose_raises_network_unavailable_when_nmcli_missing():
    def missing(argv):
        raise FileNotFoundError("nmcli")

    with pytest.raises(NetworkUnavailable):
        CameraNetwork(runner=missing).diagnose()


def test_diagnose_raises_network_unavailable_when_nmcli_hangs():
    def hanging(argv):
        raise subprocess.TimeoutExpired(argv, 10)

    with pytest.raises(NetworkUnavailable):
        CameraNetwork(runner=hanging).diagnose()


# --- profilo NetworkManager ------------------------------------------------

def test_profile_name_is_namespaced():
    # Il profilo deve essere riconoscibile come nostro: non tocchiamo
    # e non sovrascriviamo mai le connessioni esistenti dell'utente.
    assert CameraNetwork(ssid="OM-D E-M10 IV").profile_name == \
        "olympus-connect-OM-D E-M10 IV"


def test_profile_exists_checks_nmcli_connection_list():
    runner = fake_runner({"-f NAME connection show": ok(
        "casa-wifi\nolympus-connect-OM-D E-M10 IV\n")})
    assert CameraNetwork(ssid="OM-D E-M10 IV", runner=runner).profile_exists()


def test_profile_exists_false_when_absent():
    runner = fake_runner({"-f NAME connection show": ok("casa-wifi\n")})
    assert not CameraNetwork(ssid="OM-D E-M10 IV", runner=runner).profile_exists()


def test_ensure_profile_creates_profile_with_never_default():
    runner = fake_runner({
        "-f NAME connection show": ok("casa-wifi\n"),
        "connection add": ok(),
    })
    CameraNetwork(ssid="CAM", runner=runner).ensure_profile(password="secret123")

    added = next(c for c in runner.calls if "add" in c)
    joined = " ".join(added)
    # never-default è ciò che impedisce alla fotocamera di diventare la
    # default route.
    assert "ipv4.never-default" in joined
    assert "connection.autoconnect" in joined
    assert "ipv4.route-metric" in joined
    assert "ipv6.method" in joined
    assert "802-11-wireless.ssid" in joined
    assert "CAM" in added


def test_ensure_profile_passes_password_as_psk():
    runner = fake_runner({
        "-f NAME connection show": ok("\n"),
        "connection add": ok(),
    })
    CameraNetwork(ssid="CAM", runner=runner).ensure_profile(password="secret123")
    joined = " ".join(" ".join(c) for c in runner.calls)
    assert "wifi-sec.key-mgmt" in joined
    assert "secret123" in joined


def test_ensure_profile_realigns_existing_profile_without_recreating():
    runner = fake_runner({
        "-f NAME connection show": ok("olympus-connect-CAM\n"),
        "connection modify": ok(),
    })
    CameraNetwork(ssid="CAM", runner=runner).ensure_profile()
    assert not any("add" in c for c in runner.calls)
    modified = next(c for c in runner.calls if "modify" in c)
    assert "ipv4.never-default" in " ".join(modified)


def test_ensure_profile_updates_password_on_existing_profile():
    runner = fake_runner({
        "-f NAME connection show": ok("olympus-connect-CAM\n"),
        "connection modify": ok(),
    })
    CameraNetwork(ssid="CAM", runner=runner).ensure_profile(password="nuova")
    assert any("nuova" in c for c in runner.calls)


def test_ensure_profile_without_password_and_without_profile_raises():
    runner = fake_runner({"-f NAME connection show": ok("casa-wifi\n")})
    with pytest.raises(NetworkUnavailable, match="password"):
        CameraNetwork(ssid="CAM", runner=runner).ensure_profile()


def test_ensure_profile_without_ssid_raises():
    with pytest.raises(NetworkUnavailable, match="SSID"):
        CameraNetwork(ssid="", runner=runner_for()).ensure_profile(password="x")


def test_ensure_profile_never_touches_other_profiles():
    # Proprietà di sicurezza: ogni comando che modifica qualcosa deve
    # nominare il nostro profilo e nessun altro.
    runner = fake_runner({
        "-f NAME connection show": ok("casa-wifi\nlavoro-vpn\n"),
        "connection add": ok(),
    })
    CameraNetwork(ssid="CAM", runner=runner).ensure_profile(password="x")

    for call in runner.calls:
        if any(verb in call for verb in ("add", "modify", "delete", "up", "down")):
            assert "olympus-connect-CAM" in call
            assert "casa-wifi" not in call
            assert "lavoro-vpn" not in call


def test_connect_brings_profile_up():
    runner = fake_runner({"connection up": ok()})
    CameraNetwork(ssid="CAM", runner=runner).connect()
    assert runner.calls[-1] == ["nmcli", "connection", "up", "olympus-connect-CAM"]


def test_connect_raises_when_nmcli_fails():
    runner = fake_runner({"connection up": CommandResult(4, "", "no such device")})
    with pytest.raises(NetworkUnavailable, match="no such device"):
        CameraNetwork(ssid="CAM", runner=runner).connect()


def test_disconnect_brings_profile_down():
    runner = fake_runner({"connection down": ok()})
    CameraNetwork(ssid="CAM", runner=runner).disconnect()
    assert runner.calls[-1] == ["nmcli", "connection", "down", "olympus-connect-CAM"]


# --- scansione delle reti --------------------------------------------------

# Output reale di `nmcli -t -f SIGNAL,SSID device wifi list`, che include
# SSID vuoti (reti nascoste) e duplicati (più access point).
SCAN = (
    "87:casa-plus\n"
    "87:casa-5G\n"
    "77:casa-ospiti\n"
    "59:\n"
    "54:casa-wifi\n"
    "49:vicini-wifi\n"
    "45:casa-wifi\n"
)


def test_scan_ssids_orders_by_signal_strength():
    runner = fake_runner({"device wifi list": ok(SCAN)})
    found = CameraNetwork(runner=runner).scan_ssids()
    assert found[0] == "casa-plus"
    assert found.index("casa-wifi") < found.index("vicini-wifi")


def test_scan_ssids_drops_hidden_and_duplicates():
    runner = fake_runner({"device wifi list": ok(SCAN)})
    found = CameraNetwork(runner=runner).scan_ssids()
    assert "" not in found
    assert found.count("casa-wifi") == 1


def test_scan_ssids_unescapes_colons():
    # nmcli in modalità terse escapa i ':' dentro i valori.
    runner = fake_runner({"device wifi list": ok("70:rete\\:strana\n")})
    assert CameraNetwork(runner=runner).scan_ssids() == ["rete:strana"]


def test_scan_ssids_puts_camera_like_networks_first():
    # L'SSID della fotocamera è lungo e scomodo da digitare: va offerto per
    # primo anche se il segnale è più debole delle reti di casa.
    scan = "90:casa-wifi\n30:E-M10MKIV-P-XXXXXXXXX\n"
    runner = fake_runner({"device wifi list": ok(scan)})
    assert CameraNetwork(runner=runner).scan_ssids()[0] == "E-M10MKIV-P-XXXXXXXXX"


@pytest.mark.parametrize("ssid,expected", [
    ("E-M10MKIV-P-XXXXXXXXX", True),
    ("E-M1MarkIII-P-ABC123", True),
    ("E-PL10-P-XYZ", True),
    ("OM-1-P-123", True),
    ("TG-6-P-456", True),
    ("casa-wifi", False),
    ("vicini-wifi", False),
    ("", False),
])
def test_looks_like_camera(ssid, expected):
    from olympus_connect.core.network import looks_like_camera
    assert looks_like_camera(ssid) is expected


def test_scan_ssids_returns_empty_list_when_scan_fails():
    runner = fake_runner({"device wifi list": CommandResult(1, "", "no wifi")})
    # Una scansione fallita non deve impedire di inserire l'SSID a mano.
    assert CameraNetwork(runner=runner).scan_ssids() == []
