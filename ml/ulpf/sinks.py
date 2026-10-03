"""Outputs: Parquet data lake (S3/MinIO or local), JSONL, CEF syslog forward, Splunk HEC, Kafka, OpenSearch.

Each sink receives batches and reports bytes written. Sinks are configured in `ulpf_sinks` (type, tier,
enabled, config) and can be added in the console; failures are recorded per sink and retried with the
same batch (the exporter only advances a sink's cursor after a successful write).
"""
from __future__ import annotations

import io
import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path


class Sink:
    kind = "base"

    def __init__(self, name: str, config: dict):
        self.name, self.config = name, config

    def write(self, events: list[dict]) -> int:
        raise NotImplementedError

    def close(self) -> None:
        pass


def _line(e: dict) -> bytes:
    return (json.dumps(e, separators=(",", ":"), default=str) + "\n").encode()


class LakeSink(Sink):
    """Hive-partitioned Parquet: <root>/ocsf/class_uid=<c>/date=<d>/source=<s>/part-<ts>.parquet"""
    kind = "lake"

    def __init__(self, name, config):
        super().__init__(name, config)
        import pyarrow.fs as pafs
        endpoint = config.get("endpoint") or os.environ.get("ULPF_S3_ENDPOINT")
        if endpoint:
            self.fs = pafs.S3FileSystem(endpoint_override=endpoint, scheme=config.get("scheme", "http"),
                                        access_key=config.get("accessKey") or os.environ.get("ULPF_S3_ACCESS_KEY"),
                                        secret_key=config.get("secretKey") or os.environ.get("ULPF_S3_SECRET_KEY"),
                                        region=config.get("region", "us-east-1"))
            self.root = config.get("bucket") or os.environ.get("ULPF_S3_BUCKET", "ulpf-lake")
            try:
                self.fs.create_dir(self.root)
            except Exception:
                pass
        else:
            self.fs = pafs.LocalFileSystem()
            self.root = config.get("path", "/var/lib/ulpf/lake")
            Path(self.root).mkdir(parents=True, exist_ok=True)

    def write(self, events):
        import pyarrow as pa
        import pyarrow.parquet as pq
        groups: dict[tuple, list[dict]] = {}
        for e in events:
            day = datetime.fromtimestamp(e["time"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            groups.setdefault((e["class_uid"], day, e["ulpf"]["source_id"]), []).append(e)
        total = 0
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        for (cls, day, source), rows in groups.items():
            table = pa.table({
                "uid": [r["ulpf"].get("uid") for r in rows],
                "time": pa.array([r["time"] for r in rows], pa.timestamp("ms", tz="UTC")),
                "received_at": pa.array([r["metadata"].get("logged_time") for r in rows], pa.timestamp("ms", tz="UTC")),
                "source_id": [source] * len(rows),
                "class_uid": [cls] * len(rows),
                "class_name": [r.get("class_name") for r in rows],
                "severity_id": [r.get("severity_id") for r in rows],
                "src_ip": [(r.get("src_endpoint") or {}).get("ip") for r in rows],
                "src_port": [(r.get("src_endpoint") or {}).get("port") for r in rows],
                "dst_ip": [(r.get("dst_endpoint") or {}).get("ip") for r in rows],
                "dst_port": [(r.get("dst_endpoint") or {}).get("port") for r in rows],
                "user": [((r.get("actor") or {}).get("user") or {}).get("name") for r in rows],
                "action": [r.get("action") for r in rows],
                "product": [(r["metadata"].get("product") or {}).get("name") for r in rows],
                "sha256": [r["ulpf"].get("sha256") for r in rows],
                "vault_segment": [(r["ulpf"].get("vault") or {}).get("segment") for r in rows],
                "vault_offset": [(r["ulpf"].get("vault") or {}).get("offset") for r in rows],
                "ocsf": [json.dumps(r, separators=(",", ":"), default=str) for r in rows],
            })
            safe_source = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in source)
            path = f"{self.root}/ocsf/class_uid={cls}/date={day}/source={safe_source}/part-{stamp}.parquet"
            parent = path.rsplit("/", 1)[0]
            try:
                self.fs.create_dir(parent, recursive=True)
            except Exception:
                pass
            buf = io.BytesIO()
            pq.write_table(table, buf, compression="zstd")
            data = buf.getvalue()
            with self.fs.open_output_stream(path) as out:
                out.write(data)
            total += len(data)
        return total


class JsonlSink(Sink):
    kind = "jsonl"

    def write(self, events):
        root = Path(self.config.get("path", "/var/lib/ulpf/export"))
        root.mkdir(parents=True, exist_ok=True)
        data = b"".join(_line(e) for e in events)
        with open(root / f"{self.name}-{datetime.now(timezone.utc):%Y%m%d}.jsonl", "ab") as fh:
            fh.write(data)
        return len(data)


def to_cef(e: dict) -> str:
    def esc_h(v):
        return str(v).replace("\\", "\\\\").replace("|", "\\|")

    def esc_v(v):
        return str(v).replace("\\", "\\\\").replace("=", "\\=").replace("\n", " ")
    prod = e["metadata"].get("product") or {}
    ext = {"rt": e["time"], "src": (e.get("src_endpoint") or {}).get("ip"), "spt": (e.get("src_endpoint") or {}).get("port"),
           "dst": (e.get("dst_endpoint") or {}).get("ip"), "dpt": (e.get("dst_endpoint") or {}).get("port"),
           "proto": (e.get("connection_info") or {}).get("protocol_name"), "act": e.get("action"),
           "suser": ((e.get("actor") or {}).get("user") or {}).get("name"), "cnt": e.get("count"),
           "msg": e.get("message"), "cs1Label": "ulpf_uid", "cs1": e.get("ulpf", {}).get("uid"),
           "cs2Label": "sha256", "cs2": e.get("ulpf", {}).get("sha256")}
    ext_s = " ".join(f"{k}={esc_v(v)}" for k, v in ext.items() if v not in (None, ""))
    return (f"CEF:0|{esc_h(prod.get('vendor_name', 'CausalOps'))}|{esc_h(prod.get('name', 'ULPF'))}|"
            f"{esc_h(prod.get('version', '1'))}|{e['class_uid']}|{esc_h(e.get('class_name'))}|{e.get('severity_id', 0)}|{ext_s}")


class CefForwardSink(Sink):
    """RFC 5424 syslog over TCP (octet counting) or UDP, CEF payload: any SIEM accepts it."""
    kind = "cef"

    def write(self, events):
        host, port = self.config["host"], int(self.config.get("port", 514))
        lines = [f"<134>1 {datetime.fromtimestamp(e['time'] / 1000, tz=timezone.utc).isoformat()} causalops-ulpf ulpf - - - "
                 f"{to_cef(e)}".encode() for e in events]
        if self.config.get("protocol", "tcp") == "udp":
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            for ln in lines:
                s.sendto(ln, (host, port))
        else:
            with socket.create_connection((host, port), timeout=10) as s:
                s.sendall(b"".join(f"{len(ln)} ".encode() + ln for ln in lines))
        return sum(len(ln) for ln in lines)


class HecSink(Sink):
    kind = "hec"

    def write(self, events):
        import httpx
        body = b"".join(_line({"time": e["time"] / 1000, "sourcetype": "ocsf", "source": e["ulpf"]["source_id"], "event": e})
                        for e in events)
        r = httpx.post(self.config["url"].rstrip("/") + "/services/collector/event", content=body,
                       headers={"Authorization": f"Splunk {self.config.get('token', '')}"}, timeout=30,
                       verify=self.config.get("verifyTls", True))
        r.raise_for_status()
        return len(body)


class OpenSearchSink(Sink):
    kind = "opensearch"

    def write(self, events):
        import httpx
        index = self.config.get("index", "ocsf-events")
        body = b"".join(_line({"index": {"_index": index, "_id": e["ulpf"].get("uid")}}) + _line(e) for e in events)
        r = httpx.post(self.config.get("url", "http://opensearch:9200").rstrip("/") + "/_bulk", content=body,
                       headers={"Content-Type": "application/x-ndjson"}, timeout=60)
        r.raise_for_status()
        if r.json().get("errors"):
            raise RuntimeError("OpenSearch bulk reported item errors")
        return len(body)


class KafkaSink(Sink):
    kind = "kafka"

    def __init__(self, name, config):
        super().__init__(name, config)
        from kafka import KafkaProducer
        self.producer = KafkaProducer(bootstrap_servers=config.get("bootstrap", "kafka:9092"), linger_ms=50,
                                      compression_type="gzip")

    def write(self, events):
        total = 0
        topic = self.config.get("topic", "ocsf.events")
        for e in events:
            v = _line(e)
            self.producer.send(topic, key=(e["ulpf"].get("uid") or "").encode(), value=v)
            total += len(v)
        self.producer.flush(30)
        return total


KINDS = {c.kind: c for c in (LakeSink, JsonlSink, CefForwardSink, HecSink, OpenSearchSink, KafkaSink)}


def build(kind: str, name: str, config: dict) -> Sink:
    if kind not in KINDS:
        raise ValueError(f"unknown sink type {kind}; one of {sorted(KINDS)}")
    return KINDS[kind](name, config)
