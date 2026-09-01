#!/usr/bin/env python3
"""
Arabic Graphics Printer Test
Prints Arabic text as images for printers without Arabic fonts
"""

import socket
import sys
from io import BytesIO

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Error: Pillow not installed. Run: pip install Pillow")
    sys.exit(1)

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
    ARABIC_LIBS = True
except ImportError:
    ARABIC_LIBS = False
    print("Warning: arabic-reshaper/python-bidi not installed")
    print("Arabic text may appear disconnected")
    print("Run: pip install arabic-reshaper python-bidi\n")

# ESC/POS Commands
ESC = b'\x1b'
GS = b'\x1d'
INIT = ESC + b'@'
LF = b'\n'
ALIGN_CENTER = ESC + b'a\x01'
ALIGN_LEFT = ESC + b'a\x00'
PARTIAL_CUT = GS + b'V\x01'
FEED_3 = ESC + b'd\x03'


def prepare_arabic(text):
    """Reshape Arabic text for correct display."""
    if ARABIC_LIBS:
        reshaped = arabic_reshaper.reshape(text)
        return get_display(reshaped)
    return text


def text_to_image(text, font_size=24, width=384, rtl=False):
    """Convert text to monochrome image for thermal printer."""
    # Try to find an Arabic-supporting font
    font = None
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "/usr/share/fonts/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/tahoma.ttf",
        "/mnt/c/Windows/Fonts/arial.ttf",
        "/mnt/c/Windows/Fonts/tahoma.ttf",
    ]

    for path in font_paths:
        try:
            font = ImageFont.truetype(path, font_size)
            break
        except:
            continue

    if font is None:
        font = ImageFont.load_default()

    # Process Arabic text
    if rtl:
        text = prepare_arabic(text)

    # Create temporary image to measure text
    temp_img = Image.new('1', (1, 1), 1)
    temp_draw = ImageDraw.Draw(temp_img)
    bbox = temp_draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1] + 10

    # Create actual image
    img = Image.new('1', (width, text_height), 1)  # 1 = white
    draw = ImageDraw.Draw(img)

    # Center the text
    x = (width - text_width) // 2
    y = 0

    draw.text((x, y), text, font=font, fill=0)  # 0 = black

    return img


def image_to_escpos(img):
    """Convert PIL Image to ESC/POS raster graphics command."""
    # Ensure image is mode '1' (1-bit pixels)
    img = img.convert('1')

    width, height = img.size
    pixels = list(img.getdata())

    # Width in bytes (8 pixels per byte)
    width_bytes = (width + 7) // 8

    output = bytearray()

    # GS v 0 - Print raster bit image
    # Format: GS v 0 m xL xH yL yH d1...dk
    # m = 0 (normal), xL xH = width in bytes, yL yH = height in dots
    output.extend(GS + b'v0\x00')
    output.append(width_bytes & 0xFF)
    output.append((width_bytes >> 8) & 0xFF)
    output.append(height & 0xFF)
    output.append((height >> 8) & 0xFF)

    # Convert pixels to bytes
    for y in range(height):
        for x_byte in range(width_bytes):
            byte = 0
            for bit in range(8):
                x = x_byte * 8 + bit
                if x < width:
                    pixel_index = y * width + x
                    if pixels[pixel_index] == 0:  # Black pixel
                        byte |= (0x80 >> bit)
            output.append(byte)

    return bytes(output)


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


def build_graphics_ticket():
    """Build a test ticket using graphics for Arabic."""
    output = bytearray()
    output.extend(INIT)

    # Header in English (text mode)
    output.extend(ALIGN_CENTER)
    output.extend(b'=' * 32 + LF)
    output.extend(b'ARABIC GRAPHICS TEST' + LF)
    output.extend(b'=' * 32 + LF)
    output.extend(LF)

    # Arabic text as graphics
    arabic_texts = [
        ("Hello:", "مرحبا"),
        ("Chicken Shawarma:", "شاورما دجاج"),
        ("Table 5:", "الطاولة 5"),
        ("Thank you:", "شكرا لك"),
    ]

    for english, arabic in arabic_texts:
        # English label (text)
        output.extend(english.encode('ascii') + LF)

        # Arabic as image
        img = text_to_image(arabic, font_size=28, width=384, rtl=True)
        output.extend(image_to_escpos(img))
        output.extend(LF)

    # Footer
    output.extend(b'=' * 32 + LF)
    output.extend(b'If Arabic shows correctly,' + LF)
    output.extend(b'graphics mode works!' + LF)
    output.extend(b'=' * 32 + LF)

    output.extend(FEED_3)
    output.extend(PARTIAL_CUT)

    return bytes(output)


def main():
    ip = sys.argv[1] if len(sys.argv) > 1 else '192.168.68.123'
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 9100

    print(f"Arabic Graphics Printer Test")
    print(f"Printer: {ip}:{port}")
    print(f"Arabic libraries: {'Yes' if ARABIC_LIBS else 'No'}")
    print()

    print("Building graphics ticket...")
    ticket = build_graphics_ticket()

    print(f"Sending to printer ({len(ticket)} bytes)...")
    success, error = send_to_printer(ip, port, ticket)

    if success:
        print("✓ Sent successfully!")
        print("\nCheck the printout - Arabic should appear as graphics.")
    else:
        print(f"✗ Failed: {error}")


if __name__ == '__main__':
    main()
