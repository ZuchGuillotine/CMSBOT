"""CMSBOT — Multi-agent content refinement pipeline using the Autoreason pattern."""

__version__ = "0.1.0"

from cmsbot.pipeline import AutoreasonPipeline
from cmsbot.config import Config

__all__ = ["AutoreasonPipeline", "Config"]
