"""What Scrinium knows about the machine it runs on, checked at startup."""

from dataclasses import dataclass

from scrinium.core.data_dir import DataDirPermissions
from scrinium.core.disk_encryption import DiskEncryption
from scrinium.infrastructure.secret_store import SecretStore


@dataclass(frozen=True)
class LocalEnvironment:
    data_dir_permissions: DataDirPermissions
    disk_encryption: DiskEncryption
    secret_store: SecretStore
