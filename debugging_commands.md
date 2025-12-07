# Should show your printer
dns-sd -B _ipp._tcp

# Should ALSO show your printer (this is what iOS browses)
dns-sd -B _ipp._tcp,_universal

# Dump full records to verify structure
dns-sd -Z _ipp._tcp,_universal

# Attributes Probe
ipptool -tv ipp://rsmbp-personal.local:6310/printers/fake_printer get-printer-attributes.test
