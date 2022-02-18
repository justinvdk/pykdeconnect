from configparser import ConfigParser, DuplicateSectionError
from datetime import datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.x509 import Certificate, load_pem_x509_certificate
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from .devices import KdeConnectDevice

CONFIG_KEY_GENERAL = 'general'
CONFIG_KEY_ID = 'id'
CONFIG_KEY_NAME = 'name'
CONFIG_KEY_TYPE = 'type'


class KdeConnectConfig:
    config: ConfigParser
    cert_path: Path
    private_key_path: Path
    device_certs_path: Path
    cert: Certificate
    private_key: RSAPrivateKey

    def __init__(self, path: Path):
        self.path = path
        self.ensure_is_dir(path)

        self.config = ConfigParser()
        self.config.read(path / "config.ini")
        if not self.config.has_section(CONFIG_KEY_GENERAL):
            self.config.add_section(CONFIG_KEY_GENERAL)

        self.cert_path = path / "cert.pem"
        self.private_key_path = path / "privateKey.pem"

        self.device_certs_path = path / "device_certificates"
        self.ensure_is_dir(self.device_certs_path)

        if not self.private_key_path.is_file():
            self.generate_private_key()
        else:
            self.load_private_key()

        if not self.cert_path.is_file():
            self.generate_cert()
        else:
            self.load_certificate()

    def ensure_is_dir(self, path: Path):
        if path.is_dir():
            return
        elif path.exists():
            raise OSError(f'"{path}" is not a directory')
        else:
            path.mkdir(parents=True)

    def trust_device(self, device: KdeConnectDevice):
        try:
            self.config.add_section(device.device_id)
        except DuplicateSectionError:
            raise Exception(f'Device "{device.device_name}" is already trusted')
        section = self.config[device.device_id]
        section[CONFIG_KEY_NAME] = device.device_name
        section[CONFIG_KEY_TYPE] = device.device_type.value
        self.save()
        cert = device.certificate
        assert cert is not None

        with open(self.get_device_cert_path(device), "wb+") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))

    def untrust_device(self, device: KdeConnectDevice):
        self.config.remove_section(device.device_id)
        self.save()
        self.get_device_cert_path(device).unlink()

    def get_device_cert_path(self, device: KdeConnectDevice) -> Path:
        return self.device_certs_path / f"{device.device_id}.pem"

    def get_device_cert(self, device: KdeConnectDevice) -> Certificate | None:
        path = self.get_device_cert_path(device)
        if not path.exists():
            return None

        if not self.config.has_section(device.device_id):
            return None

        try:
            with open(path, 'rb') as f:
                data = f.read()
                return load_pem_x509_certificate(data)
        except:
            self.untrust_device(device)
            path.unlink(missing_ok=True)
        return None

    @property
    def device_id(self) -> str:
        device_id = self.config[CONFIG_KEY_GENERAL].get(CONFIG_KEY_ID, None)
        if device_id is None:
            device_id = '1234567890'
            self.config[CONFIG_KEY_GENERAL][CONFIG_KEY_ID] = device_id
            self.save()
        return device_id

    @device_id.setter
    def device_id(self, value: str):
        self.config[CONFIG_KEY_GENERAL][CONFIG_KEY_ID] = value
        self.save()

    def save(self):
        with open(self.path / "config.ini", "w+") as f:
            self.config.write(f)

    def generate_private_key(self):
        self.cert_path.unlink(missing_ok=True)

        key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )

        self.private_key = key
        with open(self.private_key_path, 'wb+') as f:
            f.write(key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            ))

    def load_private_key(self):
        try:
            with open(self.private_key_path, 'rb') as f:
                data = f.read()
                self.private_key = load_pem_private_key(data, None)
        except:
            self.generate_private_key()

    def generate_cert(self):
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, self.device_id),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "freundTech"),
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "pyKDEConnect"),
        ])
        cert = x509.CertificateBuilder().subject_name(
            subject
        ).issuer_name(
            issuer
        ).public_key(
            self.private_key.public_key()
        ).serial_number(
            x509.random_serial_number()
        ).not_valid_before(
            # Complain to KDEConnect, not me. That's how they do it
            datetime.utcnow() - timedelta(days=365)
        ).not_valid_after(
            datetime.utcnow() + timedelta(days=10 * 365)
        ).sign(self.private_key, hashes.SHA256())

        self.cert = cert

        with open(self.cert_path, "wb+") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))

    def load_certificate(self):
        try:
            with open(self.cert_path, 'rb') as f:
                data = f.read()
                self.cert = load_pem_x509_certificate(data)
        except:
            self.generate_cert()
