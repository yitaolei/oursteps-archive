"""Classify a worker's performance report without accessing runtime state."""
import math


VALID_STATUSES = {'complete', 'success', 'partial', 'budget_reached', 'halted',
                  'waiting_window', 'manual_priority_yield', 'interrupted', 'historical_js_challenge_hold'}


def classify_performance(perf, started_at, returned_at, worker_exit):
    if worker_exit != 0:
        return 'worker_failed_exit_' + str(worker_exit), {}
    if not isinstance(perf, dict):
        return 'invalid_current_performance', {}
    recorded = perf.get('recorded_at')
    status = perf.get('status')
    started = perf.get('started_threads')
    performance = perf.get('performance')
    fetches = performance.get('network_fetches') if isinstance(performance, dict) else None
    if (type(recorded) not in (int, float) or not math.isfinite(recorded)
            or not started_at <= recorded <= returned_at + 2
            or not isinstance(status, str) or status not in VALID_STATUSES
            or type(started) is not int or started < 0
            or type(fetches) is not int or fetches < 0):
        return 'invalid_current_performance', {}
    stats = {'started_threads': started, 'network_fetches': fetches}
    if status in ('halted', 'interrupted', 'historical_js_challenge_hold'):
        return status, stats
    if status not in ('waiting_window', 'manual_priority_yield') and started == 0 and fetches == 0:
        return 'no_work', stats
    return status, stats
