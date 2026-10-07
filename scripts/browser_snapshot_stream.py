"""Bounded binary framing and serial SSH client for historical browser imports."""
import json
import math
import os
import select
import subprocess
import tempfile
import time

from scripts.process_utils import _ACTIVE_ROOTS, _kill_tree

PROTOCOL = 1
MAX_BYTES = 5 * 1024 * 1024
MAX_LINE = 8192


class InfrastructureStop(RuntimeError):
    """The importer outcome is uncertain: stop site requests, never retry here."""


def json_line(value):
    return (json.dumps(value, separators=(',', ':')) + '\n').encode('utf-8')


def decode_line(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate_protocol_key')
            result[key] = value
        return result
    if not raw.endswith(b'\n') or len(raw) > MAX_LINE:
        raise ValueError('invalid_protocol_line')
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique)
    if not isinstance(value, dict) or type(value.get('protocol')) is not int or value['protocol'] != PROTOCOL:
        raise ValueError('protocol_mismatch')
    return value


class StreamImporter:
    """One process, one outstanding frame, bounded write/ACK deadlines.

    SQLite jobs/pages on NAS are the only checkpoint. A lost ACK may hide a
    committed page; stop and let the next candidate query exclude that success.
    No resend, second checkpoint, or network retry is performed here.
    """
    def __init__(self, argv, timeout=60):
        self.argv, self.timeout = argv, timeout
        self.proc = None
        self.stderr = None
        self.seq = 0
        self.buffer = b''
        self.timings = dict(startup_seconds=0, import_seconds=0, close_seconds=0)

    def start(self):
        started = time.monotonic()
        try:
            # A file prevents stderr backpressure from deadlocking the protocol.
            self.stderr = tempfile.TemporaryFile()
            self.proc = subprocess.Popen(self.argv, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=self.stderr, bufsize=0, start_new_session=True)
            _ACTIVE_ROOTS.add(self.proc.pid)
            os.set_blocking(self.proc.stdin.fileno(), False)
            os.set_blocking(self.proc.stdout.fileno(), False)
            ready = self._read(time.monotonic() + self.timeout)
            if ready.get('status') == 'error':
                raise InfrastructureStop('importer_error:' + str(ready.get('error', 'unknown'))[:200])
            if ready != {'protocol': PROTOCOL, 'status': 'ready', 'max_bytes': MAX_BYTES} or type(ready.get('max_bytes')) is not int:
                raise InfrastructureStop('importer_ready_mismatch')
            return self
        except Exception as exc:
            self.abort()
            raise InfrastructureStop('importer_start_failed:' + str(exc)[:200]) from exc
        finally:
            self.timings['startup_seconds'] = round(time.monotonic() - started, 6)

    def ensure_alive(self):
        try:
            if self.proc is None or self.proc.poll() is not None:
                raise InfrastructureStop('importer_eof')
            # No unsolicited output is allowed between ready/ACK and next frame.
            if self.buffer or select.select([self.proc.stdout], [], [], 0)[0]:
                raise InfrastructureStop('importer_unexpected_output_or_eof')
        except Exception as exc:
            raise InfrastructureStop('importer_health_failed:' + str(exc)[:200]) from exc

    def _read(self, deadline):
        while b'\n' not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.proc.stdout], [], [], remaining)[0]:
                raise InfrastructureStop('importer_ack_timeout')
            chunk = os.read(self.proc.stdout.fileno(), MAX_LINE + 1 - len(self.buffer))
            if not chunk:
                raise InfrastructureStop('importer_eof')
            self.buffer += chunk
            if len(self.buffer) > MAX_LINE:
                raise InfrastructureStop('importer_line_too_large')
        line, self.buffer = self.buffer.split(b'\n', 1)
        return decode_line(line + b'\n')

    def import_page(self, url, content):
        started = time.monotonic()
        try:
            self.ensure_alive()
            if not content or len(content) > MAX_BYTES:
                raise InfrastructureStop('browser_snapshot_size_invalid')
            self.seq += 1
            header = json_line(dict(protocol=PROTOCOL, seq=self.seq, url=url, length=len(content)))
            if len(header) > MAX_LINE:
                raise InfrastructureStop('importer_header_too_large')
            deadline = time.monotonic() + self.timeout
            frame = memoryview(header + content)
            while frame:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([], [self.proc.stdin], [], remaining)[1]:
                    raise InfrastructureStop('importer_write_timeout')
                try:
                    written = os.write(self.proc.stdin.fileno(), frame)
                except BlockingIOError:
                    continue
                if written <= 0:
                    raise InfrastructureStop('importer_write_eof')
                frame = frame[written:]
            ack = self._read(deadline)
            if ack.get('status') == 'error':
                raise InfrastructureStop('importer_error:' + str(ack.get('error', 'unknown'))[:200])
            if (ack.get('status') != 'imported' or type(ack.get('seq')) is not int
                    or ack['seq'] != self.seq or ack.get('url') != url
                    or type(ack.get('bytes')) is not int or ack['bytes'] != len(content)
                    or type(ack.get('snapshot_id')) is not int or ack['snapshot_id'] <= 0
                    or ack.get('thread_status') not in ('complete', 'partial')):
                raise InfrastructureStop('importer_ack_mismatch')
            seconds = ack.get('import_seconds', 0)
            if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
                raise InfrastructureStop('importer_ack_timing_mismatch')
            return ack
        except Exception as exc:
            raise InfrastructureStop('importer_stream_failed:' + str(exc)[:200]) from exc
        finally:
            self.timings['import_seconds'] += time.monotonic() - started

    def close(self):
        started = time.monotonic()
        try:
            if self.proc is not None:
                self.proc.stdin.close()
                code = self.proc.wait(timeout=self.timeout)
                extra = self.buffer + self.proc.stdout.read()
                if code or extra:
                    raise InfrastructureStop('importer_close_failed:exit=' + str(code))
        except Exception as exc:
            raise InfrastructureStop('importer_close_failed:' + str(exc)[:200]) from exc
        finally:
            self.abort()
            self.timings['close_seconds'] = round(time.monotonic() - started, 6)

    def abort(self):
        if self.proc is not None:
            if self.proc.poll() is None:
                _kill_tree(self.proc.pid)
            try:
                self.proc.wait(timeout=2)
            finally:
                _ACTIVE_ROOTS.discard(self.proc.pid)
                self.proc.stdin.close()
                self.proc.stdout.close()
                self.proc = None
        if self.stderr is not None:
            self.stderr.close()
            self.stderr = None
