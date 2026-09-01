#!/usr/bin/env python3
"""
Test different Arabic reshaping combinations to find the correct one.
"""

import socket
import sys

ESC = b'\x1b'
GS = b'\x1d'
INIT = ESC + b'@'
LF = b'\n'
FEED_3 = ESC + b'd\x03'
PARTIAL_CUT = GS + b'V\x01'

ARABIC_TEXT = "شاورما دجاج"
ARABIC_HELLO = "مرحبا"

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
    LIBS_OK = True
except ImportError:
    LIBS_OK = False
    print("ERROR: Install libraries first:")
    print("  pip install arabic-reshaper python-bidi")
    sys.exit(1)


def send_to_printer(ip, port, data):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((ip, port))
        sock.sendall(data)
        sock.close()
        return True
    except Exception as e:
        print(f"Error: {e}")
        return False


def test_combination(name, text):
    """Build test ticket for a specific combination."""
    output = bytearray()
    output.extend(INIT)
    output.extend(f"Test: {name}\n".encode('ascii', errors='replace'))
    output.extend(b'-' * 32 + LF)
    output.extend(text.encode('utf-8', errors='replace'))
    output.extend(LF)
    output.extend(b'=' * 32 + LF)
    output.extend(FEED_3)
    output.extend(PARTIAL_CUT)
    return bytes(output)


def main():
    ip = sys.argv[1] if len(sys.argv) > 1 else '192.168.68.123'
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 9100

    print(f"Testing Arabic reshaping combinations on {ip}:{port}")
    print(f"Original text: {ARABIC_TEXT}")
    print()

    # Different combinations to test
    tests = []

    # 1. Raw (no processing)
    tests.append(("1. Raw (no processing)", ARABIC_TEXT))

    # 2. Reshaped only (no bidi)
    reshaped = arabic_reshaper.reshape(ARABIC_TEXT)
    tests.append(("2. Reshaped only", reshaped))

    # 3. Bidi only (no reshape)
    bidi_only = get_display(ARABIC_TEXT)
    tests.append(("3. Bidi only", bidi_only))

    # 4. Reshape then Bidi (current)
    reshape_bidi = get_display(arabic_reshaper.reshape(ARABIC_TEXT))
    tests.append(("4. Reshape+Bidi", reshape_bidi))

    # 5. Bidi then Reshape
    bidi_reshape = arabic_reshaper.reshape(get_display(ARABIC_TEXT))
    tests.append(("5. Bidi+Reshape", bidi_reshape))

    # 6. Reshape only, reversed manually
    reshaped_rev = reshaped[::-1]
    tests.append(("6. Reshape+Reverse", reshaped_rev))

    # 7. Reshape with ligatures config
    configuration = {
        'delete_harakat': True,
        'support_ligatures': True,
        'RIAL SIGN': True,
    }
    reshaper = arabic_reshaper.ArabicReshaper(configuration=configuration)
    reshaped_config = reshaper.reshape(ARABIC_TEXT)
    tests.append(("7. Reshape+Ligatures", reshaped_config))

    # 8. Reshape+Ligatures reversed
    tests.append(("8. Reshape+Lig+Rev", reshaped_config[::-1]))

    # Send all tests
    for name, text in tests:
        print(f"  {name}...", end=' ', flush=True)
        ticket = test_combination(name, text)
        if send_to_printer(ip, port, ticket):
            print("✓")
        else:
            print("✗")
        import time
        time.sleep(1.5)

    print()
    print("="*50)
    print("Check all 8 tickets!")
    print("Find the one where Arabic is:")
    print("  - Connected (letters joined)")
    print("  - Correct direction (right-to-left)")
    print("="*50)


if __name__ == '__main__':
    main()
