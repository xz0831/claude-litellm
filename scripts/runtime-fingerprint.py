#!/usr/bin/env python3
"""Compute the immutable byte fingerprint of a claude-litellm runtime.

The fingerprint covers ``pyvenv.cfg``, the complete ``bin`` tree, and every
directory and regular file below each site-packages root. Paths, types, file
modes, sizes, link targets, and file bytes are domain-separated in a stable
order. Bytecode caches are deliberately in scope: ``-B`` prevents new writes
but does not prevent Python from loading a forged, timestamp-valid ``.pyc``.

This script intentionally uses only the standard library and is invoked with
an external Python 3.13 interpreter's ``-I -B -S`` flags. The managed runtime
is not executed until its complete byte inventory has matched.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import stat
import sys
from pathlib import Path


DOMAIN = b"claude-litellm-runtime-content-v2\0"
HEX_SHA256 = re.compile(r"[0-9a-f]{64}")
PYTHON_LIB = re.compile(r"python\d+\.\d+")


class FingerprintError(RuntimeError):
    """The runtime tree cannot be measured safely."""


def _record(hasher: "hashlib._Hash", *fields: bytes) -> None:
    for field in fields:
        hasher.update(len(field).to_bytes(8, "big"))
        hasher.update(field)


def _site_packages_roots(runtime_root: Path) -> list[Path]:
    roots: list[Path] = []
    for lib_name in ("lib", "lib64"):
        lib_root = runtime_root / lib_name
        if not lib_root.exists():
            continue
        info = lib_root.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise FingerprintError(f"unsafe runtime library root: {lib_root}")
        for python_root in sorted(lib_root.iterdir(), key=lambda item: item.name):
            if not PYTHON_LIB.fullmatch(python_root.name):
                continue
            info = python_root.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise FingerprintError(f"unsafe Python library root: {python_root}")
            candidate = python_root / "site-packages"
            if not candidate.exists():
                continue
            info = candidate.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise FingerprintError(f"unsafe site-packages root: {candidate}")
            roots.append(candidate)
    if not roots:
        raise FingerprintError(f"no site-packages root below runtime: {runtime_root}")
    return sorted(roots, key=lambda item: item.relative_to(runtime_root).as_posix())


def _hash_file(path: Path) -> tuple[int, bytes]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise FingerprintError(f"runtime path is not a regular file: {path}")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        return info.st_size, digest.digest()
    finally:
        os.close(descriptor)


def _hash_tree(
    hasher: "hashlib._Hash",
    runtime_root: Path,
    tree_root: Path,
    *,
    allow_symlinks: bool,
) -> None:
    root_info = tree_root.lstat()
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise FingerprintError(f"unsafe runtime tree root: {tree_root}")
    root_relative = tree_root.relative_to(runtime_root).as_posix().encode()
    _record(hasher, b"root", root_relative, f"{stat.S_IMODE(root_info.st_mode):04o}".encode())
    pending = [tree_root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name)
        except OSError as error:
            raise FingerprintError(f"cannot enumerate runtime directory: {directory}") from error
        child_directories: list[Path] = []
        for entry in entries:
            path = Path(entry.path)
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as error:
                raise FingerprintError(f"cannot inspect runtime path: {path}") from error
            relative = path.relative_to(runtime_root).as_posix().encode()
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISLNK(info.st_mode):
                if not allow_symlinks:
                    raise FingerprintError(f"runtime site-packages contains a symlink: {path}")
                try:
                    target = os.fsencode(os.readlink(path))
                except OSError as error:
                    raise FingerprintError(f"cannot read runtime symlink: {path}") from error
                _record(hasher, b"symlink", relative, f"{mode:04o}".encode(), target)
                continue
            if stat.S_ISDIR(info.st_mode):
                _record(hasher, b"directory", relative, f"{mode:04o}".encode())
                child_directories.append(path)
                continue
            if not stat.S_ISREG(info.st_mode):
                raise FingerprintError(f"runtime tree contains a special file: {path}")
            size, content_digest = _hash_file(path)
            _record(
                hasher,
                b"file",
                relative,
                f"{mode:04o}".encode(),
                str(size).encode(),
                content_digest,
            )
        pending.extend(reversed(child_directories))


def runtime_fingerprint(runtime_root: Path) -> str:
    try:
        root_info = runtime_root.lstat()
    except OSError as error:
        raise FingerprintError(f"runtime root is unavailable: {runtime_root}") from error
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise FingerprintError(f"unsafe runtime root: {runtime_root}")

    hasher = hashlib.sha256(DOMAIN)
    pyvenv_config = runtime_root / "pyvenv.cfg"
    try:
        pyvenv_info = pyvenv_config.lstat()
    except OSError as error:
        raise FingerprintError(f"runtime configuration is unavailable: {pyvenv_config}") from error
    if stat.S_ISLNK(pyvenv_info.st_mode) or not stat.S_ISREG(pyvenv_info.st_mode):
        raise FingerprintError(f"unsafe runtime configuration: {pyvenv_config}")
    size, content_digest = _hash_file(pyvenv_config)
    _record(
        hasher,
        b"file",
        b"pyvenv.cfg",
        f"{stat.S_IMODE(pyvenv_info.st_mode):04o}".encode(),
        str(size).encode(),
        content_digest,
    )
    _hash_tree(
        hasher,
        runtime_root,
        runtime_root / "bin",
        allow_symlinks=True,
    )
    for site_root in _site_packages_roots(runtime_root):
        _hash_tree(
            hasher,
            runtime_root,
            site_root,
            allow_symlinks=False,
        )
    return hasher.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--expect", help="exit nonzero unless the digest matches")
    args = parser.parse_args()
    if args.expect is not None and not HEX_SHA256.fullmatch(args.expect):
        parser.error("--expect must be a lowercase SHA-256 digest")
    try:
        digest = runtime_fingerprint(args.runtime_root)
    except (FingerprintError, OSError) as error:
        print(f"runtime fingerprint failed: {error}", file=sys.stderr)
        return 1
    if args.expect is not None and digest != args.expect:
        print("runtime fingerprint mismatch", file=sys.stderr)
        return 1
    print(digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
