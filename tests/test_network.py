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
                        runner=runner_for(connection="FASTWEB-6J936A",
                                          route=VIA_GATEWAY),
                        probe_tcp=lambda: False)
    diagnosis = net.diagnose()
    assert diagnosis.problem == "rete della fotocamera"
    detail = {r.name: r.detail for r in diagnosis.rungs}["rete della fotocamera"]
    assert "FASTWEB-6J936A" in detail   # deve dire a quale rete si è collegati


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
