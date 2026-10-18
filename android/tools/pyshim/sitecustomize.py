"""Build-machine shim: make tempfile.mkdtemp() usable under the Windows sandbox.

Why this file exists
--------------------
On this host, ``os.mkdir(path, 0o700)`` produces a directory that the (restricted
token) sandbox process cannot write into: every ``open()`` inside it fails with
``PermissionError: [Errno 13]``. ``tempfile.mkdtemp()`` hard-codes 0o700, and pip
unpacks *every wheel* into such a directory, so Chaquopy's
``installDebugPythonRequirements`` dies with:

    PermissionError: ...\\pip-unpack-xxxx\\flask-3.1.3-py3-none-any.whl

Creating the same directory with the default mode (0o777) works, so this module
re-implements ``mkdtemp`` with the default mode. It is loaded through PYTHONPATH
by android/tools/build.ps1 and only affects the *build machine*: it is not
packaged into the APK and never runs on the phone.

Verified with:
    os.mkdir(p, 0o777)            -> writable
    os.mkdir(p, 0o700)            -> PermissionError
    tempfile.mkdtemp()            -> PermissionError  (before this shim)
"""
import os
import random
import string
import tempfile

if os.name == "nt":
    _alphabet = string.ascii_letters + string.digits

    def mkdtemp(suffix=None, prefix=None, dir=None):
        """Drop-in replacement for tempfile.mkdtemp that uses mode 0o777."""
        suffix = "" if suffix is None else suffix
        prefix = tempfile.gettempprefix() if prefix is None else prefix
        directory = tempfile.gettempdir() if dir is None else dir
        for _ in range(tempfile.TMP_MAX):
            name = "".join(random.choices(_alphabet, k=8))
            path = os.path.join(directory, prefix + name + suffix)
            try:
                os.mkdir(path, 0o777)
                return path
            except FileExistsError:
                continue
        raise FileExistsError("no usable temporary directory name found")

    tempfile.mkdtemp = mkdtemp
