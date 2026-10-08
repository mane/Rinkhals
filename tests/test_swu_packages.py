"""Exercise the actual encrypted SWU envelope with harmless synthetic payloads."""

import contextlib
import gzip
import hashlib
import importlib.util
import io
import shutil
import struct
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("verify_swu", ROOT / "build/verify-swu.py")
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)
MODEL_CODES = {"k2p-k3": "K3", "k3m": "K3M", "ks1": "KS1", "ks1m": "KS1M"}


def elf(machine=40, program_type=1):
    # A small ELF32 with one in-bounds load segment; never executed.
    ident = b"\x7fELF\x01\x01\x01" + bytes(9)
    header = struct.pack("<HHIIIIIHHHHHH", 2, machine, 1, 0, 52, 0, 0, 52, 32, 1, 0, 0, 0)
    program = struct.pack("<IIIIIIII", program_type, 0, 0, 0, 88, 88, 5, 4)
    return ident + header + program + bytes(4)


def touch_files(prefix):
    prefix = prefix + "/" if prefix else ""
    result = {prefix + name: b"fixture\n" for name in (
        "common.py", "lvgl_rinkhals.py", "lvgl/__init__.py", "assets/icon.png",
        "assets/snake-grid.webp", "assets/snake-error.webp",
    )}
    result[prefix + "lvgl/lvgl-arm-linux-uclibc.so"] = elf()
    for name in ("AlibabaSans-Regular.ttf", "MaterialIcons-Regular.ttf"):
        result[prefix + "assets/" + name] = b"\x00\x01\x00\x00font fixture"
    return result


def ssh_files():
    return {name: elf() for name in (
        "dropbear", "sftp-server", "dropbear_rsa_host_key", "libcrypto.so.1.1",
        "libssl.so.1.1", "libatomic.so.1", "libc.so.0", "ld-uClibc",
    )}


def update_files():
    result = {name: b"fixture\n" for name in (
        "update.sh", "start.sh.patch", "rinkhals/start.sh", "rinkhals/tools.sh",
        "rinkhals/opt/rinkhals/tools/update-lock.sh", "rinkhals/usr/bin/python",
        "rinkhals/bin/busybox.rinkhals", VERIFY.TOUCH_ROOT + "/rinkhals-ui.py",
    )}
    result.update(touch_files(VERIFY.TOUCH_ROOT))
    for name in ("app.sh", "moonraker.sh", "moonraker-lifecycle.sh", "kobra.py",
                 "mmu_ace.py", "mmu_ace_metadata.py", "memory_manager.py"):
        result[VERIFY.MOONRAKER_ROOT + "/" + name] = b"fixture\n"
    result[VERIFY.WEB_ROOT + "/rinkhals-web"] = elf()
    ui = VERIFY.WEB_ROOT + "/ui/"
    result[ui + "index.html"] = (
        b'<link href="./_app/immutable/assets/base.hash.css" rel="stylesheet">'
        b'<script type="module">import("./_app/immutable/entry/start.hash.js");'
        b'import("./_app/immutable/entry/app.hash.js");</script>'
    )
    for name in ("entry/start.hash.js", "entry/app.hash.js", "assets/base.hash.css"):
        result[ui + "_app/immutable/" + name] = b"fixture\n"
    result[".version"] = result["rinkhals/.version"] = b"dev\n"
    return result


def installer_files():
    result = {name: b"fixture\n" for name in (
        "update.sh", "start.sh.patch", "rinkhals-install.py", "tools/update-lock.sh",
        "tools/backup-partitions.sh", "tools/config-reset.sh", "tools/debug-bundle.sh",
        "tools/clean-rinkhals.sh", "tools/rinkhals-uninstall.sh",
        "lib/python3.11/encodings/__init__.py",
        "lib/python3.11/site-packages/cffi/__init__.py",
        "lib/python3.11/site-packages/requests/__init__.py",
        "lib/python3.11/site-packages/certifi/cacert.pem",
    )}
    result.update(touch_files(""))
    result.update(ssh_files())
    for name in ("python", "libffi.so.8", "libstdc++.so.6", "libpython3.11.so.1.0",
                 "libz.so.1", "libgcc_s.so.1", "ld-uClibc.so.1",
                 "_cffi_backend.cpython-311-arm-linux-gnueabihf.so",
                 "lib/python3.11/site-packages/_cffi_backend.cpython-311-arm-linux-gnueabihf.so"):
        result[name] = elf()
    result[".version"] = result["rinkhals/.version"] = b"dev\n"
    return result


@unittest.skipUnless(shutil.which("zip"), "SWU fixtures require the build tool zip")
class SwuPackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="swu-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.dist = self.root / "dist"
        self.dist.mkdir()

    def package(self, files, kind="update", model="k2p-k3", digest=None,
                duplicate=None, modes=None, links=None, corrupt_gzip=False):
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w") as archive:
            root = tarfile.TarInfo(".")
            root.type = tarfile.DIRTYPE
            archive.addfile(root)
            entries = list(files.items())
            if duplicate:
                entries.append((duplicate, files[duplicate]))
            for name, data in entries:
                member = tarfile.TarInfo("./" + name)
                member.size = len(data)
                member.mode = (modes or {}).get(name, 0o755)
                archive.addfile(member, io.BytesIO(data))
            for name, target in (links or {}).items():
                member = tarfile.TarInfo("./" + name)
                member.type = tarfile.SYMTYPE
                member.linkname = target
                archive.addfile(member)
        compressed = gzip.compress(raw.getvalue())
        if corrupt_gzip:
            compressed = compressed[:-8] + bytes([compressed[-8] ^ 0xFF]) + compressed[-7:]
        with tempfile.TemporaryDirectory(dir=self.root) as stage:
            payload_dir = Path(stage) / "update_swu"
            payload_dir.mkdir()
            (payload_dir / "setup.tar.gz").write_bytes(compressed)
            (payload_dir / "setup.tar.gz.md5").write_text(
                (digest or hashlib.md5(compressed).hexdigest()) + "\n"
            )
            output = self.dist / f"{kind}-{model}.swu"
            # Use the production envelope/password selection, not a mocked ZIP.
            subprocess.run(
                ["sh", "-c", '. "$1"; compress_swu "$2" "$3" "$4"', "fixture",
                 str(ROOT / "build/tools.sh"), MODEL_CODES[model], str(output), stage],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        return output

    def tools(self, model="k2p-k3", missing=None):
        path = self.dist / f"tools-{model}.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for tool in ("backup-partitions", "config-reset", "debug-bundle", "ssh"):
                if tool == missing:
                    continue
                files = {"update.sh": b"#!/bin/sh\nexit 0\n"}
                files.update(ssh_files() if tool == "ssh" else {"update-lock.sh": b"fixture"})
                nested = self.package(files, kind=tool, model=model)
                archive.write(nested, nested.name)
                nested.unlink()
        return path

    def run_cli(self, *arguments):
        with contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()) as stderr:
            code = VERIFY.main([str(self.dist), *arguments])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_complete_four_model_export_and_nested_tools(self):
        for model in MODEL_CODES:
            self.package(update_files(), model=model)
            self.package(installer_files(), kind="installer", model=model)
            self.tools(model)
        with mock.patch.object(VERIFY, "verify_swu", wraps=VERIFY.verify_swu) as verify:
            code, stdout, stderr = self.run_cli("--expected-version", "dev")
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(verify.call_count, 24)  # 8 direct SWUs + 16 nested tools
        self.assertIn("Verified 12 artifacts", stdout)

    def test_selected_model_and_kind_do_not_require_other_packages(self):
        self.package(update_files(), model="k3m")
        self.assertEqual(self.run_cli("--model", "k3m", "--kind", "update")[0], 0)

    def test_missing_package_and_version_mismatch_fail_cli(self):
        files = update_files()
        files[".version"] = files["rinkhals/.version"] = b"20261007_01\n"
        self.package(files)
        code, _, stderr = self.run_cli("--model", "k2p-k3", "--expected-version", "dev")
        self.assertEqual(code, 1)
        self.assertIn("expected 'dev'", stderr)
        self.assertIn("installer-k2p-k3.swu", stderr)
        self.assertIn("tools-k2p-k3.zip", stderr)

    def test_cross_package_version_mismatch(self):
        self.package(update_files())
        files = installer_files()
        files[".version"] = files["rinkhals/.version"] = b"20261007_01\n"
        self.package(files, kind="installer")
        code, _, stderr = self.run_cli("--model", "k2p-k3", "--kind", "update", "--kind", "installer")
        self.assertEqual(code, 1)
        self.assertIn("versions differ", stderr)

    def test_uses_model_password(self):
        path = self.package(update_files(), model="k3m")
        with self.assertRaisesRegex(RuntimeError, "password"):
            VERIFY.verify_swu(path, "k2p-k3", "update")

    def test_stdlib_decryption_fallback(self):
        path = self.package(update_files())
        with mock.patch.object(VERIFY.shutil, "which", return_value=None):
            self.assertEqual(VERIFY.verify_swu(path, "k2p-k3", "update"), "dev")

    def test_checksum_mismatch(self):
        path = self.package(update_files(), digest="0" * 32)
        with self.assertRaisesRegex(VERIFY.ValidationError, "MD5 mismatch"):
            VERIFY.verify_swu(path, "k2p-k3", "update")

    def test_corrupt_gzip_with_matching_md5(self):
        path = self.package(update_files(), corrupt_gzip=True)
        with self.assertRaisesRegex(gzip.BadGzipFile, "CRC check failed"):
            VERIFY.verify_swu(path, "k2p-k3", "update")

    def test_unencrypted_swu_rejected(self):
        path = self.package(update_files())
        with zipfile.ZipFile(path) as archive:
            contents = {name: archive.read(name, pwd=VERIFY.PASSWORDS["k2p-k3"].encode())
                        for name in archive.namelist()}
        with zipfile.ZipFile(path, "w") as archive:
            for name, content in contents.items():
                archive.writestr(name, content)
        with self.assertRaisesRegex(VERIFY.ValidationError, "unencrypted"):
            VERIFY.verify_swu(path, "k2p-k3", "update")

    def test_missing_runtime_helpers_are_rejected(self):
        for name in ("rinkhals/opt/rinkhals/tools/update-lock.sh",
                     VERIFY.MOONRAKER_ROOT + "/moonraker-lifecycle.sh"):
            with self.subTest(name=name):
                files = update_files()
                del files[name]
                path = self.package(files)
                with self.assertRaisesRegex(VERIFY.ValidationError, "missing file"):
                    VERIFY.verify_swu(path, "k2p-k3", "update")

    def test_backend_must_be_static_executable_arm(self):
        name = VERIFY.WEB_ROOT + "/rinkhals-web"
        for data, mode, message in ((elf(machine=62), 0o755, "not an ARM"),
                                    (elf(program_type=3), 0o755, "dynamically linked"),
                                    (elf(program_type=2), 0o755, "dynamically linked"),
                                    (elf(), 0o644, "executable permissions"),
                                    (elf()[:-1], 0o755, "truncated ELF segment")):
            with self.subTest(message=message, mode=mode):
                files = update_files()
                files[name] = data
                path = self.package(files, modes={name: mode})
                with self.assertRaisesRegex(VERIFY.ValidationError, message):
                    VERIFY.verify_swu(path, "k2p-k3", "update")

    def test_referenced_web_asset_must_exist(self):
        files = update_files()
        files[VERIFY.WEB_ROOT + "/ui/index.html"] += b'<link href="./_app/immutable/chunks/missing.js">'
        path = self.package(files)
        with self.assertRaisesRegex(VERIFY.ValidationError, "missing.js"):
            VERIFY.verify_swu(path, "k2p-k3", "update")

    def test_installer_requires_fonts_lvgl_and_lock(self):
        for name in ("assets/AlibabaSans-Regular.ttf", "assets/MaterialIcons-Regular.ttf",
                     "lvgl/lvgl-arm-linux-uclibc.so", "tools/update-lock.sh"):
            with self.subTest(name=name):
                files = installer_files()
                del files[name]
                path = self.package(files, kind="installer")
                with self.assertRaisesRegex(VERIFY.ValidationError, "missing file"):
                    VERIFY.verify_swu(path, "k2p-k3", "installer")

    def test_installer_wrong_architecture_and_font_corruption(self):
        for name, data, message in (("lvgl/lvgl-arm-linux-uclibc.so", elf(machine=62), "not an ARM"),
                                    ("assets/MaterialIcons-Regular.ttf", b"broken", "invalid font")):
            files = installer_files()
            files[name] = data
            path = self.package(files, kind="installer")
            with self.assertRaisesRegex(VERIFY.ValidationError, message):
                VERIFY.verify_swu(path, "k2p-k3", "installer")

    def test_installer_patched_binaries_need_not_have_executable_tar_mode(self):
        path = self.package(installer_files(), kind="installer",
                            modes={"python": 0o644, "dropbear": 0o644, "sftp-server": 0o644})
        self.assertEqual(VERIFY.verify_swu(path, "k2p-k3", "installer"), "dev")

    def test_complete_tools_archive_and_missing_nested_tool(self):
        VERIFY.verify_tools(self.tools(), "k2p-k3")
        with self.assertRaisesRegex(VERIFY.ValidationError, "exactly"):
            VERIFY.verify_tools(self.tools(missing="ssh"), "k2p-k3")

    def test_maintenance_tools_require_lock_but_ssh_does_not(self):
        path = self.package({"update.sh": b"#!/bin/sh\n"}, kind="debug-bundle")
        with self.assertRaisesRegex(VERIFY.ValidationError, "update-lock.sh"):
            VERIFY.verify_swu(path, "k2p-k3", "debug-bundle")
        files = {"update.sh": b"#!/bin/sh\n", **ssh_files()}
        path = self.package(files, kind="ssh")
        VERIFY.verify_swu(path, "k2p-k3", "ssh")

    def test_loose_tools_are_validated_if_present(self):
        self.tools()
        self.package({"update.sh": b"fixture"}, kind="config-reset")
        code, _, stderr = self.run_cli("--model", "k2p-k3", "--kind", "tools")
        self.assertEqual(code, 1)
        self.assertIn("update-lock.sh", stderr)

    def test_relative_and_buildroot_absolute_symlinks(self):
        for target in ("python3.11", "/usr/bin/python3.11"):
            with self.subTest(target=target):
                files = update_files()
                del files["rinkhals/usr/bin/python"]
                files["rinkhals/usr/bin/python3.11"] = elf()
                path = self.package(files, links={"rinkhals/usr/bin/python": target})
                self.assertEqual(VERIFY.verify_swu(path, "k2p-k3", "update"), "dev")

    def test_cyclic_symlink_rejected(self):
        files = update_files()
        del files["rinkhals/usr/bin/python"]
        path = self.package(files, links={"rinkhals/usr/bin/python": "python"})
        with self.assertRaisesRegex(VERIFY.ValidationError, "cyclic link"):
            VERIFY.verify_swu(path, "k2p-k3", "update")

    def test_unsafe_and_duplicate_tar_members_rejected_without_extraction(self):
        files = update_files()
        files["../escape"] = b"must not escape"
        path = self.package(files)
        with self.assertRaisesRegex(VERIFY.ValidationError, "unsafe archive path"):
            VERIFY.verify_swu(path, "k2p-k3", "update")
        self.assertFalse((self.root / "escape").exists())
        path = self.package(update_files(), duplicate="update.sh")
        with self.assertRaisesRegex(VERIFY.ValidationError, "duplicate tar member"):
            VERIFY.verify_swu(path, "k2p-k3", "update")

    def test_payload_scripts_are_never_executed(self):
        marker = self.root / "payload-was-executed"
        path = self.package({"update.sh": f"#!/bin/sh\ntouch {marker}\n".encode(),
                             "update-lock.sh": b"fixture"}, kind="config-reset")
        VERIFY.verify_swu(path, "k2p-k3", "config-reset")
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
