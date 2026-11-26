"""Read-only one-second order monitor, independent of command processing."""
import logging
import queue
import threading
import time
from .api import AuthenticationRequired, SessionRenewalDeferred
from .api_trading import ApiTrading
from .account_delta import OrderChanges

LOG = logging.getLogger('bosco')


class DeltaOrderMonitor:
    def __init__(self, runner, producer):
        self.runner, self.producer = runner, producer
        self.api = ApiTrading(runner.settings)
        # Reuse one serialized native connection; parallel reconnects can revoke
        # the session used by the command/holdings client.
        if runner.api_executor is not None:
            self.api.client = runner.api_executor.client
        self.changes = OrderChanges()
        self.stop = threading.Event()
        self.wake_event = threading.Event()
        self.commands = queue.SimpleQueue()
        self.thread = threading.Thread(target=self.run, name='bosco-order-poll', daemon=True)

    def wake(self, command_id):
        self.commands.put(command_id)
        self.wake_event.set()

    def start(self):
        self.thread.start()

    def run(self):
        while not self.stop.is_set() and self.runner.running and not self.runner.stop_path.exists():
            self.wake_event.clear()
            started = time.monotonic()
            try:
                snapshots = []
                while True:
                    try:
                        key = self.commands.get_nowait()
                    except queue.Empty:
                        break
                    record = self.api.journal.read(key)
                    if record and record.get("order_snapshot"):
                        snapshots.append(record["order_snapshot"])
                if snapshots:
                    self.runner._publish_orders(self.producer, snapshots, first_observation=True)
                result = self.api.poll_order_status()
                if self.changes.observe(result['orders']):
                    self.runner.request_holdings_refresh()
                self.runner._publish_orders(self.producer, result['orders'])
            except SessionRenewalDeferred as error:
                LOG.warning('Broker session renewal deferred retry_after_s=%.1f; monitoring will resume.',error.retry_after)
                self.stop.wait(max(.05,error.retry_after))
                continue
            except AuthenticationRequired as error:
                self.runner._authentication_required(error)
                return
            except Exception as error:
                LOG.warning('Order monitor retry error_type=%s', type(error).__name__)
            self.wake_event.wait(max(.05, 1.0 - (time.monotonic() - started)))

    def close(self):
        self.stop.set()
        self.wake_event.set()
        self.thread.join()
        self.producer.flush(5)
