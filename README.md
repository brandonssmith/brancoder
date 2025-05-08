# Brancoder

A modern video conversion application with a dark theme interface, built using Python, PyQt6, and FFmpeg.

I built this with my own preferred workflow so that I can convert video files the way that I prefer to.

## Features

- Modern dark theme UI
- Support for multiple video formats (mp4, avi, mkv, mov, webm)
- Multiple codec options (h264, h265, vp9, mpeg4)
- Quality presets (High, Medium, Low)
- Real-time conversion progress
- Preview windows for input and output videos

## Requirements

- Python 3.8 or higher
- FFmpeg installed on your system
- PyQt6
- ffmpeg-python

## Installation

1. Make sure you have FFmpeg installed on your system:
   - Windows: Download from https://ffmpeg.org/download.html
   - Linux: `sudo apt-get install ffmpeg`
   - macOS: `brew install ffmpeg`

2. Install the required Python packages:
   ```bash
   pip install -r requirements.txt
   ```

## Usage

1. Run the application:
   ```bash
   python video_converter.py
   ```

2. Use the interface:
   - Click "Open Video File" to select a video for conversion
   - Choose your desired output format, codec, and quality settings
   - Click "Render" to start the conversion
   - Monitor the progress in the right panel

## Note

Make sure FFmpeg is properly installed and accessible from your system's PATH before running the application. 

## Current known errors
 - The Output preview window is currently not showing progress
