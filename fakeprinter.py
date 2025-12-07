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
from zeroconf import ServiceInfo, Zeroconf
from ippserver.server import IPPServer, IPPRequestHandler
from ippserver.behaviour import SaveFilePrinter

# Configuration
PRINTER_NAME = "HP LaserJet Pro M404dn"
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


def advertise_printer(hostname, ip, port, printer_name, uuid):
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
        b"note": b"HP LaserJet Pro M404dn",
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
        b"product": b"(HP LaserJet Pro M404dn)",
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


class PDFConvertingPrinter(SaveFilePrinter):
    """Extends SaveFilePrinter to convert PostScript to PDF using ghostscript"""

    def __init__(self, directory, convert_to_pdf=True):
        super().__init__(directory=directory, filename_ext="ps")
        self.convert_to_pdf = convert_to_pdf

    def run_after_saving(self, ps_filename, ipp_request):
        """Called after saving the PostScript file"""
        if not self.convert_to_pdf:
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
                logging.info(f"Converted to PDF: {pdf_filename}")
                # Optionally delete the PostScript file
                # os.remove(ps_filename)
            else:
                logging.error(f"Ghostscript conversion failed: {result.stderr}")
        except FileNotFoundError:
            logging.warning(
                "Ghostscript (gs) not found. Install with: brew install ghostscript"
            )
            logging.info(f"PostScript file saved as: {ps_filename}")
        except Exception as e:
            logging.error(f"Error converting to PDF: {e}")


def main():
    # Create save directory if it doesn't exist
    os.makedirs(SAVE_DIR, exist_ok=True)

    # Get local network info
    raw_hostname = socket.gethostname()
    sanitized = sanitize_hostname(raw_hostname)
    hostname = f"{sanitized}.local"
    local_ip = get_local_ip()

    logging.info(f"Original hostname: {raw_hostname}")
    logging.info(f"Sanitized hostname: {hostname}")

    # Advertise via Bonjour
    zeroconf, service_info = advertise_printer(
        hostname, local_ip, PORT, PRINTER_NAME, PRINTER_UUID
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
            request_handler=IPPRequestHandler,
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
