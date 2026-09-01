#!/usr/bin/env python3
"""
Extended Arabic Printer Test - tries many more codepage variations
"""

import socket
import sys
import time

ESC = b'\x1b'
GS = b'\x1d'
INIT = ESC + b'@'
LF = b'\n'
PARTIAL_CUT = GS + b'V\x01'
FEED_3 = ESC + b'd\x03'

ARABIC_TEXT = "شاورما دجاج"

# Many possible Arabic codepage commands for different printer brands
CODEPAGE_TESTS = [
    # Standard
    ("CP864 (0x15)", ESC + b't\x15', 'cp864'),
    ("CP1256 (0x28)", ESC + b't\x28', 'cp1256'),
    ("CP720 (0x20)", ESC + b't\x20', 'cp864'),

    # Alternative codepage numbers (some printers use these)
    ("Arabic (0x16)", ESC + b't\x16', 'cp1256'),
    ("Arabic (0x17)", ESC + b't\x17', 'cp1256'),
    ("Arabic (0x25)", ESC + b't\x25', 'cp1256'),
    ("Arabic (0x26)", ESC + b't\x26', 'cp1256'),
    ("Arabic (0x29)", ESC + b't\x29', 'cp1256'),
    ("Arabic (0x2A)", ESC + b't\x2a', 'cp1256'),
    ("Arabic (0x40)", ESC + b't\x40', 'cp1256'),
    ("Arabic (0x41)", ESC + b't\x41', 'cp1256'),
    ("Arabic (0xFF)", ESC + b't\xff', 'cp1256'),

    # UTF-8 modes (some printers)
    ("UTF8 Mode 1", ESC + b't\x0f', 'utf-8'),
    ("UTF8 Mode 2", ESC + b't\x1b', 'utf-8'),

    # No codepage command - raw UTF-8
    ("Raw UTF-8", None, 'utf-8'),

    # GS ( E command for UTF-8 (some Epson printers)
    ("Epson UTF8", GS + b'(E' + b'\x03\x00\x48\x80\x00', 'utf-8'),
]


def send_to_printer(ip, port, data, timeout=5):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((ip, port))
        sock.sendall(data)
        sock.close()
        return True
    except Exception as e:
        print(f"Error: {e}")
        return False


def build_test(name, cp_cmd, encoding):
    output = bytearray()
    output.extend(INIT)

    if cp_cmd:
        output.extend(cp_cmd)

    # Label
    output.extend(f"Test: {name}\n".encode('ascii', errors='replace'))
    output.extend(b'-' * 20 + LF)

    # Arabic text
    try:
        if encoding == 'utf-8':
            output.extend(ARABIC_TEXT.encode('utf-8'))
        else:
            output.extend(ARABIC_TEXT.encode(encoding, errors='replace'))
    except:
        output.extend(ARABIC_TEXT.encode('utf-8', errors='replace'))

    output.extend(LF)
    output.extend(b'=' * 20 + LF)
    output.extend(FEED_3)
    output.extend(PARTIAL_CUT)

    return bytes(output)


def main():
    ip = sys.argv[1] if len(sys.argv) > 1 else '192.168.68.123'
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 9100

    print(f"Testing printer at {ip}:{port}")
    print(f"Sending {len(CODEPAGE_TESTS)} test tickets...\n")
    print(f"Arabic text being tested: {ARABIC_TEXT}")
    print()

    for name, cmd, enc in CODEPAGE_TESTS:
        print(f"  {name}...", end=' ', flush=True)
        ticket = build_test(name, cmd, enc)
        if send_to_printer(ip, port, ticket):
            print("✓")
        else:
            print("✗")
        time.sleep(1.5)

    print("\n" + "="*50)
    print("Check all tickets - note which one shows Arabic correctly!")
    print("="*50)


if __name__ == '__main__':
    main()
