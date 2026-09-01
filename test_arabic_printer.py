#!/usr/bin/env python3
"""
Arabic Printer Encoding Test Script
Tests different codepages to find which works best for Arabic text on your printer.

Usage: python3 test_arabic_printer.py [IP] [PORT]
Default: 192.168.68.123:9100
"""

import socket
import sys
import time

# ESC/POS Commands
ESC = b'\x1b'
GS = b'\x1d'
INIT = ESC + b'@'
LF = b'\n'
BOLD_ON = ESC + b'E\x01'
BOLD_OFF = ESC + b'E\x00'
ALIGN_CENTER = ESC + b'a\x01'
ALIGN_LEFT = ESC + b'a\x00'
DOUBLE_HEIGHT = GS + b'!\x10'
NORMAL_SIZE = GS + b'!\x00'
PARTIAL_CUT = GS + b'V\x01'
FEED_3 = ESC + b'd\x03'

# Codepage commands
CODEPAGES = {
    'CP437 (US)': ESC + b't\x00',
    'CP850 (Western)': ESC + b't\x02',
    'CP864 (Arabic DOS)': ESC + b't\x15',
    'CP1256 (Arabic Win)': ESC + b't\x28',
    'CP720 (Arabic)': ESC + b't\x20',
    'UTF-8 (No command)': None,
}

# Test texts
ARABIC_TEXT = "مرحبا بالعالم"  # "Hello World" in Arabic
ARABIC_PRODUCT = "شاورما دجاج"  # "Chicken Shawarma"
ARABIC_NUMBER = "الطاولة ٥"  # "Table 5"
MIXED_TEXT = "Order: طلب #123"

# Try to import Arabic reshaping libraries
try:
    import arabic_reshaper
    from bidi.algorithm import get_display
    ARABIC_LIBS = True
    print("✓ Arabic libraries found (arabic_reshaper, python-bidi)")
except ImportError:
    ARABIC_LIBS = False
    print("✗ Arabic libraries NOT found")
    print("  Install with: pip install arabic-reshaper python-bidi")
    print()


def reshape_arabic(text, use_reshape=True):
    """Reshape Arabic text for proper display."""
    if not ARABIC_LIBS:
        return text
    if use_reshape:
        reshaped = arabic_reshaper.reshape(text)
        return get_display(reshaped)
    else:
        # Just bidi reorder, no reshaping (for UTF-8 printers)
        return get_display(text)


def send_to_printer(ip, port, data, timeout=5):
    """Send data to printer."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((ip, port))
        sock.sendall(data)
        sock.close()
        return True, None
    except Exception as e:
        return False, str(e)


def build_test_ticket(codepage_name, codepage_cmd, encoding, use_reshape=True):
    """Build a test ticket for a specific codepage."""
    output = bytearray()

    # Initialize
    output.extend(INIT)

    # Set codepage if provided
    if codepage_cmd:
        output.extend(codepage_cmd)

    # Header
    output.extend(ALIGN_CENTER)
    output.extend(BOLD_ON)
    output.extend(b'=' * 32 + LF)
    output.extend(f'TEST: {codepage_name}'.encode('ascii', errors='replace') + LF)
    reshape_mode = 'RESHAPED' if use_reshape else 'BIDI ONLY'
    output.extend(f'Mode: {reshape_mode}'.encode('ascii', errors='replace') + LF)
    output.extend(b'=' * 32 + LF)
    output.extend(BOLD_OFF)
    output.extend(ALIGN_LEFT)
    output.extend(LF)

    # Test Arabic texts
    tests = [
        ("Hello World:", ARABIC_TEXT),
        ("Product:", ARABIC_PRODUCT),
        ("Table:", ARABIC_NUMBER),
        ("Mixed:", MIXED_TEXT),
    ]

    for label, arabic in tests:
        output.extend(label.encode('ascii') + LF)

        # Process Arabic
        processed = reshape_arabic(arabic, use_reshape) if ARABIC_LIBS else arabic

        # Encode based on codepage
        try:
            if encoding == 'utf-8':
                encoded = processed.encode('utf-8', errors='replace')
            else:
                encoded = processed.encode(encoding, errors='replace')
        except LookupError:
            encoded = processed.encode('utf-8', errors='replace')

        output.extend(b'  ')
        output.extend(encoded)
        output.extend(LF)
        output.extend(LF)

    # Footer
    output.extend(ALIGN_CENTER)
    output.extend(b'-' * 32 + LF)
    output.extend(b'Check which prints correctly!' + LF)
    output.extend(b'-' * 32 + LF)
    output.extend(ALIGN_LEFT)

    # Feed and cut
    output.extend(FEED_3)
    output.extend(PARTIAL_CUT)

    return bytes(output)


def main():
    # Get printer IP and port
    ip = sys.argv[1] if len(sys.argv) > 1 else '192.168.68.123'
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 9100

    print(f"\n{'='*50}")
    print(f"Arabic Printer Encoding Test")
    print(f"Printer: {ip}:{port}")
    print(f"{'='*50}\n")

    # Test connection first
    print("Testing connection...")
    test_data = INIT + b'Connection OK!\n' + FEED_3 + PARTIAL_CUT
    success, error = send_to_printer(ip, port, test_data)

    if not success:
        print(f"✗ Connection failed: {error}")
        print("\nMake sure:")
        print(f"  1. Printer is on and connected to network")
        print(f"  2. IP address {ip} is correct")
        print(f"  3. Port {port} is correct (usually 9100)")
        return

    print("✓ Connection successful!\n")
    time.sleep(1)

    # Test configurations
    test_configs = [
        ('CP864 (Arabic DOS)', CODEPAGES['CP864 (Arabic DOS)'], 'cp864', True),
        ('CP1256 (Arabic Win)', CODEPAGES['CP1256 (Arabic Win)'], 'cp1256', True),
        ('UTF-8 Reshaped', None, 'utf-8', True),
        ('UTF-8 Bidi Only', None, 'utf-8', False),
    ]

    print("Sending test tickets...\n")

    for name, cmd, enc, reshape in test_configs:
        print(f"  Printing: {name}...", end=' ')

        ticket = build_test_ticket(name, cmd, enc, reshape)
        success, error = send_to_printer(ip, port, ticket)

        if success:
            print("✓ Sent")
        else:
            print(f"✗ Failed: {error}")

        time.sleep(2)  # Wait between prints

    print(f"\n{'='*50}")
    print("Check your printed tickets!")
    print("The one with correct Arabic text is your best setting.")
    print(f"{'='*50}\n")

    print("Recommended Odoo settings based on results:")
    print("  - If 'CP864' works: Select 'PC864 (Arabic DOS)'")
    print("  - If 'CP1256' works: Select 'Windows-1256 (Arabic)'")
    print("  - If 'UTF-8' works: Select 'UTF-8 (Modern Printers)'")
    print()


if __name__ == '__main__':
    main()
