#!/usr/bin/env python3
"""Validate exported SWU packages without extracting or executing their payloads.

Usage: python3 build/verify-swu.py build/dist [--model k3m] [--kind update]
The default checks the complete four-model export, including each tools ZIP.
Only the Python standard library is required; installed `unzip` accelerates
ZipCrypto decryption of large updates. Temporary storage holds one compressed
payload at a time. This checks package structure, not printer compatibility.
"""

import argparse
import fnmatch
import gzip
import hashlib
import posixpath
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path


# Keep these aligned with build/tools.sh. KS1 and KS1M share a password.
PASSWORDS = {
    "k2p-k3": "U2FsdGVkX19deTfqpXHZnB5GeyQ/dtlbHjkUnwgCi+w=",
    "k3m": "4DKXtEGStWHpPgZm8Xna9qluzAI8VJzpOsEIgd8brTLiXs8fLSu3vRx8o7fMf4h6",
    "ks1": "U2FsdGVkX1+lG6cHmshPLI/LaQr9cZCjA8HZt6Y8qmbB7riY",
    "ks1m": "U2FsdGVkX1+lG6cHmshPLI/LaQr9cZCjA8HZt6Y8qmbB7riY",
}
TOOLS = ("backup-partitions", "config-reset", "debug-bundle", "ssh")
KINDS = ("update", "installer", "tools")
TAR_NAME = "update_swu/setup.tar.gz"
MD5_NAME = TAR_NAME + ".md5"
CHUNK_SIZE = 1024 * 1024
WEB_ROOT = "rinkhals/home/rinkhals/apps/65-rinkhals-web"
TOUCH_ROOT = "rinkhals/opt/rinkhals/ui"
MOONRAKER_ROOT = "rinkhals/home/rinkhals/apps/40-moonraker"
FONTS = ("AlibabaSans-Regular.ttf", "MaterialIcons-Regular.ttf")
SSH_FILES = (
    "dropbear", "sftp-server", "dropbear_rsa_host_key", "libcrypto.so.1.1",
    "libssl.so.1.1", "libatomic.so.1", "libc.so.0", "ld-uClibc",
)
INSTALLER_LIBS = (
    "libffi.so.8", "libstdc++.so.6", "libpython3.11.so.1.0", "libz.so.1",
    "libgcc_s.so.1", "ld-uClibc.so.1",
)


class ValidationError(Exception):
    """An artifact is incomplete, corrupt, or incompatible with its label."""


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def archive_name(name):
    """Accept tar's ./ prefix, but never ambiguous or escaping member names."""
    require(not name.startswith("/") and ".." not in name.split("/"),
            f"unsafe archive path: {name!r}")
    return posixpath.normpath(name)


def zip_index(archive):
    members = {}
    for member in archive.infolist():
        name = archive_name(member.filename)
        require(name not in members, f"duplicate ZIP member: {name}")
        members[name] = member
    return members


class Payload:
    """A tar index and small selected samples; no archive paths reach disk."""

    def __init__(self, compressed):
        self.members = {}
        self.samples = {}
        with gzip.GzipFile(fileobj=compressed) as uncompressed:
            with tarfile.open(fileobj=uncompressed, mode="r|") as archive:
                for member in archive:
                    name = archive_name(member.name)
                    require(name not in self.members, f"duplicate tar member: {name}")
                    self.members[name] = member
                    if not member.isfile():
                        continue
                    # Enough for ELF/program headers, font magic and versions.
                    # HTML samples also verify the generated asset references.
                    basename = posixpath.basename(name)
                    is_binary = (".so" in basename or basename in
                                 ("rinkhals-web", "python", "dropbear", "sftp-server",
                                  "ld-uClibc", "busybox.rinkhals"))
                    limit = 0
                    if is_binary or basename.endswith(".ttf") or basename == ".version":
                        limit = 65536
                    if name.startswith(WEB_ROOT + "/ui/") and name.endswith(".html"):
                        require(member.size <= 2 * CHUNK_SIZE, f"oversized HTML: {name}")
                        limit = 2 * CHUNK_SIZE
                    if limit:
                        with archive.extractfile(member) as source:
                            self.samples[name] = source.read(limit)
            # Read through the gzip trailer too, validating its CRC and length.
            while uncompressed.read(CHUNK_SIZE):
                pass

    def file(self, name):
        """Resolve essential symlinks/hardlinks inside their payload root."""
        name = archive_name(name)
        seen = set()
        while True:
            require(name not in seen, f"cyclic link: {name}")
            seen.add(name)
            member = self.members.get(name)
            require(member is not None, f"missing file: {name}")
            if member.issym() or member.islnk():
                target = member.linkname
                if member.issym():
                    # Absolute Buildroot symlinks are relative to /rinkhals
                    # after installation, not the update staging directory.
                    root = "rinkhals" if name.startswith("rinkhals/") else ""
                    target = (root + target if target.startswith("/") else
                              posixpath.join(posixpath.dirname(name), target))
                name = posixpath.normpath(target)
                require(name != ".." and not name.startswith(("../", "/")),
                        f"escaping link: {member.name}")
                continue
            require(member.isfile() and member.size > 0, f"empty/non-file payload: {name}")
            return member, self.samples.get(name, b"")

    def files(self, *names):
        for name in names:
            self.file(name)

    def matching(self, pattern):
        names = [name for name in self.members if fnmatch.fnmatchcase(name, pattern)]
        require(names, f"missing files matching: {pattern}")
        for name in names:
            self.file(name)
        return names

    def elf(self, name, static=False, executable=False):
        member, data = self.file(name)
        require(len(data) >= 52 and data[:7] == b"\x7fELF\x01\x01\x01",
                f"not a 32-bit little-endian ELF: {name}")
        fields = struct.unpack_from("<HHIIIIIHHHHHH", data, 16)
        kind, machine, version = fields[:3]
        require(machine == 40 and kind in (2, 3) and version == 1,
                f"not an ARM executable/shared library: {name}")
        if executable:
            require(member.mode & 0o111, f"missing executable permissions: {name}")
        if static:
            offset, entry_size, count = fields[4], fields[8], fields[9]
            require(entry_size == 32 and count > 0 and offset >= 52 and
                    offset + entry_size * count <= len(data),
                    f"invalid ELF program headers: {name}")
            for index in range(count):
                header = struct.unpack_from("<IIIIIIII", data, offset + index * entry_size)
                require(header[0] not in (2, 3),
                        f"backend is dynamically linked (PT_DYNAMIC/PT_INTERP): {name}")
                require(header[1] + header[4] <= member.size,
                        f"truncated ELF segment: {name}")

    def touch_ui(self, root):
        self.files(*(root + "/" + name for name in
                     ("common.py", "lvgl_rinkhals.py", "lvgl/__init__.py",
                      "assets/icon.png", "assets/snake-grid.webp", "assets/snake-error.webp")))
        self.elf(root + "/lvgl/lvgl-arm-linux-uclibc.so")
        for font in FONTS:
            _, data = self.file(root + "/assets/" + font)
            require(data[:4] in (b"\x00\x01\x00\x00", b"OTTO", b"ttcf"),
                    f"invalid font: {root}/assets/{font}")

    def web_ui(self):
        root = WEB_ROOT + "/ui/"
        self.file(root + "index.html")
        self.matching(root + "_app/immutable/entry/start.*.js")
        self.matching(root + "_app/immutable/entry/app.*.js")
        self.matching(root + "_app/immutable/assets/*.css")
        for name, data in self.samples.items():
            if name.startswith(root) and name.endswith(".html"):
                html = data.decode("utf-8")
                # Svelte references assets in both preload tags and import().
                for reference in re.findall(r'''["']([^"']*_app/immutable/[^"']+)["']''', html):
                    asset = posixpath.normpath(posixpath.join(posixpath.dirname(name), reference))
                    require(asset.startswith(root), f"escaping UI asset reference: {reference}")
                    self.file(asset)

    def version(self):
        _, root = self.file(".version")
        _, installed = self.file("rinkhals/.version")
        version = root.decode("ascii").strip()
        require(version and root.strip() == installed.strip(), "inconsistent .version files")
        require(re.fullmatch(r"dev|[0-9a-f]{40}|[0-9]{8}_[0-9]{2}(?:_[a-z0-9_-]+)?", version),
                f"invalid version: {version!r}")
        return version

    def validate(self, kind):
        self.file("update.sh")
        if kind == "update":
            self.files("start.sh.patch", "rinkhals/start.sh", "rinkhals/tools.sh",
                       "rinkhals/opt/rinkhals/tools/update-lock.sh",
                       "rinkhals/usr/bin/python", "rinkhals/bin/busybox.rinkhals")
            self.files(*(MOONRAKER_ROOT + "/" + name for name in
                         ("app.sh", "moonraker.sh", "moonraker-lifecycle.sh", "kobra.py",
                          "mmu_ace.py", "mmu_ace_metadata.py", "memory_manager.py")))
            self.elf(WEB_ROOT + "/rinkhals-web", static=True, executable=True)
            self.web_ui()
            self.touch_ui(TOUCH_ROOT)
            self.file(TOUCH_ROOT + "/rinkhals-ui.py")
            return self.version()
        if kind == "installer":
            self.files("start.sh.patch", "rinkhals-install.py", "tools/update-lock.sh",
                       *("tools/" + tool + ".sh" for tool in
                         ("backup-partitions", "config-reset", "debug-bundle",
                          "clean-rinkhals", "rinkhals-uninstall")), *SSH_FILES, *INSTALLER_LIBS)
            self.touch_ui(".")
            self.elf("python")
            self.files("lib/python3.11/encodings/__init__.py",
                       "lib/python3.11/site-packages/cffi/__init__.py",
                       "lib/python3.11/site-packages/requests/__init__.py",
                       "lib/python3.11/site-packages/certifi/cacert.pem")
            for name in self.matching("_cffi_backend*.so"):
                self.elf(name)
            for name in self.matching("lib/python3.11/site-packages/_cffi_backend*.so"):
                self.elf(name)
            self.elf("dropbear")
            self.elf("sftp-server")
            return self.version()
        if kind in TOOLS:
            if kind == "ssh":
                # This standalone rescue server does not modify Rinkhals and
                # its build script intentionally does not include update-lock.
                self.files(*SSH_FILES)
                self.elf("dropbear")
                self.elf("sftp-server")
            else:
                self.file("update-lock.sh")
            return None
        raise ValidationError(f"unknown package kind: {kind}")


def verify_swu(path, model, kind):
    """Return the embedded version (updates/installers), otherwise None."""
    password = PASSWORDS[model].encode("ascii")
    with zipfile.ZipFile(path) as archive:
        members = zip_index(archive)
        files = {name for name, member in members.items() if not member.is_dir()}
        require(files == {TAR_NAME, MD5_NAME}, "SWU must contain setup.tar.gz and its MD5 only")
        for name in files:
            require(members[name].flag_bits & 1, f"unencrypted SWU member: {name}")
        require(members[MD5_NAME].file_size <= 128, "invalid MD5 file size")
        expected = archive.read(members[MD5_NAME], pwd=password).decode("ascii").strip()
        require(re.fullmatch(r"[0-9a-fA-F]{32}", expected), "invalid MD5 digest")
        with tempfile.TemporaryFile() as compressed:
            unzip = shutil.which("unzip")
            if unzip:
                result = subprocess.run(
                    [unzip, "-p", "-P", password.decode("ascii"), str(Path(path).resolve()),
                     members[TAR_NAME].filename],
                    stdout=compressed, stderr=subprocess.PIPE, check=False,
                )
                require(result.returncode == 0,
                        "cannot decrypt/read payload: " + result.stderr.decode("utf-8", errors="replace").strip())
            else:
                with archive.open(members[TAR_NAME], pwd=password) as source:
                    shutil.copyfileobj(source, compressed, CHUNK_SIZE)
            require(compressed.tell() == members[TAR_NAME].file_size, "incomplete decrypted payload")
            compressed.seek(0)
            digest = hashlib.md5()
            for chunk in iter(lambda: compressed.read(CHUNK_SIZE), b""):
                digest.update(chunk)
            require(digest.hexdigest() == expected.lower(), "setup.tar.gz MD5 mismatch")
            compressed.seek(0)
            return Payload(compressed).validate(kind)


def verify_tools(path, model):
    with zipfile.ZipFile(path) as archive:
        members = zip_index(archive)
        expected = {f"{tool}-{model}.swu" for tool in TOOLS}
        require(set(members) == expected, "tools ZIP must contain exactly: " + ", ".join(sorted(expected)))
        with tempfile.TemporaryDirectory(prefix="verify-swu-") as temporary:
            for tool in TOOLS:
                name = f"{tool}-{model}.swu"
                require(not members[name].flag_bits & 1, f"encrypted outer tools ZIP member: {name}")
                nested = Path(temporary) / name
                with archive.open(members[name]) as source, nested.open("wb") as target:
                    shutil.copyfileobj(source, target, CHUNK_SIZE)
                try:
                    verify_swu(nested, model, tool)
                except (ValidationError, OSError, ValueError, RuntimeError, EOFError,
                        tarfile.TarError, zipfile.BadZipFile) as error:
                    raise ValidationError(f"{name}: {error}") from error
                finally:
                    nested.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="dist directory exported by Docker or just swu-all")
    parser.add_argument("--model", action="append", choices=tuple(PASSWORDS), help="repeat to select models")
    parser.add_argument("--kind", action="append", choices=KINDS, help="repeat to select package groups")
    parser.add_argument("--expected-version", help="require update/installer .version to match this value")
    args = parser.parse_args(argv)
    models = list(dict.fromkeys(args.model or PASSWORDS))
    kinds = list(dict.fromkeys(args.kind or KINDS))
    errors = []
    versions = {}
    for model in models:
        for kind in kinds:
            path = args.directory / f"{kind}-{model}.{'zip' if kind == 'tools' else 'swu'}"
            try:
                if kind == "tools":
                    verify_tools(path, model)
                    # just swu-tools also leaves the individual SWUs in dist.
                    for tool in TOOLS:
                        loose = args.directory / f"{tool}-{model}.swu"
                        if loose.exists():
                            verify_swu(loose, model, tool)
                else:
                    version = verify_swu(path, model, kind)
                    versions[path.name] = version
                    require(args.expected_version is None or version == args.expected_version,
                            f"version {version!r}, expected {args.expected_version!r}")
                print(f"OK {path.name}", flush=True)
            except (ValidationError, OSError, ValueError, RuntimeError, EOFError,
                    tarfile.TarError, zipfile.BadZipFile) as error:
                errors.append(f"{path.name}: {error}")
                print(f"FAIL {errors[-1]}", file=sys.stderr, flush=True)
    if len(set(versions.values())) > 1:
        errors.append("versions differ between packages: " + ", ".join(
            f"{name}={version}" for name, version in versions.items()))
        print(f"FAIL {errors[-1]}", file=sys.stderr)
    if errors:
        print(f"Package verification failed: {len(errors)} error(s).", file=sys.stderr)
        return 1
    print(f"Verified {len(models) * len(kinds)} artifacts for {', '.join(models)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
