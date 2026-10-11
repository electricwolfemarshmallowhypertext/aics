"""Read CLI session identity; token measurements remain rollout-only."""
import json
import threading


class SessionIdentity:
    def __init__(self, proc, log_path):
        self.ready = threading.Event()
        self.session_id = None
        self.error = None
        self.thread = threading.Thread(target=self._read, args=(proc, log_path), daemon=True)
        self.thread.start()

    def _read(self, proc, log_path):
        try:
            with open(log_path, "a", encoding="utf-8") as log:
                for line in proc.stdout:
                    log.write(line)
                    log.flush()
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") == "thread.started":
                        identity = event.get("thread_id")
                        if not isinstance(identity, str) or not identity:
                            raise RuntimeError("CLI emitted an invalid session identity")
                        if self.session_id is not None and identity != self.session_id:
                            raise RuntimeError("CLI emitted conflicting session identities")
                        self.session_id = identity
                        self.ready.set()
        except Exception as error:
            self.error = error
        finally:
            proc.stdout.close()
            self.ready.set()

    def get(self, timeout=30):
        if not self.ready.wait(timeout):
            raise RuntimeError("CLI did not emit its session identity before timeout")
        if self.error:
            raise RuntimeError("Cannot establish CLI session identity") from self.error
        if self.session_id is None:
            raise RuntimeError("CLI exited without a session identity")
        return self.session_id
