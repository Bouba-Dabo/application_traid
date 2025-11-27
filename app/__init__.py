"""Application package initialization.

This module sets a minimal logging configuration used across `app.*` modules.
It is safe to import and will not overwrite existing logging configuration
if the application or tests configure logging themselves.
"""

import logging

# Configure a reasonable default only if the root logger has no handlers.
# This avoids clobbering test harnesses or other frameworks that configure logging.
if not logging.getLogger().hasHandlers():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s"
    )

__all__ = []
