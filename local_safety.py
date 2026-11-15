"""Conservative local-inference limits; these cannot predict every hardware fault."""
import psutil


def unsafe_reason(prompt=None):
    if prompt is not None and len(prompt) > 6000:
        return 'Local mode stopped: this message is too large for the conservative local limit (6000 characters).'
    try:
        memory = psutil.virtual_memory()
        if memory.available < 1536*1024*1024 or memory.percent >= 92:
            return 'Local mode stopped to protect the laptop: available memory is too low.'
        temperatures = psutil.sensors_temperatures()
        for name, entries in temperatures.items():
            if not any(sensor in name.lower() for sensor in ('coretemp','k10temp','cpu','zenpower','acpitz')):
                continue
            if any(entry.current >= 90 for entry in entries if entry.current is not None):
                return 'Local mode stopped to protect the laptop: CPU temperature reached 90°C.'
    except (OSError, AttributeError, RuntimeError, psutil.Error):
        return 'Local mode stopped: laptop resource status could not be checked.'
    return None
