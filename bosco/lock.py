"""Cross-process account and runner locks (flock on Linux)."""
from pathlib import Path
import portalocker
from .errors import SafetyError

class OperationLock:
    def __init__(self, path: Path, timeout: float = 300):
        self.path, self.timeout = path, timeout
        self.lock = None

    def __enter__(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lock = portalocker.Lock(str(self.path), mode='a+', timeout=self.timeout)
        try:
            self.lock.acquire()
        except portalocker.LockException:
            raise SafetyError(f'Timed out waiting for account operation lock: {self.path}') from None
        return self

    def __exit__(self, *args):
        if self.lock:
            self.lock.release()
