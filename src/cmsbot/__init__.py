"""CMSBOT package."""

from __future__ import annotations

from typing import Any

__version__ = "0.1.0"

__all__ = ["AutoreasonPipeline", "Config"]


def __getattr__(name: str) -> Any:
    if name == "AutoreasonPipeline":
        from cmsbot.pipeline import AutoreasonPipeline

        return AutoreasonPipeline
    if name == "Config":
        from cmsbot.config import Config

        return Config
    raise AttributeError(name)
