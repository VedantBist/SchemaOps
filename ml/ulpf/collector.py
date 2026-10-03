"""Intake: syslog over UDP, TCP (newline or RFC 6587 octet-counting framing) and TLS, plus HTTP (api.py).

Every message is written unchanged to the Redis stream `ulpf:raw` with its arrival time, peer and
transport. Nothing is parsed here. TCP and TLS apply back-pressure when Redis is slow; UDP cannot,
so any UDP datagram that cannot be queued is counted in `ulpf:dropped` (never silently).
"""
from __future__ import annotations

import asyncio
import logging
import os
import ssl
import time
from pathlib import Path

import redis.asyncio as aioredis

STREAM = "ulpf:raw"
MAX_MESSAGE = 64 * 1024
log = logging.getLogger("ulpf.collector")


class Intake:
    def __init__(self, r: aioredis.Redis):
        self.r = r
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=int(os.environ.get("ULPF_INTAKE_QUEUE", "50000")))

    def offer(self, data: bytes, peer: str | None, transport: str, hint: str | None = None) -> bool:
        data = data.rstrip(b"\r\n")
        if not data:
            return True
        try:
            self.queue.put_nowait((data, int(time.time() * 1000), peer or "", transport, hint or ""))
            return True
        except asyncio.QueueFull:
            return False

    async def put(self, data: bytes, peer: str | None, transport: str, hint: str | None = None) -> None:
        data = data.rstrip(b"\r\n")
        if data:
            await self.queue.put((data, int(time.time() * 1000), peer or "", transport, hint or ""))

    async def flush_loop(self) -> None:
        while True:
            first = await self.queue.get()
            batch = [first]
            while len(batch) < 1000 and not self.queue.empty():
                batch.append(self.queue.get_nowait())
            while True:
                try:
                    pipe = self.r.pipeline(transaction=True)  # XADD and the counter move together
                    for data, ts, peer, transport, hint in batch:
                        pipe.xadd(STREAM, {"d": data, "t": ts, "p": peer, "x": transport, "h": hint})
                    pipe.hincrby("ulpf:received", "total", len(batch))
                    for _, _, _, transport, _ in batch:
                        pipe.hincrby("ulpf:received", transport, 1)
                    await pipe.execute()
                    break
                except (aioredis.ConnectionError, OSError) as e:
                    log.error("redis unavailable, retrying batch of %d: %s", len(batch), e)
                    await asyncio.sleep(1)


class _Udp(asyncio.DatagramProtocol):
    def __init__(self, intake: Intake):
        self.intake = intake
        self.dropped = 0

    def datagram_received(self, data: bytes, addr) -> None:
        if not self.intake.offer(data[:MAX_MESSAGE], addr[0], "udp"):
            self.dropped += 1
            asyncio.ensure_future(self.intake.r.hincrby("ulpf:dropped", "udp_queue_full", 1))


async def _stream_reader(intake: Intake, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, transport: str):
    peer = (writer.get_extra_info("peername") or ("?",))[0]
    try:
        while True:
            first = await reader.read(1)
            if not first:
                break
            if first.isdigit():
                # RFC 6587 octet counting is "<len> <message>"; a newline-framed message may also start
                # with a digit (e.g. PAN-OS CSV without a header), so read the digits and decide.
                digits = first
                nxt = await reader.read(1)
                while nxt.isdigit() and len(digits) < 8:
                    digits += nxt
                    nxt = await reader.read(1)
                if nxt == b" " and int(digits) <= MAX_MESSAGE * 4:
                    await intake.put(await reader.readexactly(int(digits)), peer, transport)
                elif nxt in (b"\n", b""):
                    await intake.put(digits, peer, transport)
                else:
                    await intake.put(digits + nxt + await reader.readuntil(b"\n"), peer, transport)
            else:
                line = first + await reader.readuntil(b"\n")
                await intake.put(line, peer, transport)
    except asyncio.IncompleteReadError as e:
        if e.partial.strip():
            await intake.put(e.partial, peer, transport)
    except (asyncio.LimitOverrunError, ValueError, ConnectionError) as e:
        log.warning("closing %s connection from %s: %s", transport, peer, e)
    finally:
        writer.close()


def _tls_context() -> ssl.SSLContext | None:
    cert_dir = Path(os.environ.get("ULPF_TLS_DIR", "/var/lib/ulpf/tls"))
    cert, key = cert_dir / "server.crt", cert_dir / "server.key"
    if not cert.exists():
        try:
            _self_signed(cert_dir)
        except Exception as e:
            log.warning("TLS disabled (no certificate and could not create one: %s)", e)
            return None
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(cert, key)
    return ctx


def _self_signed(folder: Path) -> None:
    """Creates a local certificate on first start (offline; replace it with your PKI's in production)."""
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    folder.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "causalops-ulpf")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("ulpf"), x509.DNSName("localhost")]), False)
            .sign(key, hashes.SHA256()))
    (folder / "server.key").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                          serialization.NoEncryption()))
    (folder / "server.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))


async def start(intake: Intake) -> list:
    loop = asyncio.get_running_loop()
    udp_port = int(os.environ.get("ULPF_SYSLOG_UDP", "5514"))
    tcp_port = int(os.environ.get("ULPF_SYSLOG_TCP", "5514"))
    tls_port = int(os.environ.get("ULPF_SYSLOG_TLS", "6514"))
    servers = []
    transport, _ = await loop.create_datagram_endpoint(lambda: _Udp(intake), local_addr=("0.0.0.0", udp_port))
    servers.append(transport)
    servers.append(await asyncio.start_server(lambda r, w: _stream_reader(intake, r, w, "tcp"), "0.0.0.0", tcp_port,
                                              limit=MAX_MESSAGE * 4))
    ctx = _tls_context()
    if ctx:
        servers.append(await asyncio.start_server(lambda r, w: _stream_reader(intake, r, w, "tls"), "0.0.0.0", tls_port,
                                                  ssl=ctx, limit=MAX_MESSAGE * 4))
    asyncio.create_task(intake.flush_loop())
    log.info("syslog intake on udp/%d tcp/%d tls/%s", udp_port, tcp_port, tls_port if ctx else "off")
    return servers
