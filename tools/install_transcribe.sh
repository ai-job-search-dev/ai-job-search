#!/usr/bin/env bash
# Install the local `transcribe` command (faster-whisper speech-to-text) used by /outcome.
#
#   bash tools/install_transcribe.sh            # install or update
#   bash tools/install_transcribe.sh --check    # report whether it is installed, change nothing
#
# Installs into ~/.local/share/whisper/ (venv + script) and ~/.local/bin/transcribe (wrapper).
# Needs python3 with venv (Debian/Ubuntu: sudo apt install python3-venv). Linux, macOS and WSL.
# Downloads ~470 MB of Python packages now; the speech model (~460 MB for small.en) downloads
# to ~/.cache/huggingface on the first transcription. Everything runs offline after that.
set -euo pipefail

DEST="$HOME/.local/share/whisper"
BIN="$HOME/.local/bin"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/transcribe.py"

if [[ "${1:-}" == "--check" ]]; then
    if command -v transcribe >/dev/null && [[ -x "$DEST/venv/bin/python" ]]; then
        echo "transcribe: installed ($(command -v transcribe))"; exit 0
    fi
    echo "transcribe: not installed"; exit 1
fi

command -v python3 >/dev/null || { echo "error: python3 not found" >&2; exit 1; }
mkdir -p "$DEST" "$BIN"

if [[ ! -x "$DEST/venv/bin/python" ]]; then
    echo "Creating venv in $DEST/venv"
    python3 -m venv "$DEST/venv" || { echo "error: venv failed (Debian/Ubuntu: sudo apt install python3-venv)" >&2; exit 1; }
fi

# Pinned together: faster-whisper 1.2.1's built-in decoder breaks on av 19, which transcribe.py works around.
echo "Installing faster-whisper (this can take a few minutes)"
"$DEST/venv/bin/pip" install -q --upgrade pip
"$DEST/venv/bin/pip" install -q faster-whisper==1.2.1 av==19.0.2 ctranslate2==4.8.4 numpy==2.5.4

cp "$SRC" "$DEST/transcribe.py"
cat > "$BIN/transcribe" <<'WRAP'
#!/usr/bin/env bash
# Local speech-to-text with faster-whisper. Usage: transcribe <audio> [output.txt] [--model small.en]
exec ~/.local/share/whisper/venv/bin/python -I ~/.local/share/whisper/transcribe.py "$@"
WRAP
chmod +x "$BIN/transcribe"

echo "Installed: $BIN/transcribe"
case ":$PATH:" in
    *":$BIN:"*) ;;
    *) echo "note: $BIN is not on PATH - add 'export PATH=\"\$HOME/.local/bin:\$PATH\"' to your shell profile, or call $BIN/transcribe directly" ;;
esac
