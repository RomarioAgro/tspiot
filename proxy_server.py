import argparse
import base64
import configparser
import http.client
import json
import socket
import ssl
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.client import HTTPException, LineTooLong
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import TCPServer
from urllib.parse import urlsplit


DEFAULT_CONFIG = "proxy.ini"
DEFAULT_HTTP_PORT = 51400
DEFAULT_HTTPS_PORT = 51401
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


@dataclass(frozen=True)
class ProxyConfig:
    mode: str
    listen_host: str
    listen_port: int
    target_scheme: str
    target_host: str
    target_port: int
    connect_timeout: float
    read_timeout: float
    log_dir: Path
    cert_file: Path | None
    key_file: Path | None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_timestamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def format_timestamp_for_filename(value: datetime) -> str:
    return format_timestamp(value).replace(":", "-")


def headers_to_dict(headers) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for key in headers.keys():
        result[key] = headers.get_all(key)
    return result


def flatten_headers(headers) -> dict[str, str]:
    result: dict[str, str] = {}
    for key in headers.keys():
        values = headers.get_all(key)
        if values:
            result[key] = ", ".join(values)
    return result


def safe_decode_body(body: bytes, content_type: str | None) -> dict:
    raw_base64 = base64.b64encode(body).decode("ascii")
    if not body:
        return {
            "kind": "empty",
            "size_bytes": 0,
            "text": "",
            "json": None,
            "raw_base64": "",
        }

    charset = "utf-8"
    if content_type:
        for part in content_type.split(";")[1:]:
            part = part.strip()
            if part.lower().startswith("charset="):
                charset = part.split("=", 1)[1].strip().strip('"')
                break

    try:
        text = body.decode(charset)
    except UnicodeDecodeError:
        return {
            "kind": "binary",
            "size_bytes": len(body),
            "text": None,
            "json": None,
            "raw_base64": raw_base64,
        }

    decoded = {
        "kind": "text",
        "size_bytes": len(body),
        "charset": charset,
        "text": text,
        "json": None,
        "raw_base64": raw_base64,
    }

    if content_type and "application/json" in content_type.lower():
        try:
            decoded["kind"] = "json"
            decoded["json"] = json.loads(text)
        except json.JSONDecodeError as exc:
            decoded["json_error"] = str(exc)

    return decoded


def load_config(path: Path) -> ProxyConfig:
    parser = configparser.ConfigParser()
    if not parser.read(path, encoding="utf-8"):
        raise FileNotFoundError(f"Config file not found: {path}")

    server = parser["server"]
    target = parser["target"]
    timeouts = parser["timeouts"] if parser.has_section("timeouts") else {}
    logging = parser["logging"] if parser.has_section("logging") else {}
    tls = parser["tls"] if parser.has_section("tls") else {}

    mode = server.get("mode", "http").lower()
    if mode not in {"http", "https"}:
        raise ValueError("server.mode must be 'http' or 'https'")

    target_scheme = target.get("scheme", "http").lower()
    if target_scheme not in {"http", "https"}:
        raise ValueError("target.scheme must be 'http' or 'https'")

    listen_port = server.getint("port", fallback=DEFAULT_HTTPS_PORT if mode == "https" else DEFAULT_HTTP_PORT)
    cert_file = Path(tls.get("cert_file", "")).expanduser() if tls.get("cert_file", "") else None
    key_file = Path(tls.get("key_file", "")).expanduser() if tls.get("key_file", "") else None

    if mode == "https":
        if cert_file is None or key_file is None:
            raise ValueError("tls.cert_file and tls.key_file are required for https mode")

    return ProxyConfig(
        mode=mode,
        listen_host=server.get("host", "0.0.0.0"),
        listen_port=listen_port,
        target_scheme=target_scheme,
        target_host=target.get("host"),
        target_port=target.getint("port", fallback=443 if target_scheme == "https" else 80),
        connect_timeout=float(timeouts.get("connect_timeout", 10)),
        read_timeout=float(timeouts.get("read_timeout", 60)),
        log_dir=Path(logging.get("dir", "proxy_logs")).expanduser(),
        cert_file=cert_file,
        key_file=key_file,
    )


def ensure_log_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def make_forward_headers(headers, body: bytes) -> dict[str, str]:
    forward_headers = flatten_headers(headers)
    for name in list(forward_headers):
        if name.lower() in HOP_BY_HOP_HEADERS:
            del forward_headers[name]
    forward_headers["Content-Length"] = str(len(body))
    return forward_headers


def render_txt(record: dict) -> str:
    request = record["request"]
    upstream = record["upstream"]
    response = record["response"]
    error = record.get("error")

    lines = [
        "Proxy Request Log",
        f"request_id: {record['request_id']}",
        f"received_at_utc: {record['received_at_utc']}",
        f"mode: {record['mode']}",
        "",
        "Incoming Request:",
        f"client_ip: {request['client_ip']}",
        f"client_port: {request['client_port']}",
        f"method: {request['method']}",
        f"path: {request['path']}",
        f"query_string: {request['query_string']}",
        f"raw_target: {request['raw_target']}",
        f"http_version: {request['http_version']}",
        f"body_size_bytes: {request['body']['size_bytes']}",
        "",
        "Incoming Headers:",
    ]

    for key, values in request["headers"].items():
        for value in values:
            lines.append(f"{key}: {value}")

    lines.extend(
        [
            "",
            "Incoming Body Text:",
            request["body"]["text"] if request["body"]["text"] is not None else "<binary; see raw_base64 in json log>",
            "",
            "Upstream:",
            f"url: {upstream['url']}",
            f"scheme: {upstream['scheme']}",
            f"host: {upstream['host']}",
            f"port: {upstream['port']}",
            f"duration_ms: {upstream.get('duration_ms')}",
        ]
    )

    if response:
        lines.extend(
            [
                "",
                "Upstream Response:",
                f"status: {response['status']}",
                f"reason: {response['reason']}",
                f"body_size_bytes: {response['body']['size_bytes']}",
                "",
                "Upstream Response Headers:",
            ]
        )
        for key, values in response["headers"].items():
            for value in values:
                lines.append(f"{key}: {value}")
        lines.extend(
            [
                "",
                "Upstream Response Body Text:",
                response["body"]["text"] if response["body"]["text"] is not None else "<binary; see raw_base64 in json log>",
            ]
        )

    if error:
        lines.extend(
            [
                "",
                "Proxy Error:",
                f"type: {error['type']}",
                f"message: {error['message']}",
                f"occurred_at_utc: {error['occurred_at_utc']}",
            ]
        )

    lines.append("")
    return "\n".join(lines)


class ProxyLogger:
    def __init__(self, log_dir: Path):
        self.log_dir = log_dir.resolve()
        ensure_log_dir(self.log_dir)

    def write(self, record: dict) -> tuple[Path, Path]:
        base = f"{record['filename_timestamp']}_{record['request_id']}"
        txt_path = self.log_dir / f"{base}.txt"
        json_path = self.log_dir / f"{base}.json"
        txt_path.write_text(render_txt(record), encoding="utf-8")
        json_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return txt_path, json_path


class DebugProxyServer(HTTPServer):
    allow_reuse_address = True

    def __init__(self, address, handler_class, config: ProxyConfig, ssl_context: ssl.SSLContext | None):
        super().__init__(address, handler_class)
        self.config = config
        self.ssl_context = ssl_context
        self.proxy_logger = ProxyLogger(config.log_dir)

    def get_request(self):
        sock, addr = TCPServer.get_request(self)
        if self.ssl_context is not None:
            sock = self.ssl_context.wrap_socket(sock, server_side=True)
        return sock, addr


class ProxyHandler(BaseHTTPRequestHandler):
    server_version = "DebugProxy/1.0"
    sys_version = ""

    def handle_one_request(self) -> None:
        try:
            self.raw_requestline = self.rfile.readline(65537)
            if len(self.raw_requestline) > 65536:
                self.requestline = ""
                self.request_version = ""
                self.command = ""
                self.send_error(HTTPStatus.REQUEST_URI_TOO_LONG)
                return
            if not self.raw_requestline:
                self.close_connection = True
                return
            if not self.parse_request():
                return
            self.handle_proxy_request()
            self.wfile.flush()
        except TimeoutError as exc:
            self.log_error("Request timed out: %r", exc)
            self.close_connection = True
        except LineTooLong as exc:
            self.send_error(HTTPStatus.REQUEST_HEADER_FIELDS_TOO_LARGE, str(exc))
        except HTTPException as exc:
            self.send_error(HTTPStatus.REQUEST_HEADER_FIELDS_TOO_LARGE, str(exc))

    def handle_proxy_request(self) -> None:
        started = time.monotonic()
        received_at = utc_now()
        request_id = uuid.uuid4().hex[:8]
        parsed = urlsplit(self.path)
        body = self.read_request_body()
        config = self.server.config
        upstream_target = parsed.path or "/"
        if parsed.query:
            upstream_target += f"?{parsed.query}"
        upstream_url = f"{config.target_scheme}://{config.target_host}:{config.target_port}{upstream_target}"

        record = self.build_base_record(request_id, received_at, parsed, body, upstream_url)

        try:
            status, reason, response_headers, response_body = self.forward_request(upstream_target, body)
            duration_ms = round((time.monotonic() - started) * 1000, 3)
            record["upstream"]["duration_ms"] = duration_ms
            record["response"] = {
                "status": status,
                "reason": reason,
                "headers": response_headers,
                "body": safe_decode_body(response_body, first_header(response_headers, "Content-Type")),
            }
            self.respond_with_upstream(status, response_headers, response_body)
        except Exception as exc:
            duration_ms = round((time.monotonic() - started) * 1000, 3)
            record["upstream"]["duration_ms"] = duration_ms
            record["error"] = {
                "type": type(exc).__name__,
                "message": str(exc),
                "occurred_at_utc": format_timestamp(utc_now()),
            }
            error_body = json.dumps(
                {
                    "error": "bad_gateway",
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "request_id": request_id,
                },
                ensure_ascii=False,
            ).encode("utf-8")
            self.respond_bad_gateway(error_body)
        finally:
            txt_path, json_path = self.server.proxy_logger.write(record)
            self.log_message(
                'Logged request_id=%s method="%s" path="%s" txt="%s" json="%s"',
                request_id,
                self.command,
                self.path,
                txt_path.name,
                json_path.name,
            )

    def build_base_record(self, request_id: str, received_at: datetime, parsed, body: bytes, upstream_url: str) -> dict:
        config = self.server.config
        return {
            "request_id": request_id,
            "received_at_utc": format_timestamp(received_at),
            "filename_timestamp": format_timestamp_for_filename(received_at),
            "mode": config.mode,
            "request": {
                "client_ip": self.client_address[0],
                "client_port": self.client_address[1],
                "method": self.command,
                "path": parsed.path,
                "query_string": parsed.query,
                "raw_target": self.path,
                "http_version": self.request_version,
                "headers": headers_to_dict(self.headers),
                "body": safe_decode_body(body, self.headers.get("Content-Type")),
            },
            "upstream": {
                "url": upstream_url,
                "scheme": config.target_scheme,
                "host": config.target_host,
                "port": config.target_port,
                "duration_ms": None,
            },
            "response": None,
            "error": None,
        }

    def forward_request(self, upstream_target: str, body: bytes) -> tuple[int, str, dict[str, list[str]], bytes]:
        config = self.server.config
        timeout = max(config.connect_timeout, config.read_timeout)
        if config.target_scheme == "https":
            ssl_context = ssl._create_unverified_context()
            connection = http.client.HTTPSConnection(
                config.target_host,
                config.target_port,
                timeout=timeout,
                context=ssl_context,
            )
        else:
            connection = http.client.HTTPConnection(config.target_host, config.target_port, timeout=timeout)

        try:
            connection.connect()
            connection.sock.settimeout(config.read_timeout)
            connection.request(
                method=self.command,
                url=upstream_target,
                body=body,
                headers=make_forward_headers(self.headers, body),
            )
            response = connection.getresponse()
            response_body = response.read()
            response_headers = headers_to_dict(response.headers)
            return response.status, response.reason, response_headers, response_body
        finally:
            connection.close()

    def read_request_body(self) -> bytes:
        transfer_encoding = self.headers.get("Transfer-Encoding", "").lower()
        if "chunked" in transfer_encoding:
            return self.read_chunked_body()

        content_length = self.headers.get("Content-Length")
        if content_length is None:
            return b""

        length = int(content_length)
        return self.rfile.read(length) if length > 0 else b""

    def read_chunked_body(self) -> bytes:
        chunks = bytearray()
        while True:
            size_line = self.rfile.readline()
            if not size_line:
                break
            chunk_size = int(size_line.strip().split(b";", 1)[0], 16)
            if chunk_size == 0:
                while True:
                    trailer_line = self.rfile.readline()
                    if trailer_line in (b"\r\n", b"\n", b""):
                        return bytes(chunks)
            chunks.extend(self.rfile.read(chunk_size))
            self.rfile.read(2)
        return bytes(chunks)

    def respond_with_upstream(self, status: int, headers: dict[str, list[str]], body: bytes) -> None:
        self.send_response(status)
        for key, values in headers.items():
            if key.lower() in HOP_BY_HOP_HEADERS or key.lower() == "content-length":
                continue
            for value in values:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def respond_bad_gateway(self, body: bytes) -> None:
        self.send_response(HTTPStatus.BAD_GATEWAY)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, format_string: str, *args) -> None:
        sys.stdout.write(
            "%s - - [%s] %s\n"
            % (
                self.client_address[0],
                self.log_date_time_string(),
                format_string % args,
            )
        )


def first_header(headers: dict[str, list[str]], name: str) -> str | None:
    for key, values in headers.items():
        if key.lower() == name.lower() and values:
            return values[0]
    return None


def build_server_ssl_context(config: ProxyConfig) -> ssl.SSLContext | None:
    if config.mode != "https":
        return None
    assert config.cert_file is not None
    assert config.key_file is not None
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(config.cert_file), keyfile=str(config.key_file))
    return context


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HTTP/HTTPS debug proxy with full request/response logging.")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help=f"Path to INI config. Default: {DEFAULT_CONFIG}")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = Path(args.config).expanduser().resolve()
    config = load_config(config_path)
    ssl_context = build_server_ssl_context(config)
    server = DebugProxyServer((config.listen_host, config.listen_port), ProxyHandler, config, ssl_context)

    print(f"Config: {config_path}")
    print(f"Listening: {config.mode}://{config.listen_host}:{config.listen_port}")
    print(f"Forwarding to: {config.target_scheme}://{config.target_host}:{config.target_port}")
    print(f"Log directory: {config.log_dir.resolve()}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping proxy.")
    finally:
        server.server_close()


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, configparser.Error, socket.error) as exc:
        print(f"Startup error: {exc}", file=sys.stderr)
        raise SystemExit(1)
