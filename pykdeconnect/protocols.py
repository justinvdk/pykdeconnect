import asyncio
import ssl
from asyncio import transports, Transport
from typing import Tuple, TYPE_CHECKING

from cryptography.x509 import load_pem_x509_certificate, load_der_x509_certificate

from .const import MIN_PROTOCOL_VERSION
from .helpers import get_timestamp
from .payloads import IdentityPayload, PairPayload
from . import devices

if TYPE_CHECKING:
    from .client import KdeConnectClient


class UdpAdvertisementProtocol(asyncio.DatagramProtocol):
    client: 'KdeConnectClient'
    transport: transports.Transport

    def __init__(self, client: 'KdeConnectClient'):
        self.client = client

    def connection_made(self, transport: transports.BaseTransport) -> None:
        assert isinstance(transport, transports.Transport)
        self.transport = transport

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        payload = self.client.decoder.decode(data, IdentityPayload)
        if payload.body.deviceId == self.client.config.device_id:
            return
        if payload.body.protocolVersion < MIN_PROTOCOL_VERSION:
            return
        print("Received udp advertisement", payload)
        device = devices.KdeConnectDevice.from_payload(payload, self.client)

        if payload.body.tcpPort is not None:
            loop = asyncio.get_event_loop()
            loop.create_task(self.client.send_tcp_identity(addr[0], payload.body.tcpPort, device))


class TcpProtocol(asyncio.Protocol):
    client: 'KdeConnectClient'
    transport: transports.Transport

    def __init__(self, client: 'KdeConnectClient'):
        self.client = client

    def start_connection(self, device: 'devices.KdeConnectDevice', *, server_side: bool):
        loop = asyncio.get_event_loop()
        device_listener = device.get_protocol()
        future = loop.create_task(loop.start_tls(
            self.transport, device_listener,
            self.client.get_ssl_context(server_side, device),
            server_side=server_side
        ))
        future.add_done_callback(lambda t: device_listener.connection_made(t.result()))

    def connection_lost(self, exc: Exception | None) -> None:
        print("Lost connection before starting tls")
        if exc is not None:
            print(exc)


class TcpServerSideProtocol(TcpProtocol):
    def connection_made(self, transport: transports.BaseTransport) -> None:
        assert isinstance(transport, Transport)
        self.transport = transport

    def data_received(self, data: bytes) -> None:
        print(data)
        payload = self.client.decoder.decode(data, IdentityPayload)
        if payload.body.deviceId == self.client.config.device_id:
            self.transport.close()
            return
        if payload.body.protocolVersion < MIN_PROTOCOL_VERSION:
            self.transport.close()
            return
        print("Received tcp advertisement", payload)
        device = devices.KdeConnectDevice.from_payload(payload, self.client)

        self.start_connection(device, server_side=False)


class TcpClientSideProtocol(TcpProtocol):
    def __init__(self, client: 'KdeConnectClient', device: 'devices.KdeConnectDevice'):
        super().__init__(client)
        self.device = device

    def connection_made(self, transport: transports.BaseTransport) -> None:
        assert isinstance(transport, Transport)
        self.transport = transport
        payload = self.client.encoder.encode(self.client.identity_payload(with_port=False))
        self.transport.write(payload)
        print("Sent identity")

        self.start_connection(self.device, server_side=True)


class DeviceProtocol(asyncio.Protocol):
    transport: transports.Transport
    client: 'KdeConnectClient'
    device: 'devices.KdeConnectDevice'

    def __init__(self, device: 'devices.KdeConnectDevice', client: 'KdeConnectClient'):
        self.device = device
        self.client = client

    def connection_made(self, transport: transports.BaseTransport) -> None:
        assert isinstance(transport, Transport)
        self.transport = transport
        print("Upgraded connection to TLS:", self.device.device_name)
        self.client.known_devices.append(self.device)

    def connection_lost(self, exc: Exception | None) -> None:
        self.client.known_devices.remove(self.device)

    def data_received(self, data: bytes) -> None:
        try:
            print(data)
            payload = self.client.decoder.decode(data)
            if isinstance(payload, PairPayload):
                if payload.body.pair:
                    if self.device.wants_pairing:
                        self.device.set_paired()
                    else:
                        loop = asyncio.get_event_loop()
                        loop.create_task(self.client.on_pairing_request(self.device))
                else:
                    self.device.set_unpaired()
            else:
                # self.device.handle_message(payload)
                pass
        except Exception as e:
            print(e)

    def send_pairing_packet(self, pair) -> None:
        payload = PairPayload(get_timestamp(), PairPayload.Body(pair))
        payload_str = self.client.encoder.encode(payload)
        self.transport.write(payload_str)

    def get_certificate(self):
        ssl_obj = self.transport.get_extra_info("ssl_object")
        return load_der_x509_certificate(ssl_obj.getpeercert(True))
