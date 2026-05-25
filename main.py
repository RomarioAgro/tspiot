import argparse
import base64
import json
import ssl
import sys
import uuid
from datetime import datetime, timezone
from email.parser import BytesParser
from email.policy import default as email_policy
from http import HTTPStatus
from http.client import HTTPException, LineTooLong
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import TCPServer
from urllib.parse import parse_qs, urlsplit


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 51401
DEFAULT_LOG_DIR = "logs"


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


def choose_charset(content_type: str | None) -> str | None:
    if not content_type:
        return None

    parts = [part.strip() for part in content_type.split(";")]
    for part in parts[1:]:
        if part.lower().startswith("charset="):
            return part.split("=", 1)[1].strip().strip('"')
    return None


def safe_decode_text(body: bytes, charset: str | None) -> tuple[bool, str, str]:
    attempted = charset or "utf-8"
    try:
        return True, body.decode(attempted), attempted
    except UnicodeDecodeError:
        if attempted.lower() != "utf-8":
            try:
                return True, body.decode("utf-8"), "utf-8"
            except UnicodeDecodeError:
                pass
    return False, base64.b64encode(body).decode("ascii"), attempted


def parse_multipart_body(body: bytes, content_type: str) -> list[dict]:
    message_bytes = (
        f"Content-Type: {content_type}\r\n"
        "MIME-Version: 1.0\r\n"
        "\r\n"
    ).encode("utf-8") + body
    message = BytesParser(policy=email_policy).parsebytes(message_bytes)

    parts: list[dict] = []
    for index, part in enumerate(message.iter_parts(), start=1):
        payload = part.get_payload(decode=True) or b""
        disposition_params = dict(part.get_params(header="content-disposition", failobj=[]))
        headers = {name: part.get_all(name) for name in part.keys()}
        content_type_part = part.get_content_type()
        charset = part.get_content_charset()
        text_ok, text_value, used_charset = safe_decode_text(payload, charset)

        parts.append(
            {
                "index": index,
                "headers": headers,
                "content_type": content_type_part,
                "field_name": disposition_params.get("name"),
                "file_name": disposition_params.get("filename"),
                "size_bytes": len(payload),
                "decoded": {
                    "kind": "text" if text_ok else "binary",
                    "charset": used_charset if text_ok else None,
                    "value": text_value,
                },
                "raw_base64": base64.b64encode(payload).decode("ascii"),
            }
        )
    return parts


def decode_body(body: bytes, content_type: str | None) -> dict:
    if not body:
        return {
            "content_type": content_type,
            "kind": "empty",
            "raw_text": "",
            "raw_base64": "",
            "parsed": None,
        }

    raw_text_ok, raw_text_value, raw_charset = safe_decode_text(body, choose_charset(content_type))
    result = {
        "content_type": content_type,
        "raw_text": raw_text_value if raw_text_ok else None,
        "raw_text_charset": raw_charset if raw_text_ok else None,
        "raw_base64": base64.b64encode(body).decode("ascii"),
        "kind": "binary" if not raw_text_ok else "text",
        "parsed": None,
    }

    lowered_content_type = (content_type or "").lower()
    try:
        if "application/json" in lowered_content_type:
            parsed_json = json.loads(body.decode(choose_charset(content_type) or "utf-8"))
            result["kind"] = "json"
            result["parsed"] = parsed_json
            return result

        if "application/x-www-form-urlencoded" in lowered_content_type:
            parsed_form = parse_qs(body.decode(choose_charset(content_type) or "utf-8"), keep_blank_values=True)
            result["kind"] = "form"
            result["parsed"] = parsed_form
            return result

        if "multipart/form-data" in lowered_content_type:
            result["kind"] = "multipart"
            result["parsed"] = parse_multipart_body(body, content_type or "multipart/form-data")
            return result
    except Exception as exc:
        result["parsed"] = {"parse_error": str(exc)}
        return result

    return result


def render_txt_log(record: dict) -> str:
    body = record["body"]
    lines = [
        "Request Log",
        f"request_id: {record['request_id']}",
        f"received_at_utc: {record['received_at_utc']}",
        f"client_ip: {record['client_ip']}",
        f"client_port: {record['client_port']}",
        f"method: {record['method']}",
        f"path: {record['path']}",
        f"query_string: {record['query_string']}",
        f"http_version: {record['http_version']}",
        f"content_length: {record['content_length']}",
        f"content_type: {record['content_type']}",
        "",
        "Headers:",
    ]

    for key, values in record["headers"].items():
        for value in values:
            lines.append(f"{key}: {value}")

    lines.extend(
        [
            "",
            "Body Summary:",
            f"kind: {body['kind']}",
            f"raw_text_charset: {body.get('raw_text_charset')}",
            "",
            "Raw Body As Text:",
            body["raw_text"] if body["raw_text"] is not None else "<binary; see raw_base64>",
            "",
            "Raw Body Base64:",
            body["raw_base64"],
            "",
            "Parsed Body:",
            json.dumps(body["parsed"], ensure_ascii=False, indent=2) if body["parsed"] is not None else "<none>",
            "",
        ]
    )
    return "\n".join(lines)


def ensure_log_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


class RequestLogger:
    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        ensure_log_dir(log_dir)

    def write(self, record: dict) -> tuple[Path, Path]:
        base_name = f"{record['filename_timestamp']}_{record['request_id']}"
        txt_path = self.log_dir / f"{base_name}.txt"
        json_path = self.log_dir / f"{base_name}.json"

        txt_path.write_text(render_txt_log(record), encoding="utf-8")
        json_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return txt_path, json_path


class DebugHTTPServer(HTTPServer):
    allow_reuse_address = True

    def __init__(self, server_address, handler_class, ssl_context: ssl.SSLContext, log_dir: Path):
        super().__init__(server_address, handler_class)
        self.ssl_context = ssl_context
        self.request_logger = RequestLogger(log_dir)

    def get_request(self):
        socket, addr = TCPServer.get_request(self)
        tls_socket = self.ssl_context.wrap_socket(socket, server_side=True)
        return tls_socket, addr


class DebugRequestHandler(BaseHTTPRequestHandler):
    server_version = "DebugHTTPSLogger/1.0"
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
            self.handle_any_method()
            self.wfile.flush()
        except TimeoutError as exc:
            self.log_error("Request timed out: %r", exc)
            self.close_connection = True
            return
        except LineTooLong as exc:
            self.send_error(HTTPStatus.REQUEST_HEADER_FIELDS_TOO_LARGE, str(exc))
        except HTTPException as exc:
            self.send_error(HTTPStatus.REQUEST_HEADER_FIELDS_TOO_LARGE, str(exc))

    def do_GET(self) -> None:  # pragma: no cover
        self.handle_any_method()

    def do_POST(self) -> None:  # pragma: no cover
        self.handle_any_method()

    def do_PUT(self) -> None:  # pragma: no cover
        self.handle_any_method()

    def do_PATCH(self) -> None:  # pragma: no cover
        self.handle_any_method()

    def do_DELETE(self) -> None:  # pragma: no cover
        self.handle_any_method()

    def do_HEAD(self) -> None:  # pragma: no cover
        self.handle_any_method()

    def do_OPTIONS(self) -> None:  # pragma: no cover
        self.handle_any_method()

    def handle_any_method(self) -> None:
        received_at = utc_now()
        body = self.read_request_body()
        parsed_url = urlsplit(self.path)
        content_type = self.headers.get("Content-Type")
        request_id = uuid.uuid4().hex[:8]

        record = {
            "request_id": request_id,
            "received_at_utc": format_timestamp(received_at),
            "filename_timestamp": format_timestamp_for_filename(received_at),
            "client_ip": self.client_address[0],
            "client_port": self.client_address[1],
            "method": self.command,
            "path": parsed_url.path,
            "query_string": parsed_url.query,
            "raw_target": self.path,
            "http_version": self.request_version,
            "headers": headers_to_dict(self.headers),
            "content_type": content_type,
            "content_length": len(body),
            "body": decode_body(body, content_type),
        }

        txt_path, json_path = self.server.request_logger.write(record)
        self.log_message(
            'Logged request_id=%s method="%s" path="%s" txt="%s" json="%s"',
            request_id,
            self.command,
            self.path,
            txt_path.name,
            json_path.name,
        )
        self.respond_ok()

    def read_request_body(self) -> bytes:
        transfer_encoding = self.headers.get("Transfer-Encoding", "").lower()
        if "chunked" in transfer_encoding:
            return self.read_chunked_body()

        content_length = self.headers.get("Content-Length")
        if content_length is None:
            return b""

        try:
            length = int(content_length)
        except ValueError:
            return b""

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
                break
            chunks.extend(self.rfile.read(chunk_size))
            self.rfile.read(2)
        return bytes(chunks)

    def respond_ok(self) -> None:
        response_body = json.dumps({"status": "ok"}, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(response_body)

    def log_message(self, format_string: str, *args) -> None:
        message = "%s - - [%s] %s\n" % (
            self.client_address[0],
            self.log_date_time_string(),
            format_string % args,
        )
        sys.stdout.write(message)


def build_ssl_context(certfile: Path, keyfile: Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(certfile), keyfile=str(keyfile))
    return context


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HTTPS request logger for debugging integrations.")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Host to bind to. Default: {DEFAULT_HOST}")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port to bind to. Default: {DEFAULT_PORT}")
    parser.add_argument("--cert", required=True, help="Path to the TLS certificate file in PEM format.")
    parser.add_argument("--key", required=True, help="Path to the TLS private key file in PEM format.")
    parser.add_argument(
        "--log-dir",
        default=DEFAULT_LOG_DIR,
        help=f"Directory where per-request TXT and JSON logs are stored. Default: {DEFAULT_LOG_DIR}",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cert_path = Path(args.cert).expanduser().resolve()
    key_path = Path(args.key).expanduser().resolve()
    log_dir = Path(args.log_dir).expanduser().resolve()

    if not cert_path.is_file():
        raise FileNotFoundError(f"Certificate file not found: {cert_path}")
    if not key_path.is_file():
        raise FileNotFoundError(f"Private key file not found: {key_path}")

    ssl_context = build_ssl_context(cert_path, key_path)
    server = DebugHTTPServer((args.host, args.port), DebugRequestHandler, ssl_context, log_dir)

    print(f"Listening on https://{args.host}:{args.port}")
    print(f"Certificate: {cert_path}")
    print(f"Private key: {key_path}")
    print(f"Log directory: {log_dir}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
