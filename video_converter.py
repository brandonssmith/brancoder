import sys
import os
import subprocess
import re
import tempfile
import json
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                            QHBoxLayout, QPushButton, QLabel, QFileDialog, 
                            QComboBox, QProgressBar, QListWidget, QStyle,
                            QMessageBox, QSlider, QTextEdit, QSizePolicy, QLineEdit, QSpinBox)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl, QSize, QObject, QRect, QPoint, QTimer
from PyQt6.QtGui import QPalette, QColor, QIcon, QPainter, QPen, QBrush
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget
import ffmpeg

SETTINGS_FILE = os.path.expanduser('~/.brancoder_settings.json')

def check_ffmpeg():
    try:
        # Try to run ffmpeg -version
        result = subprocess.run(['ffmpeg', '-version'], 
                              capture_output=True, 
                              text=True, 
                              check=True)
        return True, result.stdout.split('\n')[0]
    except subprocess.CalledProcessError:
        return False, "FFmpeg is installed but returned an error"
    except FileNotFoundError:
        return False, "FFmpeg is not installed or not in PATH"

class VideoConverterThread(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal()
    error = pyqtSignal(str)
    progress_info = pyqtSignal(str)

    def __init__(self, input_file, output_file, format_options):
        super().__init__()
        self.input_file = input_file
        self.output_file = output_file
        self.format_options = format_options

    def run(self):
        try:
            # Get video duration for progress calculation
            probe = ffmpeg.probe(self.input_file)
            duration = float(probe['format']['duration'])
            
            # Prepare FFmpeg command
            stream = ffmpeg.input(self.input_file)
            stream = ffmpeg.output(stream, self.output_file, **self.format_options)
            
            # Run FFmpeg with progress monitoring
            process = ffmpeg.run_async(stream, pipe_stdout=True, pipe_stderr=True, overwrite_output=True)
            
            # Monitor FFmpeg output
            while True:
                line = process.stderr.readline().decode('utf-8')
                if not line and process.poll() is not None:
                    break
                if line:
                    # Extract progress information
                    if 'frame=' in line:
                        self.progress_info.emit(line.strip())
                        # Calculate progress percentage
                        if 'time=' in line:
                            time_match = re.search(r'time=(\d+:\d+:\d+.\d+)', line)
                            if time_match:
                                time_str = time_match.group(1)
                                h, m, s = time_str.split(':')
                                current_time = float(h) * 3600 + float(m) * 60 + float(s)
                                progress = int((current_time / duration) * 100)
                                self.progress.emit(progress)
            
            if process.returncode == 0:
                self.finished.emit()
            else:
                self.error.emit("FFmpeg process failed")
                
        except Exception as e:
            self.error.emit(str(e))

class AspectRatioVideoWidget(QVideoWidget):
    def __init__(self, aspect_ratio=(16, 9), parent=None):
        super().__init__(parent)
        self.aspect_ratio = aspect_ratio
        self.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def resizeEvent(self, event):
        width = self.width()
        ar_w, ar_h = self.aspect_ratio
        height = int(width * ar_h / ar_w)
        self.setFixedHeight(height)
        super().resizeEvent(event)

def get_ffmpeg_formats():
    try:
        result = subprocess.run(['ffmpeg', '-hide_banner', '-formats'], capture_output=True, text=True, check=True)
        formats = []
        for line in result.stdout.splitlines():
            if re.match(r'\s*E', line):  # Lines starting with E (for muxing/writing)
                parts = line.split()
                if len(parts) > 1:
                    fmt = parts[1]
                    if fmt.isalpha():
                        formats.append(fmt)
        return sorted(set(formats))
    except Exception as e:
        return ["mp4", "avi", "mkv", "mov", "webm"]  # fallback

def get_ffmpeg_video_codecs():
    try:
        result = subprocess.run(['ffmpeg', '-hide_banner', '-codecs'], capture_output=True, text=True, check=True)
        codecs = []
        for line in result.stdout.splitlines():
            # Look for lines with E (encode) and V (video)
            if re.match(r'\s*[ D][ E][VAS][. ]', line):
                if 'E' in line[2] and 'V' in line[3]:
                    parts = line.split()
                    if len(parts) > 1:
                        codec = parts[1]
                        codecs.append(codec)
        return sorted(set(codecs))
    except Exception as e:
        return ["h264", "h265", "vp9", "mpeg4"]  # fallback

def get_ffmpeg_audio_only_codecs():
    try:
        result = subprocess.run(['ffmpeg', '-hide_banner', '-codecs'], capture_output=True, text=True, check=True)
        audio_only = set()
        for line in result.stdout.splitlines():
            # Look for lines with E (encode) and A (audio), but not V (video)
            if re.match(r'\s*[ D][ E][VAS][. ]', line):
                if 'E' in line[2] and 'A' in line[3] and 'V' not in line[3]:
                    parts = line.split()
                    if len(parts) > 1:
                        codec = parts[1]
                        audio_only.add(codec)
        return audio_only
    except Exception as e:
        return set()

def get_ffmpeg_muxer_codecs(format_name):
    try:
        result = subprocess.run(['ffmpeg', '-hide_banner', f'-h', f'muxer={format_name}'], capture_output=True, text=True, check=True)
        codecs = set()
        for line in result.stdout.splitlines():
            # Look for lines like: "Supported video codecs: ..."
            if 'Supported video codecs:' in line:
                codecs.update([c.strip() for c in line.split(':', 1)[1].split(',')])
        return sorted(codecs)
    except Exception as e:
        return []

# Common codec option mappings
CODEC_OPTIONS = {
    'libx264': {'crf': (0, 51, 23), 'preset': ['ultrafast','superfast','veryfast','faster','fast','medium','slow','slower','veryslow'], 'passes': [1, 2]},
    'libx265': {'crf': (0, 51, 28), 'preset': ['ultrafast','superfast','veryfast','faster','fast','medium','slow','slower','veryslow'], 'passes': [1, 2]},
    'x264': {'crf': (0, 51, 23), 'preset': ['ultrafast','superfast','veryfast','faster','fast','medium','slow','slower','veryslow'], 'passes': [1, 2]},
    'x265': {'crf': (0, 51, 28), 'preset': ['ultrafast','superfast','veryfast','faster','fast','medium','slow','slower','veryslow'], 'passes': [1, 2]},
    'vp9': {'crf': (0, 63, 32), 'passes': [1, 2]},
    'libvpx-vp9': {'crf': (0, 63, 32), 'passes': [1, 2]},
    'mpeg4': {'bitrate': True, 'passes': [1, 2]},
    'mpeg2video': {'bitrate': True, 'passes': [1, 2]},
    'libxvid': {'bitrate': True, 'passes': [1, 2]},
    # fallback for other codecs
}

class DryRunWorker(QObject):
    finished = pyqtSignal(bool, float, str)  # success, est_size_mb, error_msg
    def __init__(self, input_file, output_format, format_options):
        super().__init__()
        self.input_file = input_file
        self.output_format = output_format
        self.format_options = format_options
    def run(self):
        try:
            with tempfile.NamedTemporaryFile(suffix=f'.{self.output_format}', delete=False) as tmp:
                tmp_path = tmp.name
            dry_run_cmd = [
                'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
                '-i', self.input_file, '-t', '2',
            ]
            if 'vcodec' in self.format_options:
                dry_run_cmd += ['-c:v', self.format_options['vcodec']]
            if 'acodec' in self.format_options:
                dry_run_cmd += ['-c:a', self.format_options['acodec']]
            if 'crf' in self.format_options:
                dry_run_cmd += ['-crf', self.format_options['crf']]
            if 'b:v' in self.format_options:
                dry_run_cmd += ['-b:v', self.format_options['b:v']]
            if 'preset' in self.format_options:
                dry_run_cmd += ['-preset', self.format_options['preset']]
            if 'pass' in self.format_options:
                dry_run_cmd += ['-pass', self.format_options['pass']]
            dry_run_cmd.append(tmp_path)
            result = subprocess.run(dry_run_cmd, capture_output=True, text=True)
            if result.returncode != 0:
                os.remove(tmp_path)
                self.finished.emit(False, 0, result.stderr)
                return
            sample_size = os.path.getsize(tmp_path)
            probe = ffmpeg.probe(self.input_file)
            duration = float(probe['format']['duration'])
            est_size = int(sample_size * (duration / 2))
            est_size_mb = est_size / (1024 * 1024)
            os.remove(tmp_path)
            self.finished.emit(True, est_size_mb, "")
        except Exception as e:
            self.finished.emit(False, 0, str(e))

class TimelineWidget(QWidget):
    positionChanged = pyqtSignal(int)
    inPointChanged = pyqtSignal(int)
    outPointChanged = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(60)
        self.setMouseTracking(True)
        
        self.duration = 0
        self.position = 0
        self.in_point = 0
        self.out_point = 0
        
        self.dragging = None  # None, 'position', 'in', or 'out'
        self.drag_start_pos = None
        self.drag_start_value = None
        
        # Colors
        self.timeline_color = QColor(0, 0, 0)  # Black for main position marker
        self.in_point_color = QColor(42, 130, 218)  # Blue
        self.out_point_color = QColor(255, 68, 68)  # Red
        self.selection_color = QColor(42, 130, 218, 50)  # Semi-transparent blue
        
        # Marker sizes
        self.marker_width = 12
        self.marker_height = 20
        self.timeline_height = 8
        
        # Set fixed height
        self.setFixedHeight(60)

    def setDuration(self, duration):
        self.duration = duration
        self.out_point = duration
        self.update()

    def setPosition(self, position):
        self.position = position
        self.update()

    def setInPoint(self, in_point):
        self.in_point = in_point
        self.update()

    def setOutPoint(self, out_point):
        self.out_point = out_point
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Draw timeline background
        timeline_rect = QRect(0, (self.height() - self.timeline_height) // 2,
                            self.width(), self.timeline_height)
        painter.fillRect(timeline_rect, QColor(42, 42, 42))
        
        # Draw selection range
        if self.duration > 0:
            in_x = int(self.in_point * self.width() / self.duration)
            out_x = int(self.out_point * self.width() / self.duration)
            selection_rect = QRect(in_x, 0, out_x - in_x, self.height())
            painter.fillRect(selection_rect, self.selection_color)
        
        # Draw in/out markers
        if self.duration > 0:
            # In point marker
            in_x = int(self.in_point * self.width() / self.duration)
            in_rect = QRect(in_x - self.marker_width // 2,
                          (self.height() - self.marker_height) // 2,
                          self.marker_width, self.marker_height)
            painter.fillRect(in_rect, self.in_point_color)
            
            # Out point marker
            out_x = int(self.out_point * self.width() / self.duration)
            out_rect = QRect(out_x - self.marker_width // 2,
                           (self.height() - self.marker_height) // 2,
                           self.marker_width, self.marker_height)
            painter.fillRect(out_rect, self.out_point_color)
        
        # Draw position marker last so it's on top
        if self.duration > 0:
            pos_x = int(self.position * self.width() / self.duration)
            pos_rect = QRect(pos_x - self.marker_width // 2,
                           (self.height() - self.marker_height) // 2,
                           self.marker_width, self.marker_height)
            painter.fillRect(pos_rect, self.timeline_color)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().x()
            if self.duration > 0:
                # Check which marker is being clicked
                in_x = int(self.in_point * self.width() / self.duration)
                out_x = int(self.out_point * self.width() / self.duration)
                pos_x = int(self.position * self.width() / self.duration)
                
                # Define click areas for each marker
                in_rect = QRect(in_x - self.marker_width, 0, self.marker_width * 2, self.height())
                out_rect = QRect(out_x - self.marker_width, 0, self.marker_width * 2, self.height())
                pos_rect = QRect(pos_x - self.marker_width, 0, self.marker_width * 2, self.height())
                
                # Convert position coordinates to integers
                click_x = int(pos)
                click_y = int(event.position().y())
                
                if in_rect.contains(click_x, click_y):
                    self.dragging = 'in'
                elif out_rect.contains(click_x, click_y):
                    self.dragging = 'out'
                elif pos_rect.contains(click_x, click_y):
                    self.dragging = 'position'
                else:
                    # Click on timeline - set position
                    new_pos = int(pos * self.duration / self.width())
                    new_pos = max(self.in_point, min(self.out_point, new_pos))
                    self.position = new_pos
                    self.positionChanged.emit(self.position)
                    self.update()
                
                self.drag_start_pos = pos
                if self.dragging == 'in':
                    self.drag_start_value = self.in_point
                elif self.dragging == 'out':
                    self.drag_start_value = self.out_point
                elif self.dragging == 'position':
                    self.drag_start_value = self.position

    def mouseMoveEvent(self, event):
        if self.dragging and self.duration > 0:
            pos = event.position().x()
            delta = pos - self.drag_start_pos
            new_value = int(self.drag_start_value + (delta * self.duration / self.width()))
            
            if self.dragging == 'in':
                new_value = max(0, min(self.out_point, new_value))
                self.in_point = new_value
                self.inPointChanged.emit(new_value)
            elif self.dragging == 'out':
                new_value = max(self.in_point, min(self.duration, new_value))
                self.out_point = new_value
                self.outPointChanged.emit(new_value)
            elif self.dragging == 'position':
                new_value = max(self.in_point, min(self.out_point, new_value))
                self.position = new_value
                self.positionChanged.emit(new_value)
            
            self.update()

    def mouseReleaseEvent(self, event):
        self.dragging = None
        self.drag_start_pos = None
        self.drag_start_value = None

class VideoConverter(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Brancoder")
        self.setMinimumSize(1200, 800)
        self.current_video_path = None
        self.output_directory = None
        self.video_fps = 30  # Default fps, will be updated when video is loaded
        
        # Enable keyboard tracking
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        
        # Check FFmpeg installation
        ffmpeg_installed, ffmpeg_version = check_ffmpeg()
        if not ffmpeg_installed:
            QMessageBox.critical(self, "FFmpeg Error", 
                               f"FFmpeg is not properly installed: {ffmpeg_version}\n"
                               "Please install FFmpeg and make sure it's in your system PATH.")
            sys.exit(1)
        else:
            print(f"FFmpeg version: {ffmpeg_version}")
        
        self.settings = self.load_settings()
        self.ffmpeg_formats = get_ffmpeg_formats()
        self.ffmpeg_codecs = get_ffmpeg_video_codecs()
        self.audio_only_codecs = get_ffmpeg_audio_only_codecs()
        self.current_allowed_codecs = self.ffmpeg_codecs.copy()
        self.selected_codec = None
        
        self.setup_ui()
        self.setup_dark_theme()
        self.setup_media_player()
        self.restore_settings()

    def setup_media_player(self):
        self.media_player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.media_player.setAudioOutput(self.audio_output)
        
        # Connect error handling
        self.media_player.errorOccurred.connect(self.handle_media_error)
        
        # Connect state changed signal
        self.media_player.mediaStatusChanged.connect(self.handle_media_status)
        
        # Connect position and duration signals
        self.media_player.positionChanged.connect(self.position_changed)
        self.media_player.durationChanged.connect(self.duration_changed)
        self.media_player.playbackStateChanged.connect(self.playback_state_changed)

    def handle_media_error(self, error, error_string):
        QMessageBox.warning(self, "Media Player Error", 
                          f"Error playing video: {error_string}\n"
                          f"Error code: {error}")

    def handle_media_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.media_player.setPosition(0)
            self.media_player.play()

    def on_file_selected(self, current, previous):
        if current is None:
            self.file_info_box.clear()
            return
        self.current_video_path = current.text()
        if not os.path.exists(self.current_video_path):
            QMessageBox.warning(self, "File Error", f"Selected file does not exist: {self.current_video_path}")
            self.file_info_box.clear()
            return
        # Show file info
        try:
            probe = ffmpeg.probe(self.current_video_path)
            fmt = probe['format']
            streams = probe['streams']
            v_stream = next((s for s in streams if s['codec_type'] == 'video'), None)
            a_stream = next((s for s in streams if s['codec_type'] == 'audio'), None)
            
            # Get video FPS
            if v_stream and 'r_frame_rate' in v_stream:
                num, den = map(int, v_stream['r_frame_rate'].split('/'))
                self.video_fps = num / den if den != 0 else 30
            else:
                self.video_fps = 30  # Default if not found
            
            info = f"File: {os.path.basename(self.current_video_path)}\n"
            info += f"Duration: {float(fmt['duration']):.2f} sec\n"
            if v_stream:
                info += f"Video: {v_stream['codec_name']} {v_stream['width']}x{v_stream['height']}"
                info += f" {v_stream.get('r_frame_rate', '')}fps\n"
            if a_stream:
                info += f"Audio: {a_stream['codec_name']} {a_stream.get('channels', '')}ch\n"
            info += f"Size: {int(fmt['size'])//1024} KB\n"
            self.file_info_box.setText(info)
        except Exception as e:
            self.file_info_box.setText(f"Could not read file info: {e}")
        # Reset the media player
        self.media_player.stop()
        self.media_player.setSource(QUrl())  # Clear source
        self.media_player.setVideoOutput(self.video_widget)
        # Do not autoplay; wait for Play button
        self.status_label.setText(f"Ready: {os.path.basename(self.current_video_path)}")

    def setup_dark_theme(self):
        palette = QPalette()
        # Main window colors
        palette.setColor(QPalette.ColorRole.Window, QColor(53, 53, 53))
        palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.Base, QColor(35, 35, 35))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(45, 45, 45))
        
        # Text colors
        palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.white)
        
        # Button colors
        palette.setColor(QPalette.ColorRole.Button, QColor(65, 65, 65))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
        palette.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.white)
        
        # Other UI elements
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(45, 45, 45))
        palette.setColor(QPalette.ColorRole.ToolTipText, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
        palette.setColor(QPalette.ColorRole.BrightText, Qt.GlobalColor.red)
        
        self.setPalette(palette)
        
        # Set stylesheet for better contrast in dropdowns and buttons
        self.setStyleSheet("""
            QComboBox {
                background-color: #414141;
                color: white;
                border: 1px solid #555555;
                padding: 5px;
                min-width: 6em;
            }
            QComboBox:hover {
                border: 1px solid #666666;
            }
            QComboBox::drop-down {
                border: none;
            }
            QComboBox::down-arrow {
                image: none;
                border: none;
            }
            QComboBox QAbstractItemView {
                background-color: #414141;
                color: white;
                selection-background-color: #2a82da;
                selection-color: white;
            }
            QPushButton {
                background-color: #414141;
                color: white;
                border: 1px solid #555555;
                padding: 5px;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                border: 1px solid #666666;
            }
            QPushButton:pressed {
                background-color: #353535;
            }
            QLabel {
                color: white;
            }
            QProgressBar {
                border: 1px solid #555555;
                border-radius: 3px;
                text-align: center;
                background-color: #353535;
            }
            QProgressBar::chunk {
                background-color: #2a82da;
                width: 10px;
            }
        """)

    def setup_ui(self):
        # Main widget and layout
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QHBoxLayout(main_widget)
        layout.setSpacing(10)

        # Left panel - File selection and info
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setSpacing(5)
        open_button = QPushButton("Open Video File")
        open_button.clicked.connect(self.open_file)
        left_layout.addWidget(open_button)
        self.file_list = QListWidget()
        self.file_list.setMaximumHeight(200)
        self.file_list.currentItemChanged.connect(self.on_file_selected)
        left_layout.addWidget(self.file_list)
        self.file_info_box = QTextEdit()
        self.file_info_box.setReadOnly(True)
        self.file_info_box.setStyleSheet("background-color: #232323; color: #00ff00; font-family: 'Consolas', 'Courier New', monospace;")
        self.file_info_box.setMaximumHeight(120)
        left_layout.addWidget(self.file_info_box)
        left_layout.addStretch(1)  # Justify all widgets to the top
        layout.addWidget(left_panel)

        # Middle panel - Preview and options
        middle_panel = QWidget()
        middle_layout = QVBoxLayout(middle_panel)
        middle_layout.setSpacing(5)
        
        # Preview area with compact layout
        preview_container = QWidget()
        preview_container_layout = QVBoxLayout(preview_container)
        preview_container_layout.setSpacing(2)  # Minimal spacing between label and preview
        
        preview_label = QLabel("Input Preview")
        preview_label.setStyleSheet("color: white; font-weight: bold; margin-bottom: 0px;")
        preview_container_layout.addWidget(preview_label)
        
        self.video_widget = QVideoWidget()
        self.video_widget.setStyleSheet("background-color: #2a2a2a;")
        self.video_widget.setMinimumHeight(400)
        preview_container_layout.addWidget(self.video_widget)
        
        # Timeline controls moved directly under preview
        timeline_container = QWidget()
        timeline_layout = QVBoxLayout(timeline_container)
        timeline_layout.setSpacing(2)
        
        # Replace sliders with custom timeline widget
        self.timeline_widget = TimelineWidget()
        self.timeline_widget.positionChanged.connect(self.set_position)
        self.timeline_widget.inPointChanged.connect(self.update_in_point)
        self.timeline_widget.outPointChanged.connect(self.update_out_point)
        timeline_layout.addWidget(self.timeline_widget)
        
        # Time label
        self.time_label = QLabel("00:00:00 / 00:00:00 [In: 00:00:00 Out: 00:00:00] (I/O: Set points, ←/→: Frame step, Space: Play/Pause)")
        self.time_label.setStyleSheet("color: white;")
        timeline_layout.addWidget(self.time_label)
        
        preview_container_layout.addWidget(timeline_container)
        middle_layout.addWidget(preview_container)

        # Playback controls
        controls_widget = QWidget()
        controls_layout = QHBoxLayout(controls_widget)
        controls_layout.setSpacing(5)
        
        # Play/Pause button
        self.play_pause_button = QPushButton()
        self.play_pause_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.play_pause_button.clicked.connect(self.play_pause)
        controls_layout.addWidget(self.play_pause_button)
        
        # Stop button
        self.stop_button = QPushButton()
        self.stop_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.stop_button.clicked.connect(self.stop)
        controls_layout.addWidget(self.stop_button)
        
        # Go to In point button
        self.go_to_in_button = QPushButton("Go to In")
        self.go_to_in_button.clicked.connect(self.go_to_in_point)
        controls_layout.addWidget(self.go_to_in_button)
        
        # Go to Out point button
        self.go_to_out_button = QPushButton("Go to Out")
        self.go_to_out_button.clicked.connect(self.go_to_out_point)
        controls_layout.addWidget(self.go_to_out_button)
        
        # Reset in/out points button
        self.reset_points_button = QPushButton("Reset Points")
        self.reset_points_button.clicked.connect(self.reset_in_out_points)
        controls_layout.addWidget(self.reset_points_button)
        
        controls_layout.addStretch(1)
        middle_layout.addWidget(controls_widget)

        # Format options
        options_group = QWidget()
        options_layout = QVBoxLayout(options_group)
        options_layout.setSpacing(5)
        
        # Output format selection
        format_layout = QHBoxLayout()
        format_label = QLabel("Output Format:")
        self.format_combo = QComboBox()
        self.format_combo.addItems(self.ffmpeg_formats)
        self.format_combo.currentTextChanged.connect(self.update_codec_list_for_format)
        format_layout.addWidget(format_label)
        format_layout.addWidget(self.format_combo)
        options_layout.addLayout(format_layout)

        # Codec selection
        codec_layout = QHBoxLayout()
        codec_label = QLabel("Video Codec:")
        self.codec_combo = QComboBox()
        self.populate_codec_combo(self.ffmpeg_codecs)
        self.codec_combo.currentTextChanged.connect(self.update_advanced_options_visibility)
        codec_layout.addWidget(codec_label)
        codec_layout.addWidget(self.codec_combo)
        options_layout.addLayout(codec_layout)

        # Quality selection
        quality_layout = QHBoxLayout()
        quality_label = QLabel("Quality:")
        self.quality_combo = QComboBox()
        self.quality_combo.addItems(["High", "Medium", "Low"])
        quality_layout.addWidget(quality_label)
        quality_layout.addWidget(self.quality_combo)
        options_layout.addLayout(quality_layout)

        middle_layout.addWidget(options_group)
        # Advanced Options Toggle
        self.advanced_toggle_btn = QPushButton("Show Advanced Options")
        self.advanced_toggle_btn.setCheckable(True)
        self.advanced_toggle_btn.setChecked(False)
        self.advanced_toggle_btn.clicked.connect(self.toggle_advanced_options)
        middle_layout.addWidget(self.advanced_toggle_btn)
        # Advanced Options Section (collapsible)
        self.advanced_group = QWidget()
        advanced_layout = QVBoxLayout(self.advanced_group)
        advanced_layout.setSpacing(5)
        # CRF
        self.crf_layout = QHBoxLayout()
        self.crf_label = QLabel("CRF:")
        self.crf_slider = QSlider(Qt.Orientation.Horizontal)
        self.crf_slider.setMinimum(0)
        self.crf_slider.setMaximum(51)
        self.crf_slider.setValue(23)
        self.crf_value_label = QLabel("23")
        self.crf_slider.valueChanged.connect(lambda v: self.crf_value_label.setText(str(v)))
        self.crf_layout.addWidget(self.crf_label)
        self.crf_layout.addWidget(self.crf_slider)
        self.crf_layout.addWidget(self.crf_value_label)
        advanced_layout.addLayout(self.crf_layout)
        # Bitrate
        self.bitrate_layout = QHBoxLayout()
        self.bitrate_label = QLabel("Bitrate (kbps):")
        self.bitrate_input = QLineEdit()
        self.bitrate_input.setPlaceholderText("e.g. 2000")
        self.bitrate_layout.addWidget(self.bitrate_label)
        self.bitrate_layout.addWidget(self.bitrate_input)
        advanced_layout.addLayout(self.bitrate_layout)
        # Preset
        self.preset_layout = QHBoxLayout()
        self.preset_label = QLabel("Preset:")
        self.preset_combo = QComboBox()
        self.preset_layout.addWidget(self.preset_label)
        self.preset_layout.addWidget(self.preset_combo)
        advanced_layout.addLayout(self.preset_layout)
        # Passes
        self.passes_layout = QHBoxLayout()
        self.passes_label = QLabel("Passes:")
        self.passes_spin = QSpinBox()
        self.passes_spin.setMinimum(1)
        self.passes_spin.setMaximum(2)
        self.passes_spin.setValue(1)
        self.passes_layout.addWidget(self.passes_label)
        self.passes_layout.addWidget(self.passes_spin)
        advanced_layout.addLayout(self.passes_layout)
        # Move Reset Advanced Options Button inside advanced options group
        self.reset_adv_btn = QPushButton("Reset Advanced Options")
        self.reset_adv_btn.clicked.connect(self.reset_advanced_options)
        advanced_layout.addWidget(self.reset_adv_btn)
        middle_layout.addWidget(self.advanced_group)
        self.advanced_group.setVisible(False)
        layout.addWidget(middle_panel)

        # Right panel - Progress, output, save, and render
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setSpacing(5)
        output_container = QWidget()
        output_container_layout = QVBoxLayout(output_container)
        output_container_layout.setSpacing(2)
        output_preview_label = QLabel("Output Preview")
        output_preview_label.setStyleSheet("color: white; font-weight: bold; margin-bottom: 0px;")
        output_container_layout.addWidget(output_preview_label)
        self.output_video_widget = AspectRatioVideoWidget((16, 9))
        self.output_video_widget.setStyleSheet("background-color: #2a2a2a;")
        self.output_video_widget.setFixedWidth(400)
        output_container_layout.addWidget(self.output_video_widget)
        right_layout.addWidget(output_container)
        # Output file name input
        file_name_layout = QHBoxLayout()
        file_name_label = QLabel("Output File Name:")
        self.file_name_input = QLineEdit()
        self.file_name_input.setPlaceholderText("Enter output file name (no extension)")
        file_name_layout.addWidget(file_name_label)
        file_name_layout.addWidget(self.file_name_input)
        right_layout.addLayout(file_name_layout)
        # Save location and render controls
        save_render_layout = QHBoxLayout()
        save_button = QPushButton("Choose Save Location")
        save_button.clicked.connect(self.choose_save_location)
        self.save_location_label = QLabel("No save location selected")
        save_render_layout.addWidget(save_button)
        save_render_layout.addWidget(self.save_location_label)
        right_layout.addLayout(save_render_layout)
        render_button = QPushButton("Render")
        render_button.clicked.connect(self.convert_video)
        render_button.setStyleSheet("""
            QPushButton {
                background-color: #2a82da;
                color: white;
                padding: 8px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1e6fc7;
            }
        """)
        right_layout.addWidget(render_button)
        # Conversion details
        progress_info_label = QLabel("Conversion Details")
        progress_info_label.setStyleSheet("color: white; font-weight: bold;")
        self.progress_info_text = QTextEdit()
        self.progress_info_text.setReadOnly(True)
        self.progress_info_text.setStyleSheet("""
            QTextEdit {
                background-color: #2a2a2a;
                color: #00ff00;
                font-family: 'Consolas', 'Courier New', monospace;
                border: 1px solid #555555;
            }
        """)
        self.progress_info_text.setMaximumHeight(100)
        right_layout.addWidget(progress_info_label)
        right_layout.addWidget(self.progress_info_text)
        
        # Progress section
        progress_label = QLabel("Conversion Progress")
        progress_label.setStyleSheet("color: white; font-weight: bold;")
        self.progress_bar = QProgressBar()
        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("color: white;")
        
        right_layout.addWidget(progress_label)
        right_layout.addWidget(self.progress_bar)
        right_layout.addWidget(self.status_label)
        
        layout.addWidget(right_panel)

        # Set layout proportions
        layout.setStretch(0, 1)  # Left panel
        layout.setStretch(1, 2)  # Middle panel
        layout.setStretch(2, 1)  # Right panel

    def load_settings(self):
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, 'r') as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def save_settings(self):
        s = {
            'format': self.format_combo.currentText(),
            'codec': self.codec_combo.currentText(),
            'quality': self.quality_combo.currentText(),
            'output_file_name': self.file_name_input.text(),
            'save_location': self.output_directory,
            'last_open_dir': getattr(self, 'last_open_dir', ''),
            'crf': self.crf_slider.value(),
            'bitrate': self.bitrate_input.text(),
            'preset': self.preset_combo.currentText() if self.preset_combo.isVisible() else '',
            'passes': self.passes_spin.value(),
        }
        try:
            with open(SETTINGS_FILE, 'w') as f:
                json.dump(s, f)
        except Exception:
            pass

    def restore_settings(self):
        s = self.settings
        if not s:
            return
        if 'format' in s and s['format'] in self.ffmpeg_formats:
            self.format_combo.setCurrentText(s['format'])
        if 'codec' in s:
            self.codec_combo.setCurrentText(s['codec'])
        if 'quality' in s:
            self.quality_combo.setCurrentText(s['quality'])
        if 'output_file_name' in s:
            self.file_name_input.setText(s['output_file_name'])
        if 'save_location' in s and s['save_location']:
            self.output_directory = s['save_location']
            self.save_location_label.setText(f"Save location: {os.path.basename(self.output_directory)}")
        if 'last_open_dir' in s:
            self.last_open_dir = s['last_open_dir']
        if 'crf' in s:
            self.crf_slider.setValue(s['crf'])
        if 'bitrate' in s:
            self.bitrate_input.setText(s['bitrate'])
        if 'preset' in s and s['preset']:
            self.preset_combo.setCurrentText(s['preset'])
        if 'passes' in s:
            self.passes_spin.setValue(s['passes'])

    def open_file(self):
        dir_ = getattr(self, 'last_open_dir', '')
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "Open Video File",
            dir_,
            "Video Files (*.mp4 *.avi *.mkv *.mov *.webm *.flv *.mpg *.mpeg *.ogg *.wmv *.m4v *.3gp *.ts *.asf *.vob *.f4v *.m2ts)"
        )
        if file_name:
            self.file_list.addItem(file_name)
            self.last_open_dir = os.path.dirname(file_name)
            self.save_settings()

    def convert_video(self):
        if self.file_list.count() == 0:
            self.status_label.setText("No file selected")
            return
        if not self.output_directory:
            self.status_label.setText("Please select a save location first")
            return
        output_name = self.file_name_input.text().strip()
        if not output_name:
            self.status_label.setText("Please enter an output file name")
            return
        input_file = self.file_list.currentItem().text()
        output_format = self.format_combo.currentText()
        output_filename = output_name + "." + output_format
        output_file = os.path.join(self.output_directory, output_filename)
        
        # Get in/out points
        in_point = self.timeline_widget.in_point / 1000  # Convert to seconds
        out_point = self.timeline_widget.out_point / 1000  # Convert to seconds
        duration = out_point - in_point
        
        # Gather advanced options
        codec = self.selected_codec or self.codec_combo.currentText().split()[0]
        opts = CODEC_OPTIONS.get(codec, {})
        format_options = {
            'vcodec': codec,
            'acodec': 'aac',
            'ss': str(in_point),  # Start time
            't': str(duration)    # Duration
        }
        if 'crf' in opts and self.advanced_group.isVisible():
            format_options['crf'] = str(self.crf_slider.value())
        if opts.get('bitrate', False) and self.advanced_group.isVisible():
            br = self.bitrate_input.text().strip()
            if br:
                format_options['b:v'] = f'{br}k'
        if 'preset' in opts and self.advanced_group.isVisible():
            format_options['preset'] = self.preset_combo.currentText()
        if 'passes' in opts and self.advanced_group.isVisible():
            format_options['pass'] = str(self.passes_spin.value())
        self.progress_info_text.clear()
        self.progress_bar.setValue(0)
        self.status_label.setText("Checking settings...")
        # Save all needed state for use after dry run
        self._pending_conversion = {
            'input_file': input_file,
            'output_file': output_file,
            'format_options': format_options
        }
        # --- DRY RUN in background thread ---
        self.dry_run_thread = QThread()
        self.dry_run_worker = DryRunWorker(input_file, output_format, format_options)
        self.dry_run_worker.moveToThread(self.dry_run_thread)
        self.dry_run_thread.started.connect(self.dry_run_worker.run)
        self.dry_run_worker.finished.connect(self.on_dry_run_finished)
        self.dry_run_worker.finished.connect(self.dry_run_thread.quit)
        self.dry_run_worker.finished.connect(self.dry_run_worker.deleteLater)
        self.dry_run_thread.finished.connect(self.dry_run_thread.deleteLater)
        self.dry_run_thread.start()

    def on_dry_run_finished(self, success, est_size_mb, error_msg):
        if not success:
            QMessageBox.critical(self, "Codec/Format Error", f"FFmpeg error: {error_msg}\n\nThis codec/format combination may be incompatible.")
            self.status_label.setText("Dry run failed. Try a different codec or format.")
            self._pending_conversion = None
            return
        # Always show a valid file size estimate
        est_size_str = f"Estimated output file size: {est_size_mb:.2f} MB" if est_size_mb > 0 else "Estimated output file size: Unknown"
        msg_box = QMessageBox(self)
        msg_box.setIcon(QMessageBox.Icon.Question)
        msg_box.setWindowTitle("Estimated File Size")
        msg_box.setText(f"{est_size_str}\nProceed with conversion?")
        msg_box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg_box.setStyleSheet(
            """
            QMessageBox {
                background-color: #232323;
                color: #ffffff;
            }
            QLabel {
                color: #ffffff;
            }
            QPushButton {
                color: #ffffff;
                background-color: #414141;
                border: 1px solid #555555;
                padding: 5px;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                border: 1px solid #666666;
            }
            """
        )
        reply = msg_box.exec()
        if reply != QMessageBox.StandardButton.Yes:
            self.status_label.setText("Conversion cancelled.")
            self._pending_conversion = None
            return
        # ... start the real conversion as before ...
        self.status_label.setText("Converting...")
        if self._pending_conversion:
            input_file = self._pending_conversion['input_file']
            output_file = self._pending_conversion['output_file']
            format_options = self._pending_conversion['format_options']
            self.converter_thread = VideoConverterThread(input_file, output_file, format_options)
            self.converter_thread.progress.connect(self.update_progress)
            self.converter_thread.finished.connect(self.conversion_finished)
            self.converter_thread.error.connect(self.conversion_error)
            self.converter_thread.progress_info.connect(self.update_progress_info)
            self.converter_thread.start()
        self._pending_conversion = None

    def update_progress(self, value):
        self.progress_bar.setValue(value)

    def conversion_finished(self):
        self.status_label.setText("Conversion completed!")
        self.progress_bar.setValue(100)
        # Play the converted video in the output preview
        output_file = os.path.join(self.output_directory, 
                                 os.path.splitext(os.path.basename(self.current_video_path))[0] + 
                                 "_converted." + self.format_combo.currentText())
        # Ensure output preview is visible and loads the video
        self.output_preview_player = QMediaPlayer()
        self.output_preview_audio = QAudioOutput()
        self.output_preview_player.setAudioOutput(self.output_preview_audio)
        self.output_preview_player.setSource(QUrl.fromLocalFile(output_file))
        self.output_preview_player.setVideoOutput(self.output_video_widget)
        # Do not autoplay; wait for user to add controls if desired
        # Optionally, you can add play controls for output preview as well

    def conversion_error(self, error_message):
        self.status_label.setText(f"Error: {error_message}")

    def choose_save_location(self):
        directory = QFileDialog.getExistingDirectory(
            self,
            "Choose Save Location",
            self.output_directory or '',
            QFileDialog.Option.ShowDirsOnly
        )
        if directory:
            self.output_directory = directory
            self.save_location_label.setText(f"Save location: {os.path.basename(directory)}")
            self.save_settings()

    def play_pause(self):
        if not self.current_video_path:
            return
        if self.media_player.source().isEmpty():
            self.media_player.setSource(QUrl.fromLocalFile(self.current_video_path))
            self.media_player.setVideoOutput(self.video_widget)
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
        else:
            self.media_player.play()

    def stop(self):
        self.media_player.stop()

    def set_position(self, position):
        self.timeline_widget.setPosition(position)
        self.media_player.setPosition(position)
        self.update_time_label()

    def position_changed(self, position):
        self.timeline_widget.setPosition(position)
        self.update_time_label()

    def duration_changed(self, duration):
        self.timeline_widget.setDuration(duration)
        self.update_time_label()

    def update_in_point(self, position):
        self.timeline_widget.setInPoint(position)
        self.update_time_label()

    def update_out_point(self, position):
        self.timeline_widget.setOutPoint(position)
        self.update_time_label()

    def reset_in_out_points(self):
        self.timeline_widget.setInPoint(0)
        self.timeline_widget.setOutPoint(self.media_player.duration())
        self.update_time_label()

    def update_time_label(self):
        position = self.media_player.position()
        duration = self.media_player.duration()
        in_point = self.timeline_widget.in_point
        out_point = self.timeline_widget.out_point
        
        def format_time(ms):
            total_seconds = ms // 1000
            m = total_seconds // 60
            s = total_seconds % 60
            # Calculate frames based on video FPS
            frames = int((ms % 1000) * self.video_fps / 1000)
            return f"{m:02d}:{s:02d}:{frames:02d}"
        
        self.time_label.setText(
            f"{format_time(position)} / {format_time(duration)} "
            f"[In: {format_time(in_point)} Out: {format_time(out_point)}]"
        )

    def playback_state_changed(self, state):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_pause_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        else:
            self.play_pause_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))

    def update_progress_info(self, info):
        self.progress_info_text.append(info)
        self.progress_info_text.verticalScrollBar().setValue(
            self.progress_info_text.verticalScrollBar().maximum()
        )

    def populate_codec_combo(self, codec_list):
        self.codec_combo.clear()
        for codec in codec_list:
            if codec in self.audio_only_codecs:
                self.codec_combo.addItem(f"{codec} (audio only)")
            else:
                self.codec_combo.addItem(codec)

    def update_codec_list_for_format(self, format_name):
        allowed_codecs = get_ffmpeg_muxer_codecs(format_name)
        if allowed_codecs:
            self.populate_codec_combo(allowed_codecs)
            self.current_allowed_codecs = allowed_codecs
        else:
            self.populate_codec_combo(self.ffmpeg_codecs)
            self.current_allowed_codecs = self.ffmpeg_codecs

    def toggle_advanced_options(self):
        visible = self.advanced_toggle_btn.isChecked()
        self.advanced_group.setVisible(visible)
        self.advanced_toggle_btn.setText("Hide Advanced Options" if visible else "Show Advanced Options")

    def update_advanced_options_visibility(self, codec_name):
        if not codec_name or not codec_name.strip():
            self.advanced_group.setVisible(False)
            self.advanced_toggle_btn.setVisible(True)  # Always show the toggle button
            return
        # Remove (audio only) marker if present
        codec = codec_name.split()[0]
        self.selected_codec = codec
        opts = CODEC_OPTIONS.get(codec, {})
        # CRF
        if 'crf' in opts:
            self.crf_layout.parentWidget().setVisible(True)
            min_crf, max_crf, default_crf = opts['crf']
            self.crf_slider.setMinimum(min_crf)
            self.crf_slider.setMaximum(max_crf)
            self.crf_slider.setValue(default_crf)
            self.crf_label.setVisible(True)
            self.crf_slider.setVisible(True)
            self.crf_value_label.setVisible(True)
        else:
            self.crf_layout.parentWidget().setVisible(False)
        # Bitrate
        if opts.get('bitrate', False):
            self.bitrate_layout.parentWidget().setVisible(True)
            self.bitrate_label.setVisible(True)
            self.bitrate_input.setVisible(True)
        else:
            self.bitrate_layout.parentWidget().setVisible(False)
        # Preset
        if 'preset' in opts:
            self.preset_layout.parentWidget().setVisible(True)
            self.preset_combo.clear()
            self.preset_combo.addItems(opts['preset'])
            self.preset_label.setVisible(True)
            self.preset_combo.setVisible(True)
        else:
            self.preset_layout.parentWidget().setVisible(False)
        # Passes
        if 'passes' in opts:
            self.passes_layout.parentWidget().setVisible(True)
            self.passes_spin.setMinimum(min(opts['passes']))
            self.passes_spin.setMaximum(max(opts['passes']))
            self.passes_spin.setValue(min(opts['passes']))
            self.passes_label.setVisible(True)
            self.passes_spin.setVisible(True)
        else:
            self.passes_layout.parentWidget().setVisible(False)
        # Show advanced group if any option is visible and toggle is checked
        show_any = any([
            'crf' in opts, opts.get('bitrate', False), 'preset' in opts, 'passes' in opts
        ])
        self.advanced_group.setVisible(show_any and self.advanced_toggle_btn.isChecked())
        self.advanced_toggle_btn.setVisible(True)  # Always show the toggle button

    def reset_advanced_options(self):
        codec_name = self.codec_combo.currentText()
        if not codec_name or not codec_name.strip():
            return
        codec = codec_name.split()[0]
        opts = CODEC_OPTIONS.get(codec, {})
        if 'crf' in opts:
            min_crf, max_crf, default_crf = opts['crf']
            self.crf_slider.setValue(default_crf)
        else:
            self.crf_slider.setValue(23)
        if opts.get('bitrate', False):
            self.bitrate_input.clear()
        else:
            self.bitrate_input.clear()
        if 'preset' in opts:
            self.preset_combo.setCurrentIndex(0)
        else:
            self.preset_combo.setCurrentIndex(0)
        if 'passes' in opts:
            self.passes_spin.setValue(min(opts['passes']))
        else:
            self.passes_spin.setValue(1)
        # Force UI update
        self.update_advanced_options_visibility(codec_name)

    def closeEvent(self, event):
        self.save_settings()
        super().closeEvent(event)

    def keyPressEvent(self, event):
        if not self.current_video_path:
            return
            
        if event.key() == Qt.Key.Key_I:
            # Set in point to current position
            current_pos = self.media_player.position()
            if current_pos <= self.timeline_widget.out_point:
                self.timeline_widget.setInPoint(current_pos)
                self.update_time_label()
                # Visual feedback
                self.status_label.setText("In point set")
                QTimer.singleShot(1000, lambda: self.status_label.setText("Ready"))
                
        elif event.key() == Qt.Key.Key_O:
            # Set out point to current position
            current_pos = self.media_player.position()
            if current_pos >= self.timeline_widget.in_point:
                self.timeline_widget.setOutPoint(current_pos)
                self.update_time_label()
                # Visual feedback
                self.status_label.setText("Out point set")
                QTimer.singleShot(1000, lambda: self.status_label.setText("Ready"))
        
        elif event.key() == Qt.Key.Key_Left:
            # Move one frame backward
            current_pos = self.media_player.position()
            frame_duration = int(1000 / self.video_fps)  # Convert to milliseconds
            new_pos = max(self.timeline_widget.in_point, current_pos - frame_duration)
            self.media_player.setPosition(new_pos)
            self.timeline_widget.setPosition(new_pos)
            self.update_time_label()
            
        elif event.key() == Qt.Key.Key_Right:
            # Move one frame forward
            current_pos = self.media_player.position()
            frame_duration = int(1000 / self.video_fps)  # Convert to milliseconds
            new_pos = min(self.timeline_widget.out_point, current_pos + frame_duration)
            self.media_player.setPosition(new_pos)
            self.timeline_widget.setPosition(new_pos)
            self.update_time_label()
        
        elif event.key() == Qt.Key.Key_Space:
            # Toggle play/pause
            self.play_pause()
                
        super().keyPressEvent(event)

    def go_to_in_point(self):
        if self.current_video_path:
            self.media_player.setPosition(self.timeline_widget.in_point)
            self.timeline_widget.setPosition(self.timeline_widget.in_point)
            self.update_time_label()

    def go_to_out_point(self):
        if self.current_video_path:
            self.media_player.setPosition(self.timeline_widget.out_point)
            self.timeline_widget.setPosition(self.timeline_widget.out_point)
            self.update_time_label()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = VideoConverter()
    window.show()
    sys.exit(app.exec()) 