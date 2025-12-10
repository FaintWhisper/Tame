# Tame

A Windows audio volume limiter that automatically reduces system volume when audio exceeds a configurable threshold, protecting against sudden loud sounds.

## Features

- Automatic volume limiting with configurable threshold
- Real-time audio peak monitoring via Windows Audio Session API (WASAPI)
- Compressor-style attack/release/hold timing controls
- Soft-knee limiting with adjustable leeway (dB)
- System tray integration with minimize-to-tray support
- Windows startup integration (starts minimized when enabled)
- Persistent settings stored in `%APPDATA%\tame\settings.json`

## Installation

### Pre-built Executable

Download `Tame.exe` from the [Releases](https://github.com/FaintWhisper/Tame/releases) page.

### Build from Source

```bash
# Clone the repository
git clone https://github.com/FaintWhisper/Tame.git
cd Tame

# Install dependencies
pip install -r requirements.txt

# Run directly
python tame.py

# Build standalone executable
build.bat
```

## Requirements

- Windows 10/11
- Python 3.8+ (for running from source)

## Usage

1. Launch `Tame.exe` or run `python tame.py`
2. Adjust the **Volume Cap** slider to set your maximum output level (default: 20%)
3. The limiter monitors audio in real-time and reduces volume when peaks exceed the threshold
4. Close the window to minimize to system tray (configurable)

## Configuration

### Basic Settings

| Setting | Description | Default |
|---------|-------------|---------|
| Volume Cap | Maximum allowed output level (0-100%) | 20% |
| Run at Windows startup | Auto-start Tame when Windows boots | Off |
| Minimize to tray on close | Hide to tray instead of exiting | On |

### Advanced Limiter Settings

| Parameter | Description | Default | Range |
|-----------|-------------|---------|-------|
| Attack | Time audio must exceed threshold before limiting | 50ms | 1-100ms |
| Release | Time to restore volume after audio drops below threshold | 500ms | 100-3000ms |
| Hold | Delay before release begins after audio drops | 150ms | 0-500ms |
| Cooldown | Pause duration after manual volume adjustment | 2.0s | 0.5-5.0s |
| Leeway | Soft-knee range above threshold (dB) | 3.0dB | 0-12dB |
| Dampening | Maximum reduction multiplier for sustained peaks | 1.0x | 1.0-3.0x |
| Dampening Speed | Time to reach maximum dampening | 0.0s | 0-5s |

### How the Limiter Works

1. **Peak Detection**: Monitors system audio output using `IAudioMeterInformation` from WASAPI
2. **Threshold Check**: Calculates potential output level based on current peak and system volume
3. **Attack Phase**: When audio exceeds threshold, waits for sustained peak (attack time) before limiting
4. **Limiting**: Reduces system volume proportionally, applying leeway curve and dampening factor
5. **Hold Phase**: After audio drops below threshold, maintains reduced volume for hold duration
6. **Release Phase**: Gradually restores volume to original level over release time

The limiter respects manual volume changes by pausing automatic control for the configured cooldown period.

## Technical Details

### Architecture

- **Audio Interface**: Windows Core Audio API (WASAPI) via `pycaw`
  - `IAudioEndpointVolume` for system volume control
  - `IAudioMeterInformation` for real-time peak metering
- **GUI Framework**: tkinter with custom toggle switch widgets
- **System Tray**: `pystray` with PIL for icon generation
- **Packaging**: PyInstaller single-file executable

### Dependencies

```
pycaw>=20230407      # Windows Core Audio Python bindings
comtypes>=1.2.0      # COM interface support
numpy>=1.24.0        # Numerical operations
pystray>=0.19.0      # System tray integration
Pillow>=10.0.0       # Image processing for tray icon
pyinstaller>=6.0.0   # Executable packaging (build only)
```

### File Structure

```
Tame/
├── tame.py           # Main application
├── Tame.spec         # PyInstaller configuration
├── build.bat         # Build script
├── setup.bat         # Development environment setup
├── requirements.txt  # Python dependencies
└── dist/
    └── Tame.exe      # Built executable
```

## Acknowledgments

Inspired by [Mufflr](https://github.com/johntringham/Mufflr) by John Tringham (C#/Avalonia).

## License

MIT License. No tracking, telemetry, or data collection.
