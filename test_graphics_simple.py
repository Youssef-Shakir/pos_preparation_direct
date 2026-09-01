#!/usr/bin/env python3
"""
Simple graphics test - prints a basic pattern to verify graphics mode works
"""

import socket
import sys

ESC = b'\x1b'
GS = b'\x1d'
INIT = ESC + b'@'
LF = b'\n'
FEED_3 = ESC + b'd\x03'
PARTIAL_CUT = GS + b'V\x01'


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


def create_simple_graphic():
    """Create a simple 64x16 test pattern."""
    width_bytes = 8  # 64 pixels / 8 = 8 bytes
    height = 16

    # Create a simple pattern (checkerboard)
    data = bytearray()

    # GS v 0 command
    data.extend(GS + b'v0\x00')
    data.append(width_bytes & 0xFF)
    data.append((width_bytes >> 8) & 0xFF)
    data.append(height & 0xFF)
    data.append((height >> 8) & 0xFF)

    # Checkerboard pattern
    for y in range(height):
        for x_byte in range(width_bytes):
            if y % 2 == 0:
                data.append(0xAA)  # 10101010
            else:
                data.append(0x55)  # 01010101

    return bytes(data)


def main():
    ip = sys.argv[1] if len(sys.argv) > 1 else '192.168.68.123'
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 9100

    print(f"Testing graphics mode on {ip}:{port}")

    output = bytearray()
    output.extend(INIT)
    output.extend(b'Graphics Test:\n')
    output.extend(b'-' * 32 + LF)

    # Add simple graphic
    output.extend(create_simple_graphic())
    output.extend(LF)

    output.extend(b'-' * 32 + LF)
    output.extend(b'If you see a checkerboard\n')
    output.extend(b'pattern above, graphics work!\n')
    output.extend(b'=' * 32 + LF)
    output.extend(FEED_3)
    output.extend(PARTIAL_CUT)

    if send_to_printer(ip, port, bytes(output)):
        print("✓ Sent! Check for checkerboard pattern.")
    else:
        print("✗ Failed to send.")


if __name__ == '__main__':
    main()
