"""Piper speech output and openWakeWord/Faster-Whisper voice input."""
from __future__ import annotations
import os, re, subprocess, threading, queue, json
from pathlib import Path
from PyQt6.QtCore import QObject, pyqtSignal
from voice_profiles import PROFILES, read_profile, save_profile, synthesis_config

ROOT=Path(__file__).resolve().parent
MODEL=ROOT/"models/tts/en_GB-alan-medium.onnx"

def spoken_result(text: str) -> str:
    """Summarize structured receipts for speech; leave the displayed text intact."""
    try:
        data=json.loads(text)
    except (ValueError,TypeError):
        return text
    if isinstance(data,dict):
        if data.get('ok') is False or data.get('error'):
            return 'The desktop action could not be completed. The details are on screen.'
        if isinstance(data.get('moved'),int) and isinstance(data.get('workspace'),int):
            count=data['moved']
            return f'Moved {count} window{"s" if count!=1 else ""} to workspace {data["workspace"]}.'
        for key in ('closed','minimized','maximized','restored'):
            if isinstance(data.get(key),int):
                count=data[key]
                return f'{key.capitalize()} {count} window{"s" if count!=1 else ""}.'
        window=data.get('window')
        if isinstance(window,dict) and data.get('ok') is True:
            action=data.get('action')
            verbs={'close':'Closed','minimize':'Minimized','maximize':'Maximized','restore':'Restored','focus':'Focused','resize':'Resized','move':'Moved'}
            if action in verbs:return verbs[action]+' the window.'
            workspace=window.get('workspace')
            if isinstance(workspace,int):return f'The window is now on workspace {workspace}.'
            return 'The window action is complete.'
        if data.get('ok') is True:return 'Done. The details are on screen.'
    if isinstance(data,(dict,list)):
        return 'The details are on screen.'
    return text

def speech_text(text: str) -> str:
    text=spoken_result(text)
    text=re.sub(r"```.*?```", " ", text, flags=re.S); text=re.sub(r"`([^`]*)`",r"\1",text)
    text=re.sub(r"https?://\S+|www\.\S+", " ", text); text=re.sub(r"!?\[[^]]*\]\([^)]*\)","",text)
    text=re.sub(r"[*_#>|~]", "", text); return re.sub(r"\s+", " ", text).strip()


class SentenceBuffer:
    """Release short complete speech units without reading fenced code aloud."""
    def __init__(self):
        self.text=''; self.in_code=False; self.ticks=''; self.structured=False
    def feed(self,piece):
        if self.structured or (not self.text.strip() and piece.lstrip().startswith(('{','['))):
            self.structured=True
            self.text+=piece
            return []
        data=self.ticks+piece; self.ticks=''; index=0
        while index<len(data):
            if data[index]=='`':
                end=index
                while end<len(data) and data[end]=='`':end+=1
                count=end-index
                if count<3 and end==len(data):
                    self.ticks=data[index:]; break
                if count>=3:self.in_code=not self.in_code
                elif not self.in_code:self.text+=data[index:end]
                index=end; continue
            if not self.in_code:self.text+=data[index]
            index+=1
        chunks=[]
        while True:
            boundary=re.search(r'[.!?][\)\]"\']*\s+|\n\s*\n',self.text)
            if boundary:
                # Don't split decimal numbers or initials inside a token.
                end=boundary.end()
            elif len(self.text)>=180:
                end=self.text.rfind(' ',40,180)
                if end<0:break
            else:break
            chunk=speech_text(self.text[:end]); self.text=self.text[end:]
            if chunk:chunks.append(chunk)
        return chunks
    def finish(self):
        if self.structured:
            summary=spoken_result(self.text)
            chunk=speech_text(summary) if summary!=self.text else 'The details are on screen.'
        else:chunk=speech_text(self.text)
        self.text=''; self.ticks=''; self.structured=False
        return [chunk] if chunk else []


def load_piper_voice(model=None):
    model = MODEL if model is None else Path(model)
    from piper.voice import PiperVoice
    from piper.config import PiperConfig
    import onnxruntime
    options=onnxruntime.SessionOptions(); options.intra_op_num_threads=2; options.inter_op_num_threads=1
    session=onnxruntime.InferenceSession(str(model),sess_options=options,providers=['CPUExecutionProvider'])
    config=PiperConfig.from_dict(json.loads(Path(str(model)+'.json').read_text()))
    return PiperVoice(session=session,config=config)

class VoiceEngine(QObject):
    audio_level=pyqtSignal(float); transcript=pyqtSignal(str); status=pyqtSignal(str)
    def __init__(self):
        super().__init__(); self.listening=False; self.muted=False; self._listen_thread=None; self._lock=threading.Lock()
        self._generation=0; self._speech_queue=queue.Queue(); self._voice=None
        self._closed=False; self._voice_model=MODEL; self._load_error=None
        self._speech_thread=threading.Thread(target=self._speech_loop,daemon=True); self._speech_thread.start()
    def set_profile(self,name):
        save_profile(name)
        self.stop_speaking()

    def set_muted(self,value):
        self.muted=bool(value)
        if self.muted:self.stop_speaking()
    def stop_speaking(self):
        with self._lock:
            self._generation+=1
            while True:
                try:self._speech_queue.get_nowait()
                except queue.Empty:break
    def speak(self,text):
        clean=speech_text(text)
        if clean and not self.muted:
            with self._lock:
                if not self._closed:self._speech_queue.put((self._generation,clean))
    def shutdown(self):
        self.stop_speaking()
        with self._lock:
            if self._closed:return
            self._closed=True; self._speech_queue.put(None)
    def _speech_loop(self):
        if MODEL.is_file():
            try:self._voice=load_piper_voice()
            except Exception as exc:self._voice=None; self._load_error=str(exc)
        while True:
            item=self._speech_queue.get()
            if item is None:return
            generation,text=item
            if generation==self._generation and not self.muted:self._speak(text,generation)
    def _ensure_profile_voice(self,profile):
        model=ROOT/'models/tts'/PROFILES[profile].get('model',MODEL.name)
        if model!=self._voice_model:
            # Keep the previous model if the replacement cannot be loaded.
            loaded=load_piper_voice(model)
            self._voice=loaded; self._voice_model=model; self._load_error=None

    def _speak(self,text,generation,profile_override=None):
        try:
            profile=profile_override or read_profile()
            try:self._ensure_profile_voice(profile)
            except Exception as exc:
                self.status.emit('Selected voice could not load; retaining the previous Piper voice: '+str(exc))
                profile='original'
            if self._voice is None:
                self.status.emit("Piper could not load; using system speech: "+str(self._load_error))
                self._speak_system(text)
                return
            import numpy as np, sounddevice as sd
            for audio_chunk in self._voice.synthesize(text, syn_config=synthesis_config(profile)):
                if self.muted or generation!=self._generation:return
                rate=audio_chunk.sample_rate; channels=audio_chunk.sample_channels
                audio=audio_chunk.audio_float_array
                block=max(1,rate//33)
                audio=audio.reshape(-1,channels)
                with sd.OutputStream(samplerate=rate,channels=channels,dtype="float32",blocksize=block) as stream:
                    for start in range(0,len(audio),block):
                        chunk=audio[start:start+block]
                        if self.muted or generation!=self._generation:return
                        rms=float(np.sqrt(np.mean(np.square(chunk)))) if chunk.size else 0.
                        self.audio_level.emit(min(1.,rms*5)); stream.write(chunk)
        except Exception as exc:
            if self.muted or generation!=self._generation:return
            try:
                if self._voice_model!=MODEL:
                    self._voice=load_piper_voice(MODEL);self._voice_model=MODEL
                    # Reuse the normal audio/reactor playback, but select original
                    # only for this fallback call; keep the saved choice reversible.
                    self._speak(text,generation,profile_override='original')
                    return
                self.status.emit(f"Piper failed; using system speech: {exc}")
                self._speak_system(text)
            except Exception as fallback_exc:
                self.status.emit(f"Speech unavailable: {fallback_exc} (Piper: {exc})")
        finally:self.audio_level.emit(0.)
    def _speak_system(self,text):
        subprocess.run(["spd-say","--wait","--language=en-GB","--voice-type=male1","--rate=-5","--pitch=-8",text],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,timeout=60)
    def start_listening(self):
        if self.listening:return
        self.listening=True; self._listen_thread=threading.Thread(target=self._listen,daemon=True); self._listen_thread.start()
    def stop_listening(self):self.listening=False
    def _listen(self):
        try:
            import numpy as np, sounddevice as sd
            from openwakeword.model import Model
            from faster_whisper import WhisperModel
            wake=Model(wakeword_models=["hey_jarvis"]); stt=WhisperModel(os.getenv("JARVIS_WHISPER_MODEL","tiny.en"),device="cpu",compute_type="int8")
            self.status.emit("Listening for Hey Jarvis")
            while self.listening:
                audio=sd.rec(1280,samplerate=16000,channels=1,dtype="int16",blocking=True).reshape(-1)
                scores=wake.predict(audio); triggered=any(float(v)>.55 for v in scores.values())
                if not triggered:continue
                self.status.emit("Wake word detected; listening")
                frames=[]
                for _ in range(40):
                    if not self.listening:break
                    block=sd.rec(1600,samplerate=16000,channels=1,dtype="float32",blocking=True).reshape(-1); frames.extend(block)
                    if len(frames)>8000 and np.sqrt(np.mean(np.square(frames[-4000:])))<.008:break
                if frames:
                    segments,_=stt.transcribe(np.asarray(frames,dtype=np.float32),language="en",vad_filter=True); text=" ".join(s.text.strip() for s in segments).strip()
                    if text:self.transcript.emit(text)
                self.status.emit("Listening for Hey Jarvis")
        except Exception as exc:self.listening=False; self.status.emit(f"Voice input unavailable: {exc}")
