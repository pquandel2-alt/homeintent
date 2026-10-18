"""Low-cardinality diagnostics for the HomeIntent event runtime."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class EventRuntimeMetrics:
    """Counters and retention gauges of one runtime generation.

    ``received`` equals selected/filter/queue outcomes, while ``queued`` also
    includes accepted coalesced events. ``coalesced`` counts evaluations
    merged into a later event of the same entity. ``dropped_*`` counts
    evaluations lost at the queue bound; ``evicted_queue`` distinguishes
    eviction from rejection. ``dropped_lossless`` remains the compatibility
    aggregate of category and protected drops. ``view_evictions`` records
    history-only entries whose evaluation had already moved elsewhere.

    ``processed`` means that event-level dispatch ended, including an
    intentional consumer suppression.  Delivery, suppression, registry
    discard and queue loss remain separate so diagnostics do not overstate
    successful downstream delivery.

    ``retained_total`` intentionally preserves its historical queue/lease
    meaning. ``retained_total_all`` additionally includes the central
    long-running scheduler's interval and heap records.
    """

    received: int = 0
    filtered_unselected: int = 0
    filtered_no_interest: int = 0
    filtered_by_interest: int = 0
    filtered_by_category: int = 0
    filtered_by_proactive: int = 0
    filtered_no_consumer: int = 0
    queued: int = 0
    rejected_queue_full: int = 0
    evicted_queue: int = 0
    dequeued: int = 0
    processed: int = 0
    discarded_registry: int = 0
    evaluation_failures: int = 0
    coalesced: int = 0
    dropped_coalescible: int = 0
    dropped_category: int = 0
    dropped_protected: int = 0
    dropped_lossless: int = 0
    dropped_critical: int = 0
    view_evictions: int = 0
    snapshot_builds: int = 0
    snapshot_failures: int = 0
    snapshot_retries: int = 0
    snapshot_degraded_batches: int = 0
    registry_degraded_batches: int = 0
    history_degraded: int = 0
    history_degraded_events: int = 0
    history_degraded_batches: int = 0
    consumer_delivered: int = 0
    consumer_suppressed_history: int = 0
    consumer_suppressed_registry: int = 0
    delivered_effect: int = 0
    delivered_monitor: int = 0
    delivered_thermal: int = 0
    delivered_proactive: int = 0
    suppressed_protected: int = 0
    suppressed_category: int = 0
    suppressed_effect: int = 0
    suppressed_monitor: int = 0
    suppressed_thermal: int = 0
    suppressed_proactive: int = 0
    worker_starts: int = 0
    worker_restarts: int = 0
    worker_unexpected_exits: int = 0
    worker_cancellations: int = 0
    worker_backoffs: int = 0
    worker_backoff_milliseconds: int = 0
    worker_circuit_breaks: int = 0
    worker_suspended: int = 0
    worker_wakeups: int = 0
    worker_recoveries: int = 0
    batch_failures: int = 0
    batch_retries: int = 0
    batch_requeued_events: int = 0
    long_running_scheduled: int = 0
    long_running_rescheduled: int = 0
    long_running_cancelled: int = 0
    long_running_due: int = 0
    long_running_enqueued: int = 0
    long_running_capacity_rejected: int = 0
    long_running_scheduler_failures: int = 0
    retained_leased: int = 0
    retained_long_running: int = 0
    retained_long_running_heap: int = 0
    max_queue_depth: int = 0
    retained_live: int = 0
    retained_latest: int = 0
    retained_gaps: int = 0
    retained_carry_previous: int = 0
    retained_priority_refs: int = 0
    retained_unprocessed: int = 0
    retained_unsafe_protected: int = 0
    retained_tombstones: int = 0
    retained_total: int = 0
    retained_total_all: int = 0
    max_retained_total: int = 0

    def as_dict(self) -> dict[str, int]:
        """Return a detached diagnostic representation."""
        return dict(asdict(self))
