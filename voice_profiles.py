"""Piper voice models and reversible delivery presets."""
import json
import os
import tempfile
from pathlib import Path

PREFERENCE = Path.home() / '.config/jarvis/voice-profile.json'
PENDING_PROFILE = Path(__file__).resolve().parent/'models/tts/pending-profile.json'
PROFILES = {
    'jarvis-real': {'label': 'Jarvis Real — clearer speech',
        'model': 'en_US-Jarvis_Real-medium.onnx', 'settings': {
            'length_scale': 1.08, 'noise_scale': 0.5, 'noise_w_scale': 0.55,
            'volume': 0.85}},
    'jarvis-real-original': {'label': 'Jarvis Real — previous delivery',
        'model': 'en_US-Jarvis_Real-medium.onnx', 'settings': {}},
    'northern': {'label': 'Northern British (different voice)',
        'model': 'en_GB-northern_english_male-medium.onnx', 'settings': {'volume': 0.9}},
    'original': {'label': 'Original voice (revert)', 'settings': {}},
    'measured': {'label': 'Measured British', 'settings': {
        'length_scale': 1.06, 'noise_scale': 0.5, 'noise_w_scale': 0.55,
        'volume': 0.9}},
    'brisk': {'label': 'Brisk British', 'settings': {
        'length_scale': 0.96, 'noise_scale': 0.55, 'noise_w_scale': 0.65,
        'volume': 0.9}},
}

def read_profile():
    try:
        name = json.loads(PREFERENCE.read_text())['profile']
        return name if name in PROFILES else 'original'
    except (OSError, ValueError, KeyError, TypeError):
        return 'original'

def save_profile(name):
    if name not in PROFILES:
        raise ValueError(f'Unknown voice profile: {name}')
    model = PROFILES[name].get('model')
    if model and not (Path(__file__).resolve().parent/'models/tts'/model).is_file():
        installer='install-local-voice' if name=='jarvis-real' else 'install-alternative-voice'
        raise ValueError('Voice is not installed. Run ~/jarvis/scripts/'+installer+' first.')
    PREFERENCE.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=PREFERENCE.parent, prefix='.voice-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump({'profile': name}, stream)
            stream.write('\n')
        os.replace(temporary, PREFERENCE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def synthesis_config(name):
    from piper.config import SynthesisConfig
    return SynthesisConfig(**PROFILES[name]['settings'])


def apply_pending_profile():
    """Activate a locally staged, validated voice when the host starts Jarvis."""
    pending=PENDING_PROFILE
    if not pending.exists():return
    data=json.loads(pending.read_text())
    name=data.get('profile')
    if name not in PROFILES:raise ValueError('Unknown staged voice profile.')
    previous=read_profile()
    backup=PREFERENCE.parent/'voice-before-local-model.json'
    if not backup.exists():
        backup.parent.mkdir(parents=True,exist_ok=True)
        backup.write_text(json.dumps({'profile':previous})+'\n');backup.chmod(0o600)
    save_profile(name)
    pending.unlink()
