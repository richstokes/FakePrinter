#!/usr/bin/env python3
"""
Fake IPP Printer using ippserver library
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


def advertise_printer(hostname, ip, port, printer_name, uuid):
    """Advertise the printer via Bonjour/mDNS"""
    zeroconf = Zeroconf()

    # Create service info
    service_type = "_ipp._tcp.local."
    service_name = f"{printer_name}.{service_type}"

    # TXT records for IPP printer
    txt = {
        b"txtvers": b"1",
        b"qtotal": b"1",
        b"rp": b"printers/fake_printer",
        b"ty": printer_name.encode(),
        b"adminurl": f"http://{hostname}:{port}/".encode(),
        b"note": b"HP LaserJet Pro M404dn",
        b"pdl": b"application/pdf,application/postscript",
        b"UUID": uuid.encode(),
        b"Color": b"T",
        b"Duplex": b"F",
        b"Staple": b"F",
        b"Copies": b"T",
        b"printer-state": b"3",  # idle
        b"printer-type": b"0x0",
    }

    info = ServiceInfo(
        service_type,
        service_name,
        addresses=[socket.inet_aton(ip)],
        port=port,
        properties=txt,
        server=hostname + ".",  # FQDN with trailing dot
    )

    print(f"Advertising printer:")
    print(f"  Service: {service_name}")
    print(f"  Hostname: {hostname}")
    print(f"  IP: {ip}")
    print(f"  Port: {port}")

    zeroconf.register_service(info)
    print(f"  -> Bonjour service registered successfully\n")

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
    hostname = socket.gethostname()
    if not hostname.endswith(".local"):
        hostname = hostname + ".local"
    local_ip = get_local_ip()

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
