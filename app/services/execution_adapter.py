"""Stub job execution adapter.

Swap this out for a real integration (a Kubernetes Job, a cloud batch API,
dispatching a CI runner, etc.) once one exists. For now it just logs and
returns a fabricated result so the scheduling pipeline is fully testable
end-to-end without any real compute backend.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExecutionResult:
    job_id: int
    region: str
    status: str  # "dispatched"
    message: str


def dispatch_job(job_id: int, region: str) -> ExecutionResult:
    """Pretend to launch `job_id` in `region`. Always "succeeds"."""
    logger.info("Dispatching job %s to region %s (stub execution adapter)", job_id, region)
    return ExecutionResult(
        job_id=job_id,
        region=region,
        status="dispatched",
        message=f"Job {job_id} dispatched to {region} (stub execution adapter)",
    )
