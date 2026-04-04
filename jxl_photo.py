#!/usr/bin/env python3
"""
jxl_photo.py - Interactive wrapper for JPEG XL processing toolkit
Cross-platform configuration manager with smart dependency detection
"""

import argparse
import json
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
    last_distance: Optional[float] = None  # For TIFF->JXL distance
    last_origin_format: Optional[str] = None  # jpeg / tiff / jxl

    dependencies_checked: bool = False
    available_features: Dict[str, bool] = field(default_factory=dict)


class ConfigManager:
    """Manages persistent configuration for jxl_tools"""

    def __init__(self):
        self.config_path = self._get_config_path()
        self.config = ToolConfig()
        self._load_config()

    def _get_config_path(self) -> Path:
        # Check script folder first (if settings file exists there, use it)
        script_config = SCRIPT_DIR / ".jxl_tools_config.json"
        if script_config.exists():
            return script_config
        # Otherwise use USERPROFILE
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

    def save_last_session(self, input_dir: str, output_mode: str,
                         workers: int, staging: Optional[str],
                         effort: int = 7, quality: int = 95,
                         distance: Optional[float] = None,
                         origin_format: Optional[str] = None) -> None:
        self.config.last_input_dir = input_dir
        self.config.last_output_mode = output_mode
        self.config.last_workers = workers
        self.config.last_staging = staging
        self.config.last_effort = effort
        self.config.last_quality = quality
        self.config.last_distance = distance
        self.config.last_origin_format = origin_format
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
        """Check tifffile via import - always test directly"""
        try:
            import tifffile
            import numpy
            return True
        except ImportError:
            return False


class DependencyChecker:
    def __init__(self, config_manager: ConfigManager):
        self.config = config_manager

    def _check_pillow(self) -> bool:
        """Check pillow via import"""
        try:
            import PIL
            return True
        except ImportError:
            return False

    def _check_rich(self) -> bool:
        """Check rich via import"""
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
        """Always verify everything, no cache for tifffile"""
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

        # Always check Python libraries directly - never cache
        status['tifffile'] = self.config._check_tiff_support()
        status['numpy'] = status['tifffile']  # numpy is tifffile dependency
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

        for variant in variations:
            path = shutil.which(variant)
            if path:
                return path

        if platform.system() == "Windows":
            common_paths = [
                Path(r"C:\\Program Files\\libjxl\\bin"),
                Path(r"C:\\Program Files (x86)\\libjxl\\bin"),
                Path(r"C:\\Program Files\\ImageMagick"),
                Path.home() / "bin",
            ]
            for base_path in common_paths:
                if base_path.exists():
                    for ext in ['.exe', '.cmd', '']:
                        full_path = base_path / f"{cmd}{ext}"
                        if full_path.exists():
                            return str(full_path)
        return None

    def _test_tool_execution(self, path: str, args: List[str]) -> bool:
        try:
            result = subprocess.run([path] + args, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace",
                                  timeout=10, shell=False)
            return result.returncode == 0
        except:
            return False

    def format_status_line(self, status: Dict[str, bool]) -> str:
        """Format dependency status - SINGLE LINE"""
        icons = {
            'cjxl': '✓' if status.get('cjxl') else '✗',
            'djxl': '✓' if status.get('djxl') else '✗',
            'exiftool': '✓' if status.get('exiftool') else '✗',
            'magick': '✓' if status.get('magick') else '⚠',
            'tifffile': '✓' if status.get('tifffile') else '✗',
            'pillow': '✓' if status.get('pillow') else '✗',
            'rich': '✓' if status.get('rich') else '✗',
        }

        parts = [
            f"[{icons['cjxl']}] cjxl/djxl",
            f"[{icons['exiftool']}] exiftool",
            f"[{icons['magick']}] magick",
            f"[{icons['tifffile']}] tifffile",
            f"[{icons['pillow']}] pillow",
            f"[{icons['rich']}] rich",
        ]

        if not status.get('magick'):
            parts[2] += " (ICC off)"
        if not status.get('tifffile'):
            parts[3] += " (TIFF off)"
        if not status.get('pillow'):
            parts[4] += " (JPG previews off)"
        if not status.get('rich'):
            parts[5] += " (basic UI)"

        return " | ".join(parts)


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
                status = "" if available else "[dim](unavailable)[/dim]"
                table.add_row(key, desc, status)

            console.print(Panel(table, title="Main Menu", border_style="green"))

            while True:
                choice = Prompt.ask("Select option", choices=[o[0] for o in options if o[2]])
                if choice:
                    return choice
        else:
            print("\n--- Main Menu ---")
            for key, desc, available in options:
                status = "" if available else " [UNAVAILABLE]"
                print(f"[{key}] {desc}{status}")

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
        # Initialize with memorized or default values
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
            options.append(("1", "JXL Lossless", "Lossless JPEG⇌JXL transcoding (recommended)", "transcode_lossless"))
            options.append(("2", "JXL Lossy   ", "Lossy — JXL→JPEG decode loses quality", "convert_lossy"))
            # dest_format will be set below based on choice
        elif origin == "tiff" and status.get('cjxl'):
            options.append(("1", "d=0   ", "Lossless (exact replica)", "jxl_tiff_encoder_lossless"))
            options.append(("2", "d=0.1 ", "Near-lossless (recommended)", "jxl_tiff_encoder"))
            options.append(("3", "d=1.0 ", "Visually lossless", "jxl_tiff_encoder"))
            options.append(("4", "Custom", "Enter any value 0-15", "jxl_tiff_encoder"))
        elif origin == "jxl" and status.get('djxl'):
            options.append(("1", "JPEG", "Standard JPEG output", "jxl_to_jpeg_smart"))
            options.append(("2", "PNG ", "PNG with transparency", "jxl_to_png"))
            if status.get('tifffile'):
                options.append(("3", "TIFF", "Lossless master", "jxl_tiff_decoder"))

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

        # Set dest_format and distance_choice based on origin and choice
        if origin == "tiff":
            if choice == "1":
                workflow['distance_choice'] = "0"
                workflow['dest_format'] = 'jxl'
                workflow['quality'] = 0.0
            elif choice == "2":
                workflow['distance_choice'] = "0.1"
                workflow['dest_format'] = 'jxl'
                workflow['quality'] = 0.1
            elif choice == "3":
                workflow['distance_choice'] = "1.0"
                workflow['dest_format'] = 'jxl'
                workflow['quality'] = 1.0
            else:
                workflow['distance_choice'] = "custom"
                workflow['dest_format'] = 'jxl'

            # Ask distance and effort for TIFF->JXL options
            # Presets 1/2/3: show distance, ask only effort
            # Custom (4): ask distance input, then effort
            if RICH_AVAILABLE and console:
                if choice == "4":
                    dist_default = workflow.get('quality', 0.1)
                    dist_str = Prompt.ask("Distance (0.0-15.0, lower=better)", default=str(dist_default))
                    try:
                        workflow['quality'] = float(dist_str)
                    except:
                        workflow['quality'] = dist_default
                custom_effort = IntPrompt.ask("Effort (1-10, higher=smaller)", default=workflow['effort'])
                workflow['effort'] = max(1, min(custom_effort, 10))
            else:
                if choice == "4":
                    dist_default = workflow.get('quality', 0.1)
                    dist_str = input(f"Distance (0.0-15.0) [{dist_default}]: ").strip()
                    try:
                        workflow['quality'] = float(dist_str) if dist_str else dist_default
                    except:
                        workflow['quality'] = dist_default
                effort_input = input(f"Effort (1-10) [{workflow['effort']}]: ").strip()
                if effort_input.isdigit():
                    workflow['effort'] = max(1, min(int(effort_input), 10))
        elif origin == "jpeg":
            workflow['dest_format'] = 'jxl'
        elif origin == "jxl":
            if choice == "1":
                workflow['dest_format'] = 'jpeg'
            elif choice == "2":
                workflow['dest_format'] = 'png'
            else:
                workflow['dest_format'] = 'tiff'
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

        # Skip recursive scan here — individual scripts do proper file discovery
        # (especially for modes 6/7 which only scan inside _EXPORT folders)
        # File count will be shown by the underlying script when it runs
        workflow['selected_files'] = []

        return True

    def _show_mode_details(self, workflow: Dict) -> None:
        """Display detailed explanation of all organization modes."""
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

            ("6", f"Marker {export_marker} (full)",
             f"ONLY processes files INSIDE folders containing [green]{export_marker}[/green].\n"
             f"Recursively finds ALL {export_marker} folders and processes everything under each.\n"
             f"Ignores ALL files outside {export_marker} folders — nothing is processed from elsewhere.\n"
             f"Best for Capture One sessions where exported files live under _EXPORT."),

            ("7", f"Marker {export_marker} (specific subfolder)",
             f"Like mode 6, but ONLY processes files inside a specific subfolder of {export_marker}.\n"
             f"Default subfolder is [green]{export_marker}/JXL[/green] (configurable).\n"
             f"Files in other subfolders within {export_marker} are ignored.\n"
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
        else:
            print("\n=== Mode Detailed Explanations ===\n")
            for key, name, desc in details:
                print(f"[{key}] {name}")
                # Strip rich tags for plain text
                clean = (desc.replace("[bold ", "")
                         .replace("[/bold]", "")
                         .replace("[green]", "")
                         .replace("[/green]", "")
                         .replace("[cyan]", "")
                         .replace("[/cyan]", "")
                         .replace("[red]", "")
                         .replace("[/red]", "")
                         .replace("[bold red]", "")
                         .replace("[bold green]", "")
                         .replace("[bold blue]", ""))
                print(f"   {clean}\n")

        # Prompt for mode selection directly (no going back to Step 4 brief list)
        valid_choices = [d[0] for d in details]
        if RICH_AVAILABLE and console:
            console.print()
            while True:
                choice = Prompt.ask(
                    "Select mode",
                    choices=valid_choices
                )
                if choice in valid_choices:
                    break
        else:
            choice = input(f"Select mode (0-8) [{'/'.join(valid_choices)}]: ").strip()
            while choice not in valid_choices:
                choice = input(f"Select mode (0-8): ").strip()

        return choice

    def _wizard_select_mode(self, workflow: Dict) -> bool:
        """Step 4: Organization Modes 0-8"""
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
            ("6", f"Marker [green]{export_marker}[/green] (full)",
             f"ONLY files INSIDE [green]{export_marker}[/green] folders — ignores everything outside"),
            ("7", f"Marker [green]{export_marker}[/green] (specific subfolder)",
             f"Like mode 6, but only a specific subfolder (e.g. [green]{export_marker}/JXL[/green])"),
            ("8", "DELETE originals ⚠️",
             "DELETES source files after conversion - IRREVERSIBLE")
        ]

        if RICH_AVAILABLE and console:
            console.print(f"\n[bold cyan]Step 4: Organization Mode[/bold cyan]")
            console.print("[dim]Items in [green]green[/green] (e.g. 'converted_jxl', '_EXPORT') are configurable in option 4[/dim]")
            console.print("[dim]Other folder names (e.g. 'JXL_16bits', '16B_TIFF') require editing the scripts directly[/dim]")
            for key, name, desc in modes:
                style = "red" if key == "8" else "green"
                console.print(f"[{key}] [bold {style}]{name}[/bold {style}]")
                console.print(f"    {desc}\n")
            console.print("[?] [bold yellow]See detailed mode explanation[/bold yellow]")
            console.print()

            valid_choices = [m[0] for m in modes] + ["?"]
            while True:
                choice = Prompt.ask("Select mode (0-8, or ? for details)", choices=valid_choices)
                if choice:
                    break
        else:
            print(f"\n--- Step 4: Organization Mode ---")
            print("Items in green (e.g. 'converted_jxl', '_EXPORT') are configurable in option 4")
            print("Other folder names (e.g. 'JXL_16bits', '16B_TIFF') require editing the scripts directly")
            for key, name, desc in modes:
                warning = " ⚠️ WARNING!" if key == "8" else ""
                print(f"[{key}] {name}{warning}")
                # Strip rich tags for plain text
                clean = (desc.replace("[bold ", "")
                         .replace("[/bold]", "")
                         .replace("[green]", "")
                         .replace("[/green]", "")
                         .replace("[cyan]", "")
                         .replace("[/cyan]", "")
                         .replace("[red]", "")
                         .replace("[/red]", "")
                         .replace("[bold red]", "")
                         .replace("[bold green]", "")
                         .replace("[bold blue]", ""))
                print(f"    {clean}\n")
            print("[?] See detailed mode explanation")
            print()

            valid_choices = [m[0] for m in modes] + ["?"]
            while True:
                choice = input("Mode (0-8, or ? for details): ").strip()
                if choice in valid_choices:
                    break

        # Handle "?" - show detailed explanations and select from there
        if choice == "?":
            choice = self._show_mode_details(workflow)
            # choice is now the selected mode (0-8), continue to confirmation flow

        # Note about configurable names
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
                print(line.replace("[bold yellow]", "").replace("[/bold yellow]", "")
                      .replace("[bold cyan]", "").replace("[/bold cyan]", "")
                      .replace("[bold red]", "").replace("[/bold red]", ""))

        workflow['mode'] = int(choice)

        if workflow['mode'] == 8:
            if not self._confirm_archive_mode():
                return False

        return True

    def _wizard_mode_specific_config(self, workflow: Dict) -> bool:
        """Step 5: Mode-specific configuration"""
        mode = workflow['mode']
        mode_config = {}
        origin = workflow['origin_format']
        dest = workflow['dest_format']

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
                console.print(f"[dim](edit CONVERTED_JXL_FOLDER / CONVERTED_TIFF_FOLDER in script to change)[/dim]")
            else:
                print(f"\n--- Step 5: Subfolder Name ---")
                print(f"Will create: '{folder_name}' in each source folder")
                print(f"(edit CONVERTED_JXL_FOLDER / CONVERTED_TIFF_FOLDER in script to change)")

        elif mode == 3:
            folder_name = f"{dest.upper()}_files"
            if RICH_AVAILABLE and console:
                console.print(f"\n[bold cyan]Step 5: Subfolder Name[/bold cyan]")
                console.print(f"Will create: [green]'{folder_name}'[/green] in each source folder")
                console.print(f"[dim](edit JXL_FOLDER_NAME / TIFF_FOLDER_NAME in script to change)[/dim]")
            else:
                print(f"\n--- Step 5: Subfolder Name ---")
                print(f"Will create: '{folder_name}' in each source folder")
                print(f"(edit JXL_FOLDER_NAME / TIFF_FOLDER_NAME in script to change)")

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

        # Prepare staging default display
        current_staging = workflow['staging']
        if current_staging:
            staging_display = current_staging
        else:
            staging_display = "system default"

        if RICH_AVAILABLE and console:
            console.print("\n[bold cyan]Step 6: Basic Parameters[/bold cyan]")

            # RAM option for TIFF
            if origin == 'tiff':
                use_ram = Confirm.ask("Use RAM for intermediate PNG? (faster)", default=workflow['use_ram'])
                workflow['use_ram'] = use_ram

            # Workers
            workers = IntPrompt.ask("Workers", default=workflow['workers'])
            workflow['workers'] = max(1, workers)

            # Quality/Distance/Effort - context aware
            if origin == 'tiff' and dest == 'jxl':
                # Show destination summary — all values were set in Step 2
                dist_choice = workflow.get('distance_choice', '')
                q = workflow.get('quality', 0.1)
                console.print(f"[dim]Distance:[/dim] {q:.2f} (set in Step 2)")
                console.print(f"[dim]Effort:[/dim] {workflow['effort']} (set in Step 2)")

            elif 'lossy' in conv_type:
                # JPEG->JXL lossy
                quality = IntPrompt.ask("Quality (1-100)", default=workflow['quality'])
                workflow['quality'] = max(1, min(quality, 100))
                effort = IntPrompt.ask("Effort (1-10)", default=workflow['effort'])
                workflow['effort'] = max(1, min(effort, 10))
            else:
                # Lossless transcoding/encoding - only effort matters
                effort = IntPrompt.ask("Effort (1-10)", default=workflow['effort'])
                workflow['effort'] = max(1, min(effort, 10))

            # Staging with memory
            staging_prompt = f"Staging [{staging_display}]"
            staging_input = Prompt.ask(staging_prompt, default=current_staging if current_staging else "")

            if staging_input.strip() == "":
                pass  # Keep current value
            elif staging_input.lower() == 'system default':
                workflow['staging'] = None
            else:
                workflow['staging'] = staging_input


            # ICC conversion when JXL->JPEG/PNG and ImageMagick available
            if origin == 'jxl' and dest in ['jpeg', 'png'] and status.get('magick'):
                convert_icc = Confirm.ask("Convert to sRGB? (recommended for compatibility)", default=False)
                if convert_icc:
                    workflow['icc_profile'] = 'sRGB'

            # TIFF compression when destination is TIFF
            if dest == 'tiff':
                compression = Prompt.ask("TIFF compression", choices=["zip", "lzw", "none"], default=workflow['compression'])
                workflow['compression'] = compression

            # Bit depth when decoding to TIFF
            if dest == 'tiff':
                depth = IntPrompt.ask("Bit depth", choices=["8", "16"], default=workflow['bit_depth'])
                workflow['bit_depth'] = int(depth) if depth else workflow['bit_depth']

            # Dry run (useful for all)
            dry_run = Confirm.ask("Dry run? (simulate without converting)", default=False)
            workflow['dry_run'] = dry_run

            # Overwrite mode (asked here so user doesn't need to enter 6A)
            console.print("Existing file handling: [0] skip | [1] overwrite all | [2] sync (reconvert if newer)")
            ow = Prompt.ask("If exists", choices=["0", "1", "2"], default="2")
            workflow['overwrite_mode'] = ow

            # D50 patch (TIFF->JXL only)
            if origin == 'tiff' and dest == 'jxl':
                d50 = Prompt.ask("D50 patch", choices=["auto", "on", "off"], default="auto")
                workflow['d50_patch'] = d50

        else:
            print("\n--- Step 6: Basic Parameters ---")

            # RAM option for TIFF
            if origin == 'tiff':
                ram_input = input(f"Use RAM for intermediate PNG? [Y/n]: ").strip().lower()
                workflow['use_ram'] = not ram_input.startswith('n')

            # Workers
            workers = input(f"Workers [{workflow['workers']}]: ").strip()
            workflow['workers'] = int(workers) if workers.isdigit() else workflow['workers']

            # Quality/Distance/Effort
            if origin == 'tiff' and dest == 'jxl':
                # Show values set in Step 2 — no prompts here
                print(f"Distance: {workflow.get('quality', 0.1):.2f} (set in Step 2)")
                print(f"Effort: {workflow['effort']} (set in Step 2)")

            elif 'lossy' in conv_type:
                quality = input(f"Quality (1-100) [{workflow['quality']}]: ").strip()
                workflow['quality'] = int(quality) if quality.isdigit() else workflow['quality']
                effort = input(f"Effort (1-10) [{workflow['effort']}]: ").strip()
                workflow['effort'] = int(effort) if effort.isdigit() else workflow['effort']
            else:
                effort = input(f"Effort (1-10) [{workflow['effort']}]: ").strip()
                workflow['effort'] = int(effort) if effort.isdigit() else workflow['effort']

            # D50 patch (TIFF->JXL only)
            if origin == 'tiff' and dest == 'jxl':
                d50_input = input("D50 patch (auto/on/off) [auto]: ").strip().lower() or "auto"
                workflow['d50_patch'] = d50_input if d50_input in ["auto", "on", "off"] else "auto"

            # Staging with memory
            staging_input = input(f"Staging [{staging_display}]: ").strip()
            if staging_input.lower() == 'system default':
                workflow['staging'] = None
            elif staging_input:
                workflow['staging'] = staging_input


            # ICC conversion
            if origin == 'jxl' and dest in ['jpeg', 'png'] and status.get('magick'):
                icc_input = input("Convert to sRGB? [y/N]: ").strip().lower()
                if icc_input.startswith('y'):
                    workflow['icc_profile'] = 'sRGB'

            # TIFF compression
            if dest == 'tiff':
                comp_input = input(f"TIFF compression (zip/lzw/none) [{workflow['compression']}]: ").strip()
                if comp_input in ['zip', 'lzw', 'none']:
                    workflow['compression'] = comp_input

            # Bit depth
            if dest == 'tiff':
                depth_input = input(f"Bit depth (8/16) [{workflow['bit_depth']}]: ").strip()
                if depth_input in ['8', '16']:
                    workflow['bit_depth'] = int(depth_input)

            # Dry run
            dry_input = input("Dry run? [y/N]: ").strip().lower()
            workflow['dry_run'] = dry_input.startswith('y')

            # Overwrite mode (asked here so user doesn't need to enter 6A)
            ow_input = input("Existing file handling (0=skip, 1=overwrite all, 2=sync) [2]: ").strip() or "2"
            workflow['overwrite_mode'] = ow_input

        # Now ask for advanced options
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
            # Convert overwrite_mode from Step 6 into overwrite/sync flags
            ow_mode = workflow.get('overwrite_mode', '2')
            if ow_mode == "1":
                advanced_options['overwrite'] = True
                advanced_options['sync'] = False
            elif ow_mode == "2":
                advanced_options['overwrite'] = False
                advanced_options['sync'] = True
            else:
                advanced_options['overwrite'] = False
                advanced_options['sync'] = False
            workflow['advanced_options'] = advanced_options
            return self._wizard_parameters_expert(workflow)

        # Script-specific advanced options
        if origin == 'tiff' and dest == 'jxl':
            # jxl_tiff_encoder advanced options
            if RICH_AVAILABLE and console:
                strip_meta = Confirm.ask("Strip metadata?", default=False)
                encode_tag = Prompt.ask("Encode tag location", choices=["xmp", "software", "off"], default="xmp")
                # D50 patch already asked in Step 6
                # Overwrite mode already asked in Step 6
                overwrite_mode = workflow.get('overwrite_mode', '2')
                delete_src = Confirm.ask("Delete source TIFFs after conversion? (mode 8)", default=False)
            else:
                strip_input = input("Strip metadata? [y/N]: ").strip().lower()
                strip_meta = strip_input.startswith('y')
                encode_tag_input = input("Encode tag (xmp/software/off) [xmp]: ").strip().lower() or "xmp"
                encode_tag = encode_tag_input if encode_tag_input in ["xmp", "software", "off"] else "xmp"
                # D50 patch already asked in Step 6
                overwrite_mode = workflow.get('overwrite_mode', '2')
                delete_src_input = input("Delete source TIFFs after conversion? [y/N]: ").strip().lower()
                delete_src = delete_src_input.startswith('y')

            # Parse overwrite mode
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

        elif origin == 'jxl' and dest == 'tiff':
            # jxl_tiff_decoder advanced options
            if RICH_AVAILABLE and console:
                use_matrix = Confirm.ask("Use ICC matrix conversion?", default=False)
                use_basic = Confirm.ask("Use basic ICC mode?", default=False) if not use_matrix else False
                target_icc = Prompt.ask("Target ICC profile", choices=["", "sRGB", "Adobe RGB", "ProPhoto", "custom"], default="")
                no_cleanup = Confirm.ask("Skip ICC cleanup?", default=False)
                overwrite_mode = workflow.get('overwrite_mode', '2')
                delete_src = Confirm.ask("Delete source JXLs after conversion? (mode 8)", default=False)
            else:
                matrix_input = input("Use ICC matrix conversion? [y/N]: ").strip().lower()
                use_matrix = matrix_input.startswith('y')
                use_basic = False
                if not use_matrix:
                    basic_input = input("Use basic ICC mode? [y/N]: ").strip().lower()
                    use_basic = basic_input.startswith('y')
                target_icc = input("Target ICC (sRGB/Adobe RGB/ProPhoto/custom/empty): ").strip()
                cleanup_input = input("Skip ICC cleanup? [y/N]: ").strip().lower()
                no_cleanup = cleanup_input.startswith('y')
                overwrite_mode = workflow.get('overwrite_mode', '2')
                delete_src_input = input("Delete source JXLs after conversion? [y/N]: ").strip().lower()
                delete_src = delete_src_input.startswith('y')

            # Parse overwrite mode
            if overwrite_mode == "1":
                overwrite, sync = True, False
            elif overwrite_mode == "2":
                overwrite, sync = False, True
            else:
                overwrite, sync = False, False

            advanced_options['matrix'] = use_matrix
            advanced_options['basic'] = use_basic
            advanced_options['target_icc'] = target_icc if target_icc else None
            advanced_options['no_icc_cleanup'] = no_cleanup
            advanced_options['overwrite'] = overwrite
            advanced_options['sync'] = sync
            advanced_options['delete_source'] = delete_src

        else:
            # jxl_jpeg_transcoder advanced options
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

            # Parse overwrite mode
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
                console.print("[dim]Example: --strip --resize 50% --effort 10[/dim]")
                expert_flags = Prompt.ask("Custom flags", default="")
            else:
                print("Enter additional flags (e.g., --strip --resize 50% --effort 10):")
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

        if workflow.get('advanced_options'):
            extra_info.append("Advanced: Yes")
        if workflow.get('expert_flags'):
            extra_info.append("Expert: Yes")
        if workflow.get('dry_run'):
            extra_info.append("DRY RUN")

        origin = workflow['origin_format']
        dest = workflow['dest_format']

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
                ow_label = "skip (no overwrite)"
            table.add_row("If exists:", ow_label)
            table.add_row("Workers:", str(workflow['workers']))

            if origin == 'tiff' and dest == 'jxl':
                table.add_row("Distance:", str(workflow['quality']))
                # Show D50 patch mode if configured
                if 'advanced_options' in workflow and workflow['advanced_options'].get('d50_patch'):
                    table.add_row("D50 Patch:", workflow['advanced_options']['d50_patch'])
            elif 'lossy' in workflow['conversion_type']:
                table.add_row("Quality:", str(workflow['quality']))
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
                ow_label = "skip (no overwrite)"
            print(f"Source: {origin.upper()}")
            print(f"Destination: {dest.upper() if dest else '?'}")
            print(f"Mode: {workflow['mode']} - {mode_names.get(workflow['mode'])}")
            print(f"Directory: {workflow['input_dir']}")
            print(f"If exists: {ow_label}")

            if origin == 'tiff' and dest == 'jxl':
                print(f"Distance: {workflow['quality']}")
                # Show D50 patch mode if configured
                if 'advanced_options' in workflow and workflow['advanced_options'].get('d50_patch'):
                    print(f"D50 Patch: {workflow['advanced_options']['d50_patch']}")
            elif 'lossy' in workflow['conversion_type']:
                print(f"Quality: {workflow['quality']}")
            print(f"Effort: {workflow['effort']}")

            if extra_info:
                print(f"Config: {', '.join(extra_info)}")
            print("\nType YES to confirm:")
            confirm = input("> ").strip()
            if confirm.upper() != "YES":
                print("Cancelling...\n")
                return False
            return True

    def execute_workflow(self, workflow: Dict, status: Dict[str, bool]) -> bool:
        """Execute the workflow - Build command dynamically"""
        origin = workflow['origin_format']
        dest = workflow['dest_format']
        mode = workflow['mode']
        input_dir = workflow['input_dir']
        workers = workflow['workers']
        advanced = workflow.get('advanced_options', {})
        expert_flags = workflow.get('expert_flags', '')

        # Determine which script to call
        if origin == 'tiff' and dest == 'jxl':
            script = 'jxl_tiff_encoder.py'
            cmd = [
                sys.executable, script,
                input_dir,
                '--mode', str(mode),
                '--workers', str(workers)
            ]

            # Distance (quality for TIFF->JXL)
            distance = workflow.get('quality', 0.1)
            cmd.extend(['--distance', str(distance)])

            # Effort
            cmd.extend(['--effort', str(workflow['effort'])])

            # RAM option
            if workflow.get('use_ram'):
                cmd.append('--ram')
            else:
                cmd.append('--no-ram')

            # Advanced options
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

        elif origin == 'jxl' and dest == 'tiff':
            script = 'jxl_tiff_decoder.py'
            cmd = [
                sys.executable, script,
                input_dir,
                '--mode', str(mode),
                '--workers', str(workers)
            ]

            # Compression
            cmd.extend(['--compression', workflow['compression']])

            # Bit depth
            cmd.extend(['--depth', str(workflow['bit_depth'])])

            # Advanced ICC options (mutually exclusive handling)
            if advanced.get('matrix'):
                cmd.append('--matrix')
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

        else:
            # JPEG/PNG/JXL - uses jxl_jpeg_transcoder
            script = 'jxl_jpeg_transcoder.py'
            cmd = [
                sys.executable, script,
                input_dir,
                '--mode', str(mode),
                '--workers', str(workers)
            ]

            # Quality or transcode mode
            if workflow['conversion_type'] == 'transcode_lossless':
                cmd.append('--force-transcode')
            elif 'lossy' in workflow['conversion_type']:
                cmd.extend(['--quality', str(workflow['quality'])])
                cmd.extend(['--distance', str(workflow['quality'])])

            # Effort
            cmd.extend(['--effort', str(workflow['effort'])])

            # ICC profile
            if workflow.get('icc_profile'):
                cmd.extend(['--icc-profile', workflow['icc_profile']])

            # Staging for transcoder
            if workflow.get('staging'):
                cmd.extend(['--staging', workflow['staging']])

            # Advanced options
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

            # Format override if needed
            if dest == 'png':
                cmd.extend(['--format', 'png'])
            elif dest == 'jpeg' or dest == 'jpg':
                cmd.extend(['--format', 'jpeg'])

            # Bit depth for PNG
            if dest == 'png' and workflow.get('bit_depth'):
                cmd.extend(['--bit-depth', str(workflow['bit_depth'])])

        # Dry run (applicable to all)
        if workflow.get('dry_run'):
            cmd.append('--dry-run')

        # Expert flags (parse and append)
        if expert_flags:
            # Split respecting quotes
            try:
                import shlex
                expert_args = shlex.split(expert_flags)
                cmd.extend(expert_args)
            except:
                # Fallback to simple split
                cmd.extend(expert_flags.split())

        # Check if script exists
        if not Path(script).exists():
            self._print_error(f"Script not found: {script}")
            self._print_error("Ensure scripts are in the same folder as jxl_photo.py")
            return False

        # Show command
        if RICH_AVAILABLE and console:
            console.print(f"\n[bold cyan]Executing:[/bold cyan]")
            console.print(f"[dim]{' '.join(cmd)}[/dim]\n")
        else:
            print(f"\nExecuting: {' '.join(cmd)}\n")

        # Execute with real-time streaming
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

            # Stream output in real-time
            for line in process.stdout:
                line = line.strip()
                if line:
                    if RICH_AVAILABLE and console:
                        # Color based on content
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

            process.wait()

            if process.returncode == 0:
                self._print_success("\n✓ Conversion completed successfully!")
                return True
            else:
                self._print_error(f"\n✗ Conversion failed (code {process.returncode})")
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
    parser = argparse.ArgumentParser(description="JXL Tools - JPEG XL Processing")
    parser.add_argument("--recheck", action="store_true", help="Force dependency recheck")
    args = parser.parse_args()

    config = ConfigManager()
    checker = DependencyChecker(config)
    menu = InteractiveMenu(config, checker)

    # Check dependencies - always verify tifffile directly
    force_check = args.recheck or not config.config.dependencies_checked
    status = checker.check_dependencies(force=force_check)

    # Display status in single line (v3 style)
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
                # Save session with current staging and effort/quality
                origin = workflow['origin_format']
                dest = workflow['dest_format']

                # Determine what quality value to save
                if origin == 'tiff' and dest == 'jxl':
                    saved_quality = workflow.get('quality') or 0.1  # Distance
                elif 'lossy' in workflow['conversion_type']:
                    saved_quality = workflow.get('quality') or 95  # JPEG quality
                else:
                    saved_quality = config.config.default_quality  # lossless transcoding - quality not used

                # Determine distance for TIFF->JXL workflows
                saved_distance = None
                if origin == 'tiff' and dest == 'jxl':
                    saved_distance = workflow.get('quality') or 0.1

                config.save_last_session(
                    workflow['input_dir'],
                    str(workflow['mode']),
                    workflow['workers'],
                    workflow['staging'],
                    workflow['effort'],
                    saved_quality,
                    saved_distance,
                    workflow['origin_format']
                )

                # Ask if execute now
                if RICH_AVAILABLE and console:
                    execute_now = Confirm.ask(
                        "\nConfiguration saved! Execute now?", 
                        default=True
                    )
                else:
                    exec_input = input("\nExecute now? [Y/n]: ").strip().lower()
                    execute_now = not exec_input.startswith('n')

                if execute_now:
                    success = menu.execute_workflow(workflow, status)
                    if success:
                        # Ask if convert another folder
                        if RICH_AVAILABLE and console:
                            again = Confirm.ask("\nConvert another folder?", default=False)
                            if not again:
                                break
                        else:
                            again = input("\nConvert another folder? [y/N]: ").strip().lower()
                            if not again.startswith('y'):
                                break
                    else:
                        # If failed, ask if retry
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
            last_distance = last.last_distance  # May be None for non-TIFF workflows
            last_origin = last.last_origin_format or "tiff"

            if RICH_AVAILABLE and console:
                settings = [
                    ["Input folder", last_dir],
                    ["Source", last_origin.upper()],
                    ["Mode", last_mode],
                    ["Workers", str(last_workers)],
                    ["Effort", str(last_effort)],
                    ["Quality", str(last_quality)],
                    ["Distance", f"{last_distance}" if last_distance is not None else "(n/a)"],
                    ["Staging", last_staging or "(none)"],
                ]
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
                print(f"  Mode:         {last_mode}")
                print(f"  Workers:      {last_workers}")
                print(f"  Effort:       {last_effort}")
                print(f"  Quality:      {last_quality}")
                print(f"  Distance:     {last_distance if last_distance is not None else '(n/a)'}")
                print(f"  Staging:      {last_staging or '(none)'}")
                print()

            # Ask for folder
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

            # Use the saved origin format from last workflow
            origin = last_origin

            # Ask for overwrite mode
            if RICH_AVAILABLE and console:
                console.print("Existing file handling: [0] skip | [1] overwrite all | [2] sync (reconvert if newer)")
                ow_choice = Prompt.ask("If exists", choices=["0", "1", "2"], default="2")
            else:
                ow_choice = input("If exists (0=skip, 1=overwrite all, 2=sync) [2]: ").strip() or "2"

            if ow_choice == "1":
                overwrite, sync = True, False
            elif ow_choice == "2":
                overwrite, sync = False, True
            else:
                overwrite, sync = False, False

            # Confirm
            if RICH_AVAILABLE and console:
                proceed = Confirm.ask(f"\n[bold]Proceed with this workflow?[/bold]", default=True)
            else:
                resp = input(f"\nProceed with this workflow? [Y/n]: ").strip().lower()
                proceed = not resp.startswith('n')

            if not proceed:
                continue

            # Build workflow for execution
            # For TIFF workflows, use last_distance as quality (distance parameter)
            # For others, use last_quality
            effective_quality = last_distance if (origin == 'tiff' and last_distance is not None) else (last_quality or 95)

            workflow = {
                'input_dir': new_folder,
                'mode': int(last_mode or 0),
                'workers': last_workers or 4,
                'staging': last_staging,
                'effort': last_effort or 7,
                'quality': effective_quality,
                'overwrite_mode': ow_choice,
            }

            if workflow['mode'] == 2:
                workflow['mode_config'] = {'output_dir': str(input_path.parent / "output")}
            else:
                workflow['mode_config'] = {}

            workflow['origin_format'] = origin
            workflow['dest_format'] = 'jxl' if origin != 'jxl' else 'tiff'
            workflow['selected_files'] = []
            workflow['use_ram'] = True
            workflow['icc_profile'] = None
            workflow['compression'] = 'zip'
            workflow['bit_depth'] = 16
            workflow['dry_run'] = False
            # Preserve advanced options from last workflow (including d50_patch)
            last_advanced = last.get('advanced_options', {})
            workflow['advanced_options'] = {
                'overwrite': overwrite,
                'sync': sync,
                'd50_patch': last_advanced.get('d50_patch', 'auto') if origin == 'tiff' else None,
                'encode_tag': last_advanced.get('encode_tag', 'xmp') if origin == 'tiff' else None,
            }
            workflow['expert_flags'] = ''

            if origin == 'jpeg':
                workflow['conversion_type'] = 'transcode_lossless'
            elif origin == 'tiff':
                workflow['conversion_type'] = 'jxl_tiff_encoder'
            else:
                workflow['conversion_type'] = 'jxl_tiff_decoder'

            menu.execute_workflow(workflow, status)

        elif choice == "3":
            status = checker.check_dependencies(force=True)
            menu.display_status(status)

        elif choice == "4":
            menu.edit_settings()

        elif choice == "5":
            # Reset all settings
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

                    # Recreate fresh config
                    config.config = ToolConfig()
                    config.save_config()

                    # Recheck dependencies
                    status = checker.check_dependencies(force=True)
                    menu.display_status(status)
                except Exception as e:
                    menu._print_error(f"Error erasing: {e}")

        elif choice == "6":
            # Move settings file between USERPROFILE and script folder
            script_config = SCRIPT_DIR / ".jxl_tools_config.json"
            # Use FIXED user profile path - do NOT use config.config_path
            # which gets updated after moves and causes toggle bug
            if platform.system() == "Windows":
                user_profile_dir = Path(os.environ.get("USERPROFILE", Path.home()))
            else:
                user_profile_dir = Path.home()
            user_config = user_profile_dir / ".jxl_tools_config.json"

            if script_config.exists():
                # Settings are in script folder → offer to move to USERPROFILE
                action = "move to User Profile"
                target = user_config
                source = script_config
            elif user_config.exists():
                # Settings are in USERPROFILE → offer to move to script folder
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
                    config.config_path = target  # update in-memory path for this session
                    if RICH_AVAILABLE and console:
                        console.print(f"[green]✓ Settings moved to {target.parent}[/green]")
                    else:
                        print(f"✓ Settings moved to {target.parent}")
                except Exception as e:
                    menu._print_error(f"Error moving settings: {e}")
            continue


if __name__ == "__main__":
    main()
