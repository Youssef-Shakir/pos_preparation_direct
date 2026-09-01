#!/usr/bin/env python3
"""
ACLAS Printer Codepage Configuration Tool
Sends ESC/POS commands to change printer's default codepage setting.
"""

import socket
import sys
import time

ESC = b'\x1b'
GS = b'\x1d'
FS = b'\x1c'
INIT = ESC + b'@'
LF = b'\n'
FEED_3 = ESC + b'd\x03'
PARTIAL_CUT = GS + b'V\x01'

# ESC/POS command to save settings to NV memory (varies by printer)
# Some printers use these commands to save settings permanently
SAVE_SETTINGS_1 = ESC + b'#' + b'E'  # Some Epson
SAVE_SETTINGS_2 = GS + b'(' + b'E'   # Some printers
SAVE_SETTINGS_3 = FS + b'.'          # Some Chinese printers

# Different codepage commands to try
CODEPAGE_COMMANDS = {
    'CP437 (US)': ESC + b't\x00',
    'CP850 (Western)': ESC + b't\x02',
    'CP864 (Arabic)': ESC + b't\x15',
    'CP1256 (Arabic Windows)': ESC + b't\x28',
    'CP720 (Arabic)': ESC + b't\x20',
    'Arabic Alt 1 (0x16)': ESC + b't\x16',
    'Arabic Alt 2 (0x17)': ESC + b't\x17',
    'Arabic Alt 3 (0x25)': ESC + b't\x25',
    'Arabic Alt 4 (0x29)': ESC + b't\x29',
    'UTF-8 Mode': ESC + b't\x0f',
}

ARABIC_TEST = "شاورما دجاج"


def send_to_printer(ip, port, data, timeout=5):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((ip, port))
        sock.sendall(data)
        sock.close()
        return True, None
    except Exception as e:
        return False, str(e)


def set_codepage_and_test(ip, port, name, codepage_cmd):
    """Set codepage and print a test."""
    output = bytearray()

    # Initialize
    output.extend(INIT)

    # Set the codepage
    output.extend(codepage_cmd)

    # Try to save to NV memory (may not work on all printers)
    output.extend(SAVE_SETTINGS_1)
    output.extend(SAVE_SETTINGS_2)

    # Print confirmation
    output.extend(f"Codepage set to: {name}\n".encode('ascii', errors='replace'))
    output.extend(b'-' * 32 + LF)

    # Test Arabic
    output.extend(b'Arabic test:\n')
    try:
        if 'UTF' in name:
            output.extend(ARABIC_TEST.encode('utf-8', errors='replace'))
        elif '1256' in name:
            output.extend(ARABIC_TEST.encode('cp1256', errors='replace'))
        elif '864' in name or '720' in name:
            output.extend(ARABIC_TEST.encode('cp864', errors='replace'))
        else:
            output.extend(ARABIC_TEST.encode('utf-8', errors='replace'))
    except:
        output.extend(ARABIC_TEST.encode('utf-8', errors='replace'))

    output.extend(LF)
    output.extend(b'=' * 32 + LF)
    output.extend(FEED_3)
    output.extend(PARTIAL_CUT)

    return send_to_printer(ip, port, bytes(output))


def print_self_test(ip, port):
    """Trigger printer self-test (shows current settings)."""
    # Common self-test commands
    commands = [
        GS + b'(' + b'H' + b'\x02\x00\x00\x34',  # GS ( H - Print info
        ESC + b'=' + b'\x01',  # Some printers
        GS + b'I' + b'\x43',   # Printer ID
    ]

    output = bytearray()
    output.extend(INIT)
    for cmd in commands:
        output.extend(cmd)

    return send_to_printer(ip, port, bytes(output))


def interactive_menu(ip, port):
    """Interactive menu to set codepage."""
    print(f"\n{'='*50}")
    print(f"ACLAS Printer Codepage Configuration")
    print(f"Printer: {ip}:{port}")
    print(f"{'='*50}\n")

    print("Available codepages:\n")
    options = list(CODEPAGE_COMMANDS.keys())
    for i, name in enumerate(options, 1):
        print(f"  {i}. {name}")

    print(f"\n  0. Test ALL codepages")
    print(f"  q. Quit\n")

    while True:
        choice = input("Select codepage (1-10, 0 for all, q to quit): ").strip().lower()

        if choice == 'q':
            print("Exiting.")
            break

        if choice == '0':
            print("\nTesting all codepages...")
            for name, cmd in CODEPAGE_COMMANDS.items():
                print(f"  Setting {name}...", end=' ', flush=True)
                success, error = set_codepage_and_test(ip, port, name, cmd)
                print("✓" if success else f"✗ {error}")
                time.sleep(2)
            print("\nCheck all printed tickets. Note which one shows Arabic correctly.")
            continue

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(options):
                name = options[idx]
                cmd = CODEPAGE_COMMANDS[name]
                print(f"\nSetting {name}...", end=' ', flush=True)
                success, error = set_codepage_and_test(ip, port, name, cmd)
                if success:
                    print("✓ Sent!")
                    print(f"Check if Arabic prints correctly.")
                    print(f"If it works, this is your codepage setting for Odoo.")
                else:
                    print(f"✗ Failed: {error}")
            else:
                print("Invalid choice.")
        except ValueError:
            print("Invalid input.")


def main():
    ip = sys.argv[1] if len(sys.argv) > 1 else '192.168.68.123'
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 9100

    # Test connection
    print(f"Testing connection to {ip}:{port}...")
    success, error = send_to_printer(ip, port, INIT + b'Connection OK\n' + FEED_3 + PARTIAL_CUT)

    if not success:
        print(f"✗ Cannot connect: {error}")
        return

    print("✓ Connected!\n")
    time.sleep(1)

    interactive_menu(ip, port)


if __name__ == '__main__':
    main()
