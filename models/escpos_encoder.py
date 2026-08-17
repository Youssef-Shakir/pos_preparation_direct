import socket
import logging
import os
from datetime import datetime

_logger = logging.getLogger(__name__)

# ── Optional dependencies ─────────────────────────────────────────────────────
try:
    from PIL import Image, ImageDraw, ImageFont
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False
    _logger.warning("Pillow not installed — Arabic graphical printing disabled. "
                    "Run: pip install Pillow")

try:
    import arabic_reshaper
    _RESHAPER_AVAILABLE = True
except ImportError:
    _RESHAPER_AVAILABLE = False
    _logger.warning("arabic_reshaper not installed — Arabic text will be corrupted. "
                    "Run: pip install arabic-reshaper")

_GRAPHIC_MODE = _PIL_AVAILABLE and _RESHAPER_AVAILABLE

# ── Font discovery ────────────────────────────────────────────────────────────
_FONT_CANDIDATES = [
    '/mnt/c/Windows/Fonts/arial.ttf',        # WSL (lowercase)
    '/mnt/c/Windows/Fonts/Arial.ttf',        # WSL (capitalised)
    '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
    '/usr/share/fonts/truetype/freefont/FreeSans.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
]

_FONT_PATH = None
if _PIL_AVAILABLE:
    for _fp in _FONT_CANDIDATES:
        if os.path.isfile(_fp):
            _FONT_PATH = _fp
            break
    if not _FONT_PATH:
        _logger.warning("No Arabic-capable TrueType font found — "
                        "graphical Arabic printing disabled.")
        _GRAPHIC_MODE = False

# ── ESC/POS constants ─────────────────────────────────────────────────────────
ESC = b'\x1b'
GS  = b'\x1d'

INIT        = ESC + b'@'
LF          = b'\n'
FEED_3      = ESC + b'd\x03'
PARTIAL_CUT = GS  + b'V\x01'
FULL_CUT    = GS  + b'V\x00'
BEEP        = ESC + b'\x07'


# ── Graphical helpers ─────────────────────────────────────────────────────────

def _shape(text):
    """Reshape Arabic text into correct contextual letter forms."""
    if _RESHAPER_AVAILABLE and _has_arabic(text):
        return arabic_reshaper.reshape(text)
    return text


def _has_arabic(text):
    return any('؀' <= ch <= 'ۿ' for ch in text)


def _make_img(text, size):
    """Render *text* into a compact 1-bit PIL image at *size* pt."""
    font = ImageFont.truetype(_FONT_PATH, size)
    tmp  = Image.new('RGB', (4000, 400), 'white')
    bbox = ImageDraw.Draw(tmp).textbbox((0, 0), text, font=font)
    w = max(bbox[2] - bbox[0] + 8, 1)
    h = max(bbox[3] - bbox[1] + 12, 1)
    img = Image.new('1', (w, h), 1)
    ImageDraw.Draw(img).text((-bbox[0] + 4, -bbox[1] + 5), text, font=font, fill=0)
    return img


def _make_item_img(qty, name, size):
    """Render qty and Arabic name as a single bitmap (no mixed text/image gaps)."""
    font     = ImageFont.truetype(_FONT_PATH, size)
    qty_str  = f'{int(qty)}x  '
    ara_str  = _shape(name)

    tmp  = Image.new('RGB', (4000, 400), 'white')
    draw = ImageDraw.Draw(tmp)
    q_bb = draw.textbbox((0, 0), qty_str, font=font)
    a_bb = draw.textbbox((0, 0), ara_str, font=font)

    q_w = q_bb[2] - q_bb[0]
    h   = max(q_bb[3] - q_bb[1], a_bb[3] - a_bb[1]) + 12
    w   = q_w + (a_bb[2] - a_bb[0]) + 16

    img  = Image.new('1', (w, h), 1)
    draw = ImageDraw.Draw(img)
    draw.text((-q_bb[0] + 4,        -q_bb[1] + 5), qty_str, font=font, fill=0)
    draw.text((q_w + 8 - a_bb[0],   -a_bb[1] + 5), ara_str, font=font, fill=0)
    return img


def _img_to_escstar(img):
    """
    Convert a PIL image to ESC * (24-dot column bitmap) bytes.

    Pre-flip + RTL column order: the two horizontal flips cancel so each glyph
    prints in its correct orientation while the column send order preserves the
    visual layout produced by PIL.
    """
    img    = img.transpose(Image.FLIP_LEFT_RIGHT)   # pre-flip
    w, h   = img.size
    pixels = img.load()
    out    = bytearray(ESC + b'\x33\x18')           # line spacing = 24 dots

    for band in range((h + 23) // 24):
        r = band * 24
        out += ESC + b'*\x21' + bytes([w & 0xFF, (w >> 8) & 0xFF])

        for x in range(w - 1, -1, -1):              # RTL column order
            b1 = b2 = b3 = 0
            for bit in range(8):
                if r + bit      < h and pixels[x, r + bit]      == 0:
                    b1 |= 0x80 >> bit
                if r + 8 + bit  < h and pixels[x, r + 8 + bit]  == 0:
                    b2 |= 0x80 >> bit
                if r + 16 + bit < h and pixels[x, r + 16 + bit] == 0:
                    b3 |= 0x80 >> bit
            out += bytes([b1, b2, b3])

        out += LF

    out += ESC + b'\x32'                            # restore default line spacing
    return bytes(out)


def _render_text(text, size):
    """Return ESC * bytes for *text* shaped and rendered at *size* pt."""
    return _img_to_escstar(_make_img(_shape(text), size))


def _render_item(qty, name, size):
    """Return ESC * bytes for a qty + Arabic-name item line."""
    return _img_to_escstar(_make_item_img(qty, name, size))


def _sep(char='-', n=32):
    return (char * n + '\n').encode('ascii')


# ── Text-mode fallback (non-graphical printers) ───────────────────────────────

def _encode_text_fallback(text, codepage):
    """Best-effort byte encoding when PIL is unavailable."""
    try:
        return text.encode(codepage, errors='replace')
    except (LookupError, Exception):
        return text.encode('utf-8', errors='replace')


# ── Public API ────────────────────────────────────────────────────────────────

def build_preparation_ticket(data, printer_settings):
    """
    Build an ESC/POS byte sequence for a preparation ticket.

    data keys:
        table_name, floor_name, waiter_name, order_name,
        customer_note, lines (list of {product_name, qty, note})

    printer_settings keys:
        codepage (str), auto_cut (bool), beep (bool), footer_text (str),
        font_size_header (int, default 36), font_size_item (int, default 30),
        font_size_small (int, default 24)
    """
    codepage   = printer_settings.get('codepage', 'cp437')
    auto_cut   = printer_settings.get('auto_cut', True)
    beep       = printer_settings.get('beep', True)
    footer_txt = printer_settings.get('footer_text', '')
    sz_header  = printer_settings.get('font_size_header', 36)
    sz_item    = printer_settings.get('font_size_item', 30)
    sz_small   = printer_settings.get('font_size_small', 24)

    out = bytearray(INIT)

    def rtext(text, size=sz_small):
        if _GRAPHIC_MODE:
            return _render_text(text, size)
        return _encode_text_fallback(text, codepage) + LF

    def ritem(qty, name, size=sz_item):
        if _GRAPHIC_MODE:
            return _render_item(qty, name, size)
        return _encode_text_fallback(f'{int(qty)}x {name}', codepage) + LF

    # ── Header ────────────────────────────────────────────────────────────────
    table_name = data.get('table_name', '')
    floor_name = data.get('floor_name', '')
    if table_name:
        header = f"{floor_name} - TABLE {table_name}" if floor_name else f"TABLE {table_name}"
    else:
        header = "TAKEAWAY"

    out += rtext(header, sz_header)
    out += _sep('=')

    # ── Order meta ────────────────────────────────────────────────────────────
    order_name = data.get('order_name', '')
    if order_name:
        out += rtext(f"Order: {order_name}", sz_small)

    waiter_name = data.get('waiter_name', '')
    if waiter_name:
        out += rtext(f"Waiter: {waiter_name}", sz_small)

    out += rtext(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}", sz_small)
    out += _sep('-')

    # ── Item lines ────────────────────────────────────────────────────────────
    for line in data.get('lines', []):
        qty  = line.get('qty', 1)
        name = line.get('product_name', 'Unknown')
        out += ritem(qty, name, sz_item)

        note = line.get('note', '')
        if note:
            out += rtext(f"   >> {note}", sz_small)

    # ── Customer note ─────────────────────────────────────────────────────────
    customer_note = data.get('customer_note', '')
    if customer_note:
        out += _sep('-')
        out += rtext("NOTE:", sz_small)
        out += rtext(customer_note, sz_small)

    # ── Footer ────────────────────────────────────────────────────────────────
    out += _sep('=')
    out += rtext("** PREPARATION TICKET **", sz_small)
    if footer_txt:
        out += rtext(footer_txt, sz_small)

    if beep:
        out += BEEP + BEEP

    out += FEED_3
    if auto_cut:
        out += PARTIAL_CUT

    return bytes(out)


def send_to_printer(ip, port, data, timeout=5):
    """
    Send raw ESC/POS bytes to a network printer over TCP port 9100.

    Returns:
        (success: bool, error_message: str | None)
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((ip, int(port)))
        sock.sendall(data)
        sock.close()
        return True, None
    except socket.timeout:
        return False, f"Connection timeout to {ip}:{port}"
    except ConnectionRefusedError:
        return False, f"Connection refused by {ip}:{port}"
    except OSError as e:
        return False, f"Socket error: {e}"
