"""Mock usb.core so extractor can be imported without pyusb/libusb installed."""
import sys
import types

# Create a fake usb.core module so extractor.py can import at test time
# even when libusb is not available (e.g., CI environments).
if "usb" not in sys.modules:
    usb_mod = types.ModuleType("usb")
    usb_core = types.ModuleType("usb.core")
    usb_util = types.ModuleType("usb.util")

    class FakeUSBError(Exception):
        pass

    class FakeUSBTimeoutError(FakeUSBError):
        pass

    usb_core.USBError = FakeUSBError
    usb_core.USBTimeoutError = FakeUSBTimeoutError
    usb_core.find = lambda **kwargs: None
    usb_util.dispose_resources = lambda dev: None
    usb_util.claim_interface = lambda dev, intf: None
    usb_util.release_interface = lambda dev, intf: None

    usb_mod.core = usb_core
    usb_mod.util = usb_util
    sys.modules["usb"] = usb_mod
    sys.modules["usb.core"] = usb_core
    sys.modules["usb.util"] = usb_util
