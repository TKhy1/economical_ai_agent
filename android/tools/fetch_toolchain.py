"""Bootstrap the Android build toolchain inside the repo, without Android Studio.

Downloads (and verifies) into ``<repo>/.toolchain``:

    jdk21/                     OpenJDK 21 (Gradle 8.13 / AGP 8.13 need JDK 17+;
                               the system JDK 25 is too new for Gradle 8.13)
    gradle-8.13/               Gradle distribution
    android-sdk/
        platform-tools/        adb, fastboot
        platforms/android-36/  android.jar (compileSdk 36 = Android 16)
        build-tools/36.0.0/    aapt2, d8, zipalign, apksigner
        licenses/              the standard SDK licence hash, so AGP never prompts

Everything lives inside the workspace (no %LOCALAPPDATA% writes), so the
sandbox can't block it and the checkout stays self-contained.

Usage:
    venv\\Scripts\\python.exe android\\tools\\fetch_toolchain.py

Mirrors are overridable because dl.google.com and services.gradle.org are not
reachable from every network (they time out on this machine):

    ANDROID_SDK_MIRROR, GRADLE_DIST_URL, JDK_URL
"""
import hashlib
import os
import shutil
import sys
import time
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ANDROID_DIR = os.path.dirname(HERE)
REPO = os.path.dirname(ANDROID_DIR)
TOOLCHAIN = os.path.join(REPO, ".toolchain")
DOWNLOADS = os.path.join(TOOLCHAIN, "downloads")
SDK = os.path.join(TOOLCHAIN, "android-sdk")

SDK_MIRROR = os.environ.get(
    "ANDROID_SDK_MIRROR", "https://mirrors.cloud.tencent.com/AndroidSDK/"
)
GRADLE_URL = os.environ.get(
    "GRADLE_DIST_URL",
    "https://mirrors.cloud.tencent.com/gradle/gradle-8.13-bin.zip",
)
JDK_URL = os.environ.get(
    "JDK_URL",
    "https://mirrors.huaweicloud.com/openjdk/21.0.2/openjdk-21.0.2_windows-x64_bin.zip",
)

# SHA-1 values come straight from the SDK repository index (repository2-3.xml).
SDK_ARCHIVES = [
    # (archive name, expected sha1, destination inside android-sdk, name inside the zip)
    ("platform-tools_r37.0.1-win.zip", "e03e78b1d80b396f1c3358e31251cb31740e1110",
     "platform-tools", "platform-tools"),
    ("platform-36_r02.zip", "2c1a80dd4d9f7d0e6dd336ec603d9b5c55a6f576",
     os.path.join("platforms", "android-36"), None),
    ("build-tools_r36_windows.zip", "f16ccffd34de8790dede813a6c7d8e2c11a27b50",
     os.path.join("build-tools", "36.0.0"), None),
]

EXTRA_ARCHIVES = [
    ("gradle-8.13-bin.zip", GRADLE_URL, "gradle-8.13"),
    ("openjdk-21.0.2_windows-x64_bin.zip", JDK_URL, "jdk21"),
]

# Standard hash of the android-sdk-license text; AGP reads it from licenses/.
SDK_LICENSE_HASH = "24333f8a63b6825ea9c5514f83c2829b004d1fee"


def log(message):
    print(message, flush=True)


def sha1_of(path):
    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def http_get(url, dest, expected_sha1=None, label=""):
    """Download `url` to `dest`, resuming a partial `.part` file when possible."""
    label = label or os.path.basename(dest)
    if os.path.exists(dest):
        if not expected_sha1 or sha1_of(dest) == expected_sha1:
            log("  [skip] %s (already present)" % label)
            return dest
        log("  [redo] %s (checksum mismatch)" % label)
        os.remove(dest)

    part = dest + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    request = urllib.request.Request(url)
    if have:
        request.add_header("Range", "bytes=%d-" % have)
    try:
        response = urllib.request.urlopen(request, timeout=60)
    except Exception as exc:  # noqa: BLE001 — surface a readable message
        raise SystemExit("download failed for %s: %s" % (url, exc))

    total = response.headers.get("Content-Length")
    total = (have + int(total)) if total and total.isdigit() else None
    mode = "ab" if have and response.status == 206 else "wb"
    if mode == "wb":
        have = 0
    start = time.time()
    done = have
    last_report = 0
    with open(part, mode) as out:
        while True:
            chunk = response.read(262144)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if total and (done - last_report) > total / 20:
                last_report = done
                pct = done * 100 // total
                speed = done / max(time.time() - start, 1e-6) / 1048576
                log("  %s %3d%%  %5.1f MB / %.1f MB  (%.1f MB/s)"
                    % (label, pct, done / 1048576,
                       total / 1048576, speed))
    os.replace(part, dest)
    if expected_sha1:
        actual = sha1_of(dest)
        if actual != expected_sha1:
            os.remove(dest)
            raise SystemExit("%s: sha1 mismatch\n  expected %s\n  actual   %s"
                             % (label, expected_sha1, actual))
        log("  [ok] %s (sha1 verified)" % label)
    else:
        log("  [ok] %s (%.1f MB)" % (label, os.path.getsize(dest) / 1048576))
    return dest


def zip_top_dir(path):
    """Return the single top-level directory inside a zip, if it has one."""
    with zipfile.ZipFile(path) as archive:
        names = {name.split("/")[0] for name in archive.namelist() if name}
        nested = [name for name in archive.namelist() if "/" in name]
    if len(names) == 1 and nested:
        return names.pop()
    return None


def install_zip(path, dest, inner=None, flatten=False):
    """Extract `path` into `dest`, dropping a redundant top-level directory."""
    if os.path.isdir(dest) and os.listdir(dest):
        log("  [skip] %s (already extracted)" % os.path.relpath(dest, TOOLCHAIN))
        return
    staging = dest + ".staging"
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging, exist_ok=True)
    with zipfile.ZipFile(path) as archive:
        archive.extractall(staging)
    source = staging
    top = zip_top_dir(path)
    if top and (inner in (None, top)) and flatten:
        source = os.path.join(staging, top)
    elif inner and os.path.isdir(os.path.join(staging, inner)):
        source = os.path.join(staging, inner)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.move(source, dest)
    shutil.rmtree(staging, ignore_errors=True)
    log("  [ok] extracted -> %s" % os.path.relpath(dest, TOOLCHAIN))


def write_licenses():
    directory = os.path.join(SDK, "licenses")
    os.makedirs(directory, exist_ok=True)
    for name in ("android-sdk-license", "android-sdk-preview-license"):
        target = os.path.join(directory, name)
        if not os.path.exists(target):
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(SDK_LICENSE_HASH + "\n")
            log("  [ok] wrote licenses/%s" % name)


def write_local_properties():
    target = os.path.join(ANDROID_DIR, "local.properties")
    sdk_dir = SDK.replace("\\", "/")
    content = "# Generated by android/tools/fetch_toolchain.py - do not commit.\n"
    content += "sdk.dir=%s\n" % sdk_dir
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(content)
    log("  [ok] wrote android/local.properties")


def main():
    os.makedirs(DOWNLOADS, exist_ok=True)
    log("toolchain root : %s" % TOOLCHAIN)
    log("sdk mirror     : %s" % SDK_MIRROR)

    log("\n[1/4] Android SDK packages")
    for name, sha1, dest, inner in SDK_ARCHIVES:
        path = os.path.join(DOWNLOADS, name)
        http_get(SDK_MIRROR + name, path, sha1, name)
        install_zip(path, os.path.join(SDK, dest), inner=inner,
                    flatten=(inner is None))
    write_licenses()

    log("\n[2/4] Gradle 8.13")
    for name, url, dest in EXTRA_ARCHIVES[:1]:
        path = os.path.join(DOWNLOADS, name)
        http_get(url, path, None, name)
        install_zip(path, os.path.join(TOOLCHAIN, dest), inner="gradle-8.13",
                    flatten=True)

    log("\n[3/4] OpenJDK 21")
    name, url, dest = EXTRA_ARCHIVES[1]
    path = os.path.join(DOWNLOADS, name)
    http_get(url, path, None, name)
    install_zip(path, os.path.join(TOOLCHAIN, dest), flatten=True)

    log("\n[4/4] Local configuration")
    write_local_properties()

    log("\nDone. Toolchain summary:")
    for probe in (
        os.path.join(TOOLCHAIN, "jdk21", "bin", "java.exe"),
        os.path.join(TOOLCHAIN, "gradle-8.13", "bin", "gradle.bat"),
        os.path.join(SDK, "platform-tools", "adb.exe"),
        os.path.join(SDK, "build-tools", "36.0.0", "aapt2.exe"),
        os.path.join(SDK, "platforms", "android-36", "android.jar"),
    ):
        log("  %-6s %s" % ("ok" if os.path.exists(probe) else "MISS", probe))


if __name__ == "__main__":
    main()
