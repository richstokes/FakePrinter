#!/usr/bin/env python3
"""
Virtual IPP Printer using ippserver library
Receives print jobs and saves them to ./print_jobs/ directory
Optionally converts PostScript to PDF using ghostscript
"""

import os
import socket
import subprocess
import logging
from io import BytesIO
from http.client import HTTPResponse
from zeroconf import ServiceInfo, Zeroconf
from ippserver.server import IPPServer, IPPRequestHandler
from ippserver.behaviour import SaveFilePrinter

# Configuration
PRINTER_NAME = "HP LaserJet Pro M404dn"
PRINTER_DESCRIPTION = "1st Floor Copy Room"
PORT = 6310
SAVE_DIR = "./print_jobs"
PRINTER_UUID = "82c9bd0f-e313-4a2b-be52-8474a31d481c"
CONVERT_TO_PDF = True  # Set to True to auto-convert PostScript to PDF


# Get local IP address
def get_local_ip():
    """Get the local IP address of this machine"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def sanitize_hostname(hostname):
    """Sanitize hostname to be DNS-compliant (RFC 1123).
    Hostnames must contain only letters, digits, and hyphens,
    and must start and end with a letter or digit.
    """
    # Remove .local suffix if present
    if hostname.endswith(".local"):
        hostname = hostname[:-6]

    # Replace invalid characters with hyphens
    sanitized = "".join(c if c.isalnum() else "-" for c in hostname)

    # Remove leading/trailing hyphens
    sanitized = sanitized.strip("-")

    # If empty or invalid, use fallback
    if not sanitized or not sanitized[0].isalnum():
        sanitized = "fakeprinter"

    return sanitized.lower()


def advertise_printer(hostname, ip, port, printer_name, printer_description, uuid):
    """Advertise the printer via Bonjour/mDNS with AirPrint support"""
    # Support both IPv4 and IPv6 - iOS prefers IPv6 and Apple's spec expects both
    zeroconf = Zeroconf()

    # For AirPrint, iOS looks for the _universal subtype
    # The type must be in the format: _<subtype>._sub._<service>._<protocol>.local.
    # The service name still uses the base type (_ipp._tcp.local.)
    airprint_type = "_universal._sub._ipp._tcp.local."
    base_type = "_ipp._tcp.local."
    service_name = f"{printer_name}.{base_type}"

    # TXT records for IPP printer with AirPrint support
    # URF (Universal Raster Format) is REQUIRED for AirPrint on iOS
    txt = {
        b"txtvers": b"1",
        b"qtotal": b"1",
        b"rp": b"printers/fake_printer",
        b"ty": printer_name.encode(),
        b"adminurl": f"http://{hostname}:{port}/".encode(),
        b"note": printer_description.encode(),
        b"pdl": b"application/pdf,image/jpeg,image/urf",  # Include image/urf for AirPrint discovery
        b"UUID": uuid.encode(),
        b"Color": b"T",
        b"Duplex": b"F",
        b"Staple": b"F",
        b"Copies": b"T",
        b"printer-state": b"3",  # idle
        b"printer-type": b"0x809046",  # AirPrint capable printer
        # AirPrint-specific keys
        b"URF": b"CP1,MT1-8-11,OB9,OFU0,PQ4,RS360,SRGB24,V1.4,W8,DM3",  # Required for AirPrint!
        b"air": b"none",  # Authentication method: none (not an "enable" flag)
        b"kind": b"document,envelope,photo",
        b"priority": b"50",
        b"product": f"({printer_name})".encode(),
        b"usb_MFG": b"HP",
        b"usb_MDL": b"LaserJet Pro M404dn",
    }

    # Register IPP service with AirPrint subtype
    # Python-zeroconf registers both the base type and subtype PTR records automatically
    info = ServiceInfo(
        airprint_type,  # Type includes the _universal subtype for iOS discovery
        service_name,  # Name uses the base _ipp._tcp.local. type
        addresses=[socket.inet_aton(ip)],
        port=port,
        properties=txt,
        server=hostname + ".",  # FQDN with trailing dot
    )

    print(f"Advertising printer with AirPrint support:")
    print(f"  Service name: {service_name}")
    print(f"  Service type: {airprint_type}")
    print(f"  Hostname: {hostname}")
    print(f"  IP: {ip}")
    print(f"  Port: {port}")

    zeroconf.register_service(info)
    print(f"  -> Bonjour service registered successfully (AirPrint enabled)\n")

    return zeroconf, info


class ChunkedIPPRequestHandler(IPPRequestHandler):
    """
    Wrapper for IPPRequestHandler that handles Transfer-Encoding: chunked.
    iOS sends chunked IPP requests without Content-Length, which breaks
    many BaseHTTPRequestHandler-based servers that don't decode chunks.
    """

    def parse_request(self):
        """Override to handle chunked transfer encoding before processing IPP."""
        # Call parent to parse HTTP headers
        if not super().parse_request():
            return False

        # Debug: print all headers
        transfer_encoding = self.headers.get("Transfer-Encoding", "")
        content_length = self.headers.get("Content-Length", "")

        print(
            f"   📋 Headers: Transfer-Encoding='{transfer_encoding}', Content-Length='{content_length}'"
        )

        # Only decode if EXACTLY "chunked" transfer encoding AND no Content-Length
        # Must be case-insensitive exact match, not just substring
        has_chunked = transfer_encoding.strip().lower() == "chunked"
        has_content_length = bool(content_length)

        if has_chunked and not has_content_length:
            print(f"   🔄 Bypassing chunked decoding - reading raw stream...")
            try:
                # iOS appears to send chunked encoding header but not actual chunked format
                # Just read all available data directly
                # First, peek at what we have
                initial_peek = self.rfile.read(20)
                print(
                    f"      First 20 bytes: {initial_peek.hex()} = {initial_peek[:10]}"
                )

                # Check if this looks like IPP data (starts with version bytes 0x01xx or 0x02xx)
                if len(initial_peek) >= 2 and initial_peek[0] in (0x01, 0x02):
                    print(
                        f"      ✅ Detected IPP protocol data, reading without chunk decoding..."
                    )
                    # Read the rest of the data
                    remaining = self.rfile.read()
                    full_body = initial_peek + remaining

                    # Decode IPP operation from bytes 2-3 (big-endian 16-bit integer)
                    if len(full_body) >= 4:
                        operation_id = (full_body[2] << 8) | full_body[3]
                        operation_names = {
                            0x0002: "Print-Job",
                            0x000B: "Get-Printer-Attributes",
                            0x0004: "Validate-Job",
                            0x0008: "Cancel-Job",
                            0x0009: "Get-Job-Attributes",
                            0x000A: "Get-Jobs",
                        }
                        op_name = operation_names.get(
                            operation_id, f"Unknown-0x{operation_id:04x}"
                        )
                        print(
                            f"      📋 IPP Operation: {op_name} (0x{operation_id:04x})"
                        )

                    # Replace rfile with full body
                    self.rfile = BytesIO(full_body)
                    self.headers["Content-Length"] = str(len(full_body))
                    print(f"      ✅ Read {len(full_body)} bytes of IPP data")

                else:
                    # Try traditional chunked decoding
                    print(f"      Attempting traditional chunked decoding...")
                    # Put back what we read
                    self.rfile = BytesIO(initial_peek + self.rfile.read())

                    chunks = []
                    chunk_count = 0

                    while True:
                        # Read chunk size line (hex number, possibly followed by ;extensions)
                        size_line = self.rfile.readline()
                        if not size_line:
                            print(f"      No more data to read")
                            break

                        # Remove whitespace
                        size_line = size_line.strip()
                        if not size_line:
                            print(f"      Empty line, continuing...")
                            continue

                        # Parse chunk size (hex), ignore any extensions after ;
                        try:
                            chunk_size_str = size_line.split(b";")[0].strip()
                            chunk_size = int(chunk_size_str, 16)
                            print(
                                f"      Chunk {chunk_count}: size={chunk_size} bytes (hex: {chunk_size_str})"
                            )
                        except ValueError as ve:
                            print(f"      ❌ Invalid hex chunk size: {size_line[:50]}")
                            raise ValueError(f"Invalid chunk size: {size_line[:50]}")

                        # If chunk size is 0, we're done
                        if chunk_size == 0:
                            print(f"      Terminating chunk (size=0) received")
                            # Read any trailing headers and final CRLF
                            while True:
                                trailer = self.rfile.readline()
                                if not trailer or trailer in (b"\r\n", b"\n"):
                                    break
                            break

                        # Read chunk data
                        chunk_data = self.rfile.read(chunk_size)
                        actual_len = len(chunk_data)
                        if actual_len != chunk_size:
                            print(
                                f"      ❌ Expected {chunk_size} bytes, got {actual_len}"
                            )
                            raise ValueError(
                                f"Expected {chunk_size} bytes, got {actual_len}"
                            )

                        chunks.append(chunk_data)
                        chunk_count += 1

                        # Read trailing CRLF after chunk data
                        trailing = self.rfile.readline()

                    # Combine all chunks
                    full_body = b"".join(chunks)

                    # Replace rfile with BytesIO containing decoded body
                    self.rfile = BytesIO(full_body)

                    # Add Content-Length header for IPP handler
                    self.headers["Content-Length"] = str(len(full_body))

                    print(
                        f"   ✅ Decoded {chunk_count} chunks: {len(full_body)} total bytes"
                    )

            except Exception as e:
                print(f"   ❌ Error decoding chunked request: {e}")
                import traceback

                traceback.print_exc()
                self.send_error(400, f"Bad chunked encoding: {e}")
                return False

        return True


class PDFConvertingPrinter(SaveFilePrinter):
    """Extends SaveFilePrinter to convert PostScript to PDF using ghostscript"""

    def __init__(self, directory, convert_to_pdf=True):
        super().__init__(directory=directory, filename_ext="ps")
        self.convert_to_pdf = convert_to_pdf

    def run_after_saving(self, ps_filename, ipp_request):
        """Called after saving the PostScript file"""
        # Log the print job
        print(f"\n📄 Print job received!")
        print(f"   File: {ps_filename}")
        print(f"   Size: {os.path.getsize(ps_filename)} bytes")
        if hasattr(ipp_request, "operation_id"):
            print(f"   Operation: {ipp_request.operation_id}")

        if not self.convert_to_pdf:
            print()
            return

        # Convert PS to PDF using ghostscript
        pdf_filename = ps_filename.rsplit(".", 1)[0] + ".pdf"

        try:
            # Check if ghostscript is available
            result = subprocess.run(
                [
                    "gs",
                    "-dSAFER",
                    "-dBATCH",
                    "-dNOPAUSE",
                    "-dQUIET",
                    "-sDEVICE=pdfwrite",
                    f"-sOutputFile={pdf_filename}",
                    ps_filename,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0:
                print(f"   ✅ Converted to PDF: {pdf_filename}")
                # Optionally delete the PostScript file
                # os.remove(ps_filename)
            else:
                print(f"   ❌ Ghostscript conversion failed: {result.stderr}")
        except FileNotFoundError:
            print(
                "   ⚠️  Ghostscript (gs) not found. Install with: brew install ghostscript"
            )
            print(f"   PostScript file saved as: {ps_filename}")
        except Exception as e:
            print(f"   ❌ Error converting to PDF: {e}")

        print()


def main():
    # Create save directory if it doesn't exist
    os.makedirs(SAVE_DIR, exist_ok=True)

    # Get local network info
    raw_hostname = socket.gethostname()
    sanitized = sanitize_hostname(raw_hostname)
    hostname = f"{sanitized}.local"
    local_ip = get_local_ip()

    # Advertise via Bonjour
    zeroconf, service_info = advertise_printer(
        hostname, local_ip, PORT, PRINTER_NAME, PRINTER_DESCRIPTION, PRINTER_UUID
    )

    # Create the printer behavior (save files and optionally convert to PDF)
    printer_behavior = PDFConvertingPrinter(
        directory=SAVE_DIR, convert_to_pdf=CONVERT_TO_PDF
    )

    # Start IPP server
    print(f"Starting IPP server on port {PORT}")
    print(f"Saving print jobs to: {os.path.abspath(SAVE_DIR)}")
    print(
        f"PDF conversion: {'enabled' if CONVERT_TO_PDF else 'disabled (saving as PostScript)'}"
    )
    print(f"Printer URI: ipp://{hostname}:{PORT}/printers/fake_printer\n")

    try:
        server = IPPServer(
            address=("0.0.0.0", PORT),
            request_handler=ChunkedIPPRequestHandler,
            behaviour=printer_behavior,
        )

        print("Server started. Press Ctrl+C to stop.\n")
        server.serve_forever()

    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        zeroconf.unregister_service(service_info)
        zeroconf.close()
        print("Server stopped.")


if __name__ == "__main__":
    main()
