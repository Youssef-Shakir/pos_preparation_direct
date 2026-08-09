import socket
import logging

_logger = logging.getLogger(__name__)

# ESC/POS Command Constants
ESC = b'\x1b'
GS = b'\x1d'

# Initialize printer
INIT = ESC + b'@'

# Text formatting
BOLD_ON = ESC + b'E\x01'
BOLD_OFF = ESC + b'E\x00'
DOUBLE_HEIGHT_ON = GS + b'!\x10'
DOUBLE_WIDTH_ON = GS + b'!\x20'
DOUBLE_SIZE_ON = GS + b'!\x30'
NORMAL_SIZE = GS + b'!\x00'

# Alignment
ALIGN_LEFT = ESC + b'a\x00'
ALIGN_CENTER = ESC + b'a\x01'
ALIGN_RIGHT = ESC + b'a\x02'

# Line feeds
LF = b'\n'
FEED_3 = ESC + b'd\x03'

# Cut paper
PARTIAL_CUT = GS + b'V\x01'
FULL_CUT = GS + b'V\x00'

# Beep
BEEP = ESC + b'\x07'

# Codepage selection
CODEPAGE_PC437 = ESC + b't\x00'
CODEPAGE_PC850 = ESC + b't\x02'
CODEPAGE_PC860 = ESC + b't\x03'
CODEPAGE_PC1252 = ESC + b't\x10'
CODEPAGE_PC864 = ESC + b't\x15'  # Arabic DOS
CODEPAGE_PC1256 = ESC + b't\x28'  # Arabic Windows

CODEPAGE_MAP = {
    'cp437': CODEPAGE_PC437,
    'cp850': CODEPAGE_PC850,
    'cp860': CODEPAGE_PC860,
    'cp1252': CODEPAGE_PC1252,
    'cp864': CODEPAGE_PC864,
    'cp1256': CODEPAGE_PC1256,
    'utf8': None,  # UTF-8 printers don't need codepage command
}


def encode_text(text, codepage='cp437'):
    """Encode text to bytes using the specified codepage."""
    try:
        if codepage == 'utf8':
            return text.encode('utf-8', errors='replace')
        return text.encode(codepage, errors='replace')
    except LookupError:
        # Unknown codepage, try UTF-8 then ASCII
        try:
            return text.encode('utf-8', errors='replace')
        except Exception:
            return text.encode('ascii', errors='replace')
    except Exception:
        return text.encode('ascii', errors='replace')


def build_separator(char='-', width=42):
    """Build a separator line."""
    return encode_text(char * width) + LF


def build_preparation_ticket(data, printer_settings):
    """
    Build ESC/POS byte sequence for a preparation ticket.

    Args:
        data: dict with keys:
            - table_name: str
            - floor_name: str
            - waiter_name: str
            - order_name: str
            - customer_note: str
            - lines: list of dict with product_name, qty, note
        printer_settings: dict with keys:
            - codepage: str (cp437, cp850, etc.)
            - paper_width: int (32, 42, 48)
            - auto_cut: bool
            - beep: bool

    Returns:
        bytes: ESC/POS command sequence
    """
    codepage = printer_settings.get('codepage', 'cp437')
    width = printer_settings.get('paper_width', 42)
    auto_cut = printer_settings.get('auto_cut', True)
    beep = printer_settings.get('beep', True)

    output = bytearray()

    # Initialize printer
    output.extend(INIT)

    # Set codepage
    if codepage in CODEPAGE_MAP and CODEPAGE_MAP[codepage] is not None:
        output.extend(CODEPAGE_MAP[codepage])

    # Header - Table/Floor info (large, bold, centered)
    output.extend(ALIGN_CENTER)
    output.extend(DOUBLE_SIZE_ON)
    output.extend(BOLD_ON)

    table_name = data.get('table_name', '')
    floor_name = data.get('floor_name', '')

    if table_name:
        if floor_name:
            header = f"{floor_name} - TABLE {table_name}"
        else:
            header = f"TABLE {table_name}"
    else:
        header = "TAKEAWAY"

    output.extend(encode_text(header, codepage))
    output.extend(LF)
    output.extend(NORMAL_SIZE)
    output.extend(BOLD_OFF)

    # Separator
    output.extend(build_separator('=', width))

    # Order info (left aligned)
    output.extend(ALIGN_LEFT)

    order_name = data.get('order_name', '')
    if order_name:
        output.extend(encode_text(f"Order: {order_name}", codepage))
        output.extend(LF)

    waiter_name = data.get('waiter_name', '')
    if waiter_name:
        output.extend(encode_text(f"Waiter: {waiter_name}", codepage))
        output.extend(LF)

    # Timestamp
    from datetime import datetime
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    output.extend(encode_text(f"Time: {timestamp}", codepage))
    output.extend(LF)

    # Separator
    output.extend(build_separator('-', width))

    # Order lines (double height, bold)
    output.extend(DOUBLE_HEIGHT_ON)
    output.extend(BOLD_ON)

    lines = data.get('lines', [])
    for line in lines:
        qty = line.get('qty', 1)
        product_name = line.get('product_name', 'Unknown')

        # Format: "2x Product Name"
        line_text = f"{int(qty)}x {product_name}"
        output.extend(encode_text(line_text, codepage))
        output.extend(LF)

        # Line note (smaller, indented)
        note = line.get('note', '')
        if note:
            output.extend(NORMAL_SIZE)
            output.extend(encode_text(f"   >> {note}", codepage))
            output.extend(LF)
            output.extend(DOUBLE_HEIGHT_ON)

    output.extend(NORMAL_SIZE)
    output.extend(BOLD_OFF)

    # Customer note
    customer_note = data.get('customer_note', '')
    if customer_note:
        output.extend(build_separator('-', width))
        output.extend(BOLD_ON)
        output.extend(encode_text("NOTE:", codepage))
        output.extend(LF)
        output.extend(BOLD_OFF)
        output.extend(encode_text(customer_note, codepage))
        output.extend(LF)

    # Footer
    output.extend(build_separator('=', width))
    output.extend(ALIGN_CENTER)
    output.extend(encode_text("** PREPARATION TICKET **", codepage))
    output.extend(LF)

    # Custom footer text
    footer_text = printer_settings.get('footer_text', '')
    if footer_text:
        output.extend(encode_text(footer_text, codepage))
        output.extend(LF)

    output.extend(ALIGN_LEFT)

    # Beep
    if beep:
        output.extend(BEEP)
        output.extend(BEEP)

    # Feed and cut
    output.extend(FEED_3)
    if auto_cut:
        output.extend(PARTIAL_CUT)

    return bytes(output)


def send_to_printer(ip, port, data, timeout=5):
    """
    Send raw ESC/POS data to a network printer.

    Args:
        ip: Printer IP address
        port: Printer port (usually 9100)
        data: bytes to send
        timeout: Connection timeout in seconds

    Returns:
        tuple: (success: bool, error_message: str or None)
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((ip, port))
        sock.sendall(data)
        sock.close()
        return True, None
    except socket.timeout:
        return False, f"Connection timeout to {ip}:{port}"
    except ConnectionRefusedError:
        return False, f"Connection refused by {ip}:{port}"
    except socket.error as e:
        return False, f"Socket error: {str(e)}"
    except Exception as e:
        return False, f"Error: {str(e)}"
