#!/usr/bin/env bash
# Rende Olympus Connect avviabile dal menu delle applicazioni.
#
# Installa solo nella home dell'utente: nessun sudo, nessuna modifica di
# sistema. Per disinstallare basta ./install-desktop.sh --rimuovi
#
# Va rieseguito se sposti la cartella del progetto: il file .desktop
# contiene il percorso assoluto del venv.

set -euo pipefail

PROGETTO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RISORSE="$PROGETTO/olympus_connect/resources"
NOME="olympus-connect"

APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONE="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
BIN="$HOME/.local/bin"

rimuovi() {
    rm -fv "$APPS/$NOME.desktop" "$BIN/$NOME"
    find "$ICONE" -name "$NOME.*" -print -delete 2>/dev/null || true
    aggiorna_cache
    echo "Rimosso."
}

aggiorna_cache() {
    command -v update-desktop-database >/dev/null &&
        update-desktop-database "$APPS" 2>/dev/null || true
    command -v gtk-update-icon-cache >/dev/null &&
        gtk-update-icon-cache -qtf "$ICONE" 2>/dev/null || true
}

if [[ "${1:-}" == "--rimuovi" ]]; then
    rimuovi
    exit 0
fi

# -- venv -----------------------------------------------------------------

PYTHON="$PROGETTO/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
    echo "Errore: manca $PYTHON" >&2
    echo "Crea prima il venv:" >&2
    echo "  python3 -m venv --system-site-packages .venv" >&2
    echo "  .venv/bin/pip install -e ." >&2
    exit 1
fi

# -- icone ----------------------------------------------------------------
#
# Due varianti: sotto i 48 px quella dettagliata diventa illeggibile, perche'
# le tre onde Wi-Fi si fondono in una macchia. La variante semplificata ha
# due onde e colori piatti, quindi resta nitida fino a 16 px.

if command -v rsvg-convert >/dev/null; then
    for dim in 16 22 24 32; do
        cartella="$ICONE/${dim}x${dim}/apps"
        mkdir -p "$cartella"
        rsvg-convert -w "$dim" -h "$dim" \
            "$RISORSE/$NOME-small.svg" -o "$cartella/$NOME.png"
    done
    for dim in 48 64 128 256 512; do
        cartella="$ICONE/${dim}x${dim}/apps"
        mkdir -p "$cartella"
        rsvg-convert -w "$dim" -h "$dim" \
            "$RISORSE/$NOME.svg" -o "$cartella/$NOME.png"
    done
    echo "Icone PNG installate: 16, 22, 24, 32 (semplificate), 48-512 (dettagliate)"
else
    echo "rsvg-convert assente: installo solo l'SVG scalabile" >&2
fi

mkdir -p "$ICONE/scalable/apps"
install -m 644 "$RISORSE/$NOME.svg" "$ICONE/scalable/apps/$NOME.svg"

# -- comando da terminale -------------------------------------------------

mkdir -p "$BIN"
cat > "$BIN/$NOME" <<EOF
#!/usr/bin/env bash
# Generato da install-desktop.sh — non modificare a mano.
exec "$PYTHON" -m olympus_connect "\$@"
EOF
chmod +x "$BIN/$NOME"

# -- voce di menu ---------------------------------------------------------

mkdir -p "$APPS"
cat > "$APPS/$NOME.desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Olympus Connect
GenericName=Scarico foto dalla fotocamera
GenericName[en]=Camera photo downloader
Comment=Scarica foto e video da una fotocamera Olympus via Wi-Fi
Comment[en]=Download photos and videos from an Olympus camera over Wi-Fi
Exec=$BIN/$NOME
Icon=$NOME
Terminal=false
Categories=Graphics;Photography;
Keywords=olympus;fotocamera;camera;foto;photo;wifi;raw;orf;scarica;download;
StartupNotify=true
StartupWMClass=olympus-connect
EOF
chmod 644 "$APPS/$NOME.desktop"

aggiorna_cache

echo
echo "Installato."
echo "  menu      : Grafica / Fotografia -> Olympus Connect"
echo "  terminale : $NOME"
echo
if ! printf '%s' ":$PATH:" | grep -q ":$BIN:"; then
    echo "Nota: $BIN non è nel PATH, quindi il comando da terminale non"
    echo "      sarà raggiungibile per nome. La voce di menu funziona comunque."
fi
