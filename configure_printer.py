#!/usr/bin/env python3
"""
ACLAS KP7X Printer Configuration Tool
Sends commands to configure printer settings and save to NV memory.
"""

import socket
import sys
import time

ESC = b'\x1b'
GS = b'\x1d'
FS = b'\x1c'
DLE = b'\x10'
INIT = ESC + b'@'
LF = b'\n'
FEED_3 = ESC + b'd\x03'
PARTIAL_CUT = GS + b'V\x01'


def send_raw(ip, port, data, timeout=5):
    """Send raw bytes to printer."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((ip, port))
        sock.sendall(data)
        time.sleep(0.5)
        sock.close()
        return True
    except Exception as e:
        print(f"Error: {e}")
        return False


def configure_printer(ip, port):
    """Send configuration commands to printer."""

    print(f"Configuring printer at {ip}:{port}")
    print("="*50)

    commands = []

    # Initialize printer
    commands.append(("Initialize", INIT))

    # ============================================
    # CODEPAGE SETTINGS
    # ============================================

    # ESC t n - Select character code table
    # Try setting to UTF-8 compatible mode
    commands.append(("Set Codepage UTF-8 (0x0F)", ESC + b't\x0f'))

    # Some printers use this for Arabic
    commands.append(("Set Codepage Arabic (0x15)", ESC + b't\x15'))

    # ============================================
    # TAKE SENSOR (Paper take detection)
    # ============================================

    # GS a n - Enable/disable Automatic Status Back (ASB)
    # n = 0: Disable, n = 1-255: Enable various status
    commands.append(("Enable ASB", GS + b'a\xff'))

    # ESC c 4 n - Select paper sensor to output paper-end signals
    # n bit 0: Paper roll near-end sensor enabled
    # n bit 1: Paper roll near-end sensor disabled
    commands.append(("Paper sensor enable", ESC + b'c4\x00'))

    # ESC c 3 n - Select paper sensor to stop printing
    commands.append(("Paper sensor stop", ESC + b'c3\x01'))

    # GS ( A - Execute test print
    # This sometimes shows current settings
    commands.append(("Request status", GS + b'(A\x02\x00\x00\x02'))

    # ============================================
    # TAKE SENSE / PRESENTER MODE
    # ============================================

    # For kitchen printers, "take sense" usually means:
    # - Wait for paper to be taken before printing next
    # - Beep until paper is taken

    # ESC c 5 n - Enable/disable panel buttons
    commands.append(("Panel buttons enable", ESC + b'c5\x00'))

    # GS ( D - Configure presenter/retractor
    # This is for printers with paper presenter
    commands.append(("Presenter config 1", GS + b'(D\x02\x00\x01\x01'))
    commands.append(("Presenter config 2", GS + b'(D\x02\x00\x02\x01'))

    # ============================================
    # SAVE TO NV MEMORY
    # ============================================

    # Different printers use different save commands
    commands.append(("Save settings 1 (ESC #E)", ESC + b'#E'))
    commands.append(("Save settings 2 (GS (E)", GS + b'(E\x03\x00\x01\x00\x00'))
    commands.append(("Save settings 3 (FS .)", FS + b'.'))
    commands.append(("Save settings 4 (ESC @!)", ESC + b'@!'))

    # ============================================
    # ACLAS SPECIFIC COMMANDS (if any)
    # ============================================

    # Some ACLAS printers respond to these
    commands.append(("ACLAS config 1", ESC + b'!0'))
    commands.append(("ACLAS config 2", GS + b'!\x00'))

    # ============================================
    # SEND ALL COMMANDS
    # ============================================

    all_data = bytearray()
    all_data.extend(INIT)

    for name, cmd in commands:
        print(f"  Sending: {name}...", end=' ', flush=True)
        all_data.extend(cmd)
        print("queued")

    # Add confirmation print
    all_data.extend(LF)
    all_data.extend(b'Configuration commands sent!\n')
    all_data.extend(b'Restart printer to apply.\n')
    all_data.extend(b'='*32 + LF)
    all_data.extend(FEED_3)
    all_data.extend(PARTIAL_CUT)

    print()
    print("Sending all commands...")
    if send_raw(ip, port, bytes(all_data)):
        print("✓ Commands sent successfully!")
    else:
        print("✗ Failed to send commands")

    print()
    print("="*50)
    print("IMPORTANT: Power cycle (turn off/on) the printer")
    print("to apply the saved settings!")
    print("="*50)


def set_specific_codepage(ip, port, codepage_num):
    """Set a specific codepage."""
    data = bytearray()
    data.extend(INIT)
    data.extend(ESC + b't' + bytes([codepage_num]))

    # Try to save
    data.extend(ESC + b'#E')
    data.extend(GS + b'(E\x03\x00\x01\x00\x00')
    data.extend(FS + b'.')

    data.extend(f'\nCodepage set to: {codepage_num} (0x{codepage_num:02X})\n'.encode())
    data.extend(b'Power cycle printer to apply!\n')
    data.extend(FEED_3)
    data.extend(PARTIAL_CUT)

    return send_raw(ip, port, bytes(data))


def main():
    ip = sys.argv[1] if len(sys.argv) > 1 else '192.168.68.123'
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 9100

    print()
    print("ACLAS KP7X Configuration Tool")
    print("="*50)
    print()
    print("Options:")
    print("  1. Send all configuration commands")
    print("  2. Set codepage to UTF-8 (0x0F)")
    print("  3. Set codepage to Arabic CP864 (0x15)")
    print("  4. Set codepage to Arabic CP1256 (0x28)")
    print("  5. Set codepage to CP720 Arabic (0x20)")
    print("  6. Custom codepage number")
    print("  q. Quit")
    print()

    choice = input("Select option: ").strip().lower()

    if choice == '1':
        configure_printer(ip, port)
    elif choice == '2':
        print("Setting UTF-8 codepage...")
        set_specific_codepage(ip, port, 0x0F)
        print("Done! Power cycle the printer.")
    elif choice == '3':
        print("Setting CP864 Arabic codepage...")
        set_specific_codepage(ip, port, 0x15)
        print("Done! Power cycle the printer.")
    elif choice == '4':
        print("Setting CP1256 Arabic codepage...")
        set_specific_codepage(ip, port, 0x28)
        print("Done! Power cycle the printer.")
    elif choice == '5':
        print("Setting CP720 Arabic codepage...")
        set_specific_codepage(ip, port, 0x20)
        print("Done! Power cycle the printer.")
    elif choice == '6':
        num = input("Enter codepage number (decimal): ").strip()
        try:
            cp = int(num)
            print(f"Setting codepage {cp}...")
            set_specific_codepage(ip, port, cp)
            print("Done! Power cycle the printer.")
        except:
            print("Invalid number")
    elif choice == 'q':
        print("Exiting.")
    else:
        print("Invalid option")


if __name__ == '__main__':
    main()
