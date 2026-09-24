"""Eccezioni del core. Nessun messaggio tecnico crudo verso l'utente:
la GUI mappa questi tipi su testi in italiano."""


class OlympusError(Exception):
    """Base di tutti gli errori del core."""


class CameraNotReachable(OlympusError):
    """Nessuna rotta, connessione rifiutata, o la fotocamera non risponde."""


class CameraAsleep(OlympusError):
    """La fotocamera ha chiuso la connessione o l'access point a metà operazione."""


class ProtocolError(OlympusError):
    """Risposta inattesa dalla fotocamera."""

    def __init__(self, message: str, raw: bytes = b"") -> None:
        super().__init__(message)
        self.raw = raw


class IncompleteDownload(OlympusError):
    """Il trasferimento è finito con una dimensione diversa da quella attesa."""


class NetworkUnavailable(OlympusError):
    """nmcli assente o non utilizzabile: la gestione della rete è indisponibile."""


class WrongNetwork(OlympusError):
    """Il PC è associato a una rete Wi-Fi che non è quella della fotocamera."""
