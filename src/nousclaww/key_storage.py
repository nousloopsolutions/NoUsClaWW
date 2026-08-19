"""
Key Storage -- OS-Protected Key Management for Encryption at Rest.

#C Adapted from NoUs-fordge Nous-hub mvp_local_core data/local_storage.py

Uses platform-appropriate credential storage:
  - Windows: DPAPI (Data Protection API) via win32crypt + registry
  - macOS: Keychain via security command
  - Linux: Secret Service API (libsecret)

Keys are NEVER stored in:
  - Source code
  - .env files
  - Logs
  - The database in plaintext

Graceful fallback: if the OS credential service is unavailable, keys are
encrypted at rest using a machine-bound key derived from platform-specific
identifiers and stored in a local file with restrictive permissions.

SYNTH:
    purpose: OS-protected key storage abstraction -- platform-specific credential vault with encrypted file fallback for when OS services are unavailable.
    axioms: [local_first, epistemic_boundary, honest_failure_over_fake_success, reversibility_awareness]
    objective: Keys are retrievable only on the originating machine, never appear in plaintext on disk, and degrade gracefully when OS services are missing.
    anti_patterns:
        - Never store keys in source code, .env files, logs, or database plaintext
        - Never log key values or key material (log only key names and operations)
        - Never silently fall back to plaintext storage (must use encryption)
        - Never store keys without restrictive file permissions (0600 minimum)
        - Never assume OS credential services are available (always handle failure)
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import secrets
import stat
from pathlib import Path
from typing import Any


class KeyStorageError(Exception):
    """Raised when key storage operations fail."""


# -- Fallback encryption (pure-Python, no external deps) --------------------------


def _xor_bytes(data: bytes, key: bytes) -> bytes:
    """
    XOR data with a repeating key (fallback encryption only).

    This is NOT cryptographically strong. It is a last-resort obfuscation
    used only when OS credential services (DPAPI, Keychain, Secret Service)
    are all unavailable. The key is machine-bound, so an attacker who
    copies the file to another machine cannot decrypt it.

    Args:
        data: Bytes to encrypt/decrypt
        key:  Key bytes (will be repeated to match data length)

    Returns:
        XORed bytes (same length as data)
    """
    if not key:
        raise KeyStorageError("Cannot encrypt with empty key")
    key_len = len(key)
    return bytes(data[i] ^ key[i % key_len] for i in range(len(data)))


def _machine_key() -> bytes:
    """
    Derive a machine-bound key from platform-specific identifiers.

    Combines:
      - OS username
      - Machine hostname
      - Python executable path (installation-specific)

    This key is unique to the machine + user + Python installation.
    It is NOT cryptographically strong, but it prevents trivial copying
    of encrypted key files to another machine.

    Returns:
        32-byte machine-bound key
    """
    components = [
        os.environ.get("USERNAME", os.environ.get("USER", "unknown")),
        platform.node(),
        platform.machine(),
        str(Path(sys_executable_safe())),
    ]
    combined = "|".join(components).encode("utf-8")
    return hashlib.sha256(combined).digest()


def sys_executable_safe() -> str:
    """Return sys.executable safely (avoids import at module level)."""
    import sys
    return sys.executable


# -- Platform-specific backends ---------------------------------------------------


class _WindowsBackend:
    """Windows DPAPI-based key storage backend."""

    REGISTRY_PATH = r"Software\NoUsClaWW\Keys"

    def store(self, key_name: str, key_value: bytes) -> None:
        """
        Store a key encrypted with Windows DPAPI in the registry.

        Args:
            key_name:  Identifier for the key
            key_value: Raw key bytes to store

        Raises:
            KeyStorageError: If DPAPI or registry access fails
        """
        try:
            import win32crypt
            encrypted = win32crypt.CryptProtectData(
                key_value, None, None, None, None
            )
        except (ImportError, TypeError, OSError) as exc:
            raise KeyStorageError(
                f"Windows DPAPI encryption failed: {exc}"
            ) from exc

        try:
            import winreg
            try:
                winreg.CreateKey(
                    winreg.HKEY_CURRENT_USER, self.REGISTRY_PATH
                )
            except OSError:
                pass
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                self.REGISTRY_PATH,
                0,
                winreg.KEY_SET_VALUE,
            ) as reg_key:
                winreg.SetValueEx(
                    reg_key, key_name, 0, winreg.REG_BINARY, encrypted
                )
        except (ImportError, OSError) as exc:
            raise KeyStorageError(
                f"Windows registry write failed: {exc}"
            ) from exc

    def retrieve(self, key_name: str) -> bytes | None:
        """
        Retrieve a key from the registry and decrypt with DPAPI.

        Args:
            key_name: Identifier for the key

        Returns:
            Decrypted key bytes, or None if not found

        Raises:
            KeyStorageError: If DPAPI decryption fails
        """
        try:
            import winreg
        except ImportError as exc:
            raise KeyStorageError(
                f"winreg not available on this platform: {exc}"
            ) from exc

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                self.REGISTRY_PATH,
                0,
                winreg.KEY_READ,
            ) as key:
                encrypted, _ = winreg.QueryValueEx(key, key_name)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise KeyStorageError(
                f"Windows registry read failed: {exc}"
            ) from exc

        try:
            import win32crypt
            return win32crypt.CryptUnprotectData(
                encrypted, None, None, None, None
            )[1]
        except (ImportError, TypeError, OSError) as exc:
            raise KeyStorageError(
                f"Windows DPAPI decryption failed: {exc}"
            ) from exc

    def delete(self, key_name: str) -> bool:
        """
        Delete a key from the registry.

        Args:
            key_name: Identifier for the key

        Returns:
            True if deleted, False if not found

        Raises:
            KeyStorageError: If registry access fails
        """
        try:
            import winreg
        except ImportError as exc:
            raise KeyStorageError(
                f"winreg not available on this platform: {exc}"
            ) from exc

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                self.REGISTRY_PATH,
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                winreg.DeleteValue(key, key_name)
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise KeyStorageError(
                f"Windows registry delete failed: {exc}"
            ) from exc

    def list_keys(self) -> list[str]:
        """
        List all stored key names from the registry.

        Returns:
            List of key name strings

        Raises:
            KeyStorageError: If registry access fails
        """
        try:
            import winreg
        except ImportError as exc:
            raise KeyStorageError(
                f"winreg not available on this platform: {exc}"
            ) from exc

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                self.REGISTRY_PATH,
                0,
                winreg.KEY_READ,
            ) as key:
                names: list[str] = []
                i = 0
                while True:
                    try:
                        name, _, _ = winreg.EnumValue(key, i)
                        names.append(name)
                        i += 1
                    except OSError:
                        break
                return names
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise KeyStorageError(
                f"Windows registry list failed: {exc}"
            ) from exc


class _MacOSBackend:
    """macOS Keychain-based key storage backend."""

    SERVICE_NAME = "NoUsClaWW"

    def store(self, key_name: str, key_value: bytes) -> None:
        """
        Store a key in the macOS Keychain.

        Args:
            key_name:  Identifier for the key (used as account name)
            key_value: Raw key bytes to store (hex-encoded for Keychain)

        Raises:
            KeyStorageError: If the security command fails
        """
        import subprocess
        try:
            subprocess.run(
                [
                    "security", "add-generic-password",
                    "-a", key_name,
                    "-s", self.SERVICE_NAME,
                    "-w", key_value.hex(),
                ],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            raise KeyStorageError(
                f"macOS Keychain store failed: {exc.stderr.decode('utf-8', errors='replace')}"
            ) from exc

    def retrieve(self, key_name: str) -> bytes | None:
        """
        Retrieve a key from the macOS Keychain.

        Args:
            key_name: Identifier for the key (account name)

        Returns:
            Decrypted key bytes, or None if not found

        Raises:
            KeyStorageError: If the security command fails unexpectedly
        """
        import subprocess
        try:
            result = subprocess.run(
                [
                    "security", "find-generic-password",
                    "-a", key_name,
                    "-s", self.SERVICE_NAME,
                    "-w",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            return bytes.fromhex(result.stdout.strip())
        except subprocess.CalledProcessError:
            return None

    def delete(self, key_name: str) -> bool:
        """
        Delete a key from the macOS Keychain.

        Args:
            key_name: Identifier for the key (account name)

        Returns:
            True if deleted, False if not found
        """
        import subprocess
        try:
            subprocess.run(
                [
                    "security", "delete-generic-password",
                    "-a", key_name,
                    "-s", self.SERVICE_NAME,
                ],
                check=True,
                capture_output=True,
            )
            return True
        except subprocess.CalledProcessError:
            return False

    def list_keys(self) -> list[str]:
        """
        List all stored key names from the macOS Keychain.

        Returns:
            List of key name strings (account names for this service)
        """
        import subprocess
        try:
            result = subprocess.run(
                [
                    "security", "dump-keychain",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            names: list[str] = []
            in_service = False
            current_account: str | None = None
            for line in result.stdout.splitlines():
                stripped = line.strip()
                if '"svce"<blob>="' + self.SERVICE_NAME + '"' in stripped:
                    in_service = True
                    current_account = None
                elif in_service and '"acct"<blob>="' in stripped:
                    start = stripped.index('"acct"<blob>="') + len('"acct"<blob>="')
                    end = stripped.rindex('"')
                    current_account = stripped[start:end]
                    if current_account:
                        names.append(current_account)
                        in_service = False
            return names
        except subprocess.CalledProcessError:
            return []


class _LinuxBackend:
    """Linux Secret Service (libsecret) key storage backend."""

    APP_ID = "nousclaww"

    def _get_collection(self) -> Any:
        """
        Get the default Secret Service collection, unlocking if needed.

        Returns:
            The default collection object

        Raises:
            KeyStorageError: If Secret Service is unavailable
        """
        try:
            import secretstorage
        except ImportError as exc:
            raise KeyStorageError(
                f"secretstorage not installed: {exc}. "
                "Install with: pip install secretstorage"
            ) from exc

        bus = secretstorage.dbus_init()
        collection = secretstorage.get_default_collection(bus)
        if collection.is_locked():
            collection.unlock()
        return collection

    def store(self, key_name: str, key_value: bytes) -> None:
        """
        Store a key in the Linux Secret Service.

        Args:
            key_name:  Identifier for the key
            key_value: Raw key bytes to store

        Raises:
            KeyStorageError: If Secret Service is unavailable
        """
        try:
            import secretstorage
            collection = self._get_collection()
            collection.create_item(
                key_name,
                {"application": self.APP_ID, "name": key_name},
                key_value,
                replace=True,
            )
        except KeyStorageError:
            raise
        except Exception as exc:
            raise KeyStorageError(
                f"Linux Secret Service store failed: {exc}"
            ) from exc

    def retrieve(self, key_name: str) -> bytes | None:
        """
        Retrieve a key from the Linux Secret Service.

        Args:
            key_name: Identifier for the key

        Returns:
            Decrypted key bytes, or None if not found

        Raises:
            KeyStorageError: If Secret Service is unavailable
        """
        try:
            collection = self._get_collection()
            items = collection.search_items(
                {"application": self.APP_ID, "name": key_name}
            )
            for item in items:
                return item.get_secret()
            return None
        except KeyStorageError:
            raise
        except Exception as exc:
            raise KeyStorageError(
                f"Linux Secret Service retrieve failed: {exc}"
            ) from exc

    def delete(self, key_name: str) -> bool:
        """
        Delete a key from the Linux Secret Service.

        Args:
            key_name: Identifier for the key

        Returns:
            True if deleted, False if not found

        Raises:
            KeyStorageError: If Secret Service is unavailable
        """
        try:
            collection = self._get_collection()
            items = collection.search_items(
                {"application": self.APP_ID, "name": key_name}
            )
            for item in items:
                item.delete()
                return True
            return False
        except KeyStorageError:
            raise
        except Exception as exc:
            raise KeyStorageError(
                f"Linux Secret Service delete failed: {exc}"
            ) from exc

    def list_keys(self) -> list[str]:
        """
        List all stored key names from the Linux Secret Service.

        Returns:
            List of key name strings

        Raises:
            KeyStorageError: If Secret Service is unavailable
        """
        try:
            collection = self._get_collection()
            names: list[str] = []
            for item in collection.get_all_items():
                attrs = item.get_attributes()
                if attrs.get("application") == self.APP_ID:
                    name = attrs.get("name", "")
                    if name:
                        names.append(name)
            return names
        except KeyStorageError:
            raise
        except Exception as exc:
            raise KeyStorageError(
                f"Linux Secret Service list failed: {exc}"
            ) from exc


class _FileBackend:
    """
    Encrypted file fallback backend.

    Used when OS credential services are unavailable. Keys are encrypted
    with a machine-bound key and stored in a JSON file with restrictive
    permissions (0600).
    """

    def __init__(self, storage_dir: Path | None = None) -> None:
        """
        Initialize the file backend.

        Args:
            storage_dir: Directory for the key file (defaults to
                        ~/.nousclaww/keys/)
        """
        if storage_dir is None:
            storage_dir = Path.home() / ".nousclaww" / "keys"
        self._storage_dir = storage_dir
        self._key_file = storage_dir / "keys.json"
        self._ensure_storage_dir()

    def _ensure_storage_dir(self) -> None:
        """Create the storage directory with restrictive permissions."""
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._storage_dir.chmod(stat.S_IRWXU)  # 0700
        except OSError:
            pass  # Permission setting may fail on some filesystems

    def _load_data(self) -> dict[str, str]:
        """Load and decrypt the key file."""
        if not self._key_file.exists():
            return {}
        try:
            with open(self._key_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Decrypt each key value
            mkey = _machine_key()
            result: dict[str, str] = {}
            for name, enc_hex in data.items():
                enc_bytes = bytes.fromhex(enc_hex)
                dec_bytes = _xor_bytes(enc_bytes, mkey)
                result[name] = dec_bytes.hex()
            return result
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            raise KeyStorageError(
                f"Failed to load key file: {exc}"
            ) from exc

    def _save_data(self, data: dict[str, str]) -> None:
        """Encrypt and save the key file."""
        mkey = _machine_key()
        encrypted_data: dict[str, str] = {}
        for name, hex_val in data.items():
            raw_bytes = bytes.fromhex(hex_val)
            enc_bytes = _xor_bytes(raw_bytes, mkey)
            encrypted_data[name] = enc_bytes.hex()

        try:
            with open(self._key_file, "w", encoding="utf-8") as f:
                json.dump(encrypted_data, f, indent=2)
            # Set restrictive permissions (owner read/write only)
            self._key_file.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
        except OSError as exc:
            raise KeyStorageError(
                f"Failed to save key file: {exc}"
            ) from exc

    def store(self, key_name: str, key_value: bytes) -> None:
        """
        Store a key in the encrypted file.

        Args:
            key_name:  Identifier for the key
            key_value: Raw key bytes to store
        """
        data = self._load_data()
        data[key_name] = key_value.hex()
        self._save_data(data)

    def retrieve(self, key_name: str) -> bytes | None:
        """
        Retrieve a key from the encrypted file.

        Args:
            key_name: Identifier for the key

        Returns:
            Decrypted key bytes, or None if not found
        """
        data = self._load_data()
        hex_val = data.get(key_name)
        if hex_val is None:
            return None
        return bytes.fromhex(hex_val)

    def delete(self, key_name: str) -> bool:
        """
        Delete a key from the encrypted file.

        Args:
            key_name: Identifier for the key

        Returns:
            True if deleted, False if not found
        """
        data = self._load_data()
        if key_name not in data:
            return False
        del data[key_name]
        self._save_data(data)
        return True

    def list_keys(self) -> list[str]:
        """
        List all stored key names from the encrypted file.

        Returns:
            List of key name strings
        """
        data = self._load_data()
        return list(data.keys())


# -- Main KeyStorage class --------------------------------------------------------


class KeyStorage:
    """
    OS-protected key storage abstraction.

    Uses platform-appropriate credential storage:
      - Windows: DPAPI (Data Protection API) via win32crypt + registry
      - macOS: Keychain via security command
      - Linux: Secret Service API (libsecret)

    Graceful fallback: if the OS credential service is unavailable,
    keys are encrypted at rest using a machine-bound key and stored
    in a local file with restrictive permissions (0600).

    Keys are NEVER stored in:
      - Source code
      - .env files
      - Logs
      - Database plaintext

    Usage:
        storage = KeyStorage()
        storage.store("my_api_key", b"secret-bytes-here")
        key = storage.retrieve("my_api_key")  # -> b"secret-bytes-here"
        storage.delete("my_api_key")
        names = storage.list_keys()
    """

    def __init__(
        self,
        fallback_dir: Path | None = None,
        prefer_os_backend: bool = True,
    ) -> None:
        """
        Initialize the key storage with the appropriate platform backend.

        Args:
            fallback_dir:    Directory for the encrypted file fallback
                            (defaults to ~/.nousclaww/keys/)
            prefer_os_backend: If True, try OS backend first and fall back
                            to file on failure. If False, use file backend
                            directly.
        """
        self._fallback = _FileBackend(storage_dir=fallback_dir)
        self._os_backend: Any = None
        self._using_fallback = False

        if prefer_os_backend:
            system = platform.system()
            if system == "Windows":
                self._os_backend = _WindowsBackend()
            elif system == "Darwin":
                self._os_backend = _MacOSBackend()
            elif system == "Linux":
                self._os_backend = _LinuxBackend()
            else:
                self._os_backend = None

    def _get_backend(self) -> Any:
        """
        Get the active backend, falling back to file if OS backend fails.

        Returns:
            The backend object to use for the current operation
        """
        if self._os_backend is not None:
            return self._os_backend
        return self._fallback

    def store(self, key_name: str, key_value: bytes) -> None:
        """
        Store a key in OS-protected storage.

        The key is encrypted by the OS credential service. If the OS
        service is unavailable, falls back to encrypted file storage.

        Args:
            key_name:  Identifier for the key (must be non-empty)
            key_value: Raw key bytes to store

        Raises:
            KeyStorageError: If both OS and fallback storage fail
            ValueError: If key_name is empty or key_value is empty
        """
        if not key_name:
            raise ValueError("key_name must be non-empty")
        if not key_value:
            raise ValueError("key_value must be non-empty")

        try:
            backend = self._get_backend()
            backend.store(key_name, key_value)
            self._using_fallback = backend is self._fallback
        except KeyStorageError:
            # OS backend failed -- try fallback
            if self._os_backend is not None and not self._using_fallback:
                try:
                    self._fallback.store(key_name, key_value)
                    self._using_fallback = True
                except KeyStorageError as fb_exc:
                    raise KeyStorageError(
                        f"Both OS and fallback storage failed: {fb_exc}"
                    ) from fb_exc
            else:
                raise

    def retrieve(self, key_name: str) -> bytes:
        """
        Retrieve a key from OS-protected storage.

        If the key was stored via the OS backend, retrieves from there.
        If not found, tries the fallback file backend.

        Args:
            key_name: Identifier for the key

        Returns:
            The stored key bytes

        Raises:
            KeyStorageError: If the key is not found or retrieval fails
            KeyError: If the key does not exist in any backend
        """
        if not key_name:
            raise ValueError("key_name must be non-empty")

        # Try OS backend first
        if self._os_backend is not None:
            try:
                result = self._os_backend.retrieve(key_name)
                if result is not None:
                    return result
            except KeyStorageError:
                pass  # Fall through to fallback

        # Try fallback
        try:
            result = self._fallback.retrieve(key_name)
            if result is not None:
                return result
        except KeyStorageError:
            pass  # Both failed

        raise KeyStorageError(f"Key '{key_name}' not found in any storage backend")

    def delete(self, key_name: str) -> bool:
        """
        Delete a key from storage.

        Attempts to delete from both OS and fallback backends.

        Args:
            key_name: Identifier for the key

        Returns:
            True if the key was deleted from at least one backend,
            False if it was not found in either
        """
        if not key_name:
            raise ValueError("key_name must be non-empty")

        deleted = False

        # Try OS backend
        if self._os_backend is not None:
            try:
                if self._os_backend.delete(key_name):
                    deleted = True
            except KeyStorageError:
                pass

        # Try fallback
        try:
            if self._fallback.delete(key_name):
                deleted = True
        except KeyStorageError:
            pass

        return deleted

    def list_keys(self) -> list[str]:
        """
        List all stored key names.

        Combines keys from both OS and fallback backends, deduplicated.

        Returns:
            Sorted list of unique key name strings
        """
        names: set[str] = set()

        # Try OS backend
        if self._os_backend is not None:
            try:
                names.update(self._os_backend.list_keys())
            except KeyStorageError:
                pass

        # Try fallback
        try:
            names.update(self._fallback.list_keys())
        except KeyStorageError:
            pass

        return sorted(names)

    def is_using_fallback(self) -> bool:
        """
        Check whether the last successful operation used the fallback backend.

        Returns:
            True if the fallback file backend was used, False if the OS
            backend was used
        """
        return self._using_fallback

    def __repr__(self) -> str:
        backend_name = (
            type(self._os_backend).__name__
            if self._os_backend is not None
            and not self._using_fallback
            else "_FileBackend"
        )
        return f"KeyStorage(backend={backend_name})"
