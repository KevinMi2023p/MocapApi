"""Mocap Studio local service.

The service is deliberately SDK-independent. Providers normalize incoming motion
data into one scene model, while the browser UI talks only to the local HTTP API.
"""

APPLICATION_ID = "io.github.KevinMi2023p.MocapStudio"
__version__ = "0.3.0"
