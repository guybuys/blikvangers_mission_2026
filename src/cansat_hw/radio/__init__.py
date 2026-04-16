"""RFM69HCW packet radio (SPI)."""

__all__ = ["RFM69"]


def __getattr__(name: str):
	"""Lazy import: ``spidev``/``gpiozero`` alleen bij ``from cansat_hw.radio import RFM69``."""
	if name == "RFM69":
		from cansat_hw.radio.rfm69 import RFM69

		return RFM69
	raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
