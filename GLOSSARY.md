# Glossary

## Device setup

**Ready device**: a device that adb lists in the `device` state and that has finished booting,
so commands can drive it. A listed device that is *unauthorised*, *offline*, *without
permissions*, *still booting* or *not answering* is not ready.
_Avoid_: connected device (a connected phone may still be unauthorised).

**Blocker**: a failed setup check that stops every run until it is fixed (no adb, no ready
device, Python older than 3.9).
_Avoid_: error, failure (both also describe a failed test case).

**Warning**: a setup check that does not stop a run but makes it worse, such as slow screen
reads because the UiAutomator2 server is missing.
