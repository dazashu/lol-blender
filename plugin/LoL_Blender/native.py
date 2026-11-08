"""Load `_rsblender`, the Rust half. Everything else calls `rs()`."""

import os
import sys

BIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin")

#: Must equal `_rsblender.version()`, so a half-updated install fails early.
REQUIRED_NATIVE_VERSION = "0.25.0"

_module = None
_error = None


class NativeMissing(RuntimeError):
    """The native module could not be loaded. The message is shown to the user."""


def _load():
    global _module, _error

    if BIN_DIR not in sys.path:
        sys.path.insert(0, BIN_DIR)

    try:
        import _rsblender  # noqa: PLC0415  (deliberately deferred)
    except ImportError as e:
        _error = NativeMissing(
            "LoL Blender: the native module could not be loaded from %s.\n"
            "Build it with build.ps1, or reinstall the addon from a release zip.\n"
            "Underlying error: %s" % (BIN_DIR, e))
        raise _error from e

    got = _rsblender.version()
    if got != REQUIRED_NATIVE_VERSION:
        _error = NativeMissing(
            "LoL Blender: native module version mismatch - the addon expects %s but %s is %s.\n"
            "This install is half-updated; rebuild or reinstall so both halves match."
            % (REQUIRED_NATIVE_VERSION, _rsblender.__file__, got))
        raise _error

    _module = _rsblender
    return _module


def rs():
    """The native module. Raises `NativeMissing` if it is unavailable."""
    if _module is not None:
        return _module
    if _error is not None:
        raise _error
    return _load()


def available():
    """True if the native module loaded."""
    try:
        rs()
    except NativeMissing:
        return False
    return True


def status():
    """One line describing the native module, for the preferences panel."""
    try:
        return "loaded, version %s" % rs().version()
    except NativeMissing as e:
        return str(e).splitlines()[0]
