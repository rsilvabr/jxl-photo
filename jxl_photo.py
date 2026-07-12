#!/usr/bin/env python3
"""
jxl_photo_v2.py - Interactive wrapper with Auto Mode
Adds [A] Auto Mode that analyzes folder structure and recommends best options.
Based on jxl_photo.py - all original features preserved.
"""

import argparse
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Initialize wrapper logger early so manifest/path validation code can log safely.
logger = logging.getLogger("jxl_photo")

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.box import SIMPLE as BOX_SIMPLE
    from rich.prompt import Prompt, IntPrompt, Confirm
    from rich.progress import Progress, TextColumn, BarColumn, TaskProgressColumn
    RICH_AVAILABLE = True
    console = Console(force_terminal=True)
except ImportError:
    RICH_AVAILABLE = False
    console = None

try:
    from prompt_toolkit import prompt
    from prompt_toolkit.completion import PathCompleter
    PROMPT_TOOLKIT_AVAILABLE = True
except ImportError:
    PROMPT_TOOLKIT_AVAILABLE = False


# Backport of Path.is_relative_to for Python < 3.9
def _is_relative_to(path: Path, anchor: Path) -> bool:
    """Return True if path is relative to anchor (Python < 3.9 compatible)."""
    try:
        path.relative_to(anchor)
        return True
    except ValueError:
        return False


SCRIPT_DIR = Path(__file__).parent.resolve()

@dataclass
class ToolConfig:
    """Configuration dataclass for JXL tools"""
    cjxl_path: Optional[str] = None
    djxl_path: Optional[str] = None
    exiftool_path: Optional[str] = None
    magick_path: Optional[str] = None

    staging_dir: Optional[str] = None
    default_workers: int = 4
    default_quality: int = 95
    default_effort: int = 7
    confirm_delete: bool = True
    export_marker: str = "_EXPORT"

    last_input_dir: Optional[str] = None
    last_output_mode: Optional[str] = None
    last_workers: Optional[int] = None
    last_staging: Optional[str] = None
    last_effort: Optional[int] = None
    last_quality: Optional[int] = None
    last_distance: Optional[float] = None
    last_origin_format: Optional[str] = None
    last_dest_format: Optional[str] = None
    last_conversion_type: Optional[str] = None
    last_d50_patch: Optional[str] = None
    last_encode_tag: Optional[str] = None
    last_jpeg_thumbnail: Optional[bool] = None  # True = embed, False = don't embed, None = ask each time
    last_multipage_mode: Optional[str] = None
    last_thumbnail_mode: Optional[str] = None
    last_thumbnail_suffix: Optional[str] = None
    last_thumbnail_handling: Optional[str] = None
    last_no_reconstruct_multipage: Optional[bool] = None
    last_depth_policy: Optional[str] = None
    last_advanced_options: Optional[Dict] = None
    last_use_ram: Optional[bool] = None
    last_compression: Optional[str] = None
    last_bit_depth: Optional[int] = None
    last_add_preview: Optional[bool] = None
    last_mode_config: Optional[Dict] = None

    dependencies_checked: bool = False
    available_features: Dict[str, bool] = field(default_factory=dict)


class ConfigManager:
    """Manages persistent configuration for jxl_tools"""

    def __init__(self):
        self.config_path = self._get_config_path()
        self.config = ToolConfig()
        self._load_config()

    def _get_config_path(self) -> Path:
        script_config = SCRIPT_DIR / ".jxl_tools_config.json"
        if script_config.exists():
            return script_config
        if platform.system() == "Windows":
            config_dir = Path(os.environ.get("USERPROFILE", Path.home()))
        else:
            config_dir = Path.home()
        return config_dir / ".jxl_tools_config.json"

    def _load_config(self) -> None:
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    valid_fields = {k: v for k, v in data.items()
                                  if k in ToolConfig.__dataclass_fields__}
                    self.config = ToolConfig(**valid_fields)
            except Exception as e:
                print(f"Warning: Corrupted config file: {e}. Using defaults.")
                self.config = ToolConfig()

    def save_config(self) -> None:
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(asdict(self.config), f, indent=2, ensure_ascii=False)
        except IOError as e:
            print(f"Error: Failed to save config: {e}")

    def save_last_session(self, input_dir: Optional[str] = None, output_mode: Optional[str] = None,
                          workers: Optional[int] = None, staging: Optional[str] = None,
                          effort: Optional[int] = None, quality: Optional[int] = None,
                          distance: Optional[float] = None,
                          origin_format: Optional[str] = None,
                          dest_format: Optional[str] = None,
                          conversion_type: Optional[str] = None,
                          d50_patch: Optional[str] = None,
                          encode_tag: Optional[str] = None,
                          jpeg_thumbnail: Optional[bool] = None,
                          multipage_mode: Optional[str] = None,
                          thumbnail_mode: Optional[str] = None,
                          thumbnail_suffix: Optional[str] = None,
                          thumbnail_handling: Optional[str] = None,
                          no_reconstruct_multipage: Optional[bool] = None,
                          depth_policy: Optional[str] = None,
                          advanced_options: Optional[Dict] = None,
                          use_ram: Optional[bool] = None,
                          compression: Optional[str] = None,
                          bit_depth: Optional[int] = None,
                          add_preview: Optional[bool] = None,
                          mode_config: Optional[Dict] = None) -> None:
        if input_dir is not None:
            self.config.last_input_dir = input_dir
        if output_mode is not None:
            self.config.last_output_mode = output_mode
        if workers is not None:
            self.config.last_workers = workers
        if staging is not None:
            self.config.last_staging = staging
        if effort is not None:
            self.config.last_effort = effort
        if quality is not None:
            self.config.last_quality = quality
        if distance is not None:
            self.config.last_distance = distance
        if origin_format is not None:
            self.config.last_origin_format = origin_format
        if dest_format is not None:
            self.config.last_dest_format = dest_format
        if conversion_type is not None:
            self.config.last_conversion_type = conversion_type
        if d50_patch is not None:
            self.config.last_d50_patch = d50_patch
        if encode_tag is not None:
            self.config.last_encode_tag = encode_tag
        if jpeg_thumbnail is not None:
            self.config.last_jpeg_thumbnail = jpeg_thumbnail
        if multipage_mode is not None:
            self.config.last_multipage_mode = multipage_mode
        if thumbnail_mode is not None:
            self.config.last_thumbnail_mode = thumbnail_mode
        if thumbnail_suffix is not None:
            self.config.last_thumbnail_suffix = thumbnail_suffix
        if thumbnail_handling is not None:
            self.config.last_thumbnail_handling = thumbnail_handling
        if no_reconstruct_multipage is not None:
            self.config.last_no_reconstruct_multipage = no_reconstruct_multipage
        if depth_policy is not None:
            self.config.last_depth_policy = depth_policy
        if advanced_options is not None:
            # Persist a copy of the advanced options dict for repeat workflow.
            self.config.last_advanced_options = dict(advanced_options)
        if use_ram is not None:
            self.config.last_use_ram = use_ram
        if compression is not None:
            self.config.last_compression = compression
        if bit_depth is not None:
            self.config.last_bit_depth = bit_depth
        if add_preview is not None:
            self.config.last_add_preview = add_preview
        if mode_config is not None:
            self.config.last_mode_config = dict(mode_config)
        self.save_config()

    def update_tool_paths(self, tools: Dict[str, Optional[str]]) -> None:
        self.config.cjxl_path = tools.get('cjxl')
        self.config.djxl_path = tools.get('djxl')
        self.config.exiftool_path = tools.get('exiftool')
        self.config.magick_path = tools.get('magick')
        self.config.dependencies_checked = True
        self.save_config()

    def get_available_features(self) -> Dict[str, bool]:
        return {
            'cjxl': self.config.cjxl_path is not None,
            'djxl': self.config.djxl_path is not None,
            'exiftool': self.config.exiftool_path is not None,
            'magick': self.config.magick_path is not None,
            'icc_profiles': self.config.magick_path is not None,
            'tiff': self._check_tiff_support(),
        }

    def _check_tiff_support(self) -> bool:
        try:
            import tifffile
            import numpy
            return True
        except ImportError:
            return False

    def _check_imagecodecs(self) -> bool:
        """Check if imagecodecs is available for compressed TIFF support."""
        try:
            import imagecodecs
            return True
        except ImportError:
            return False


class DependencyChecker:
    def __init__(self, config_manager: ConfigManager):
        self.config = config_manager

    def _check_pillow(self) -> bool:
        try:
            import PIL
            return True
        except ImportError:
            return False

    def _check_rich(self) -> bool:
        try:
            import rich
            import rich.console
            import rich.table
            import rich.panel
            import rich.prompt
            return True
        except ImportError:
            return False

    def check_dependencies(self, force: bool = False) -> Dict[str, bool]:
        tools_to_check = {
            'cjxl': ['cjxl', '--version'],
            'djxl': ['djxl', '--version'],
            'exiftool': ['exiftool', '-ver'],
            'magick': ['magick', '--version'],
        }

        detected_paths = {}
        status = {}

        for tool_name, test_cmd in tools_to_check.items():
            path = self._detect_tool(test_cmd[0])
            if path and self._test_tool_execution(path, test_cmd[1:]):
                detected_paths[tool_name] = path
                status[tool_name] = True
            else:
                detected_paths[tool_name] = None
                status[tool_name] = False

        self.config.update_tool_paths(detected_paths)

        status['tifffile'] = self.config._check_tiff_support()
        status['numpy'] = status['tifffile']
        status['imagecodecs'] = self.config._check_imagecodecs()
        status['pillow'] = self._check_pillow()
        status['rich'] = self._check_rich()
        status['icc_profiles'] = status.get('magick', False)

        self.config.config.available_features = status
        self.config.save_config()

        return status

    def _detect_tool(self, cmd: str) -> Optional[str]:
        variations = [cmd]
        if platform.system() == "Windows":
            variations.extend([f"{cmd}.exe", f"{cmd}.cmd", f"{cmd}.bat"])
            if cmd == "exiftool":
                variations.extend(["exiftool(-k)", "exiftool(-k).exe",
                                   "exiftool-k", "exiftool-k.exe"])

        for variant in variations:
            path = shutil.which(variant)
            if path:
                return path

        return None

    def _test_tool_execution(self, path: str, args: List[str]) -> bool:
        try:
            result = subprocess.run([path] + args, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace",
                                  timeout=10, shell=False)
            return result.returncode == 0
        except Exception:
            return False

    def format_status_line(self, status: Dict[str, bool]) -> str:
        icons = {
            'cjxl': '✓' if status.get('cjxl') else '✗',
            'djxl': '✓' if status.get('djxl') else '✗',
            'exiftool': '✓' if status.get('exiftool') else '✗',
            'magick': '✓' if status.get('magick') else '⚠',
            'tifffile': '✓' if status.get('tifffile') else '✗',
            'imagecodecs': '✓' if status.get('imagecodecs') else '⚠',
            'pillow': '✓' if status.get('pillow') else '✗',
            'rich': '✓' if status.get('rich') else '✗',
        }

        parts = [
            f"[{icons['cjxl']}] cjxl/djxl",
            f"[{icons['exiftool']}] exiftool",
            f"[{icons['magick']}] magick",
            f"[{icons['tifffile']}] tifffile",
            f"[{icons['imagecodecs']}] imagecodecs",
            f"[{icons['pillow']}] pillow",
            f"[{icons['rich']}] rich",
        ]

        if not status.get('magick'):
            parts[2] += " (ICC off)"
        if not status.get('tifffile'):
            parts[3] += " (TIFF off)"
        if not status.get('imagecodecs'):
            parts[4] += " (LZW/ZIP TIFFs need: pip install imagecodecs)"
        if not status.get('pillow'):
            parts[5] += " (JPG previews off)"
        if not status.get('rich'):
            parts[6] += " (basic UI)"

        return " | ".join(parts)


class FolderAnalyzer:
    """Analyzes folder structure to recommend best mode."""

    def __init__(self, root_path: Path, origin: str, dest: str, export_marker: str = "_EXPORT"):
        self.root = root_path
        self.origin = origin
        self.dest = dest
        self.export_marker = export_marker

    def analyze(self) -> Dict[str, Any]:
        """Scan folder and return analysis results."""
        result = {
            'folder_count': 0,
            'total_files': 0,
            'has_export_marker': False,
            'export_marker_paths': [],
            'has_subfolders': False,
            'subfolders': [],
            'has_flat_structure': False,
            'has_recursive_structure': False,
            'file_distribution': {},
            'recommended_mode': None,
            'confidence': 'low',
            'reasoning': [],
        }

        if not self.root.exists() or not self.root.is_dir():
            return result

        # Collect all items
        all_items = list(self.root.rglob('*'))
        all_folders = [p for p in all_items if p.is_dir() and not self._is_hidden(p)]
        all_files = [p for p in all_items if p.is_file() and not self._is_hidden(p)]

        # Filter by extension
        origin_exts = self._get_extensions(self.origin)
        dest_exts = self._get_extensions(self.dest)

        origin_files = [f for f in all_files if f.suffix.lower() in origin_exts]
        dest_files = [f for f in all_files if f.suffix.lower() in dest_exts]

        result['total_files'] = len(origin_files)
        result['folder_count'] = len(all_folders)

        # Check for export markers (case-insensitive, prefix or suffix only)
        marker_lower = self.export_marker.lower()
        for folder in all_folders:
            name_lower = folder.name.lower()
            if name_lower.startswith(marker_lower) or name_lower.endswith(marker_lower):
                result['has_export_marker'] = True
                result['export_marker_paths'].append(str(folder))

        # Analyze structure
        immediate_subfolders = [d for d in self.root.iterdir() if d.is_dir() and not self._is_hidden(d)]

        if len(immediate_subfolders) > 0:
            result['has_subfolders'] = True
            result['subfolders'] = [str(d) for d in immediate_subfolders[:5]]

            # Check if it's a flat structure (origin files in root)
            if any(f.parent == self.root for f in origin_files):
                result['has_flat_structure'] = True

            # Check if subfolders contain origin files
            subfolder_with_files = set(f.parent for f in origin_files if f.parent != self.root)
            if len(subfolder_with_files) > 1:
                result['has_recursive_structure'] = True

        # Count files per folder
        folder_counts = {}
        for f in origin_files:
            parent = str(f.parent.relative_to(self.root))
            folder_counts[parent] = folder_counts.get(parent, 0) + 1
        result['file_distribution'] = folder_counts

        # Determine recommendation
        self._recommend(result, origin_files, dest_files)

        return result

    def _is_hidden(self, path: Path) -> bool:
        """Check if path is hidden (starts with dot or has hidden attribute on Windows)."""
        if path.name.startswith('.'):
            return True
        if platform.system() == "Windows":
            try:
                import ctypes
                attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
                return attrs != -1 and (attrs & 2)  # FILE_ATTRIBUTE_HIDDEN
            except Exception:
                return False
        return False

    def _get_extensions(self, fmt: str) -> set:
        """Get file extensions for a format."""
        mapping = {
            'jpeg': {'.jpg', '.jpeg'},
            'tiff': {'.tif', '.tiff'},
            'jxl': {'.jxl'},
            'png': {'.png'},
        }
        return mapping.get(fmt.lower(), set())

    def _recommend(self, result: Dict, origin_files: List[Path], dest_files: List[Path]):
        """Determine recommended mode based on analysis."""
        reasoning = []
        confidence = 'low'

        # Mode 6 or 7: export marker detected
        if result['has_export_marker']:
            export_paths = result['export_marker_paths']
            reasoning.append(f"Found {len(export_paths)} folder(s) with '{self.export_marker}' in name")

            # Check if there's a JXL subfolder inside _EXPORT (mode 7)
            has_jxl_subfolder = False
            for export_path in export_paths[:3]:
                export_dir = Path(export_path)
                for subdir in export_dir.iterdir():
                    if subdir.is_dir() and 'jxl' in subdir.name.lower():
                        has_jxl_subfolder = True
                        break

            if has_jxl_subfolder:
                result['recommended_mode'] = 7
                confidence = 'high'
                reasoning.append("Detected JXL subfolder inside export folder — Mode 7 recommended (specific subfolder)")
            else:
                result['recommended_mode'] = 6
                confidence = 'high'
                reasoning.append("Mode 6 recommended — processes all files inside export folders")

        # Mode 3: recursive subfolders with files
        elif result['has_recursive_structure'] and result['total_files'] > 10:
            result['recommended_mode'] = 3
            confidence = 'high'
            reasoning.append(f"Recursive structure detected — {len(result['file_distribution'])} subfolders with files")
            reasoning.append(f"Mode 3 recommended — creates '{self.dest}_files' subfolder in each location")

        # Mode 2: flat output folder
        elif result['has_subfolders'] and result['total_files'] > 5:
            result['recommended_mode'] = 2
            confidence = 'medium'
            reasoning.append(f"Multiple subfolders found ({len(result['file_distribution'])}) with many files")
            reasoning.append("Mode 2 recommended — merges all to single output folder")

        # Mode 1: single subfolder
        elif result['has_subfolders'] and len(result['subfolders']) == 1:
            result['recommended_mode'] = 1
            confidence = 'medium'
            reasoning.append("Single subfolder structure detected")
            reasoning.append(f"Mode 1 recommended — creates 'converted_{self.dest}' subfolder")

        # Mode 0: flat (files in root)
        elif result['has_flat_structure'] and result['total_files'] > 0:
            result['recommended_mode'] = 0
            confidence = 'high'
            reasoning.append("Files found in root folder — in-place mode works well")
            reasoning.append("Mode 0 recommended — files stay side by side")

        # Mode 5: folder suffix when source type is in folder name
        elif any(self.origin.lower() in Path(p).name.lower() for p in result['subfolders'][:3]):
            result['recommended_mode'] = 5
            confidence = 'medium'
            reasoning.append(f"Folder names contain '{self.origin}' — Mode 5 (suffix) recommended")

        else:
            result['recommended_mode'] = 0
            confidence = 'low'
            reasoning.append("No clear structure pattern — defaulting to Mode 0 (in-place)")
            reasoning.append("Use manual mode selection if this doesn't fit your workflow")

        result['confidence'] = confidence
        result['reasoning'] = reasoning

    def format_report(self, analysis: Dict) -> str:
        """Format analysis as human-readable report."""
        lines = []

        lines.append(f"\n{'='*60}")
        lines.append("FOLDER ANALYSIS")
        lines.append(f"{'='*60}")
        lines.append(f"Total {self.origin.upper()} files: {analysis['total_files']}")
        lines.append(f"Total folders scanned: {analysis['folder_count']}")
        lines.append(f"Export folder found: {'Yes' if analysis['has_export_marker'] else 'No'}")

        if analysis['has_export_marker']:
            lines.append(f"  Locations: {', '.join(analysis['export_marker_paths'][:2])}")
            if len(analysis['export_marker_paths']) > 2:
                lines.append(f"  ... and {len(analysis['export_marker_paths'])-2} more")

        if analysis['has_subfolders']:
            lines.append(f"Subfolders: {len(analysis['subfolders'])}")
            for sub in analysis['subfolders'][:3]:
                lines.append(f"  - {sub}")
            if len(analysis['subfolders']) > 3:
                lines.append(f"  ... and {len(analysis['subfolders'])-3} more")

        lines.append(f"\nFile distribution: {len(analysis['file_distribution'])} folder(s)")
        for folder, count in list(analysis['file_distribution'].items())[:3]:
            lines.append(f"  {folder}: {count} file(s)")
        if len(analysis['file_distribution']) > 3:
            lines.append(f"  ... and {len(analysis['file_distribution'])-3} more folder(s)")

        mode_names = {
            0: "In-place (same folder)",
            1: "Subfolder (converted_{dest})",
            2: "Flat (all to one folder)",
            3: "Recursive subfolders ({dest}_files)",
            4: "Sibling folder (rename)",
            5: "Folder suffix",
            6: f"Marker export (full)",
            7: f"Marker export (specific subfolder)",
        }

        lines.append(f"\n{'='*60}")
        if analysis['recommended_mode'] is not None:
            rec_mode = analysis['recommended_mode']
            rec_name = mode_names.get(rec_mode, f"Mode {rec_mode}")
            confidence = analysis['confidence']
            confidence_icon = {"high": "✓✓", "medium": "✓", "low": "?"}[confidence]

            lines.append(f"{confidence_icon} RECOMMENDED: Mode {rec_mode} — {rec_name}")
            lines.append(f"   Confidence: {confidence}")

            if analysis['reasoning']:
                lines.append(f"   Reasoning:")
                for r in analysis['reasoning']:
                    lines.append(f"     - {r}")

        lines.append(f"{'='*60}\n")

        return "\n".join(lines)

    def compute_folder_mappings(self, analysis: Dict, mode: int) -> List[Tuple[str, str, int]]:
        """Compute source -> destination folder mappings for a given mode.
        Returns list of (source_path, dest_path, file_count).
        """
        mappings = []
        origin_exts = self._get_extensions(self.origin)

        if mode == 6:
            # Process all files inside export folders
            for export_path in analysis['export_marker_paths']:
                export_dir = Path(export_path)
                origin_files = [
                    f for f in export_dir.rglob('*')
                    if f.is_file() and f.suffix.lower() in origin_exts
                ]
                if origin_files:
                    mappings.append((str(export_dir), str(export_dir), len(origin_files)))

        elif mode == 7:
            # Export folder / specific subfolder (auto-detect or default to JXL)
            for export_path in analysis['export_marker_paths']:
                export_dir = Path(export_path)
                # Try common subfolder names, or auto-detect any subfolder with origin files
                subfolder_name = "JXL"
                jxl_subfolder = export_dir / subfolder_name
                if not jxl_subfolder.exists() or not any(
                    f.is_file() and f.suffix.lower() in origin_exts
                    for f in jxl_subfolder.rglob('*')
                ):
                    # Auto-detect first subfolder containing origin files
                    for subdir in export_dir.iterdir():
                        if subdir.is_dir():
                            if any(
                                f.is_file() and f.suffix.lower() in origin_exts
                                for f in subdir.rglob('*')
                            ):
                                jxl_subfolder = subdir
                                break
                    else:
                        jxl_subfolder = export_dir / subfolder_name  # fallback
                origin_files = [
                    f for f in jxl_subfolder.rglob('*')
                    if f.is_file() and f.suffix.lower() in origin_exts
                ] if jxl_subfolder.exists() else []
                if origin_files:
                    mappings.append((str(jxl_subfolder), str(jxl_subfolder), len(origin_files)))

        elif mode == 0:
            # In-place: each folder that has files
            for folder, count in analysis['file_distribution'].items():
                if count > 0:
                    src = str(self.root / folder) if folder != '.' else str(self.root)
                    mappings.append((src, src, count))

        elif mode == 1:
            # Subfolder: converted_{dest} inside each source folder
            for folder, count in analysis['file_distribution'].items():
                if count > 0:
                    src = str(self.root / folder) if folder != '.' else str(self.root)
                    dest = str(Path(src) / f"converted_{self.dest}")
                    mappings.append((src, dest, count))

        elif mode == 2:
            # Flat: all to one output folder
            total = sum(analysis['file_distribution'].values())
            if total > 0:
                out_dir = str(self.root / f"output_{self.dest}")
                mappings.append((str(self.root), out_dir, total))

        elif mode == 3:
            # Recursive: each folder gets its own {dest}_files subfolder
            for folder, count in analysis['file_distribution'].items():
                if count > 0 and folder != '.':
                    src = str(self.root / folder)
                    dest = str(Path(src) / f"{self.dest}_files")
                    mappings.append((src, dest, count))

        elif mode in [4, 5]:
            # Sibling/suffix: folder rename
            for folder, count in analysis['file_distribution'].items():
                if count > 0 and folder != '.':
                    src = str(self.root / folder)
                    if mode == 4:
                        # Replace origin in folder name with dest
                        new_name = re.sub(re.escape(self.origin), self.dest, folder, flags=re.IGNORECASE)
                        dest = str(self.root / new_name)
                    else:
                        dest = str(self.root / f"{folder}_{self.dest}")
                    mappings.append((src, dest, count))

        return mappings

    def generate_manifest(self, analysis: Dict, mode: int) -> List[Tuple[str, str, int]]:
        """Generate manifest entries based on mode.

        Returns list of (source, destination, file_count, mode).
        """
        mappings = []
        origin_exts = self._get_extensions(self.origin)

        if mode == 6:
            # For mode 6/7, generate one entry per export folder
            for export_path in analysis['export_marker_paths']:
                export_dir = Path(export_path)
                origin_files = [
                    f for f in export_dir.rglob('*')
                    if f.is_file() and f.suffix.lower() in origin_exts
                ]
                if origin_files:
                    mappings.append((str(export_dir), str(export_dir), len(origin_files), 6))
        elif mode == 7:
            # Mode 7: export / subfolder (auto-detect or default to JXL)
            for export_path in analysis['export_marker_paths']:
                export_dir = Path(export_path)
                subfolder_name = "JXL"
                jxl_subfolder = export_dir / subfolder_name
                if not jxl_subfolder.exists() or not any(
                    f.is_file() and f.suffix.lower() in origin_exts
                    for f in jxl_subfolder.rglob('*')
                ):
                    # Auto-detect first subfolder containing origin files
                    for subdir in export_dir.iterdir():
                        if subdir.is_dir():
                            if any(
                                f.is_file() and f.suffix.lower() in origin_exts
                                for f in subdir.rglob('*')
                            ):
                                jxl_subfolder = subdir
                                break
                    else:
                        jxl_subfolder = export_dir / subfolder_name  # fallback
                origin_files = [
                    f for f in jxl_subfolder.rglob('*')
                    if f.is_file() and f.suffix.lower() in origin_exts
                ] if jxl_subfolder.exists() else []
                if origin_files:
                    mappings.append((str(jxl_subfolder), str(jxl_subfolder), len(origin_files), 7))
        else:
            # For other modes, use compute_folder_mappings
            mappings = [
                (src, dst, count, mode)
                for src, dst, count in self.compute_folder_mappings(analysis, mode)
            ]

        return mappings

    def detect_mode_for_entry(self, source: str, dest: str, original_mode: int = 0) -> int:
        """Auto-detect mode from source/dest pair.

        - Preserves explicitly generated manifest modes.
        - If source == dest -> mode 0 (in-place)
        - If dest is a subfolder of source and contains export-like structure -> mode 7
        - Otherwise -> mode 0 (default)
        """
        src_path = Path(source)
        dst_path = Path(dest)

        # Preserve the mode stored in the manifest. The manifest was generated with
        # a specific mode, so re-detecting would lose modes 1/2/3/4/5 and could
        # turn them into 0 or 7 incorrectly.
        if original_mode is not None and original_mode != 0:
            return original_mode

        if src_path == dst_path:
            return 0

        # Check if dest is a subfolder of source with export-like name
        try:
            rel = dst_path.relative_to(src_path)
            dest_str_lower = str(dst_path).lower()
            marker_lower = self.export_marker.lower()
            marker_in_dest = marker_lower in dest_str_lower
            jxl_in_dest = 'jxl' in dest_str_lower
            if marker_in_dest or jxl_in_dest:
                # Distinguish mode 7 (subfolder) from mode 6 (same export folder).
                if src_path == dst_path.parent and (marker_in_dest or jxl_in_dest):
                    return 7
                return 6
        except ValueError:
            pass

        return 0


class InteractiveMenu:
    def __init__(self, config_manager: ConfigManager,
                 dependency_checker: DependencyChecker):
        self.config = config_manager
        self.checker = dependency_checker

    def display_status(self, status: Dict[str, bool]) -> None:
        """Display status in single line at top (v3 style)"""
        status_line = self.checker.format_status_line(status)

        if RICH_AVAILABLE and console:
            grid = Table.grid(expand=True)
            grid.add_column()
            grid.add_row(Panel(
                status_line,
                title="[bold blue]JXL Tools Environment[/bold blue]",
                border_style="blue"
            ))
            console.print(grid)
        else:
            print("=" * 60)
            print(f"JXL Tools Environment: {status_line}")
            print("=" * 60)

    def show_main_menu(self, has_last_session: bool) -> str:
        """Display main menu - NO DEFAULT"""
        options = []

        if has_last_session:
            last_info = f"({self.config.config.last_output_mode or 'unknown'})"
            options.append(("1", "New workflow", True))
            # Manifest workflows (mode 99) cannot be repeated because the manifest
            # file path and entries are not persisted.
            if self.config.config.last_output_mode == "99":
                options.append(("2", f"Repeat last workflow {last_info} [manifest]", False))
            else:
                options.append(("2", f"Repeat last workflow {last_info}", True))
        else:
            options.append(("1", "New workflow", True))
            options.append(("2", "Repeat last workflow (none saved)", False))

        options.extend([
            ("3", "Check dependencies again", True),
            ("4", "Edit default settings", True),
            ("5", "Reset all settings", True),
            ("6", "Move settings file", True),
            ("0", "Exit", True),
        ])

        if RICH_AVAILABLE and console:
            table = Table(show_header=False, box=None)
            table.add_column("Key", style="bold cyan")
            table.add_column("Option")
            table.add_column("Status", justify="center")

            for key, desc, available in options:
                status_str = "" if available else "[dim](unavailable)[/dim]"
                table.add_row(key, desc, status_str)

            console.print(Panel(table, title="Main Menu", border_style="green"))

            while True:
                choice = Prompt.ask("Select option", choices=[o[0] for o in options if o[2]])
                if choice:
                    return choice
        else:
            print("\n--- Main Menu ---")
            for key, desc, available in options:
                status_str = "" if available else " [UNAVAILABLE]"
                print(f"[{key}] {desc}{status_str}")

            valid_choices = [o[0] for o in options if o[2]]
            while True:
                choice = input("\nSelect option: ").strip()
                if choice in valid_choices:
                    return choice
                print(f"Invalid choice. Valid options: {', '.join(valid_choices)}")

    def edit_settings(self) -> None:
        current = self.config.config

        if RICH_AVAILABLE and console:
            console.print("[bold]Current Settings:[/bold]")
            console.print(f"Staging: {current.staging_dir or 'system default'}")
            console.print(f"Workers: {current.default_workers}")
            console.print(f"Quality: {current.default_quality}")
            console.print(f"Effort: {current.default_effort}")
            console.print(f"Confirm deletes: {current.confirm_delete}")
            console.print(f"Export marker: {current.export_marker}")

            new_staging = Prompt.ask("Staging dir (empty=system default)", default=current.staging_dir or "")
            new_workers = IntPrompt.ask("Workers", default=current.default_workers)
            new_quality = IntPrompt.ask("Quality (1-100)", default=current.default_quality)
            new_effort = IntPrompt.ask("Effort (1-10)", default=current.default_effort)
            new_confirm = Confirm.ask("Confirm before delete?", default=current.confirm_delete)
            new_marker = Prompt.ask("Export marker", default=current.export_marker)
        else:
            print("\n--- Edit Settings ---")
            print(f"Current staging: {current.staging_dir or 'system default'}")
            new_staging = input("New staging (empty=keep, 'none'=system default): ").strip()

            print(f"Current workers: {current.default_workers}")
            workers_input = input("New workers: ").strip()
            new_workers = int(workers_input) if workers_input.isdigit() else current.default_workers

            print(f"Current quality: {current.default_quality}")
            quality_input = input("New quality (1-100): ").strip()
            new_quality = int(quality_input) if quality_input.isdigit() else current.default_quality

            print(f"Current effort: {current.default_effort}")
            effort_input = input("New effort (1-10): ").strip()
            new_effort = int(effort_input) if effort_input.isdigit() else current.default_effort

            confirm_input = input("Confirm before delete? (y/n): ").strip().lower()
            new_confirm = confirm_input.startswith('y') if confirm_input else current.confirm_delete

            print(f"Current marker: {current.export_marker}")
            new_marker = input("New export marker: ").strip() or current.export_marker

        if new_staging.lower() == 'none':
            self.config.config.staging_dir = None
        elif new_staging:
            self.config.config.staging_dir = new_staging

        self.config.config.export_marker = new_marker or "_EXPORT"
        self.config.config.default_workers = max(1, min(new_workers, 32))
        self.config.config.default_quality = max(1, min(new_quality, 100))
        self.config.config.default_effort = max(1, min(new_effort, 10))
        self.config.config.confirm_delete = new_confirm

        self.config.save_config()
        self._print_success("Settings saved!")

    def run_wizard(self, status: Dict[str, bool]) -> Optional[Dict[str, Any]]:
        """Main workflow wizard with 3-tier parameters"""
        last_staging = self.config.config.last_staging
        if last_staging is None:
            last_staging = self.config.config.staging_dir

        workflow = {
            'input_dir': None,
            'origin_format': None,
            'dest_format': None,
            'conversion_type': None,
            'mode': None,
            'workers': self.config.config.last_workers or self.config.config.default_workers,
            'quality': self.config.config.last_quality or self.config.config.default_quality,
            'effort': self.config.config.last_effort or self.config.config.default_effort,
            'staging': last_staging,
            'selected_files': [],
            'icc_profile': None,
            'use_ram': True,
            'compression': 'zip',
            'bit_depth': 16,
            'dry_run': False,
            'advanced_options': {},
            'expert_flags': '',
            'mode_config': {},
            'auto_mode_used': False,
        }

        if not self._wizard_select_origin(workflow, status):
            return None
        if not self._wizard_select_destination(workflow, status):
            return None
        if not self._wizard_select_files(workflow):
            return None
        if not self._wizard_select_mode(workflow):
            return None
        if not self._wizard_mode_specific_config(workflow):
            return None
        self.config.save_last_session(mode_config=workflow.get('mode_config'))
        if not self._wizard_parameters_basic(workflow, status):
            return None
        if not self._wizard_confirm(workflow):
            return None

        return workflow

    def _wizard_select_origin(self, workflow: Dict, status: Dict[str, bool]) -> bool:
        """Step 1: Select source format - NO PNG"""
        options = []

        if status.get('cjxl'):
            options.append(("1", "JPEG", ".jpg, .jpeg", True))

        if status.get('tifffile'):
            options.append(("2", "TIFF", ".tif, .tiff", True))
        else:
            options.append(("2", "TIFF", ".tif (requires: pip install tifffile numpy)", False))

        if status.get('djxl'):
            options.append(("3", "JXL", ".jxl", True))

        if not options:
            self._print_error("No formats available.")
            return False

        if RICH_AVAILABLE and console:
            console.print("\n[bold cyan]Step 1: Source Format[/bold cyan]")
            table = Table(show_header=True)
            table.add_column("#", justify="center", style="cyan")
            table.add_column("Format", style="green")
            table.add_column("Extensions")
            table.add_column("Status", style="dim")

            for key, name, desc, avail in options:
                status_text = "✓ Available" if avail else "✗ Unavailable"
                table.add_row(key, name, desc, status_text)

            console.print(table)

            valid_choices = [o[0] for o in options if o[3]]
            while True:
                choice = Prompt.ask("Select source format", choices=valid_choices)
                if choice:
                    break
        else:
            print("\n--- Step 1: Source Format ---")
            for key, name, desc, avail in options:
                status_str = "" if avail else " [UNAVAILABLE]"
                print(f"[{key}] {name:12} - {desc}{status_str}")

            valid_choices = [o[0] for o in options if o[3]]
            while True:
                choice = input(f"\nSelect ({'/'.join(valid_choices)}): ").strip()
                if choice in valid_choices:
                    break
                print("Invalid selection.")

        format_map = {"1": "jpeg", "2": "tiff", "3": "jxl"}
        workflow['origin_format'] = format_map.get(choice)
        return True

    def _wizard_select_destination(self, workflow: Dict, status: Dict[str, bool]) -> bool:
        """Step 2: Destination"""
        origin = workflow['origin_format']
        options = []

        if origin == "jpeg" and status.get('cjxl'):
            options.append(("1", "JXL Lossless  ", "Lossless JPEG ⇌ JXL transcoding (recommended)", "transcode_lossless"))
            options.append(("2", "JXL Lossy     ", "Lossy — JPEG→JXL encode loses quality", "convert_lossy"))
        elif origin == "tiff" and status.get('cjxl'):
            options.append(("1", "d=0   ", "Lossless (exact replica)", "jxl_tiff_encoder_lossless"))
            options.append(("2", "d=0.1 ", "Near-lossless (recommended)", "jxl_tiff_encoder"))
            options.append(("3", "d=1.0 ", "Visually lossless", "jxl_tiff_encoder"))
            options.append(("4", "Custom", "Enter any value 0-15", "jxl_tiff_encoder"))
        elif origin == "jxl" and status.get('djxl'):
            options.append(("1", "JPEG Auto-Detect", "Auto: lossless if jbrd present, else lossy", "jxl_to_jpeg_auto"))
            options.append(("2", "JPEG Lossless   ", "Force lossless transcoding (requires jbrd)", "jxl_to_jpeg_lossless"))
            options.append(("3", "JPEG Lossy      ", "Force lossy conversion with quality/ICC control", "jxl_to_jpeg_force"))
            options.append(("4", "PNG             ", "PNG with transparency", "jxl_to_png"))
            if status.get('tifffile'):
                options.append(("5", "TIFF            ", "Lossless master", "jxl_tiff_decoder"))

        if not options:
            self._print_error(f"No conversions available for {origin}.")
            return False

        if RICH_AVAILABLE and console:
            console.print(f"\n[bold cyan]Step 2: Destination[/bold cyan]")
            for key, name, desc, _ in options:
                console.print(f"[{key}] [bold]{name}[/bold] - {desc}")

            valid_choices = [o[0] for o in options]
            while True:
                choice = Prompt.ask("Select destination", choices=valid_choices)
                if choice:
                    break
        else:
            print(f"\n\n--- Step 2: Destination ---")
            for key, name, desc, _ in options:
                print(f"[{key}] {name} - {desc}")

            valid_choices = [o[0] for o in options]
            while True:
                choice = input(f"Select ({'/'.join(valid_choices)}): ").strip()
                if choice in valid_choices:
                    break

        selected = next(o for o in options if o[0] == choice)
        workflow['conversion_type'] = selected[3]

        if origin == "tiff":
            if choice == "1":
                workflow['distance_choice'] = "0"
                workflow['dest_format'] = 'jxl'
                workflow['distance'] = 0.0
            elif choice == "2":
                workflow['distance_choice'] = "0.1"
                workflow['dest_format'] = 'jxl'
                workflow['distance'] = 0.1
            elif choice == "3":
                workflow['distance_choice'] = "1.0"
                workflow['dest_format'] = 'jxl'
                workflow['distance'] = 1.0
            else:
                workflow['distance_choice'] = "custom"
                workflow['dest_format'] = 'jxl'

            if RICH_AVAILABLE and console:
                if choice == "4":
                    dist_default = workflow.get('distance', 0.1)
                    dist_str = Prompt.ask("Distance (0.0-15.0, lower=better)", default=str(dist_default))
                    try:
                        workflow['distance'] = float(dist_str)
                    except ValueError:
                        workflow['distance'] = dist_default
                custom_effort = IntPrompt.ask("Effort (1-10, higher=smaller)", default=workflow['effort'])
                workflow['effort'] = max(1, min(custom_effort, 10))
            else:
                if choice == "4":
                    dist_default = workflow.get('distance', 0.1)
                    dist_str = input(f"Distance (0.0-15.0) [{dist_default}]: ").strip()
                    try:
                        workflow['distance'] = float(dist_str) if dist_str else dist_default
                    except ValueError:
                        workflow['distance'] = dist_default
                effort_input = input(f"Effort (1-10) [{workflow['effort']}]: ").strip()
                if effort_input.isdigit():
                    workflow['effort'] = max(1, min(int(effort_input), 10))
        elif origin == "jpeg":
            workflow['dest_format'] = 'jxl'
        elif origin == "jxl":
            if choice in ["1", "2", "3"]:
                # Choices 1, 2, 3 = JPEG output (Auto, Lossless, Lossy)
                workflow['dest_format'] = 'jpeg'
            elif choice == "4":
                workflow['dest_format'] = 'png'
            elif choice == "5":
                workflow['dest_format'] = 'tiff'
            else:
                workflow['dest_format'] = 'jpeg'
        return True

    def _wizard_select_files(self, workflow: Dict) -> bool:
        """Step 3: Files"""
        origin = workflow['origin_format']
        default_dir = self.config.config.last_input_dir or os.getcwd()

        if RICH_AVAILABLE and console:
            console.print(f"\n[bold cyan]Step 3: Source Directory[/bold cyan]")
            input_dir = Prompt.ask("Directory containing files", default=default_dir)
        else:
            print(f"\n--- Step 3: Source Directory ---")
            input_dir = input(f"Directory [{default_dir}]: ").strip() or default_dir

        path = Path(input_dir).expanduser().resolve()
        if not path.exists() or not path.is_dir():
            self._print_error(f"Invalid directory: {path}")
            return False

        workflow['input_dir'] = str(path)
        workflow['selected_files'] = []

        return True

    def _wizard_auto_mode(self, workflow: Dict) -> bool:
        """Auto Mode: analyze folder and recommend mode."""
        origin = workflow['origin_format']
        dest = workflow['dest_format']
        input_dir = Path(workflow['input_dir'])
        export_marker = self.config.config.export_marker

        if RICH_AVAILABLE and console:
            console.print(f"\n[bold cyan]Auto Mode: Analyzing folder structure...[/bold cyan]")
            console.print("[dim]This may take a moment for large folders...[/dim]")
        else:
            print("\n--- Auto Mode: Analyzing folder structure... ---")
            print("(This may take a moment for large folders...)")

        analyzer = FolderAnalyzer(input_dir, origin, dest, export_marker)
        try:
            analysis = analyzer.analyze()
        except Exception as e:
            self._print_error(f"Auto Mode analysis failed: {e}")
            logger.error(f"Auto Mode analysis failed: {e}")
            return False

        # Print analysis report
        report = analyzer.format_report(analysis)
        if RICH_AVAILABLE and console:
            console.print(report)
        else:
            print(report)

        if analysis['recommended_mode'] is None:
            self._print_error("Could not analyze folder structure.")
            return False

        rec_mode = analysis['recommended_mode']
        confidence = analysis['confidence']

        # Compute folder mappings for the recommended mode
        mappings = analyzer.compute_folder_mappings(analysis, rec_mode)

        # Build recommendation options
        mode_names = {
            0: "In-place", 1: "Subfolder", 2: "Flat", 3: "Recursive subfolders",
            4: "Sibling (rename)", 5: "Suffix", 6: f"Export (full)", 7: f"Export (specific subfolder)"
        }

        rec_name = mode_names.get(rec_mode, f"Mode {rec_mode}")

        if RICH_AVAILABLE and console:
            confidence_label = {
                'high': '[green](high confidence)[/green]',
                'medium': '[yellow](medium confidence)[/yellow]',
                'low': '[dim](low confidence — verify)[/dim]'
            }.get(confidence, '')

            console.print(f"Auto Mode recommends: [bold cyan]Mode {rec_mode} — {rec_name}[/bold cyan] {confidence_label}")
            console.print()

            # Show folder preview
            if mappings:
                console.print("[bold]Folder preview:[/bold]")
                for src, dst, count in mappings[:10]:
                    rel_src = Path(src).relative_to(input_dir) if _is_relative_to(Path(src), input_dir) else Path(src)
                    rel_dst = Path(dst).relative_to(input_dir) if _is_relative_to(Path(dst), input_dir) else Path(dst)
                    src_display = self._truncate_path(str(rel_src))
                    dst_display = self._truncate_path(str(rel_dst))
                    console.print(f"  [dim]{src_display}[/dim]")
                    console.print(f"    → [green]{dst_display}[/green] ({count} file(s))")
                    console.print()
                if len(mappings) > 10:
                    console.print(f"  [dim]... and {len(mappings) - 10} more folder(s)[/dim]")
                    console.print()
            else:
                console.print("[dim]No folders to process with this mode.[/dim]")
                console.print()

        # Show menu and handle choice
        return self._wizard_auto_mode_menu(workflow, analyzer, analysis, rec_mode, mode_names, mappings)

    def _wizard_auto_mode_menu(self, workflow: Dict, analyzer: FolderAnalyzer, analysis: Dict, 
                               rec_mode: int, mode_names: Dict, mappings: List) -> bool:
        """Show the auto mode menu and handle user choice."""
        origin = workflow['origin_format']
        dest = workflow['dest_format']
        input_dir = Path(workflow['input_dir'])
        rec_name = mode_names.get(rec_mode, f"Mode {rec_mode}")
        
        if RICH_AVAILABLE and console:
            console.print("[bold]What to do:[/bold]")
            options = [
                ("Y", f"Use Mode {rec_mode} — {rec_name}", True),
                ("P", "Generate manifest CSV (edit in Excel)", True),
                ("V", "View manifest", True),
                ("N", "Choose mode manually", True),
            ]

            for key, desc, avail in options:
                console.print(f"[{key}] {desc}")

            choice = Prompt.ask("Select", choices=["Y", "P", "V", "N"], default="Y")
        else:
            print("What to do:")
            print("[Y] Use this mode")
            print("[P] Generate manifest CSV")
            print("[V] View manifest")
            print("[N] Choose mode manually")
            choice = input("Select [Y/P/V/N]: ").strip().upper()
            if choice not in ["Y", "P", "V", "N"]:
                choice = "Y"

        if choice == "Y":
            workflow['mode'] = rec_mode
            workflow['auto_mode_used'] = True

            if RICH_AVAILABLE and console:
                console.print(f"[green]✓ Using Mode {rec_mode} — {rec_name}[/green]")
            else:
                print(f"✓ Using Mode {rec_mode} — {rec_name}")

            return True

        elif choice == "P":
            # Generate manifest CSV
            manifest_path = self._generate_manifest(analyzer, analysis, rec_mode)
            if manifest_path:
                if RICH_AVAILABLE and console:
                    console.print(f"[green]✓ Manifest saved to:[/green] {manifest_path}")
                    console.print("[dim]Edit in Excel, then run with [M] Run from manifest[/dim]")
                else:
                    print(f"✓ Manifest saved to: {manifest_path}")
                    print("Edit in Excel, then run with [M] Run from manifest")
            # After generating, ask again
            return self._wizard_auto_mode_post_manifest(workflow, analyzer, analysis, rec_mode, mode_names, mappings)

        elif choice == "V":
            # View manifest
            manifest_path = self._get_latest_manifest()
            if manifest_path and Path(manifest_path).exists():
                self._view_manifest(manifest_path, input_dir)
                # After viewing, ask again
                return self._wizard_auto_mode_post_manifest(workflow, analyzer, analysis, rec_mode, mode_names, mappings)
            else:
                if RICH_AVAILABLE and console:
                    console.print("[yellow]No manifest found. Generate one first with [P][/yellow]")
                else:
                    print("No manifest found. Generate one first with [P]")
                # No manifest - return to same menu so user can select [P]
                return self._wizard_auto_mode_menu(workflow, analyzer, analysis, rec_mode, mode_names, mappings)

        else:
            if RICH_AVAILABLE and console:
                console.print("[dim]Switching to manual mode selection...[/dim]")
            else:
                print("Switching to manual mode selection...")
            return self._wizard_select_mode_manual(workflow)

    def _wizard_auto_mode_post_manifest(self, workflow: Dict, analyzer: FolderAnalyzer, analysis: Dict, rec_mode: int, mode_names: Dict, mappings: List) -> bool:
        """Ask what to do after manifest was generated/viewed."""
        if RICH_AVAILABLE and console:
            console.print()
            console.print("[bold]After manifest:[/bold]")
            options = [
                ("Y", f"Use Mode {rec_mode} — {mode_names.get(rec_mode, f'Mode {rec_mode}')}", True),
                ("M", "Run from manifest", True),
                ("N", "Choose mode manually", True),
            ]
            for key, desc, avail in options:
                console.print(f"[{key}] {desc}")
            choice = Prompt.ask("Select", choices=["Y", "M", "N"], default="Y")
        else:
            print()
            print("After manifest:")
            print("[Y] Use this mode")
            print("[M] Run from manifest")
            print("[N] Choose mode manually")
            choice = input("Select [Y/M/N]: ").strip().upper()
            if choice not in ["Y", "M", "N"]:
                choice = "Y"

        if choice == "Y":
            workflow['mode'] = rec_mode
            workflow['auto_mode_used'] = True
            return True
        elif choice == "M":
            manifest_path = self._get_latest_manifest()
            if manifest_path and Path(manifest_path).exists():
                return self._wizard_run_from_manifest(workflow)
            else:
                if RICH_AVAILABLE and console:
                    console.print("[red]No manifest found![/red]")
                else:
                    print("No manifest found!")
                return False
        else:
            return self._wizard_select_mode_manual(workflow)

    def _truncate_path(self, path: str, max_len: int = 50) -> str:
        """Truncate path for display, keeping start and end."""
        if len(path) <= max_len:
            return path
        # Keep start and end
        start_len = max_len // 2 - 2
        return path[:start_len] + "..." + path[-max_len//2+3:]

    def _generate_manifest(self, analyzer: FolderAnalyzer, analysis: Dict, mode: int) -> Optional[str]:
        """Generate manifest CSV and return path."""
        import csv
        from datetime import datetime

        mappings = analyzer.generate_manifest(analysis, mode)

        if not mappings:
            if RICH_AVAILABLE and console:
                console.print("[yellow]No folders to add to manifest.[/yellow]")
            else:
                print("No folders to add to manifest.")
            return None

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        manifest_dir = SCRIPT_DIR / "manifests"
        manifest_dir.mkdir(exist_ok=True)
        manifest_path = manifest_dir / f"manifest_{timestamp}.csv"

        with open(manifest_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["Source", "Destination", "Mode"])
            for src, dst, count, entry_mode in mappings:
                writer.writerow([src, dst, entry_mode])

        return str(manifest_path)

    def _get_latest_manifest(self) -> Optional[str]:
        """Get the most recent manifest file."""
        manifest_dir = SCRIPT_DIR / "manifests"
        if not manifest_dir.exists():
            return None
        manifests = list(manifest_dir.glob("manifest_*.csv"))
        if not manifests:
            return None
        return str(sorted(manifests, key=lambda p: p.stat().st_mtime, reverse=True)[0])

    def _view_manifest(self, manifest_path: str, input_dir: Path) -> None:
        """View manifest contents in a table."""
        import csv

        if not Path(manifest_path).exists():
            return

        entries = []
        with open(manifest_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            header = next(reader, None)
            for row in reader:
                if row and not (len(row) == 1 and row[0].strip().startswith('#')):
                    source = row[0].strip()
                    dest = row[1].strip() if len(row) > 1 else source
                    if not source.startswith('#'):
                        entries.append((source, dest))

        if not entries:
            if RICH_AVAILABLE and console:
                console.print("[yellow]Manifest is empty.[/yellow]")
            else:
                print("Manifest is empty.")
            return

        if RICH_AVAILABLE and console:
            table = Table(show_header=True, header_style="bold cyan")
            table.add_column("#", justify="right", style="dim")
            table.add_column("Source", style="red")
            table.add_column("Destination", style="green")

            for i, (src, dst) in enumerate(entries, 1):
                src_display = self._truncate_path(src)
                dst_display = self._truncate_path(dst)
                table.add_row(str(i), src_display, dst_display)

            console.print(Panel(table, title=f"[bold]Manifest[/bold] — {manifest_path}", border_style="blue"))
            console.print(f"[dim]Total: {len(entries)} entry(ies)[/dim]")
        else:
            print(f"\n=== Manifest: {manifest_path} ===")
            print(f"Total: {len(entries)} entry(ies)\n")
            for i, (src, dst) in enumerate(entries, 1):
                print(f"  {i}. {src}")
                print(f"     -> {dst}")
                print()

    def _wizard_run_from_manifest(self, workflow: Dict) -> bool:
        """Run workflow from manifest file."""
        manifest_path = self._get_latest_manifest()
        if not manifest_path or not Path(manifest_path).exists():
            if RICH_AVAILABLE and console:
                console.print("[red]No manifest found![/red]")
            else:
                print("No manifest found!")
            return False

        # Load manifest
        entries = []
        with open(manifest_path, 'r', encoding='utf-8') as f:
            import csv
            reader = csv.reader(f)
            header = next(reader, None)
            for row in reader:
                if row and len(row) >= 1:
                    source = row[0].strip()
                    dest = row[1].strip() if len(row) > 1 else source
                    entry_mode = int(row[2].strip()) if len(row) > 2 and row[2].strip().isdigit() else 0
                    if source and not source.startswith('#'):
                        # Validate paths to prevent directory traversal
                        if '..' in source or '..' in dest:
                            logger.warning(f"Skipping manifest entry with path traversal: {source} -> {dest}")
                            continue
                        entries.append((source, dest, entry_mode))

        if not entries:
            if RICH_AVAILABLE and console:
                console.print("[yellow]Manifest is empty or only has comments.[/yellow]")
            else:
                print("Manifest is empty or only has comments.")
            return False

        # Show preview
        if RICH_AVAILABLE and console:
            console.print(f"\n[bold cyan]Manifest:[/bold cyan] {manifest_path}")
            console.print(f"[bold]Entries to process:[/bold] {len(entries)}")
            table = Table(show_header=True, header_style="bold cyan")
            table.add_column("#", justify="right", style="dim")
            table.add_column("Source", style="red")
            table.add_column("Destination", style="green")
            table.add_column("Mode", style="magenta")
            for i, (src, dst, mode) in enumerate(entries[:15], 1):
                table.add_row(str(i), self._truncate_path(src), self._truncate_path(dst), str(mode))
            console.print(table)
            if len(entries) > 15:
                console.print(f"[dim]... and {len(entries) - 15} more entries[/dim]")
            console.print()

            proceed = Confirm.ask("Proceed with manifest?", default=True)
        else:
            print(f"\nManifest: {manifest_path}")
            print(f"Entries to process: {len(entries)}\n")
            for i, (src, dst, mode) in enumerate(entries[:15], 1):
                print(f"  {i}. {src}")
                print(f"     -> {dst} (mode {mode})")
            if len(entries) > 15:
                print(f"  ... and {len(entries) - 15} more entries")
            print()
            proceed_input = input("Proceed with manifest? [Y/n]: ").strip().lower()
            proceed = not proceed_input.startswith('n')

        if not proceed:
            return False

        # Execute each entry
        # For manifest mode, we set mode to special value 99 (manifest mode)
        workflow['mode'] = 99  # Special mode for manifest execution
        workflow['manifest_entries'] = entries
        workflow['manifest_path'] = manifest_path
        workflow['auto_mode_used'] = True

        return True

    def _wizard_select_mode(self, workflow: Dict) -> bool:
        """Step 4: Organization Modes — now with [A] Auto Mode"""
        origin = workflow['origin_format']
        dest = workflow['dest_format']
        export_marker = self.config.config.export_marker

        modes = [
            ("0", "In-place",
             f"{origin.upper()} and {dest.upper()} side by side in same folder"),
            ("1", "Subfolder",
             f"Creates [green]'converted_{dest}'[/green] subfolder"),
            ("2", "Flat -> output folder",
             f"All files from subfolders merged to single output folder (recursive)"),
            ("3", "Recursive subfolders",
             f"Creates [green]'{dest.upper()}_files'[/green] in each subfolder"),
            ("4", "Sibling folder (rename)",
             f"Replaces {origin.upper()} with {dest.upper()} in folder name"),
            ("5", "Folder suffix",
             f"Appends [green]_{dest.upper()}[/green] to folder name"),
            ("6", f"Marker [green]export[/green] (full)",
             f"ONLY files INSIDE folders with 'export' in name — ignores everything outside"),
            ("7", f"Marker [green]export[/green] (specific subfolder)",
             f"Like mode 6, but only a specific subfolder (e.g. [green].../Export/JXL[/green])"),
            ("8", "DELETE originals ⚠️",
             "DELETES source files after conversion - IRREVERSIBLE")
        ]

        if RICH_AVAILABLE and console:
            console.print(f"\n[bold cyan]Step 4: Organization Mode[/bold cyan]")
            console.print("[dim]Items in [green]green[/green] (e.g. 'converted_jxl', '_EXPORT') are configurable in option 4[/dim]")
            console.print("[dim]Other folder names (e.g. 'JXL_16bits', '16B_TIFF') require editing the scripts directly[/dim]")

            # Highlight auto mode and manifest
            console.print()
            console.print(Panel.fit(
                "[bold yellow][A] Auto Mode[/bold yellow] — analyze folder structure and recommend best mode",
                border_style="yellow"
            ))
            console.print()

            for key, name, desc in modes:
                style = "red" if key == "8" else "green"
                console.print(f"[{key}] [bold {style}]{name}[/bold {style}]")
                console.print(f"    {desc}\n")

            console.print("[M] [bold]Run from manifest[/bold] — execute a previously generated manifest CSV")
            console.print("[?] [bold yellow]See detailed mode explanation[/bold yellow]")
            console.print()

            valid_choices = [m[0] for m in modes] + ["?", "A", "M"]
            while True:
                choice = Prompt.ask("Select mode", choices=valid_choices)
                if choice:
                    break
        else:
            print(f"\n--- Step 4: Organization Mode ---")
            print("[A] Auto Mode — analyze folder and recommend best mode")
            print()
            for key, name, desc in modes:
                warning = " ⚠️ WARNING!" if key == "8" else ""
                print(f"[{key}] {name}{warning}")
                clean = (desc.replace("[bold green]", "").replace("[/bold green]", "")
                         .replace("[bold red]", "").replace("[/bold red]", "")
                         .replace("[bold blue]", "").replace("[/bold blue]", "")
                         .replace("[bold cyan]", "").replace("[/bold cyan]", "")
                         .replace("[bold yellow]", "").replace("[/bold yellow]", "")
                         .replace("[bold]", "").replace("[/bold]", "")
                         .replace("[green]", "").replace("[/green]", "")
                         .replace("[cyan]", "").replace("[/cyan]", "")
                         .replace("[red]", "").replace("[/red]", "")
                         .replace("[yellow]", "").replace("[/yellow]", "")
                         .replace("[dim]", "").replace("[/dim]", ""))
                print(f"    {clean}\n")
            print("[M] Run from manifest — execute a previously generated manifest CSV")
            print("[?] See detailed mode explanation")
            print()

            valid_choices = [m[0] for m in modes] + ["?", "A", "M"]
            while True:
                choice = input("Mode (0-8, A for auto, M for manifest, or ? for details): ").strip().upper()
                if choice in valid_choices:
                    break

        # Handle "?" - show detailed explanations
        if choice == "?":
            return self._show_mode_details_and_select(workflow)

        # Handle Auto Mode
        if choice == "A":
            return self._wizard_auto_mode(workflow)

        # Handle Manifest
        if choice == "M":
            return self._wizard_run_from_manifest(workflow)

        # Handle mode 8 with confirmation
        if choice == "8":
            if not self._confirm_archive_mode():
                return False
            workflow['delete_source'] = True

        workflow['mode'] = int(choice)

        note_lines = [
            "[bold yellow]Configurable items:[/bold yellow] green names like 'converted_jxl', '_EXPORT', '_TIFF', etc.",
            "can be customized in [bold cyan]Edit default settings (option 4)[/bold cyan] before running.",
        ]
        if int(choice) == 8:
            note_lines.append("[bold red]WARNING: Mode 8 will DELETE your original files after conversion![/bold red]")
        if RICH_AVAILABLE and console:
            console.print()
            console.print(Panel(
                "\n".join(note_lines),
                title="[yellow]Tip[/yellow]",
                border_style="yellow"
            ))
        else:
            print()
            for line in note_lines:
                print(line.replace("[bold green]", "").replace("[/bold green]", "")
                      .replace("[bold red]", "").replace("[/bold red]", "")
                      .replace("[bold blue]", "").replace("[/bold blue]", "")
                      .replace("[bold cyan]", "").replace("[/bold cyan]", "")
                      .replace("[bold yellow]", "").replace("[/bold yellow]", "")
                      .replace("[bold]", "").replace("[/bold]", "")
                      .replace("[green]", "").replace("[/green]", "")
                      .replace("[cyan]", "").replace("[/cyan]", "")
                      .replace("[red]", "").replace("[/red]", "")
                      .replace("[yellow]", "").replace("[/yellow]", "")
                      .replace("[dim]", "").replace("[/dim]", ""))

        return True

    def _wizard_select_mode_manual(self, workflow: Dict) -> bool:
        """Manual mode selection (called after declining auto recommendation)."""
        origin = workflow['origin_format']
        dest = workflow['dest_format']
        export_marker = self.config.config.export_marker

        modes = [
            ("0", "In-place", f"{origin.upper()} and {dest.upper()} side by side"),
            ("1", "Subfolder", f"Creates 'converted_{dest}' subfolder"),
            ("2", "Flat", "All to one folder (recursive)"),
            ("3", "Recursive", f"'{dest}_files' in each subfolder"),
            ("4", "Sibling", f"Renames folder {origin}→{dest}"),
            ("5", "Suffix", f"Adds _{dest} to folder name"),
            ("6", f"Marker export (full)", f"Only inside folders with 'export' in name"),
            ("7", f"Marker export (subfolder)", f"Only .../Export/JXL style subfolder"),
            ("8", "DELETE originals ⚠️", "DELETES source files!"),
        ]

        if RICH_AVAILABLE and console:
            console.print(f"\n[bold cyan]Select Mode Manually[/bold cyan]")
            for key, name, desc in modes:
                style = "red" if key == "8" else "green"
                console.print(f"[{key}] [bold {style}]{name}[/bold {style}] — {desc}")

            valid_choices = [m[0] for m in modes]
            while True:
                choice = Prompt.ask("Select mode", choices=valid_choices)
                if choice:
                    break
        else:
            print("\n--- Select Mode Manually ---")
            for key, name, desc in modes:
                print(f"[{key}] {name} — {desc}")
            valid_choices = [m[0] for m in modes]
            choice = input(f"Select ({'/'.join(valid_choices)}): ").strip()
            while choice not in valid_choices:
                choice = input(f"Select ({'/'.join(valid_choices)}): ").strip()

        if choice == "8":
            if not self._confirm_archive_mode():
                return False

        workflow['mode'] = int(choice)
        return True

    def _show_mode_details_and_select(self, workflow: Dict) -> bool:
        """Display detailed explanation of all modes and let user select."""
        origin = workflow['origin_format']
        dest = workflow['dest_format']
        export_marker = self.config.config.export_marker

        details = [
            ("0", "In-place",
             f"{origin.upper()} and {dest.upper()} stay side by side in the SAME folder.\n"
             f"Input file/folder determines output location.\n"
             f"Single file -> same folder; folder -> flat output in that folder.\n"
             f"[bold green]Non-recursive[/bold green] - subfolders are NOT processed."),

            ("1", "Subfolder",
             f"Creates a [green]'converted_{dest}'[/green] subfolder next to each source folder.\n"
             f"Example: [cyan]F:/Photos/2024/[/cyan] -> [cyan]F:/Photos/2024/converted_{dest}/[/cyan]\n"
             f"Works on folder input only. Non-recursive."),

            ("2", "Flat -> output folder",
             f"All {origin.upper()} files from ALL subfolders are merged into a single output folder.\n"
             f"Fully recursive — every subfolder is scanned.\n"
             f"If you specify an output root, files land there.\n"
             f"Otherwise uses the input root as output root."),

            ("3", "Recursive subfolders",
             f"Each subfolder gets its own [green]'{dest.upper()}_files'[/green] subfolder.\n"
             f"Preserves folder structure: [cyan]F:/Photos/2024/A/[/cyan] -> [cyan]F:/Photos/2024/A/{dest.upper()}_files/[/cyan]"),

            ("4", "Sibling folder (rename)",
             f"Renames the folder, replacing [green]{origin.upper()}[/green] with [green]{dest.upper()}[/green] in the folder name.\n"
             f"[cyan]F:/Photos/JXL_raw/[/cyan] -> [cyan]F:/Photos/{dest.upper()}_raw/[/cyan]\n"
             f"If {origin.upper()} is not found, appends {dest.upper()} and logs a warning."),

            ("5", "Folder suffix",
             f"Adds [green]_{dest.upper()}[/green] suffix to the folder name.\n"
             f"[cyan]F:/Photos/Raw/[/cyan] -> [cyan]F:/Photos/Raw_{dest.upper()}/[/cyan]"),

            ("6", f"Marker export (full)",
             f"ONLY processes files INSIDE folders containing 'export' in the name.\n"
             f"Recursively finds ALL export folders and processes everything under each.\n"
             f"Ignores ALL files outside export folders.\n"
             f"Works with _EXPORT, Export_Lightroom, Lightroom_Export, etc. (case-insensitive)."),

            ("7", f"Marker export (specific subfolder)",
             f"Like mode 6, but ONLY processes files inside a specific subfolder of export folders.\n"
             f"Default subfolder is [green].../Export/JXL[/green] (configurable).\n"
             f"Files in other subfolders within export folders are ignored.\n"
             f"Use when you keep different color-space variants in separate subfolders."),

            ("8", "DELETE originals",
             f"[bold red]DANGEROUS![/bold red] Same as mode 0 (recursive), but...\n"
             f"[bold red]DELETES the original {origin.upper()} files after successful conversion.[/bold red]\n"
             f"This is IRREVERSIBLE. Always test with a small batch first."),
        ]

        if RICH_AVAILABLE and console:
            console.print(f"\n[bold cyan]--- Mode Detailed Explanations ---[/bold cyan]\n")
            for key, name, desc in details:
                style = "red" if key == "8" else "blue"
                console.print(f"[{key}] [bold {style}]{name}[/bold {style}]")
                console.print(Panel.fit(desc, border_style=style))
                console.print()

            valid_choices = [d[0] for d in details] + ["A", "M"]
            while True:
                choice = Prompt.ask("Select mode", choices=valid_choices)
                if choice in valid_choices:
                    break
        else:
            print("\n=== Mode Detailed Explanations ===\n")
            for key, name, desc in details:
                print(f"[{key}] {name}")
                clean = (desc.replace("[bold green]", "").replace("[/bold green]", "")
                         .replace("[bold red]", "").replace("[/bold red]", "")
                         .replace("[bold blue]", "").replace("[/bold blue]", "")
                         .replace("[bold cyan]", "").replace("[/bold cyan]", "")
                         .replace("[bold yellow]", "").replace("[/bold yellow]", "")
                         .replace("[bold ", "").replace("[/bold]", "")
                         .replace("[green]", "").replace("[/green]", "")
                         .replace("[cyan]", "").replace("[/cyan]", "")
                         .replace("[red]", "").replace("[/red]", "")
                         .replace("[yellow]", "").replace("[/yellow]", "")
                         .replace("[dim]", "").replace("[/dim]", ""))
                print(f"   {clean}\n")
            valid_choices = [d[0] for d in details] + ["A", "M"]
            choice = input("Select mode [0-8/A/M/?]: ").strip().upper()
            while choice not in valid_choices:
                choice = input("Select mode [0-8/A/M/?]: ").strip().upper()

        # Handle Auto and Manifest from detail view
        if choice == "A":
            return self._wizard_auto_mode(workflow)

        if choice == "M":
            return self._wizard_run_from_manifest(workflow)

        if choice == "8":
            if not self._confirm_archive_mode():
                return self._show_mode_details_and_select(workflow)

        workflow['mode'] = int(choice)
        return True

    def _wizard_mode_specific_config(self, workflow: Dict) -> bool:
        """Step 5: Mode-specific configuration"""
        mode = workflow['mode']
        mode_config = {}
        origin = workflow['origin_format']
        dest = workflow['dest_format']

        # Manifest mode (99) doesn't need mode-specific config
        if mode == 99:
            if RICH_AVAILABLE and console:
                console.print(f"\n[bold cyan]Step 5: Manifest Mode[/bold cyan]")
                console.print(f"[dim]Running {len(workflow.get('manifest_entries', []))} entries from manifest[/dim]")
            else:
                print(f"\n--- Step 5: Manifest Mode ---")
            workflow['mode_config'] = mode_config
            return True

        if mode in [6, 7]:
            current = self.config.config.export_marker
            if RICH_AVAILABLE and console:
                console.print(f"\n[bold cyan]Step 5: Marker Configuration[/bold cyan]")
                console.print(f"Current: [green]{current}[/green]")
                new_marker = Prompt.ask("EXPORT marker", default=current)
            else:
                print(f"\n--- Step 5: Marker Configuration ---")
                print(f"Current: {current}")
                new_marker = input(f"EXPORT marker [{current}]: ").strip() or current

            if new_marker != current:
                mode_config['export_marker'] = new_marker
                self.config.config.export_marker = new_marker
                self.config.save_config()

        elif mode == 2:
            default_out = Path(workflow['input_dir']).parent / "output"
            if RICH_AVAILABLE and console:
                console.print(f"\n[bold cyan]Step 5: Flat Folder[/bold cyan]")
                output_dir = Prompt.ask("Output directory", default=str(default_out))
            else:
                print(f"\n--- Step 5: Flat Folder ---")
                output_dir = input(f"Destination [{default_out}]: ").strip() or str(default_out)
            mode_config['output_dir'] = output_dir

        elif mode == 1:
            folder_name = f"converted_{dest}"
            if RICH_AVAILABLE and console:
                console.print(f"\n[bold cyan]Step 5: Subfolder Name[/bold cyan]")
                console.print(f"Will create: [green]'{folder_name}'[/green] in each source folder")
            else:
                print(f"\n--- Step 5: Subfolder Name ---")
                print(f"Will create: '{folder_name}' in each source folder")

        elif mode == 3:
            folder_name = f"{dest.upper()}_files"
            if RICH_AVAILABLE and console:
                console.print(f"\n[bold cyan]Step 5: Subfolder Name[/bold cyan]")
                console.print(f"Will create: [green]'{folder_name}'[/green] in each source folder")
            else:
                print(f"\n--- Step 5: Subfolder Name ---")
                print(f"Will create: '{folder_name}' in each source folder")

        elif mode in [4, 5]:
            if RICH_AVAILABLE and console:
                console.print(f"\n[bold cyan]Step 5: Rename Configuration[/bold cyan]")
                console.print(f"Example: folder_{origin} → [green]folder_{dest}[/green]")
            else:
                print(f"\n--- Step 5: Rename Configuration ---")
                print(f"Example: folder_{origin} → folder_{dest}")

        else:
            if RICH_AVAILABLE and console:
                console.print(f"\n[bold cyan]Step 5: Confirmation[/bold cyan]")
                console.print(f"Mode {mode} - OK")
            else:
                print(f"\n--- Step 5: Confirmation ---")

        workflow['mode_config'] = mode_config
        return True

    def _confirm_archive_mode(self) -> bool:
        from datetime import datetime
        now = datetime.now()
        token = now.strftime("%H%M")

        if RICH_AVAILABLE and console:
            console.print("[bold red]⚠️  DELETE ORIGINALS MODE[/bold red]")
            console.print("[red]Original files will be DELETED[/red]")
            console.print(f"Enter current time ({token}) to confirm:")
        else:
            print("\n⚠️  DELETE ORIGINALS MODE")
            print("⚠️  Original files will be DELETED")
            print(f"Enter {token} to confirm:")

        user_input = input("> ").strip()

        if user_input != token:
            self._print_error("Confirmation failed!")
            return False

        self._print_success("Confirmed!")
        return True

    def _wizard_parameters_basic(self, workflow: Dict, status: Dict[str, bool]) -> bool:
        """Step 6: Basic Parameters (always shown)"""
        conv_type = workflow['conversion_type']
        origin = workflow['origin_format']
        dest = workflow['dest_format']

        current_staging = workflow['staging']
        staging_display = current_staging if current_staging else "system default"

        if RICH_AVAILABLE and console:
            console.print("\n[bold cyan]Step 6: Basic Parameters[/bold cyan]")

            if origin == 'tiff':
                use_ram = Confirm.ask("Use RAM for intermediate PNG? (faster)", default=workflow['use_ram'])
                workflow['use_ram'] = use_ram

            workers = IntPrompt.ask("Workers", default=workflow['workers'])
            workflow['workers'] = max(1, workers)

            if origin == 'tiff' and dest == 'jxl':
                dist_choice = workflow.get('distance_choice', '')
                q = workflow.get('distance', 0.1)
                console.print(f"[dim]Distance:[/dim] {q:.2f} (set in Step 2)")
                console.print(f"[dim]Effort:[/dim] {workflow['effort']} (set in Step 2)")

            elif origin == 'jxl' and dest == 'tiff':
                # No effort parameter for JXL decoding - djxl doesn't use it
                pass
            elif 'lossy' in conv_type:
                if conv_type == 'convert_lossy':
                    # JPEG -> JXL lossy uses cjxl distance, not JPEG quality
                    distance = float(Prompt.ask("Distance (0=lossless, 0.1=near-lossless, 1=visually lossless)", default=str(workflow.get('distance', 0.5))))
                    workflow['distance'] = max(0.0, min(distance, 15.0))
                else:
                    quality = IntPrompt.ask("Quality (1-100)", default=workflow['quality'])
                    workflow['quality'] = max(1, min(quality, 100))
                effort = IntPrompt.ask("Effort (1-10)", default=workflow['effort'])
                workflow['effort'] = max(1, min(effort, 10))
            else:
                effort = IntPrompt.ask("Effort (1-10)", default=workflow['effort'])
                workflow['effort'] = max(1, min(effort, 10))

            staging_prompt = f"Staging [{staging_display}]"
            staging_input = Prompt.ask(staging_prompt, default=current_staging if current_staging else "")

            if staging_input.strip() == "":
                pass
            elif staging_input.lower() == 'system default':
                workflow['staging'] = None
            else:
                workflow['staging'] = staging_input

            # Quality and ICC settings for JPEG output (only for lossy conversion)
            # Quality and ICC settings for JPEG output (for lossy modes: AUTO and FORCE_LOSSY)
            if origin == 'jxl' and dest == 'jpeg' and workflow.get('conversion_type') in ['jxl_to_jpeg_auto', 'jxl_to_jpeg_force']:
                quality = IntPrompt.ask("Quality (1-100)", default=workflow.get('quality', 95))
                workflow['quality'] = max(1, min(quality, 100))
                
                if status.get('magick'):
                    convert_icc = Confirm.ask("Convert to sRGB?", default=False)
                    if convert_icc:
                        workflow['icc_profile'] = 'sRGB'

            if dest == 'tiff':
                compression = Prompt.ask("TIFF compression", choices=["zip", "lzw", "none"], default=workflow['compression'])
                workflow['compression'] = compression
                depth = IntPrompt.ask("Bit depth", choices=["8", "16"], default=workflow['bit_depth'])
                workflow['bit_depth'] = int(depth) if depth else workflow['bit_depth']
                # Preview option for JXL→TIFF
                add_preview = Confirm.ask("Add JPEG preview? (for faster viewing)", default=True)
                workflow['add_preview'] = add_preview

                # Matrix/Basic mode and target ICC for JXL→TIFF
                if origin == 'jxl' and dest == 'tiff':
                    decode_mode = Prompt.ask(
                        "Decode mode",
                        choices=["roundtrip", "basic", "matrix", "none"],
                        default="roundtrip"
                    )
                    advanced_options = workflow.setdefault('advanced_options', {})
                    if decode_mode == "matrix":
                        advanced_options['matrix'] = True
                    elif decode_mode == "basic":
                        advanced_options['basic'] = True
                    elif decode_mode == "none":
                        advanced_options['none'] = True

                    target_icc = Prompt.ask(
                        "Target ICC (file path or sRGB)",
                        default=""
                    )
                    if target_icc and target_icc.strip():
                        advanced_options['target_icc'] = target_icc.strip()

            dry_run = Confirm.ask("Dry run? (simulate without converting)", default=False)
            workflow['dry_run'] = dry_run

            console.print("Existing file handling: [1] overwrite all | [2] sync (reconvert if newer)")
            ow = Prompt.ask("If exists", choices=["1", "2"], default="2")
            workflow['overwrite_mode'] = ow

            if origin == 'tiff' and dest == 'jxl':
                d50 = Prompt.ask("D50 patch", choices=["auto", "on", "off"], default="auto")
                workflow['d50_patch'] = d50

        else:
            print("\n--- Step 6: Basic Parameters ---")

            if origin == 'tiff':
                ram_input = input(f"Use RAM for intermediate PNG? [Y/n]: ").strip().lower()
                workflow['use_ram'] = not ram_input.startswith('n')

            workers = input(f"Workers [{workflow['workers']}]: ").strip()
            workflow['workers'] = int(workers) if workers.isdigit() else workflow['workers']

            if origin == 'tiff' and dest == 'jxl':
                print(f"Distance: {workflow.get('distance', 0.1):.2f} (set in Step 2)")
                print(f"Effort: {workflow['effort']} (set in Step 2)")
            elif origin == 'jxl' and dest == 'tiff':
                # No effort parameter for JXL decoding - djxl doesn't use it
                pass
            elif 'lossy' in conv_type:
                if conv_type == 'convert_lossy':
                    # JPEG -> JXL lossy uses cjxl distance, not JPEG quality
                    distance = input(f"Distance (0=lossless, 0.1=near-lossless, 1=visually lossless) [{workflow.get('distance', 0.5)}]: ").strip()
                    workflow['distance'] = max(0.0, min(float(distance) if distance else workflow.get('distance', 0.5), 15.0))
                else:
                    quality = input(f"Quality (1-100) [{workflow['quality']}]: ").strip()
                    workflow['quality'] = int(quality) if quality.isdigit() else workflow['quality']
                effort = input(f"Effort (1-10) [{workflow['effort']}]: ").strip()
                workflow['effort'] = int(effort) if effort.isdigit() else workflow['effort']
            else:
                effort = input(f"Effort (1-10) [{workflow['effort']}]: ").strip()
                workflow['effort'] = int(effort) if effort.isdigit() else workflow['effort']

            if origin == 'tiff' and dest == 'jxl':
                d50_input = input("D50 patch (auto/on/off) [auto]: ").strip().lower() or "auto"
                workflow['d50_patch'] = d50_input if d50_input in ["auto", "on", "off"] else "auto"

            staging_input = input(f"Staging [{staging_display}]: ").strip()
            if staging_input.lower() == 'system default':
                workflow['staging'] = None
            elif staging_input:
                workflow['staging'] = staging_input

            # Quality and ICC settings for JPEG output (only for lossy conversion)
            # Quality and ICC settings for JPEG output (for lossy modes: AUTO and FORCE_LOSSY)
            if origin == 'jxl' and dest == 'jpeg' and workflow.get('conversion_type') in ['jxl_to_jpeg_auto', 'jxl_to_jpeg_force']:
                quality = input(f"Quality (1-100) [{workflow.get('quality', 95)}]: ").strip()
                if quality.isdigit():
                    workflow['quality'] = max(1, min(int(quality), 100))
                
                if status.get('magick'):
                    icc_input = input("Convert to sRGB? [y/N]: ").strip().lower()
                    if icc_input.startswith('y'):
                        workflow['icc_profile'] = 'sRGB'

            if dest == 'tiff':
                comp_input = input(f"TIFF compression (zip/lzw/none) [{workflow['compression']}]: ").strip()
                if comp_input in ['zip', 'lzw', 'none']:
                    workflow['compression'] = comp_input
                depth_input = input(f"Bit depth (8/16) [{workflow['bit_depth']}]: ").strip()
                if depth_input in ['8', '16']:
                    workflow['bit_depth'] = int(depth_input)
                # Preview option for JXL→TIFF
                preview_input = input("Add JPEG preview? (Y/n) [Y]: ").strip().lower()
                workflow['add_preview'] = not preview_input.startswith('n')

                # Matrix/Basic mode and target ICC for JXL→TIFF
                if origin == 'jxl' and dest == 'tiff':
                    decode_mode = input("Decode mode (roundtrip/basic/matrix/none) [roundtrip]: ").strip().lower() or "roundtrip"
                    advanced_options = workflow.setdefault('advanced_options', {})
                    if decode_mode == "matrix":
                        advanced_options['matrix'] = True
                    elif decode_mode == "basic":
                        advanced_options['basic'] = True
                    elif decode_mode == "none":
                        advanced_options['none'] = True

                    target_icc = input("Target ICC (file path, sRGB, or empty): ").strip()
                    if target_icc:
                        advanced_options['target_icc'] = target_icc

            dry_input = input("Dry run? [y/N]: ").strip().lower()
            workflow['dry_run'] = dry_input.startswith('y')

            ow_input = input("Existing file handling (1=overwrite, 2=sync) [2]: ").strip() or "2"
            workflow['overwrite_mode'] = ow_input

        # Persist basic parameters that the repeat workflow needs to restore.
        self.config.save_last_session(
            use_ram=workflow.get('use_ram'),
            compression=workflow.get('compression'),
            bit_depth=workflow.get('bit_depth'),
            add_preview=workflow.get('add_preview')
        )

        return self._wizard_parameters_advanced(workflow, status)

    def _wizard_parameters_advanced(self, workflow: Dict, status: Dict[str, bool]) -> bool:
        """Step 6A: Advanced Options (optional)"""
        conv_type = workflow['conversion_type']
        origin = workflow['origin_format']
        dest = workflow['dest_format']

        advanced_options = {}

        if RICH_AVAILABLE and console:
            console.print("\n[bold cyan]Step 6A: Advanced Options[/bold cyan]")
            show_advanced = Confirm.ask("Configure advanced options?", default=False)
        else:
            print("\n--- Step 6A: Advanced Options ---")
            adv_input = input("Configure advanced options? [y/N]: ").strip().lower()
            show_advanced = adv_input.startswith('y')

        if not show_advanced:
            ow_mode = workflow.get('overwrite_mode', '2')
            if ow_mode == "1":
                advanced_options['overwrite'] = True
                advanced_options['sync'] = False
            else:
                # Default and only other option is sync (2)
                advanced_options['overwrite'] = False
                advanced_options['sync'] = True
            if origin == 'tiff' and dest == 'jxl':
                advanced_options['d50_patch'] = workflow.get('d50_patch', 'auto')
                advanced_options['encode_tag'] = workflow.get('encode_tag', 'xmp')
                advanced_options['multipage_mode'] = self.config.config.last_multipage_mode or 'ignore'
                advanced_options['thumbnail_mode'] = self.config.config.last_thumbnail_mode or 'exclude'
                advanced_options['thumbnail_suffix'] = self.config.config.last_thumbnail_suffix or '_thumbnail'
            elif origin == 'jxl' and dest == 'tiff':
                advanced_options['thumbnail_handling'] = self.config.config.last_thumbnail_handling or 'include'
                advanced_options['thumbnail_suffix'] = self.config.config.last_thumbnail_suffix or '_thumbnail'
                advanced_options['no_reconstruct_multipage'] = bool(self.config.config.last_no_reconstruct_multipage)
                advanced_options['depth_policy'] = self.config.config.last_depth_policy or 'preserve_thumbnails'
            # Preserve decode-mode/target-icc chosen earlier when not showing advanced
            existing = workflow.get('advanced_options', {})
            for key in ('matrix', 'basic', 'none', 'target_icc'):
                if key in existing:
                    advanced_options[key] = existing[key]
            workflow['advanced_options'] = advanced_options
            return self._wizard_parameters_expert(workflow)

        if origin == 'tiff' and dest == 'jxl':
            if RICH_AVAILABLE and console:
                strip_meta = Confirm.ask("Strip metadata?", default=False)
                encode_tag = Prompt.ask("Encode tag location", choices=["xmp", "software", "off"], default="xmp")
                # Thumbnail option
                thumb_default = self.config.config.last_jpeg_thumbnail if self.config.config.last_jpeg_thumbnail is not None else False
                embed_thumb = Confirm.ask("Embed JPEG thumbnail for fast preview? (~20KB per file)", default=thumb_default)
                self.config.save_last_session(jpeg_thumbnail=embed_thumb)
                # Multi-page TIFF options
                mp_default = self.config.config.last_multipage_mode or "ignore"
                multipage_mode = Prompt.ask(
                    "Multi-page TIFF handling",
                    choices=["ignore", "skip", "split", "split_all"],
                    default=mp_default
                )
                self.config.save_last_session(multipage_mode=multipage_mode)
                thumbnail_mode = "exclude"
                thumbnail_suffix = "_thumbnail"
                if multipage_mode in ("split", "split_all"):
                    tm_default = self.config.config.last_thumbnail_mode or "exclude"
                    thumbnail_mode = Prompt.ask(
                        "Thumbnail handling when splitting",
                        choices=["exclude", "include"],
                        default=tm_default
                    )
                    self.config.save_last_session(thumbnail_mode=thumbnail_mode)
                    ts_default = self.config.config.last_thumbnail_suffix or "_thumbnail"
                    thumbnail_suffix = Prompt.ask("Thumbnail suffix", default=ts_default)
                    self.config.save_last_session(thumbnail_suffix=thumbnail_suffix)
                overwrite_mode = workflow.get('overwrite_mode', '2')
                delete_src = workflow.get('delete_source', False)
                if not delete_src:
                    delete_src = Confirm.ask("Delete source TIFFs after conversion? (mode 8)", default=False)
            else:
                strip_input = input("Strip metadata? [y/N]: ").strip().lower()
                strip_meta = strip_input.startswith('y')
                encode_tag_input = input("Encode tag (xmp/software/off) [xmp]: ").strip().lower() or "xmp"
                encode_tag = encode_tag_input if encode_tag_input in ["xmp", "software", "off"] else "xmp"
                # Thumbnail option
                thumb_default = "y" if self.config.config.last_jpeg_thumbnail else "n"
                thumb_input = input(f"Embed JPEG thumbnail? (~20KB) [{thumb_default}/n]: ").strip().lower() or thumb_default
                embed_thumb = thumb_input.startswith('y')
                self.config.save_last_session(jpeg_thumbnail=embed_thumb)
                # Multi-page TIFF options
                mp_default = self.config.config.last_multipage_mode or "ignore"
                mp_input = input(f"Multi-page TIFF handling (ignore/skip/split/split_all) [{mp_default}]: ").strip().lower() or mp_default
                multipage_mode = mp_input if mp_input in ["ignore", "skip", "split", "split_all"] else "ignore"
                self.config.save_last_session(multipage_mode=multipage_mode)
                thumbnail_mode = "exclude"
                thumbnail_suffix = "_thumbnail"
                if multipage_mode in ("split", "split_all"):
                    tm_default = self.config.config.last_thumbnail_mode or "exclude"
                    tm_input = input(f"Thumbnail handling when splitting (exclude/include) [{tm_default}]: ").strip().lower() or tm_default
                    thumbnail_mode = tm_input if tm_input in ["exclude", "include"] else "exclude"
                    self.config.save_last_session(thumbnail_mode=thumbnail_mode)
                    ts_default = self.config.config.last_thumbnail_suffix or "_thumbnail"
                    ts_input = input(f"Thumbnail suffix [{ts_default}]: ").strip()
                    thumbnail_suffix = ts_input if ts_input else ts_default
                    self.config.save_last_session(thumbnail_suffix=thumbnail_suffix)
                overwrite_mode = workflow.get('overwrite_mode', '2')
                delete_src = workflow.get('delete_source', False)
                if not delete_src:
                    delete_src_input = input("Delete source TIFFs after conversion? [y/N]: ").strip().lower()
                    delete_src = delete_src_input.startswith('y')

            if overwrite_mode == "1":
                overwrite, sync = True, False
            elif overwrite_mode == "2":
                overwrite, sync = False, True
            else:
                overwrite, sync = False, False

            advanced_options['strip'] = strip_meta
            advanced_options['encode_tag'] = encode_tag
            advanced_options['d50_patch'] = workflow.get('d50_patch', 'auto')
            advanced_options['overwrite'] = overwrite
            advanced_options['sync'] = sync
            advanced_options['delete_source'] = delete_src
            advanced_options['embed_thumbnail'] = embed_thumb
            advanced_options['multipage_mode'] = multipage_mode
            advanced_options['thumbnail_mode'] = thumbnail_mode
            advanced_options['thumbnail_suffix'] = thumbnail_suffix

        elif origin == 'jxl' and dest == 'tiff':
            if RICH_AVAILABLE and console:
                use_matrix = Confirm.ask("Use ICC matrix conversion?", default=False)
                use_none = False
                use_basic = False
                if not use_matrix:
                    icc_mode = Prompt.ask("ICC mode", choices=["auto", "basic", "none"], default="auto")
                    use_basic = (icc_mode == "basic")
                    use_none = (icc_mode == "none")
                target_icc = Prompt.ask("Target ICC profile", choices=["", "sRGB", "custom"], default="")
                if target_icc == "custom":
                    target_icc = Prompt.ask("Enter ICC profile path")
                no_cleanup = Confirm.ask("Skip ICC cleanup?", default=False)
                # Thumbnail reconstruction handling
                th_default = self.config.config.last_thumbnail_handling if hasattr(self.config.config, 'last_thumbnail_handling') else "include"
                th_default = th_default or "include"
                thumbnail_handling = Prompt.ask(
                    "Thumbnail handling for multi-page TIFFs",
                    choices=["ignore", "include", "generate"],
                    default=th_default
                )
                if thumbnail_handling == "generate":
                    console.print("[yellow]generate is not yet implemented; using include[/yellow]")
                    thumbnail_handling = "include"
                self.config.save_last_session(thumbnail_handling=thumbnail_handling)
                ts_default = self.config.config.last_thumbnail_suffix or "_thumbnail"
                thumbnail_suffix = Prompt.ask("Thumbnail suffix", default=ts_default)
                self.config.save_last_session(thumbnail_suffix=thumbnail_suffix)
                no_recon_default = self.config.config.last_no_reconstruct_multipage if self.config.config.last_no_reconstruct_multipage is not None else False
                no_reconstruct_multipage = Confirm.ask("Decode every JXL to its own TIFF (no multi-page reconstruction)?", default=no_recon_default)
                self.config.save_last_session(no_reconstruct_multipage=no_reconstruct_multipage)
                dp_default = self.config.config.last_depth_policy or "preserve_thumbnails"
                depth_policy = Prompt.ask(
                    "Bit depth policy",
                    choices=["force16", "preserve_thumbnails", "preserve_original"],
                    default=dp_default
                )
                self.config.save_last_session(depth_policy=depth_policy)
                overwrite_mode = workflow.get('overwrite_mode', '2')
                delete_src = Confirm.ask("Delete source JXLs after conversion? (mode 8)", default=False)
            else:
                matrix_input = input("Use ICC matrix conversion? [y/N]: ").strip().lower()
                use_matrix = matrix_input.startswith('y')
                use_none = False
                use_basic = False
                if not use_matrix:
                    print("ICC mode: auto = use ICC from XMP or djxl (default)")
                    print("          basic = force Basic mode (djxl ICC)")
                    print("          none  = no ICC handling")
                    icc_mode = input("ICC mode [auto/basic/none]: ").strip().lower()
                    use_basic = (icc_mode == "basic")
                    use_none = (icc_mode == "none")
                target_icc = input("Target ICC (sRGB/custom/empty): ").strip()
                if target_icc.lower() == "custom":
                    target_icc = input("Enter ICC profile path: ").strip()
                cleanup_input = input("Skip ICC cleanup? [y/N]: ").strip().lower()
                no_cleanup = cleanup_input.startswith('y')
                # Thumbnail reconstruction handling
                th_default = getattr(self.config.config, 'last_thumbnail_handling', None) or "include"
                th_input = input(f"Thumbnail handling for multi-page TIFFs (ignore/include/generate) [{th_default}]: ").strip().lower() or th_default
                thumbnail_handling = th_input if th_input in ["ignore", "include", "generate"] else "include"
                if thumbnail_handling == "generate":
                    print("generate is not yet implemented; using include")
                    thumbnail_handling = "include"
                self.config.save_last_session(thumbnail_handling=thumbnail_handling)
                ts_default = getattr(self.config.config, 'last_thumbnail_suffix', None) or "_thumbnail"
                ts_input = input(f"Thumbnail suffix [{ts_default}]: ").strip()
                thumbnail_suffix = ts_input if ts_input else ts_default
                self.config.save_last_session(thumbnail_suffix=thumbnail_suffix)
                no_recon_default = "y" if self.config.config.last_no_reconstruct_multipage else "n"
                no_recon_input = input(f"Decode every JXL to its own TIFF (no multi-page reconstruction)? [{no_recon_default}/n]: ").strip().lower() or no_recon_default
                no_reconstruct_multipage = no_recon_input.startswith('y')
                self.config.save_last_session(no_reconstruct_multipage=no_reconstruct_multipage)
                dp_default = getattr(self.config.config, 'last_depth_policy', None) or "preserve_thumbnails"
                dp_input = input(f"Bit depth policy (force16/preserve_thumbnails/preserve_original) [{dp_default}]: ").strip().lower() or dp_default
                depth_policy = dp_input if dp_input in ["force16", "preserve_thumbnails", "preserve_original"] else "preserve_thumbnails"
                self.config.save_last_session(depth_policy=depth_policy)
                overwrite_mode = workflow.get('overwrite_mode', '2')
                delete_src_input = input("Delete source JXLs after conversion? [y/N]: ").strip().lower()
                delete_src = delete_src_input.startswith('y')

            if overwrite_mode == "1":
                overwrite, sync = True, False
            elif overwrite_mode == "2":
                overwrite, sync = False, True
            else:
                overwrite, sync = False, False

            advanced_options['matrix'] = use_matrix
            advanced_options['basic'] = use_basic
            advanced_options['none'] = use_none
            advanced_options['target_icc'] = target_icc if target_icc else None
            advanced_options['no_icc_cleanup'] = no_cleanup
            advanced_options['overwrite'] = overwrite
            advanced_options['sync'] = sync
            advanced_options['delete_source'] = delete_src
            advanced_options['thumbnail_handling'] = thumbnail_handling
            advanced_options['thumbnail_suffix'] = thumbnail_suffix
            advanced_options['no_reconstruct_multipage'] = no_reconstruct_multipage
            advanced_options['depth_policy'] = depth_policy

        else:
            if RICH_AVAILABLE and console:
                no_md5 = Confirm.ask("Skip MD5 verification? (faster)", default=False)
                no_verify = Confirm.ask("Skip validation? (faster, risky)", default=False)
                overwrite_mode = workflow.get('overwrite_mode', '2')
                delete_src = Confirm.ask("Delete source after conversion?", default=False)
                output_suffix = Prompt.ask("Output suffix (e.g., _converted)", default="")
            else:
                md5_input = input("Skip MD5 verification? [y/N]: ").strip().lower()
                no_md5 = md5_input.startswith('y')
                verify_input = input("Skip validation? [y/N]: ").strip().lower()
                no_verify = verify_input.startswith('y')
                overwrite_mode = workflow.get('overwrite_mode', '2')
                del_input = input("Delete source after? [y/N]: ").strip().lower()
                delete_src = del_input.startswith('y')
                output_suffix = input("Output suffix (e.g., _converted): ").strip()

            if overwrite_mode == "1":
                overwrite, sync = True, False
            elif overwrite_mode == "2":
                overwrite, sync = False, True
            else:
                overwrite, sync = False, False

            advanced_options['no_md5'] = no_md5
            advanced_options['no_verify'] = no_verify
            advanced_options['overwrite'] = overwrite
            advanced_options['sync'] = sync
            advanced_options['delete_source'] = delete_src
            advanced_options['output_suffix'] = output_suffix if output_suffix else None

        workflow['advanced_options'] = advanced_options
        self.config.save_last_session(advanced_options=advanced_options)
        return self._wizard_parameters_expert(workflow)

    def _wizard_parameters_expert(self, workflow: Dict) -> bool:
        """Step 6B: Expert Mode (free-form flags)"""
        if RICH_AVAILABLE and console:
            console.print("\n[bold cyan]Step 6B: Expert Mode[/bold cyan]")
            show_expert = Confirm.ask("Add custom command-line flags?", default=False)
        else:
            print("\n--- Step 6B: Expert Mode ---")
            expert_input = input("Add custom flags? [y/N]: ").strip().lower()
            show_expert = expert_input.startswith('y')

        if show_expert:
            if RICH_AVAILABLE and console:
                console.print("[dim]Enter any additional flags as they would appear on command line:[/dim]")
                console.print("[dim]Example: --strip --effort 10 --d50-patch warm[/dim]")
                expert_flags = Prompt.ask("Custom flags", default="")
            else:
                print("Enter additional flags (e.g., --strip --effort 10 --d50-patch warm):")
                expert_flags = input("> ").strip()

            workflow['expert_flags'] = expert_flags

        return True

    def _wizard_confirm(self, workflow: Dict) -> bool:
        """Step 7: Final Confirmation"""
        mode_names = {
            0: "In-place", 1: "Subfolder", 2: "Flat", 3: "Recursive subfolders",
            4: "Sibling (rename)", 5: "Suffix", 6: "EXPORT full", 7: "EXPORT only", 8: "DELETE originals"
        }

        extra_info = []
        if workflow.get('use_ram'):
            extra_info.append("RAM: Yes")
        if workflow.get('icc_profile'):
            extra_info.append(f"ICC: {workflow['icc_profile']}")
        staging_display = workflow['staging'] or "system default"
        extra_info.append(f"Staging: {staging_display}")
        if workflow.get('auto_mode_used'):
            extra_info.append("Auto Mode: Yes")
        if workflow.get('advanced_options'):
            extra_info.append("Advanced: Yes")
        if workflow.get('expert_flags'):
            extra_info.append("Expert: Yes")
        if workflow.get('dry_run'):
            extra_info.append("DRY RUN")

        origin = workflow['origin_format']
        dest = workflow['dest_format']

        # Handle manifest mode (99) specially
        if workflow.get('mode') == 99:
            manifest_path = workflow.get('manifest_path', 'unknown')
            manifest_entries = workflow.get('manifest_entries', [])
            if RICH_AVAILABLE and console:
                console.print("\n[bold cyan]Step 7: Summary (Manifest Mode)[/bold cyan]")
                table = Table.grid(expand=True)
                table.add_column(style="bold")
                table.add_column()
                table.add_row("Mode:", "Manifest (auto-detect per entry)")
                table.add_row("Manifest:", manifest_path)
                table.add_row("Entries:", str(len(manifest_entries)))
                table.add_row("Workers:", str(workflow['workers']))
                if extra_info:
                    table.add_row("Config:", ", ".join(extra_info))
                console.print(Panel(table, border_style="green"))
                console.print("\n[yellow]Type YES to confirm[/yellow]")
                confirm = Prompt.ask("Confirm")
                if confirm.upper() != "YES":
                    console.print("[dim]Cancelling...[/dim]\n")
                    return False
                return True
            else:
                print("\n--- Step 7: Summary (Manifest Mode) ---")
                print(f"Mode: Manifest (auto-detect per entry)")
                print(f"Manifest: {manifest_path}")
                print(f"Entries: {len(manifest_entries)}")
                print(f"Workers: {workflow['workers']}")
                print("\nType YES to confirm:")
                confirm = input("> ").strip()
                if confirm.upper() != "YES":
                    print("Cancelling...\n")
                    return False
                return True

        if RICH_AVAILABLE and console:
            console.print("\n[bold cyan]Step 7: Summary[/bold cyan]")
            table = Table.grid(expand=True)
            table.add_column(style="bold")
            table.add_column()
            table.add_row("Source:", origin.upper())
            table.add_row("Destination:", dest.upper() if dest else "?")
            table.add_row("Mode:", f"{workflow['mode']} - {mode_names.get(workflow['mode'])}")
            table.add_row("Directory:", workflow['input_dir'])
            adv = workflow.get('advanced_options', {})
            if adv.get('overwrite'):
                ow_label = "overwrite all"
            elif adv.get('sync'):
                ow_label = "sync (reconvert if newer)"
            else:
                ow_label = "sync (default)"
            table.add_row("If exists:", ow_label)
            table.add_row("Workers:", str(workflow['workers']))

            if origin == 'tiff' and dest == 'jxl':
                table.add_row("Distance:", str(workflow['distance']))
                if 'advanced_options' in workflow and workflow['advanced_options'].get('d50_patch'):
                    table.add_row("D50 Patch:", workflow['advanced_options']['d50_patch'])
            elif 'lossy' in workflow['conversion_type']:
                table.add_row("Quality:", str(workflow['quality']))
            elif origin == 'jxl' and dest == 'jpeg':
                # JXL->JPEG: show quality for AUTO and FORCE_LOSSY modes
                if workflow.get('conversion_type') in ['jxl_to_jpeg_auto', 'jxl_to_jpeg_force']:
                    table.add_row("Quality:", str(workflow.get('quality', 95)))
            elif origin == 'jxl' and dest == 'tiff':
                # JXL->TIFF: show preview option
                preview_status = "Yes" if workflow.get('add_preview', True) else "No"
                table.add_row("JPEG Preview:", preview_status)
            table.add_row("Effort:", str(workflow['effort']))

            if extra_info:
                table.add_row("Config:", ", ".join(extra_info))
            console.print(Panel(table, border_style="green"))

            console.print("\n[yellow]Type YES to confirm[/yellow]")
            confirm = Prompt.ask("Confirm")
            if confirm.upper() != "YES":
                console.print("[dim]Cancelling...[/dim]\n")
                return False
            return True
        else:
            print("\n--- Step 7: Summary ---")
            adv = workflow.get('advanced_options', {})
            if adv.get('overwrite'):
                ow_label = "overwrite all"
            elif adv.get('sync'):
                ow_label = "sync (reconvert if newer)"
            else:
                ow_label = "sync (default)"
            print(f"Source: {origin.upper()}")
            print(f"Destination: {dest.upper() if dest else '?'}")
            print(f"Mode: {workflow['mode']} - {mode_names.get(workflow['mode'])}")
            print(f"Directory: {workflow['input_dir']}")
            print(f"If exists: {ow_label}")
            print(f"Workers: {workflow['workers']}")

            if origin == 'tiff' and dest == 'jxl':
                print(f"Distance: {workflow['distance']}")
                if 'advanced_options' in workflow and workflow['advanced_options'].get('d50_patch'):
                    print(f"D50 Patch: {workflow['advanced_options']['d50_patch']}")
            elif 'lossy' in workflow['conversion_type']:
                print(f"Quality: {workflow['quality']}")
            elif origin == 'jxl' and dest == 'jpeg':
                # JXL->JPEG: show quality for AUTO and FORCE_LOSSY modes
                if workflow.get('conversion_type') in ['jxl_to_jpeg_auto', 'jxl_to_jpeg_force']:
                    print(f"Quality: {workflow.get('quality', 95)}")
            elif origin == 'jxl' and dest == 'tiff':
                # JXL->TIFF: show preview option
                preview_status = "Yes" if workflow.get('add_preview', True) else "No"
                print(f"JPEG Preview: {preview_status}")
            print(f"Effort: {workflow['effort']}")

            if extra_info:
                print(f"Config: {', '.join(extra_info)}")
            print("\nType YES to confirm:")
            confirm = input("> ").strip()
            if confirm.upper() != "YES":
                print("Cancelling...\n")
                return False
            return True

    def _execute_manifest_workflow(self, workflow: Dict, status: Dict[str, bool]) -> bool:
        """Execute workflow from manifest entries."""
        manifest_entries = workflow.get('manifest_entries', [])
        if not manifest_entries:
            self._print_error("No manifest entries found!")
            return False

        origin = workflow['origin_format']
        dest = workflow['dest_format']
        workers = workflow['workers']
        advanced = workflow.get('advanced_options', {})
        dry_run = workflow.get('dry_run', False)
        staging = workflow.get('staging')

        # Determine which script to use
        if origin == 'tiff' and dest == 'jxl':
            script = 'jxl_tiff_encoder.py'
        elif origin == 'jxl' and dest == 'tiff':
            script = 'jxl_tiff_decoder.py'
        else:
            script = 'jxl_jpeg_transcoder.py'

        # Resolve script path relative to SCRIPT_DIR (not CWD)
        script_path = SCRIPT_DIR / script
        if not script_path.exists():
            self._print_error(f"Script not found: {script_path}")
            return False
        script = str(script_path)

        total_entries = len(manifest_entries)
        ok_count = 0
        skip_count = 0
        error_count = 0

        if RICH_AVAILABLE and console:
            console.print(f"\n[bold cyan]Executing manifest: {total_entries} entry(ies)[/bold cyan]")
            console.print(f"[dim]Script: {script} | Workers: {workers}[/dim]")
            if dry_run:
                console.print("[yellow]DRY RUN MODE[/yellow]")
            console.print()
        else:
            print(f"\nExecuting manifest: {total_entries} entry(ies)")
            print(f"Script: {script} | Workers: {workers}")
            if dry_run:
                print("DRY RUN MODE")
            print()

        # Create analyzer once outside the loop for efficiency
        analyzer = FolderAnalyzer(Path("."), origin, dest, self.config.config.export_marker)
        
        for i, (source, dest_path, entry_mode) in enumerate(manifest_entries, 1):
            # Preserve the mode the manifest was generated with (important for modes 6/7)
            detected_mode = analyzer.detect_mode_for_entry(source, dest_path, original_mode=entry_mode)

            if RICH_AVAILABLE and console:
                console.print(f"[{i}/{total_entries}] [bold]{detected_mode}[/bold] | {self._truncate_path(source, 40)} → {self._truncate_path(dest_path, 40)}")
            else:
                print(f"[{i}/{total_entries}] Mode {detected_mode} | {source} → {dest_path}")

            if dry_run:
                if RICH_AVAILABLE and console:
                    console.print(f"  [blue]DRY: would process[/blue]")
                else:
                    print(f"  DRY: would process")
                skip_count += 1
                continue

            # Build command for this entry
            cmd = self._build_manifest_entry_cmd(
                script=script,
                source=source,
                dest_path=dest_path,
                mode=detected_mode,
                origin=origin,
                dest=dest,
                workers=workers,
                workflow=workflow,
                advanced=advanced
            )

            if cmd is None:
                error_count += 1
                continue

            # Execute
            result = self._run_subprocess(cmd)
            if result:
                ok_count += 1
            else:
                error_count += 1

        # Summary
        if RICH_AVAILABLE and console:
            console.print()
            console.print(f"[bold]Manifest complete:[/bold] {ok_count} OK | {skip_count} skipped | {error_count} errors")
        else:
            print(f"\nManifest complete: {ok_count} OK | {skip_count} skipped | {error_count} errors")

        return error_count == 0

    def _build_manifest_entry_cmd(self, script: str, source: str, dest_path: str, mode: int,
                                   origin: str, dest: str, workers: int,
                                   workflow: Dict, advanced: Dict) -> Optional[List]:
        """Build command line for a single manifest entry."""
        cmd = [sys.executable, script, source, dest_path, '--mode', str(mode), '--workers', str(workers)]

        # Pass configured export marker so scripts match the wrapper's detection.
        export_marker = self.config.config.export_marker
        if export_marker and export_marker != "_EXPORT":
            cmd.extend(['--export-marker', export_marker])

        if origin == 'tiff' and dest == 'jxl':
            distance = workflow.get('distance', 0.1)
            cmd.extend(['--distance', str(distance)])
            cmd.extend(['--effort', str(workflow.get('effort', 7))])

            if workflow.get('use_ram'):
                cmd.append('--ram')
            else:
                cmd.append('--no-ram')

            if advanced.get('strip'):
                cmd.append('--strip')
            if advanced.get('d50_patch'):
                cmd.extend(['--d50-patch', advanced['d50_patch']])
            if advanced.get('overwrite'):
                cmd.append('--overwrite')
            if advanced.get('sync'):
                cmd.append('--sync')
            if workflow.get('staging'):
                cmd.extend(['--staging', workflow['staging']])
            if advanced.get('encode_tag'):
                cmd.extend(['--encode-tag', advanced['encode_tag']])
            if advanced.get('embed_thumbnail'):
                cmd.append('--embed-thumbnail')
            if advanced.get('delete_source'):
                cmd.append('--delete-source')
            if advanced.get('multipage_mode'):
                cmd.extend(['--multipage-mode', advanced['multipage_mode']])
            if advanced.get('thumbnail_mode'):
                cmd.extend(['--thumbnail-mode', advanced['thumbnail_mode']])
            if advanced.get('thumbnail_suffix'):
                cmd.extend(['--thumbnail-suffix', advanced['thumbnail_suffix']])

        elif origin == 'jxl' and dest == 'tiff':
            cmd.extend(['--compression', workflow.get('compression', 'zip')])
            cmd.extend(['--depth', str(workflow.get('bit_depth', 16))])

            if advanced.get('matrix'):
                cmd.append('--matrix')
            elif advanced.get('none'):
                cmd.append('--none')
            elif advanced.get('basic'):
                cmd.append('--basic')

            if advanced.get('target_icc'):
                cmd.extend(['--target-icc', advanced['target_icc']])
            if advanced.get('no_icc_cleanup'):
                cmd.append('--no-icc-cleanup')
            if advanced.get('overwrite'):
                cmd.append('--overwrite')
            if advanced.get('sync'):
                cmd.append('--sync')
            if workflow.get('staging'):
                cmd.extend(['--staging', workflow['staging']])
            if advanced.get('delete_source'):
                cmd.append('--delete-source')
            if not workflow.get('add_preview', True):
                cmd.append('--no-preview')
            if advanced.get('thumbnail_handling'):
                cmd.extend(['--thumbnail-handling', advanced['thumbnail_handling']])
            if advanced.get('thumbnail_suffix'):
                cmd.extend(['--thumbnail-suffix', advanced['thumbnail_suffix']])
            if advanced.get('no_reconstruct_multipage'):
                cmd.append('--no-reconstruct-multipage')
            if advanced.get('depth_policy'):
                cmd.extend(['--depth-policy', advanced['depth_policy']])

        else:
            # JPEG/JXL/PNG transcoder
            conv_type = workflow.get('conversion_type', '')

            if conv_type == 'transcode_lossless':
                # JPEG->JXL lossless
                cmd.append('--force-transcode')
            elif conv_type == 'convert_lossy':
                # JPEG->JXL lossy
                cmd.append('--force-convert')
                cmd.extend(['--distance', str(workflow.get('distance', 1.0))])
            elif conv_type == 'jxl_to_jpeg_auto':
                # JXL->JPEG: auto-detect per-file (no extra flag needed)
                pass
            elif conv_type == 'jxl_to_jpeg_lossless':
                # JXL->JPEG: force lossless transcode (requires jbrd)
                cmd.append('--force-transcode')
                cmd.append('--decode')
            elif conv_type == 'jxl_to_jpeg_force':
                # JXL->JPEG: force lossy
                cmd.append('--force-convert')
                cmd.append('--decode')
            elif conv_type == 'jxl_to_png':
                # JXL->PNG: force convert (don't transcode even if jbrd present)
                cmd.append('--force-convert')
                cmd.append('--decode')

            # Add quality for JXL->JPEG/PNG (used in convert mode)
            if origin == 'jxl' and dest in ['jpeg', 'png']:
                cmd.extend(['--quality', str(workflow.get('quality', 95))])

            cmd.extend(['--effort', str(workflow.get('effort', 7))])

            if workflow.get('icc_profile'):
                cmd.extend(['--icc-profile', workflow['icc_profile']])
            if workflow.get('staging'):
                cmd.extend(['--staging', workflow['staging']])
            if advanced.get('no_md5'):
                cmd.append('--no-md5')
            if advanced.get('no_verify'):
                cmd.append('--no-verify')
            if advanced.get('overwrite'):
                cmd.append('--overwrite')
            if advanced.get('sync'):
                cmd.append('--sync')
            if advanced.get('delete_source'):
                cmd.append('--delete-source')
            if advanced.get('output_suffix'):
                cmd.extend(['--output-suffix', advanced['output_suffix']])

            if dest == 'png':
                cmd.extend(['--format', 'png'])
            elif dest in ['jpeg', 'jpg']:
                cmd.extend(['--format', 'jpeg'])

            if dest == 'png' and workflow.get('bit_depth'):
                cmd.extend(['--bit-depth', str(workflow['bit_depth'])])


        if workflow.get('dry_run'):
            cmd.append('--dry-run')

        if workflow.get('expert_flags'):
            try:
                import shlex
                expert_args = shlex.split(workflow['expert_flags'], posix=(os.name != 'nt'))
                cmd.extend(expert_args)
            except ValueError:
                cmd.extend(workflow['expert_flags'].split())

        return cmd

    def _run_subprocess(self, cmd: List) -> bool:
        """Run subprocess and return True if success."""
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace"
            )

            for line in process.stdout:
                line = line.strip()
                if line:
                    if RICH_AVAILABLE and console:
                        if "[OK]" in line or "✓" in line:
                            console.print(f"  [green]{line}[/green]")
                        elif "[ERROR]" in line or "✗" in line:
                            console.print(f"  [red]{line}[/red]")
                        elif "[WARNING]" in line or "⚠" in line:
                            console.print(f"  [yellow]{line}[/yellow]")
                        elif "DRY" in line:
                            console.print(f"  [blue]{line}[/blue]")
                        else:
                            console.print(f"  {line}")
                    else:
                        print(f"  {line}")

            process.wait(timeout=3600)  # 1 hour timeout
            return process.returncode == 0

        except subprocess.TimeoutExpired:
            process.kill()
            if RICH_AVAILABLE and console:
                console.print("  [red]Timeout: Process killed after 1 hour[/red]")
            else:
                print("  Timeout: Process killed after 1 hour")
            return False

        except Exception as e:
            if RICH_AVAILABLE and console:
                console.print(f"  [red]Error: {e}[/red]")
            else:
                print(f"  Error: {e}")
            return False

    def execute_workflow(self, workflow: Dict, status: Dict[str, bool]) -> bool:
        """Execute the workflow - Build command dynamically"""

        # Handle manifest mode (mode 99)
        if workflow.get('mode') == 99:
            return self._execute_manifest_workflow(workflow, status)

        origin = workflow['origin_format']
        dest = workflow['dest_format']
        mode = workflow['mode']
        input_dir = workflow['input_dir']
        workers = workflow['workers']
        advanced = workflow.get('advanced_options', {})
        expert_flags = workflow.get('expert_flags', '')

        if origin == 'tiff' and dest == 'jxl':
            script = str(SCRIPT_DIR / 'jxl_tiff_encoder.py')
            cmd = [
                sys.executable, script,
                input_dir,
                '--mode', str(mode),
                '--workers', str(workers)
            ]

            export_marker = self.config.config.export_marker
            if export_marker and export_marker != "_EXPORT":
                cmd.extend(['--export-marker', export_marker])

            # Mode 2: flat output folder
            if mode == 2:
                output_dir = workflow.get('mode_config', {}).get('output_dir')
                if output_dir:
                    Path(output_dir).mkdir(parents=True, exist_ok=True)
                    cmd.append(output_dir)

            distance = workflow.get('distance', 0.1)
            cmd.extend(['--distance', str(distance)])
            cmd.extend(['--effort', str(workflow['effort'])])

            if workflow.get('use_ram'):
                cmd.append('--ram')
            else:
                cmd.append('--no-ram')

            if advanced.get('strip'):
                cmd.append('--strip')
            if advanced.get('d50_patch'):
                cmd.extend(['--d50-patch', advanced['d50_patch']])
            if advanced.get('overwrite'):
                cmd.append('--overwrite')
            if advanced.get('delete_source'):
                cmd.append('--delete-source')
            if advanced.get('sync'):
                cmd.append('--sync')
            if workflow.get('staging'):
                cmd.extend(['--staging', workflow['staging']])
            if advanced.get('encode_tag'):
                cmd.extend(['--encode-tag', advanced['encode_tag']])
            if advanced.get('embed_thumbnail'):
                cmd.append('--embed-thumbnail')
            if advanced.get('multipage_mode'):
                cmd.extend(['--multipage-mode', advanced['multipage_mode']])
            if advanced.get('thumbnail_mode'):
                cmd.extend(['--thumbnail-mode', advanced['thumbnail_mode']])
            if advanced.get('thumbnail_suffix'):
                cmd.extend(['--thumbnail-suffix', advanced['thumbnail_suffix']])

        elif origin == 'jxl' and dest == 'tiff':
            script = str(SCRIPT_DIR / 'jxl_tiff_decoder.py')
            cmd = [
                sys.executable, script,
                input_dir,
                '--mode', str(mode),
                '--workers', str(workers)
            ]

            export_marker = self.config.config.export_marker
            if export_marker and export_marker != "_EXPORT":
                cmd.extend(['--export-marker', export_marker])

            # Mode 2: flat output folder
            if mode == 2:
                output_dir = workflow.get('mode_config', {}).get('output_dir')
                if output_dir:
                    Path(output_dir).mkdir(parents=True, exist_ok=True)
                    cmd.append(output_dir)

            cmd.extend(['--compression', workflow['compression']])
            cmd.extend(['--depth', str(workflow['bit_depth'])])
            
            # Preview option
            if not workflow.get('add_preview', True):
                cmd.append('--no-preview')

            if advanced.get('matrix'):
                cmd.append('--matrix')
            elif advanced.get('none'):
                cmd.append('--none')
            elif advanced.get('basic'):
                cmd.append('--basic')

            if advanced.get('target_icc'):
                cmd.extend(['--target-icc', advanced['target_icc']])
            if advanced.get('no_icc_cleanup'):
                cmd.append('--no-icc-cleanup')
            if advanced.get('delete_source'):
                cmd.append('--delete-source')
            if advanced.get('overwrite'):
                cmd.append('--overwrite')
            if advanced.get('sync'):
                cmd.append('--sync')
            if workflow.get('staging'):
                cmd.extend(['--staging', workflow['staging']])
            if advanced.get('thumbnail_handling'):
                cmd.extend(['--thumbnail-handling', advanced['thumbnail_handling']])
            if advanced.get('thumbnail_suffix'):
                cmd.extend(['--thumbnail-suffix', advanced['thumbnail_suffix']])
            if advanced.get('no_reconstruct_multipage'):
                cmd.append('--no-reconstruct-multipage')
            if advanced.get('depth_policy'):
                cmd.extend(['--depth-policy', advanced['depth_policy']])

        else:
            script = str(SCRIPT_DIR / 'jxl_jpeg_transcoder.py')
            
            conv_type = workflow.get('conversion_type', '')
            
            cmd = [
                sys.executable, script,
                input_dir,
                '--mode', str(mode),
                '--workers', str(workers)
            ]

            export_marker = self.config.config.export_marker
            if export_marker and export_marker != "_EXPORT":
                cmd.extend(['--export-marker', export_marker])

            # Mode 2: flat output folder
            if mode == 2:
                output_dir = workflow.get('mode_config', {}).get('output_dir')
                if output_dir:
                    Path(output_dir).mkdir(parents=True, exist_ok=True)
                    cmd.append(output_dir)

            # Handle conversion type flags
            # Note: transcoder auto-detects direction based on file extensions
            if conv_type == 'transcode_lossless':
                # JPEG->JXL lossless
                cmd.append('--force-transcode')
            elif conv_type == 'convert_lossy':
                # JPEG->JXL lossy
                cmd.append('--force-convert')
                cmd.extend(['--distance', str(workflow.get('distance', 1.0))])
            elif conv_type == 'jxl_to_jpeg_auto':
                # JXL->JPEG: auto-detect per-file (new in transcoder)
                # No flag needed - transcoder will check jbrd for each file
                pass
            elif conv_type == 'jxl_to_jpeg_lossless':
                # JXL->JPEG: force lossless transcode (requires jbrd)
                cmd.append('--force-transcode')
                cmd.append('--decode')
            elif conv_type == 'jxl_to_jpeg_force':
                # JXL->JPEG: force lossy
                cmd.append('--force-convert')
                cmd.append('--decode')
            elif conv_type == 'jxl_to_png':
                # JXL->PNG: force convert (don't transcode even if jbrd present)
                # User explicitly chose PNG, respect that choice
                cmd.append('--force-convert')
                cmd.append('--decode')
            # Note: --format is added later based on dest, no need to duplicate here
            
            # Add quality for JXL->JPEG/PNG (used in convert mode)
            if origin == 'jxl' and dest in ['jpeg', 'png']:
                cmd.extend(['--quality', str(workflow.get('quality', 95))])

            cmd.extend(['--effort', str(workflow['effort'])])

            if workflow.get('icc_profile'):
                cmd.extend(['--icc-profile', workflow['icc_profile']])

            if workflow.get('staging'):
                cmd.extend(['--staging', workflow['staging']])

            if advanced.get('no_md5'):
                cmd.append('--no-md5')
            if advanced.get('no_verify'):
                cmd.append('--no-verify')
            if advanced.get('overwrite'):
                cmd.append('--overwrite')
            if advanced.get('sync'):
                cmd.append('--sync')
            if advanced.get('delete_source'):
                cmd.append('--delete-source')
            if advanced.get('output_suffix'):
                cmd.extend(['--output-suffix', advanced['output_suffix']])

            if dest == 'png':
                cmd.extend(['--format', 'png'])
            elif dest in ['jpeg', 'jpg']:
                cmd.extend(['--format', 'jpeg'])

            if dest == 'png' and workflow.get('bit_depth'):
                cmd.extend(['--bit-depth', str(workflow['bit_depth'])])

        if workflow.get('dry_run'):
            cmd.append('--dry-run')

        if expert_flags:
            try:
                import shlex
                expert_args = shlex.split(expert_flags)
                cmd.extend(expert_args)
            except ValueError:
                cmd.extend(expert_flags.split())

        if not Path(script).exists():
            self._print_error(f"Script not found: {script}")
            self._print_error("Ensure scripts are in the same folder as jxl_photo_v2.py")
            return False

        if RICH_AVAILABLE and console:
            console.print(f"\n[bold cyan]Executing:[/bold cyan]")
            console.print(f"[dim]{' '.join(cmd)}[/dim]\n")
        else:
            print(f"\nExecuting: {' '.join(cmd)}\n")

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace"
            )

            for line in process.stdout:
                line = line.strip()
                if line:
                    if RICH_AVAILABLE and console:
                        if "[OK]" in line or "Processing" in line or "✓" in line:
                            console.print(f"[green]{line}[/green]")
                        elif "[ERROR]" in line or "Error" in line or "✗" in line:
                            console.print(f"[red]{line}[/red]")
                        elif "[WARNING]" in line or "⚠" in line:
                            console.print(f"[yellow]{line}[/yellow]")
                        elif "DRY RUN" in line or "simulation" in line.lower():
                            console.print(f"[blue]{line}[/blue]")
                        else:
                            console.print(line)
                    else:
                        print(line)

            process.wait(timeout=3600)  # 1 hour timeout

            if process.returncode == 0:
                self._print_success("✓ Conversion completed successfully!\n")
                return True
            else:
                self._print_error(f"\n✗ Conversion failed (code {process.returncode})")
                return False

        except subprocess.TimeoutExpired:
            process.kill()
            self._print_error("Timeout: Process killed after 1 hour")
            return False
        except FileNotFoundError:
            self._print_error(f"Script not found: {script}")
            return False
        except Exception as e:
            self._print_error(f"Error executing: {e}")
            return False

    def _print_success(self, message: str) -> None:
        if RICH_AVAILABLE and console:
            console.print(f"[green]✓[/green] {message}")
        else:
            print(f"✓ {message}")

    def _print_error(self, message: str) -> None:
        if RICH_AVAILABLE and console:
            console.print(f"[red]✗[/red] {message}")
        else:
            print(f"✗ {message}")


def main():
    parser = argparse.ArgumentParser(description="JXL Tools v2 - JPEG XL Processing with Auto Mode")
    parser.add_argument("--recheck", action="store_true", help="Force dependency recheck")
    args = parser.parse_args()

    config = ConfigManager()
    checker = DependencyChecker(config)
    menu = InteractiveMenu(config, checker)

    force_check = args.recheck or not config.config.dependencies_checked
    status = checker.check_dependencies(force=force_check)

    menu.display_status(status)

    if not status.get('cjxl') and not status.get('djxl'):
        print("\nERROR: cjxl/djxl not found!")
        sys.exit(1)

    # Main loop
    while True:
        has_last = bool(config.config.last_input_dir)
        choice = menu.show_main_menu(has_last)

        if choice == "0":
            print("Exiting...")
            break

        elif choice == "1":
            workflow = menu.run_wizard(status)
            if workflow:
                origin = workflow['origin_format']
                dest = workflow['dest_format']

                if origin == 'tiff' and dest == 'jxl':
                    saved_distance = workflow.get('distance') if workflow.get('distance') is not None else 0.1
                    saved_quality = None
                elif 'lossy' in workflow['conversion_type']:
                    saved_quality = workflow.get('quality') if workflow.get('quality') is not None else 95
                    saved_distance = None
                else:
                    saved_quality = config.config.default_quality
                    saved_distance = None

                config.save_last_session(
                    workflow['input_dir'],
                    str(workflow['mode']),
                    workflow['workers'],
                    workflow['staging'],
                    workflow['effort'],
                    saved_quality,
                    saved_distance,
                    workflow['origin_format'],
                    workflow['dest_format'],
                    workflow['conversion_type'],
                    workflow.get('advanced_options', {}).get('d50_patch'),
                    workflow.get('advanced_options', {}).get('encode_tag')
                )

                if RICH_AVAILABLE and console:
                    execute_now = Confirm.ask("\nConfiguration saved! Execute now?", default=True)
                else:
                    exec_input = input("\nExecute now? [Y/n]: ").strip().lower()
                    execute_now = not exec_input.startswith('n')

                if execute_now:
                    success = menu.execute_workflow(workflow, status)
                    if success:
                        if RICH_AVAILABLE and console:
                            again = Confirm.ask("\nConvert another folder?", default=False)
                            if not again:
                                break
                        else:
                            again = input("\nConvert another folder? [y/N]: ").strip().lower()
                            if not again.startswith('y'):
                                break
                    else:
                        if RICH_AVAILABLE and console:
                            retry = Confirm.ask("Try again?", default=True)
                            if not retry:
                                break
                        else:
                            retry = input("Try again? [Y/n]: ").strip().lower()
                            if retry.startswith('n'):
                                break
                else:
                    print("\nConfiguration saved. Use 'Repeat last workflow' to execute later.")
            else:
                continue

        elif choice == "2" and has_last:
            last = config.config
            last_dir = last.last_input_dir or ""
            last_mode = last.last_output_mode or "0"
            last_workers = last.last_workers or 4
            last_staging = last.last_staging or ""
            last_effort = last.last_effort or 7
            last_quality = last.last_quality or 95
            last_distance = last.last_distance
            last_origin = last.last_origin_format or "tiff"
            last_dest = last.last_dest_format or ("jxl" if last_origin != "jxl" else "jpeg")
            last_conv_type = last.last_conversion_type or ""
            last_d50_patch = last.last_d50_patch or "auto"
            last_encode_tag = last.last_encode_tag or "xmp"

            if RICH_AVAILABLE and console:
                settings = [
                    ["Input folder", last_dir],
                    ["Source", last_origin.upper()],
                    ["Destination", last_dest.upper()],
                    ["Mode", last_mode],
                    ["Workers", str(last_workers)],
                    ["Effort", str(last_effort)],
                ]
                # Show relevant field based on origin format
                # TIFF→JXL: uses distance | JPEG→JXL: uses quality | JXL→?: may use quality if JPEG dest
                if last_origin == 'tiff' and last_distance is not None:
                    settings.append(["Distance", f"{last_distance}"])
                elif last_origin == 'jpeg':
                    settings.append(["Quality", str(last_quality)])
                elif last_origin == 'jxl' and last_quality is not None and last_conv_type in ['jxl_to_jpeg_auto', 'jxl_to_jpeg_force']:
                    settings.append(["Quality", str(last_quality)])
                settings.append(["Staging", last_staging or "(none)"])
                
                t = Table(box=BOX_SIMPLE, show_header=False, pad_edge=False)
                t.add_column("", style="cyan")
                t.add_column("")
                for row in settings:
                    t.add_row(*row)
                console.print(Panel(t, title="[bold]Last Workflow Settings[/bold]", border_style="green"))
            else:
                print("\n=== Last Workflow Settings ===")
                print(f"  Input folder: {last_dir}")
                print(f"  Source:       {last_origin.upper()}")
                print(f"  Destination:  {last_dest.upper()}")
                print(f"  Mode:         {last_mode}")
                print(f"  Workers:      {last_workers}")
                print(f"  Effort:       {last_effort}")
                # Show relevant field based on origin format
                if last_origin == 'tiff' and last_distance is not None:
                    print(f"  Distance:     {last_distance}")
                elif last_origin == 'jpeg':
                    print(f"  Quality:      {last_quality}")
                elif last_origin == 'jxl' and last_quality is not None:
                    print(f"  Quality:      {last_quality}")
                print(f"  Staging:      {last_staging or '(none)'}")
                print()

            if RICH_AVAILABLE and console:
                new_folder = Prompt.ask(f"\n[bold cyan]Input folder[/bold cyan]", default=last_dir).strip()
            else:
                new_folder = input(f"\nInput folder [{last_dir}]: ").strip()

            if not new_folder:
                new_folder = last_dir

            input_path = Path(new_folder)
            if not input_path.exists():
                if RICH_AVAILABLE and console:
                    console.print(f"[red]Folder not found: {new_folder}[/red]")
                else:
                    print(f"Folder not found: {new_folder}")
                continue

            origin = last_origin

            if RICH_AVAILABLE and console:
                console.print("Existing file handling: [1] overwrite all | [2] sync (reconvert if newer)")
                ow_choice = Prompt.ask("If exists", choices=["1", "2"], default="2")
            else:
                ow_choice = input("If exists (1=overwrite, 2=sync) [2]: ").strip() or "2"

            if ow_choice == "1":
                overwrite, sync = True, False
            else:
                overwrite, sync = False, True

            if RICH_AVAILABLE and console:
                proceed = Confirm.ask(f"\n[bold]Proceed with this workflow?[/bold]", default=True)
            else:
                resp = input(f"\nProceed with this workflow? [Y/n]: ").strip().lower()
                proceed = not resp.startswith('n')

            if not proceed:
                continue

            # Separate distance (TIFF) and quality (JPEG) - don't mix!
            if origin == 'tiff':
                workflow = {
                    'input_dir': new_folder,
                    'mode': int(last_mode or 0),
                    'workers': last_workers or 4,
                    'staging': last_staging,
                    'effort': last_effort or 7,
                    'distance': last_distance if last_distance is not None else 0.1,
                    'overwrite_mode': ow_choice,
                }
            else:
                workflow = {
                    'input_dir': new_folder,
                    'mode': int(last_mode or 0),
                    'workers': last_workers or 4,
                    'staging': last_staging,
                    'effort': last_effort or 7,
                    'quality': last_quality or 95,
                    'overwrite_mode': ow_choice,
                }

            if workflow['mode'] == 2:
                output_dir = config.config.last_mode_config.get('output_dir') if config.config.last_mode_config else None
                workflow['mode_config'] = {'output_dir': output_dir or str(input_path.parent / "output")}
            elif workflow['mode'] in (6, 7):
                marker = config.config.last_mode_config.get('export_marker') if config.config.last_mode_config else None
                workflow['mode_config'] = {'export_marker': marker} if marker else {}
            else:
                workflow['mode_config'] = dict(config.config.last_mode_config or {})

            workflow['origin_format'] = origin
            workflow['dest_format'] = last_dest
            workflow['selected_files'] = []
            workflow['use_ram'] = config.config.last_use_ram if config.config.last_use_ram is not None else True
            workflow['icc_profile'] = None
            workflow['compression'] = config.config.last_compression or 'zip'
            workflow['bit_depth'] = config.config.last_bit_depth or 16
            workflow['dry_run'] = False
            fallback_advanced = {
                'overwrite': overwrite,
                'sync': sync,
                'd50_patch': last_d50_patch if origin == 'tiff' else None,
                'encode_tag': last_encode_tag if origin == 'tiff' else None,
                'embed_thumbnail': config.config.last_jpeg_thumbnail if origin == 'tiff' else None,
                'multipage_mode': config.config.last_multipage_mode or 'ignore',
                'thumbnail_mode': config.config.last_thumbnail_mode or 'exclude',
                'thumbnail_suffix': config.config.last_thumbnail_suffix or '_thumbnail',
                'thumbnail_handling': config.config.last_thumbnail_handling or 'include',
                'no_reconstruct_multipage': bool(config.config.last_no_reconstruct_multipage),
                'depth_policy': config.config.last_depth_policy or 'preserve_thumbnails',
                'matrix': False,
                'basic': False,
                'none': False,
                'target_icc': None,
                'no_icc_cleanup': False,
            }
            workflow['advanced_options'] = config.config.last_advanced_options or fallback_advanced
            # Ensure overwrite/sync reflect the choice just made in this dialog,
            # not a stale value from a previous session's last_advanced_options.
            workflow['advanced_options']['overwrite'] = overwrite
            workflow['advanced_options']['sync'] = sync
            workflow['expert_flags'] = ''
            workflow['auto_mode_used'] = False
            
            # Use saved conversion type or fallback to defaults
            if last_conv_type:
                workflow['conversion_type'] = last_conv_type
            elif origin == 'jpeg':
                workflow['conversion_type'] = 'transcode_lossless'
            elif origin == 'tiff':
                workflow['conversion_type'] = 'jxl_tiff_encoder'
            elif origin == 'jxl' and last_dest == 'tiff':
                workflow['conversion_type'] = 'jxl_tiff_decoder'
            elif origin == 'jxl' and last_dest == 'jpeg':
                # Default to lossy if no previous conversion type saved
                workflow['conversion_type'] = 'jxl_to_jpeg_force'
            elif origin == 'jxl' and last_dest == 'png':
                workflow['conversion_type'] = 'jxl_to_png'
            else:
                workflow['conversion_type'] = 'jxl_to_jpeg_force'

            menu.execute_workflow(workflow, status)

        elif choice == "3":
            status = checker.check_dependencies(force=True)
            menu.display_status(status)

        elif choice == "4":
            menu.edit_settings()

        elif choice == "5":
            if RICH_AVAILABLE and console:
                confirm = Confirm.ask(
                    "[red]This will erase all saved settings. Continue?[/red]",
                    default=False
                )
            else:
                confirm_input = input("\nErase all settings? [y/N]: ").strip().lower()
                confirm = confirm_input == 'y'

            if confirm:
                try:
                    if config.config_path.exists():
                        config.config_path.unlink()
                        if RICH_AVAILABLE and console:
                            console.print("[green]✓ Settings erased![/green]")
                        else:
                            print("✓ Settings erased!")

                    config.config = ToolConfig()
                    config.save_config()

                    status = checker.check_dependencies(force=True)
                    menu.display_status(status)
                except Exception as e:
                    menu._print_error(f"Error erasing: {e}")

        elif choice == "6":
            script_config = SCRIPT_DIR / ".jxl_tools_config.json"
            if platform.system() == "Windows":
                user_profile_dir = Path(os.environ.get("USERPROFILE", Path.home()))
            else:
                user_profile_dir = Path.home()
            user_config = user_profile_dir / ".jxl_tools_config.json"

            if script_config.exists():
                action = "move to User Profile"
                target = user_config
                source = script_config
            elif user_config.exists():
                action = "move to script folder"
                target = script_config
                source = user_config
            else:
                if RICH_AVAILABLE and console:
                    console.print("[yellow]No settings file found.[/yellow]")
                else:
                    print("No settings file found.")
                continue

            if RICH_AVAILABLE and console:
                confirm = Confirm.ask(
                    f"[yellow]Move settings to {action}?[/yellow]",
                    default=True
                )
            else:
                confirm_input = input(f"\nMove settings to {action}? [Y/n]: ").strip().lower()
                confirm = not confirm_input.startswith('n')

            if confirm:
                try:
                    shutil.move(str(source), str(target))
                    config.config_path = target
                    if RICH_AVAILABLE and console:
                        console.print(f"[green]✓ Settings moved to {target.parent}[/green]")
                    else:
                        print(f"✓ Settings moved to {target.parent}")
                except Exception as e:
                    menu._print_error(f"Error moving: {e}")
            continue


if __name__ == "__main__":
    main()
