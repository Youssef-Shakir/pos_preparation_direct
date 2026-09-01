#!/usr/bin/env python3
"""
Enable Take Sense / Paper Pickup Sensor on ACLAS KP7X
"""

import socket
import time
import sys

ESC = b'\x1b'
GS = b'\x1d'
FS = b'\x1c'
INIT = ESC + b'@'
LF = b'\n'
FEED_3 = ESC + b'd\x03'
PARTIAL_CUT = GS + b'V\x01'


def send_raw(ip, port, data):
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


def main():
    ip = sys.argv[1] if len(sys.argv) > 1 else '192.168.68.123'
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 9100

    print(f"Enabling Take Sense on {ip}:{port}")
    print("="*50)

    # Try various commands that might enable take sense / presenter mode
    commands_to_try = [
        # GS ( D pL pH fn m - Presenter/retractor commands
        ("Presenter enable 1", GS + b'(D\x02\x00\x01\x01'),
        ("Presenter enable 2", GS + b'(D\x02\x00\x02\x01'),
        ("Presenter enable 3", GS + b'(D\x03\x00\x30\x01\x01'),

        # GS ( K - Presenter settings
        ("Presenter K 1", GS + b'(K\x02\x00\x31\x01'),
        ("Presenter K 2", GS + b'(K\x02\x00\x32\x01'),

        # ESC c 4 n - Paper sensor
        ("Paper sensor 1", ESC + b'c4\x01'),
        ("Paper sensor 2", ESC + b'c4\x03'),

        # GS a - ASB enable
        ("ASB all", GS + b'a\xff'),

        # Some printers use proprietary commands
        ("Custom 1", ESC + b'p\x00\x19\x19'),  # Pulse
        ("Custom 2", GS + b'r\x01'),  # Transmit status

        # ACLAS specific attempts
        ("ACLAS 1", FS + b'p\x01'),
        ("ACLAS 2", FS + b'q\x01'),
        ("ACLAS 3", ESC + b'p\x01'),
    ]

    data = bytearray()
    data.extend(INIT)

    for name, cmd in commands_to_try:
        print(f"  Adding: {name}")
        data.extend(cmd)

    # Try to save settings
    data.extend(ESC + b'#E')
    data.extend(GS + b'(E\x03\x00\x01\x00\x00')
    data.extend(FS + b'.')

    # Print confirmation
    data.extend(LF)
    data.extend(b'Take Sense commands sent!\n')
    data.extend(b'Power cycle printer to apply.\n')
    data.extend(b'='*32 + LF)
    data.extend(FEED_3)
    data.extend(PARTIAL_CUT)

    print()
    print("Sending commands...")
    if send_raw(ip, port, bytes(data)):
        print("✓ Sent!")
        print()
        print("="*50)
        print("NOW: Power cycle the printer!")
        print()
        print("If take sense still doesn't work, you may need to:")
        print("1. Check DIP switches inside the printer")
        print("2. Use the original PC-SW utility from the CD")
        print("3. Contact ACLAS support")
        print("="*50)
    else:
        print("✗ Failed")


if __name__ == '__main__':
    main()
