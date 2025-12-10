"""
Tame - Audio Volume Limiter
Automatically reduces system volume when audio gets too loud to protect your ears.
"""

import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
import json
import os
from pathlib import Path
import winreg
from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume, IAudioMeterInformation
from ctypes import cast, POINTER

try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False


class Settings:
    """User settings management"""
    
    def __init__(self):
        self.app_data = Path(os.getenv('APPDATA')) / 'tame'
        self.app_data.mkdir(exist_ok=True)
        self.settings_file = self.app_data / 'settings.json'
        self.load()
    
    def load(self):
        if self.settings_file.exists():
            try:
                with open(self.settings_file, 'r') as f:
                    data = json.load(f)
                    self.volume_cap = data.get('volume_cap', 0.2)
                    self.show_close_notifications = data.get('show_close_notifications', True)
                    self.run_at_startup = data.get('run_at_startup', False)
                    # New settings with defaults
                    self.attack_time = data.get('attack_time', 0.05)  # 50ms - sustained peak
                    self.release_time = data.get('release_time', 0.5)  # 500ms
                    self.hold_time = data.get('hold_time', 0.15)  # 150ms
                    self.user_cooldown = data.get('user_cooldown', 2.0)  # 2s
                    self.leeway_db = data.get('leeway_db', 3.0)  # 3dB leeway
                    self.dampening = data.get('dampening', 1.0)  # 1x (no dampening by default)
                    self.dampening_speed = data.get('dampening_speed', 0.0)  # 0s (instant) by default
                    self.voice_mode = data.get('voice_mode', False)
            except:
                self.set_defaults()
        else:
            self.set_defaults()
    
    def set_defaults(self):
        self.volume_cap = 0.2
        self.show_close_notifications = True
        self.run_at_startup = False
        self.attack_time = 0.05   # 50ms - wait for sustained peak
        self.release_time = 0.5   # 500ms release
        self.hold_time = 0.15     # 150ms hold
        self.user_cooldown = 2.0  # 2s user cooldown
        self.leeway_db = 3.0      # 3dB leeway above threshold
        self.dampening = 1.0      # 1x (no dampening by default)
        self.dampening_speed = 0.0  # 0s (instant) by default
        self.voice_mode = False
    
    def save(self):
        data = {
            'volume_cap': self.volume_cap,
            'show_close_notifications': self.show_close_notifications,
            'run_at_startup': self.run_at_startup,
            'attack_time': self.attack_time,
            'release_time': self.release_time,
            'hold_time': self.hold_time,
            'user_cooldown': self.user_cooldown,
            'leeway_db': self.leeway_db,
            'dampening': self.dampening,
            'dampening_speed': self.dampening_speed,
            'voice_mode': self.voice_mode
        }
        with open(self.settings_file, 'w') as f:
            json.dump(data, f, indent=2)


class ToggleSwitch(tk.Canvas):
    """Custom toggle switch widget"""
    
    def __init__(self, parent, variable=None, command=None, text="", 
                 width=50, height=26, on_color="#4CAF50", off_color="#ccc"):
        # Try to get parent bg, fallback to system color
        try:
            bg = parent.cget('background')
        except:
            bg = '#f0f0f0'
        
        super().__init__(parent, width=width + 250, height=height, 
                        bg=bg, highlightthickness=0)
        
        self.width = width
        self.height = height
        self.on_color = on_color
        self.off_color = off_color
        self.variable = variable
        self.command = command
        self.text = text
        
        # Draw the switch
        self._draw()
        
        # Bind click
        self.bind("<Button-1>", self._toggle)
        
        # Track variable changes
        if self.variable:
            self.variable.trace_add("write", lambda *args: self._draw())
    
    def _draw(self):
        self.delete("all")
        
        is_on = self.variable.get() if self.variable else False
        
        # Track background (rounded rectangle)
        radius = self.height // 2
        color = self.on_color if is_on else self.off_color
        
        # Draw rounded track
        self.create_oval(0, 0, self.height, self.height, fill=color, outline=color)
        self.create_oval(self.width - self.height, 0, self.width, self.height, fill=color, outline=color)
        self.create_rectangle(radius, 0, self.width - radius, self.height, fill=color, outline=color)
        
        # Draw thumb (circle)
        thumb_x = self.width - self.height + 3 if is_on else 3
        self.create_oval(thumb_x, 3, thumb_x + self.height - 6, self.height - 3, 
                        fill="white", outline="#ddd")
        
        # Draw label text
        self.create_text(self.width + 10, self.height // 2, 
                        text=self.text, anchor=tk.W, font=('Arial', 10))
    
    def _toggle(self, event=None):
        if self.variable:
            self.variable.set(not self.variable.get())
        if self.command:
            self.command()


class AudioController:
    """Controls and monitors Windows system volume using cached interfaces"""
    
    def __init__(self):
        # Get audio device once and cache interfaces
        devices = AudioUtilities.GetSpeakers()
        
        # Volume control interface
        vol_interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        self._volume_ctrl = cast(vol_interface, POINTER(IAudioEndpointVolume))
        
        # Audio meter interface for real peak levels
        meter_interface = devices.Activate(IAudioMeterInformation._iid_, CLSCTX_ALL, None)
        self._meter = cast(meter_interface, POINTER(IAudioMeterInformation))
        
        self._cached_volume = self._volume_ctrl.GetMasterVolumeLevelScalar()
        self._last_set_volume = self._cached_volume
        self.user_set_time = None
        self.user_set_volume = self._cached_volume
    
    def get_peak(self):
        """Get current audio peak level (0.0 to 1.0) - FAST"""
        try:
            return self._meter.GetPeakValue()
        except:
            return 0.0
    
    def get_raw_peak(self):
        """Get audio peak normalized to 0-1 as if volume were 100%"""
        try:
            peak = self._meter.GetPeakValue()
            vol = self._cached_volume
            if vol > 0.01:
                # Normalize: if volume is 50%, peak of 0.25 means raw audio is 0.5
                return min(1.0, peak / vol)
            return peak
        except:
            return 0.0
    
    def get_volume(self):
        """Get current system volume (0.0 to 1.0)"""
        try:
            self._cached_volume = self._volume_ctrl.GetMasterVolumeLevelScalar()
            return self._cached_volume
        except:
            return self._cached_volume
    
    def set_volume(self, level):
        """Set system volume (0.0 to 1.0)"""
        try:
            level = max(0.0, min(1.0, level))
            self._volume_ctrl.SetMasterVolumeLevelScalar(level, None)
            self._last_set_volume = level
            self._cached_volume = level
        except:
            pass
    
    def check_user_changed(self):
        """Check if user manually changed volume - returns True if changed"""
        current = self.get_volume()
        if abs(current - self._last_set_volume) > 0.01:
            self.user_set_time = time.time()
            self.user_set_volume = current
            self._last_set_volume = current
            return True
        return False


class VolumeLimiter:
    """Audio limiter with sustained peak detection"""
    
    def __init__(self, settings, audio_ctrl):
        self.settings = settings
        self.audio = audio_ctrl
        self.is_running = True
        
        # State
        self.volume_cap = settings.volume_cap
        self.original_volume = audio_ctrl.get_volume()  # Volume before limiting started
        self.current_peak = 0.0
        self.current_volume = self.original_volume
        
        # Limiter state
        self.is_limiting = False
        self.last_over_threshold_time = 0
        self.time_over_threshold = 0.0  # How long audio has been over threshold
        self.peak_start_time = 0.0      # When peak started
        
        # Timing parameters (loaded from settings)
        self.attack_time = settings.attack_time    # How long peak must sustain before limiting
        self.release_time = settings.release_time  # Release time in seconds
        self.hold_time = settings.hold_time        # Hold before release
        self.user_cooldown = settings.user_cooldown
        self.leeway_db = settings.leeway_db        # dB leeway above threshold
        self.dampening = settings.dampening        # Max dampening factor for sustained peaks
        self.dampening_speed = settings.dampening_speed  # Multiplier of attack_time to reach max dampening
        
        # Voice mode - optimized for speech protection
        self.voice_mode = settings.voice_mode
        
        # Computed release rate (volume units per second)
        self._update_release_rate()
        
        # Threading
        self._stop = threading.Event()
        self._thread = None
        
        # UI data (updated atomically)
        self.ui_peak = 0.0
        self.ui_volume = self.original_volume
    
    def _update_release_rate(self):
        """Calculate release rate from release time"""
        if self.release_time > 0:
            self.release_rate = 1.0 / self.release_time  # Full volume restore in release_time
        else:
            self.release_rate = 10.0  # Very fast
    
    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
    
    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
    
    def _run(self):
        """Main limiter loop - runs at high frequency"""
        last_time = time.time()
        
        while not self._stop.is_set():
            if not self.is_running:
                time.sleep(0.05)
                self.time_over_threshold = 0.0  # Reset when disabled
                continue
            
            now = time.time()
            dt = now - last_time
            last_time = now
            
            # Check for user volume changes - let user freely adjust
            if self.audio.check_user_changed():
                new_user_vol = self.audio.user_set_volume
                self.original_volume = new_user_vol
                self.is_limiting = False
                self.time_over_threshold = 0.0
            
            # Skip if in user cooldown
            if self.audio.user_set_time and (now - self.audio.user_set_time) < self.user_cooldown:
                time.sleep(0.02)
                continue
            
            # Get current peak level
            raw_peak = self.audio.get_raw_peak()
            self.current_peak = raw_peak
            self.current_volume = self.audio.get_volume()
            
            # Calculate what the output would be at original volume
            potential_output = raw_peak * self.original_volume
            
            # Calculate effective threshold with leeway
            # leeway_db of 3 means allow ~1.41x (√2) over threshold before full limiting
            # Convert dB to linear: 10^(dB/20)
            leeway_factor = 10 ** (self.leeway_db / 20)
            soft_threshold = self.volume_cap * leeway_factor  # Upper limit (hard cap)
            
            if potential_output > self.volume_cap and raw_peak > 0.001:
                # Audio is over threshold - accumulate time
                self.time_over_threshold += dt
                self.last_over_threshold_time = now
                
                if self.time_over_threshold >= self.attack_time:
                    # Sustained peak detected - start or continue limiting
                    if not self.is_limiting:
                        self.is_limiting = True
                    
                    # Calculate how far into the leeway zone we are (0 to 1)
                    # 0 = at volume_cap, 1 = at soft_threshold (max leeway)
                    if potential_output >= soft_threshold:
                        # Beyond leeway - full limiting
                        leeway_ratio = 1.0
                    else:
                        # In leeway zone - partial limiting
                        leeway_ratio = (potential_output - self.volume_cap) / (soft_threshold - self.volume_cap)
                    
                    # Reduction is proportional to how long over threshold
                    # sustained_factor goes from 1.0 at attack_time to dampening over dampening_speed seconds
                    time_since_attack = self.time_over_threshold - self.attack_time
                    if self.dampening_speed > 0.001:
                        # Ramp from 1.0 to dampening over dampening_speed seconds
                        ramp_progress = min(1.0, time_since_attack / self.dampening_speed)
                    else:
                        # Instant dampening
                        ramp_progress = 1.0
                    sustained_factor = 1.0 + (self.dampening - 1.0) * ramp_progress
                    sustained_factor = max(1.0, min(self.dampening, sustained_factor))
                    
                    # Target volume: softer reduction in leeway zone
                    # At volume_cap: minimal reduction, at soft_threshold: full reduction
                    base_target = self.volume_cap / raw_peak
                    
                    # Blend between original volume and base_target based on leeway_ratio
                    target_volume = self.original_volume * (1 - leeway_ratio) + base_target * leeway_ratio
                    
                    # Apply sustained factor for longer peaks (divide = more reduction)
                    target_volume = target_volume / sustained_factor
                    target_volume = max(0.01, min(1.0, target_volume))
                    
                    self.audio.set_volume(target_volume)
            else:
                # Audio is under threshold
                self.time_over_threshold = 0.0  # Reset accumulator
                
                if self.is_limiting:
                    time_since_loud = now - self.last_over_threshold_time
                    
                    if time_since_loud > self.hold_time:
                        # RELEASE: Gradually return to original volume
                        current = self.audio.get_volume()
                        target = self.original_volume
                        
                        if current < target - 0.005:
                            # Increase volume gradually
                            new_vol = current + self.release_rate * dt
                            new_vol = min(new_vol, target)
                            self.audio.set_volume(new_vol)
                        else:
                            # Reached original volume, stop limiting
                            self.audio.set_volume(target)
                            self.is_limiting = False
            
            # Update UI data
            self.ui_peak = self.audio.get_raw_peak()
            self.ui_volume = self.audio.get_volume()
            
            # Sleep for ~50Hz update rate
            time.sleep(0.02)
    
    def save_settings(self):
        self.settings.volume_cap = self.volume_cap
        self.settings.attack_time = self.attack_time
        self.settings.release_time = self.release_time
        self.settings.hold_time = self.hold_time
        self.settings.user_cooldown = self.user_cooldown
        self.settings.leeway_db = self.leeway_db
        self.settings.dampening = self.dampening
        self.settings.dampening_speed = self.dampening_speed
        self.settings.voice_mode = self.voice_mode
        self.settings.save()


class TameGUI:
    """Lightweight GUI"""
    
    def __init__(self, root, start_minimized=False):
        self.root = root
        self.root.title("Tame")
        self.root.geometry("460x780")
        self.root.resizable(False, False)
        
        # Initialize audio and limiter
        self.settings = Settings()
        self.audio = AudioController()
        self.limiter = VolumeLimiter(self.settings, self.audio)
        
        # Audio history for graph
        self.peak_history = [0.0] * 100
        
        # System tray
        self.tray_icon = None
        self._setup_tray()
        
        self._create_widgets()
        
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        
        # Start limiter
        self.limiter.start()
        
        # Position window
        self._position_window()
        
        # Start minimized to tray if requested
        if start_minimized:
            self.root.withdraw()
        
        # Start UI updates (slower rate)
        self._schedule_ui_update()
    
    def _create_widgets(self):
        main = ttk.Frame(self.root, padding="15")
        main.pack(fill=tk.BOTH, expand=True)
        
        # Title
        ttk.Label(main, text="Tame", font=('Arial', 16, 'bold')).pack(pady=(0, 10))
        
        # Status frame
        status_frame = ttk.Frame(main)
        status_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(status_frame, text="Status:").pack(side=tk.LEFT)
        self.status_label = ttk.Label(status_frame, text="Running", foreground="green")
        self.status_label.pack(side=tk.LEFT, padx=10)
        
        # === Volume Cap Slider ===
        self._create_slider(main, "Volume Cap:", 0.05, 1.0, 0.01,
                           self.limiter.volume_cap, self._on_cap_change, "%")
        
        # === Advanced Settings Frame ===
        adv_frame = ttk.LabelFrame(main, text="Advanced Settings", padding="10")
        adv_frame.pack(fill=tk.X, pady=10)
        
        # Attack Time (1ms to 100ms)
        self.attack_label = self._create_slider(adv_frame, "Attack:", 0.001, 0.1, 0.001,
                           self.limiter.attack_time, self._on_attack_change, "ms", 1000)
        
        # Release Time (100ms to 3s)
        self.release_label = self._create_slider(adv_frame, "Release:", 0.1, 3.0, 0.05,
                           self.limiter.release_time, self._on_release_change, "ms", 1000)
        
        # Hold Time (0 to 500ms)
        self.hold_label = self._create_slider(adv_frame, "Hold:", 0.0, 0.5, 0.01,
                           self.limiter.hold_time, self._on_hold_change, "ms", 1000)
        
        # User Cooldown (0.5s to 5s)
        self.cooldown_label = self._create_slider(adv_frame, "Cooldown:", 0.5, 5.0, 0.1,
                           self.limiter.user_cooldown, self._on_cooldown_change, "s", 1)
        
        # Leeway (0 to 12 dB)
        self.leeway_label = self._create_slider(adv_frame, "Leeway:", 0.0, 12.0, 0.5,
                           self.limiter.leeway_db, self._on_leeway_change, "dB", 1)
        
        # Dampening (1x to 5x)
        self.dampening_label = self._create_slider(adv_frame, "Dampening:", 1.0, 5.0, 0.1,
                           self.limiter.dampening, self._on_dampening_change, "x", 1)
        
        # Dampening Speed (0 to 2 seconds)
        self.dampening_speed_label = self._create_slider(adv_frame, "Damp Speed:", 0.0, 2.0, 0.05,
                           self.limiter.dampening_speed, self._on_dampening_speed_change, "s", 1)
        
        # Audio level display
        levels_frame = ttk.Frame(main)
        levels_frame.pack(fill=tk.X, pady=10)
        
        ttk.Label(levels_frame, text="Audio Level:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.peak_label = ttk.Label(levels_frame, text="0%", width=8)
        self.peak_label.grid(row=0, column=1, sticky=tk.W, pady=2)
        
        ttk.Label(levels_frame, text="System Vol:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.vol_label = ttk.Label(levels_frame, text="0%", width=8)
        self.vol_label.grid(row=1, column=1, sticky=tk.W, pady=2)
        
        # Audio level graph
        graph_frame = ttk.Frame(main)
        graph_frame.pack(fill=tk.X, pady=5)
        
        self.graph_canvas = tk.Canvas(graph_frame, width=380, height=50, bg='#1a1a1a', 
                                      highlightthickness=1, highlightbackground='#333')
        self.graph_canvas.pack()
        
        # Bottom buttons frame
        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill=tk.X, pady=10)
        
        # Toggle button
        self.toggle_btn = ttk.Button(btn_frame, text="Disable", command=self._toggle)
        self.toggle_btn.pack(side=tk.LEFT, padx=5)
        
        # Reset to defaults button
        reset_btn = ttk.Button(btn_frame, text="Reset Defaults", command=self._reset_defaults)
        reset_btn.pack(side=tk.RIGHT, padx=5)
        
        # Startup toggle
        self.startup_var = tk.BooleanVar(value=self.settings.run_at_startup)
        startup_toggle = ToggleSwitch(
            main, text="Run at Windows startup",
            variable=self.startup_var, command=self._on_startup_change
        )
        startup_toggle.pack(anchor=tk.W, pady=5)
        
        # Minimize to tray toggle
        self.minimize_var = tk.BooleanVar(value=self.settings.show_close_notifications)
        minimize_toggle = ToggleSwitch(
            main, text="Minimize to tray on close",
            variable=self.minimize_var, command=self._on_minimize_change
        )
        minimize_toggle.pack(anchor=tk.W, pady=5)
    
    def _create_slider(self, parent, label_text, from_, to, resolution, initial, callback, unit, multiplier=100):
        """Create a labeled slider with value display"""
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X, pady=3)
        
        ttk.Label(frame, text=label_text, width=10).pack(side=tk.LEFT)
        
        # Format value based on unit
        if unit == "%":
            val_text = f"{int(initial * multiplier)}%"
        elif unit == "ms":
            val_text = f"{int(initial * multiplier)}ms"
        elif unit == "dB":
            val_text = f"{initial:.1f}dB"
        elif unit == "x":
            val_text = f"{initial:.1f}x"
        else:
            val_text = f"{initial:.1f}s"
        
        val_label = ttk.Label(frame, text=val_text, width=8)
        val_label.pack(side=tk.RIGHT)
        
        var = tk.DoubleVar(value=initial)
        slider = tk.Scale(
            frame, from_=from_, to=to,
            variable=var, orient=tk.HORIZONTAL,
            resolution=resolution, showvalue=False, length=200,
            command=lambda v, cb=callback, lbl=val_label, u=unit, m=multiplier: 
                self._slider_callback(v, cb, lbl, u, m)
        )
        slider.pack(side=tk.RIGHT, padx=5)
        
        # Store reference for resetting
        setattr(self, f"slider_{label_text.replace(':', '').replace(' ', '_').lower()}", 
                (slider, var, val_label, unit, multiplier))
        
        return val_label
    
    def _slider_callback(self, val, callback, label, unit, multiplier):
        """Generic slider callback"""
        v = float(val)
        callback(v)
        if unit == "%":
            label.config(text=f"{int(v * multiplier)}%")
        elif unit == "ms":
            label.config(text=f"{int(v * multiplier)}ms")
        elif unit == "dB":
            label.config(text=f"{v:.1f}dB")
        elif unit == "x":
            label.config(text=f"{v:.1f}x")
        else:
            label.config(text=f"{v:.1f}s")
    
    def _on_cap_change(self, val):
        self.limiter.volume_cap = float(val)
    
    def _on_attack_change(self, val):
        self.limiter.attack_time = float(val)
    
    def _on_release_change(self, val):
        self.limiter.release_time = float(val)
        self.limiter._update_release_rate()
    
    def _on_hold_change(self, val):
        self.limiter.hold_time = float(val)
    
    def _on_cooldown_change(self, val):
        self.limiter.user_cooldown = float(val)
    
    def _on_leeway_change(self, val):
        self.limiter.leeway_db = float(val)
    
    def _on_dampening_change(self, val):
        self.limiter.dampening = float(val)
    
    def _on_dampening_speed_change(self, val):
        self.limiter.dampening_speed = float(val)
    
    def _update_slider_displays(self):
        """Update all slider positions and labels to match current limiter values"""
        sliders = [
            ('slider_volume_cap', self.limiter.volume_cap),
            ('slider_attack', self.limiter.attack_time),
            ('slider_release', self.limiter.release_time),
            ('slider_hold', self.limiter.hold_time),
            ('slider_cooldown', self.limiter.user_cooldown),
            ('slider_leeway', self.limiter.leeway_db),
            ('slider_dampening', self.limiter.dampening),
            ('slider_damp_speed', self.limiter.dampening_speed),
        ]
        for attr, value in sliders:
            if hasattr(self, attr):
                slider, var, label, unit, mult = getattr(self, attr)
                var.set(value)
                if unit == "%":
                    label.config(text=f"{int(value * mult)}%")
                elif unit == "ms":
                    label.config(text=f"{int(value * mult)}ms")
                elif unit == "dB":
                    label.config(text=f"{value:.1f}dB")
                elif unit == "x":
                    label.config(text=f"{value:.1f}x")
                else:
                    label.config(text=f"{value:.1f}s")
    
    def _reset_defaults(self):
        """Reset advanced settings to defaults (preserves volume cap)"""
        self.limiter.attack_time = 0.05  # 50ms
        self.limiter.release_time = 0.5
        self.limiter.hold_time = 0.15
        self.limiter.user_cooldown = 2.0
        self.limiter.leeway_db = 3.0     # 3dB leeway
        self.limiter.dampening = 2.0     # 2x max dampening
        self.limiter.dampening_speed = 0.1  # 100ms to reach max
        self.limiter._update_release_rate()
        
        self._update_slider_displays()
    
    def _toggle(self):
        self.limiter.is_running = not self.limiter.is_running
        if self.limiter.is_running:
            self.toggle_btn.config(text="Disable")
            self.status_label.config(text="Running", foreground="green")
        else:
            self.toggle_btn.config(text="Enable")
            self.status_label.config(text="Stopped", foreground="red")
    
    def _on_startup_change(self):
        enabled = self.startup_var.get()
        self.settings.run_at_startup = enabled
        self._update_startup_registry()
    
    def _update_startup_registry(self):
        """Update Windows startup registry with correct flags"""
        key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
            if self.settings.run_at_startup:
                # Add --minimized flag if minimize to tray is also enabled
                exe_path = sys.executable
                if self.settings.show_close_notifications:
                    exe_path = f'"{exe_path}" --minimized'
                winreg.SetValueEx(key, "Tame", 0, winreg.REG_SZ, exe_path)
            else:
                try:
                    winreg.DeleteValue(key, "Tame")
                except:
                    pass
            winreg.CloseKey(key)
        except:
            pass
    
    def _on_minimize_change(self):
        """Handle minimize to tray checkbox change"""
        self.settings.show_close_notifications = self.minimize_var.get()
        self.settings.save()
        # Update registry if startup is enabled (to add/remove --minimized flag)
        if self.settings.run_at_startup:
            self._update_startup_registry()
    
    def _schedule_ui_update(self):
        """Update UI at 10Hz - much less CPU intensive"""
        try:
            peak = self.limiter.ui_peak  # Raw audio level (0-1)
            vol = self.limiter.ui_volume
            
            # Show raw peak as percentage (this is the audio level relative to system volume)
            peak_pct = int(peak * 100)
            vol_pct = int(vol * 100)
            self.peak_label.config(text=f"{peak_pct}%")
            self.vol_label.config(text=f"{vol_pct}%")
            
            # Update graph with raw peak level
            self.peak_history.pop(0)
            self.peak_history.append(peak)
            self._draw_graph()
        except:
            pass
        
        self.root.after(100, self._schedule_ui_update)
    
    def _draw_graph(self):
        """Draw the audio level graph"""
        canvas = self.graph_canvas
        canvas.delete("all")
        
        w = 340
        h = 50
        num_points = len(self.peak_history)
        
        # Draw threshold line - this is where limiting kicks in
        # Limiting starts when peak * original_volume > volume_cap
        # So threshold peak = volume_cap / original_volume
        original_vol = self.limiter.original_volume if self.limiter.original_volume > 0 else 1.0
        threshold = min(1.0, self.limiter.volume_cap / original_vol)
        cap_y = h - (threshold * h)
        canvas.create_line(0, cap_y, w, cap_y, fill='#ff4444', width=1, dash=(4, 2))
        
        # Draw waveform
        if num_points < 2:
            return
        
        step = w / (num_points - 1)
        points = []
        
        for i, peak in enumerate(self.peak_history):
            x = i * step
            y = h - (peak * h)
            points.extend([x, y])
        
        if len(points) >= 4:
            # Draw filled area under the line
            fill_points = [0, h] + points + [w, h]
            canvas.create_polygon(fill_points, fill='#2d5a2d', outline='')
            
            # Draw the line on top
            canvas.create_line(points, fill='#44ff44', width=2, smooth=True)
    
    def _position_window(self):
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        ww = self.root.winfo_width()
        wh = self.root.winfo_height()
        self.root.geometry(f"+{sw - ww - 20}+{sh - wh - 80}")
    
    def _setup_tray(self):
        """Setup system tray icon"""
        if not TRAY_AVAILABLE:
            return
        
        # Create a simple icon (green circle)
        def create_icon():
            img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.ellipse([8, 8, 56, 56], fill=(76, 175, 80, 255))
            draw.text((22, 18), "M", fill=(255, 255, 255, 255))
            return img
        
        menu = pystray.Menu(
            pystray.MenuItem("Show", self._show_window, default=True),
            pystray.MenuItem("Exit", self._exit_app)
        )
        
        self.tray_icon = pystray.Icon("Tame", create_icon(), "Tame - Volume Limiter", menu)
        
        # Run tray icon in separate thread
        tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
        tray_thread.start()
    
    def _show_window(self, icon=None, item=None):
        """Show the main window"""
        self.root.after(0, self._do_show_window)
    
    def _do_show_window(self):
        """Actually show the window (must be called from main thread)"""
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
    
    def _exit_app(self, icon=None, item=None):
        """Exit the application"""
        self.root.after(0, self._do_exit)
    
    def _do_exit(self):
        """Actually exit (must be called from main thread)"""
        self.limiter.save_settings()
        self.limiter.stop()
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.destroy()
    
    def _on_closing(self):
        if self.settings.show_close_notifications:
            # Minimize to tray instead of closing
            self.root.withdraw()
            return
        
        self._do_exit()


def main():
    # Check for --minimized flag (used when starting at login)
    start_minimized = "--minimized" in sys.argv
    
    root = tk.Tk()
    app = TameGUI(root, start_minimized=start_minimized)
    root.mainloop()


if __name__ == "__main__":
    main()
