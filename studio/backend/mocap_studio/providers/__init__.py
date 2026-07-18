"""Motion data providers shipped with Mocap Studio."""

from .base import MotionFrame, Provider, ProviderCapabilities, ProviderError
from .bvh import BvhProvider
from .demo import DemoProvider

__all__ = [
    "BvhProvider",
    "DemoProvider",
    "MotionFrame",
    "Provider",
    "ProviderCapabilities",
    "ProviderError",
]
