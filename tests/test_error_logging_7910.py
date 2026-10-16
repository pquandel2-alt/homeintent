from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))

from homeintent import error_logging  # noqa: E402


def test_boundary_failure_logging_is_redacted_and_rate_limited(caplog, monkeypatch):
    secret = "/config/secrets.yaml token=super-secret api_key=123456"
    logger = logging.getLogger("homeintent.test.rollback")
    clock = iter((10.0, 11.0, 71.0))
    monkeypatch.setattr(error_logging.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(error_logging, "_LAST_WARNING", {})
    caplog.set_level(logging.DEBUG, logger=logger.name)

    assert error_logging.log_rate_limited_boundary_failure(
        logger, "rollback", RuntimeError(secret)
    )
    assert not error_logging.log_rate_limited_boundary_failure(
        logger, "rollback", RuntimeError(secret)
    )
    assert error_logging.log_rate_limited_boundary_failure(
        logger, "rollback", RuntimeError(secret)
    )

    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 2
    assert "error_type=RuntimeError" in caplog.text
    assert secret not in caplog.text
