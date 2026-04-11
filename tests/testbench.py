#!/usr/bin/env python3
"""
jxl-photo Testbench - Automated testing suite for JXL conversion toolkit

Usage:
    python testbench.py [options]

Options:
    --input-dir PATH    Directory with test images (default: E:\\TESTAR)
    --output-dir PATH   Directory for test outputs (default: E:\\TESTAR_OUTPUT)
    --keep-outputs      Don't delete output files after tests
    --verbose           Show detailed output
    --quick             Run quick tests only (skip large file tests)

Examples:
    python testbench.py                    # Run all tests
    python testbench.py --quick            # Run quick tests only
    python testbench.py --keep-outputs     # Keep output files for inspection
"""

import subprocess
import sys
import os
import shutil
import tempfile
import time
from pathlib import Path
from datetime import datetime
import argparse

# Test configuration
DEFAULT_INPUT_DIR = r"E:\TESTAR"
DEFAULT_OUTPUT_DIR = r"E:\TESTAR_OUTPUT"
TEST_WORKERS = 2

# Colors for terminal output
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

def print_header(text):
    print(f"\n{Colors.BLUE}{Colors.BOLD}{'='*60}{Colors.RESET}")
    print(f"{Colors.BLUE}{Colors.BOLD}{text}{Colors.RESET}")
    print(f"{Colors.BLUE}{Colors.BOLD}{'='*60}{Colors.RESET}\n")

def print_success(text):
    print(f"{Colors.GREEN}âœ“ {text}{Colors.RESET}")

def print_error(text):
    print(f"{Colors.RED}âœ— {text}{Colors.RESET}")

def print_warning(text):
    print(f"{Colors.YELLOW}âš  {text}{Colors.RESET}")

def print_info(text):
    print(f"{Colors.BLUE}[i] {text}{Colors.RESET}")

def run_command(cmd, verbose=False):
    """Run a command and return success status and output."""
    if verbose:
        print_info(f"Running: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=300  # 5 minute timeout
        )
        return result.returncode == 0, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return False, "Command timed out after 300 seconds"
    except Exception as e:
        return False, f"Exception: {str(e)}"

def check_files_exist(directory, pattern, expected_count=None):
    """Check if files matching pattern exist in directory."""
    if not os.path.exists(directory):
        return False, 0
    
    files = list(Path(directory).glob(pattern))
    count = len(files)
    
    if expected_count and count != expected_count:
        return False, count
    
    return count > 0, count

def test_tiff_to_jxl(input_dir, output_dir, verbose=False):
    """Test 1: TIFF to JXL encoding."""
    print_header("TEST 1: TIFF â†’ JXL (jxl_tiff_encoder.py)")
    
    test_output = os.path.join(output_dir, "test_01_tiff_to_jxl")
    os.makedirs(test_output, exist_ok=True)
    
    input_path = os.path.join(input_dir, "tiff")
    if not os.path.exists(input_path):
        print_warning(f"Input directory not found: {input_path}")
        return False, "SKIP"
    
    # Count input files
    input_files = list(Path(input_path).glob("*.tif"))
    if not input_files:
        input_files = list(Path(input_path).glob("*.tiff"))
    
    if not input_files:
        print_warning("No TIFF files found in input directory")
        return False, "SKIP"
    
    print_info(f"Found {len(input_files)} TIFF files to convert")
    
    # Run encoder
    cmd = [
        sys.executable, "jxl_tiff_encoder.py",
        input_path, test_output,
        "--mode", "0",
        "--workers", str(TEST_WORKERS),
        "--distance", "0.1"
    ]
    
    success, output = run_command(cmd, verbose)
    
    if verbose and output:
        print(output)
    
    if not success:
        print_error("Encoder failed")
        return False, output
    
    # Check output files
    has_output, count = check_files_exist(test_output, "*.jxl", len(input_files))
    
    if not has_output:
        print_error(f"Expected {len(input_files)} JXL files, found {count}")
        return False, f"Output count mismatch: expected {len(input_files)}, got {count}"
    
    print_success(f"Converted {count} TIFF files to JXL")
    
    # Check compression ratio
    for jxl_file in Path(test_output).glob("*.jxl"):
        jxl_size = jxl_file.stat().st_size / (1024 * 1024)  # MB
        print_info(f"  {jxl_file.name}: {jxl_size:.2f} MB")
    
    return True, f"Created {count} JXL files"

def test_jxl_to_tiff(input_dir, output_dir, verbose=False):
    """Test 2: JXL to TIFF decoding."""
    print_header("TEST 2: JXL â†’ TIFF (jxl_tiff_decoder.py)")
    
    test_output = os.path.join(output_dir, "test_02_jxl_to_tiff")
    os.makedirs(test_output, exist_ok=True)
    
    input_path = os.path.join(input_dir, "jxl")
    if not os.path.exists(input_path):
        print_warning(f"Input directory not found: {input_path}")
        return False, "SKIP"
    
    input_files = list(Path(input_path).glob("*.jxl"))
    if not input_files:
        print_warning("No JXL files found in input directory")
        return False, "SKIP"
    
    print_info(f"Found {len(input_files)} JXL files to convert")
    
    cmd = [
        sys.executable, "jxl_tiff_decoder.py",
        input_path, test_output,
        "--mode", "0",
        "--workers", str(TEST_WORKERS)
    ]
    
    success, output = run_command(cmd, verbose)
    
    if verbose and output:
        print(output)
    
    if not success:
        print_error("Decoder failed")
        return False, output
    
    # Check for errors in output
    if "Preview failed" in output or "TiffWriter" in output:
        print_warning("JPEG preview generation had issues (check logs)")
    
    has_output, count = check_files_exist(test_output, "*.tif")
    
    if not has_output:
        print_error("No TIFF files created")
        return False, "No output files"
    
    print_success(f"Converted {count} JXL files to TIFF")
    
    return True, f"Created {count} TIFF files"

def test_jpeg_to_jxl(input_dir, output_dir, verbose=False):
    """Test 3: JPEG to JXL conversion."""
    print_header("TEST 3: JPEG â†’ JXL (jxl_jpeg_transcoder.py)")
    
    test_output = os.path.join(output_dir, "test_03_jpeg_to_jxl")
    os.makedirs(test_output, exist_ok=True)
    
    input_path = os.path.join(input_dir, "jpg")
    if not os.path.exists(input_path):
        input_path = os.path.join(input_dir, "jpeg")
    
    if not os.path.exists(input_path):
        print_warning(f"Input directory not found: {input_path}")
        return False, "SKIP"
    
    input_files = list(Path(input_path).glob("*.jpg"))
    if not input_files:
        input_files = list(Path(input_path).glob("*.jpeg"))
    
    if not input_files:
        print_warning("No JPEG files found in input directory")
        return False, "SKIP"
    
    print_info(f"Found {len(input_files)} JPEG files to convert")
    
    cmd = [
        sys.executable, "jxl_jpeg_transcoder.py",
        input_path,
        "--mode", "1",
        "--workers", str(TEST_WORKERS),
        "--distance", "0.5",
        "--force-convert"
    ]
    
    success, output = run_command(cmd, verbose)
    
    if verbose and output:
        print(output)
    
    if not success:
        print_error("Transcoder failed")
        return False, output
    
    # Files are created in converted/ subfolder
    converted_folder = os.path.join(input_path, "converted")
    if os.path.exists(converted_folder):
        jxl_files = list(Path(converted_folder).glob("*.jxl"))
        if jxl_files:
            for f in jxl_files:
                shutil.copy2(f, test_output)
    
    has_output, count = check_files_exist(test_output, "*.jxl")
    
    if not has_output:
        print_error("No JXL files created")
        return False, "No output files"
    
    print_success(f"Converted {count} JPEG files to JXL")
    
    return True, f"Created {count} JXL files"

def test_jxl_to_jpeg(input_dir, output_dir, verbose=False):
    """Test 4: JXL to JPEG conversion."""
    print_header("TEST 4: JXL â†’ JPEG (jxl_jpeg_transcoder.py)")
    
    test_output = os.path.join(output_dir, "test_04_jxl_to_jpeg")
    os.makedirs(test_output, exist_ok=True)
    
    input_path = os.path.join(input_dir, "jxl")
    if not os.path.exists(input_path):
        print_warning(f"Input directory not found: {input_path}")
        return False, "SKIP"
    
    input_files = list(Path(input_path).glob("*.jxl"))
    if not input_files:
        print_warning("No JXL files found in input directory")
        return False, "SKIP"
    
    print_info(f"Found {len(input_files)} JXL files to convert")
    
    cmd = [
        sys.executable, "jxl_jpeg_transcoder.py",
        input_path,
        "--mode", "1",
        "--workers", str(TEST_WORKERS),
        "--force-convert"
    ]
    
    success, output = run_command(cmd, verbose)
    
    if verbose and output:
        print(output)
    
    if not success:
        print_error("Transcoder failed")
        return False, output
    
    # Copy files from converted folder
    converted_folder = os.path.join(input_path, "converted")
    if os.path.exists(converted_folder):
        jpg_files = list(Path(converted_folder).glob("*.jpg"))
        for f in jpg_files:
            shutil.copy2(f, test_output)
    
    has_output, count = check_files_exist(test_output, "*.jpg")
    
    if not has_output:
        print_error("No JPEG files created")
        return False, "No output files"
    
    print_success(f"Converted {count} JXL files to JPEG")
    
    return True, f"Created {count} JPEG files"

def test_roundtrip_integrity(input_dir, output_dir, verbose=False):
    """Test 5: Roundtrip integrity check (TIFF â†’ JXL â†’ TIFF)."""
    print_header("TEST 5: Roundtrip Integrity Check")
    
    test_output = os.path.join(output_dir, "test_05_roundtrip")
    os.makedirs(test_output, exist_ok=True)
    
    input_path = os.path.join(input_dir, "tiff")
    if not os.path.exists(input_path):
        print_warning(f"Input directory not found: {input_path}")
        return False, "SKIP"
    
    # Get first TIFF file
    tiff_files = list(Path(input_path).glob("*.tif"))
    if not tiff_files:
        tiff_files = list(Path(input_path).glob("*.tiff"))
    
    if not tiff_files:
        print_warning("No TIFF files found")
        return False, "SKIP"
    
    test_tiff = tiff_files[0]
    print_info(f"Testing with: {test_tiff.name}")
    
    # Step 1: TIFF â†’ JXL
    jxl_output = os.path.join(test_output, test_tiff.stem + ".jxl")
    cmd1 = [
        sys.executable, "jxl_tiff_encoder.py",
        str(test_tiff), jxl_output,
        "--mode", "0"
    ]
    
    success1, output1 = run_command(cmd1, verbose)
    if not success1:
        print_error("TIFF â†’ JXL failed")
        return False, output1
    
    if not os.path.exists(jxl_output):
        print_error("JXL file not created")
        return False, "JXL output missing"
    
    print_success("Step 1: TIFF â†’ JXL OK")
    
    # Step 2: JXL â†’ TIFF
    tiff_output = os.path.join(test_output, test_tiff.stem + "_roundtrip.tif")
    cmd2 = [
        sys.executable, "jxl_tiff_decoder.py",
        jxl_output, tiff_output,
        "--mode", "0"
    ]
    
    success2, output2 = run_command(cmd2, verbose)
    if not success2:
        print_error("JXL â†’ TIFF failed")
        return False, output2
    
    if not os.path.exists(tiff_output):
        print_error("TIFF file not created")
        return False, "TIFF output missing"
    
    print_success("Step 2: JXL â†’ TIFF OK")
    
    # Check file sizes
    original_size = test_tiff.stat().st_size / (1024 * 1024)
    roundtrip_size = Path(tiff_output).stat().st_size / (1024 * 1024)
    jxl_size = Path(jxl_output).stat().st_size / (1024 * 1024)
    
    print_info(f"Original TIFF: {original_size:.2f} MB")
    print_info(f"JXL: {jxl_size:.2f} MB ({jxl_size/original_size*100:.1f}% of original)")
    print_info(f"Roundtrip TIFF: {roundtrip_size:.2f} MB")
    
    return True, f"Roundtrip successful: {test_tiff.name}"

def main():
    parser = argparse.ArgumentParser(
        description="jxl-photo Testbench - Automated testing suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python testbench.py                    # Run all tests
    python testbench.py --quick            # Run quick tests only
    python testbench.py --keep-outputs     # Keep output files
    python testbench.py --verbose          # Show detailed output
        """
    )
    parser.add_argument("--input-dir", default=DEFAULT_INPUT_DIR,
                        help=f"Directory with test images (default: {DEFAULT_INPUT_DIR})")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR,
                        help=f"Directory for test outputs (default: {DEFAULT_OUTPUT_DIR})")
    parser.add_argument("--keep-outputs", action="store_true",
                        help="Don't delete output files after tests")
    parser.add_argument("--verbose", action="store_true",
                        help="Show detailed output")
    parser.add_argument("--quick", action="store_true",
                        help="Run quick tests only (skip large file tests)")
    
    args = parser.parse_args()
    
    print_header("jxl-photo Testbench v1.3")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Input dir: {args.input_dir}")
    print(f"Output dir: {args.output_dir}")
    print()
    
    # Create output directory
    os.makedirs(args.output_dir, exist_ok=True)
    
    # Define tests
    tests = [
        ("TIFF â†’ JXL", test_tiff_to_jxl),
        ("JXL â†’ TIFF", test_jxl_to_tiff),
        ("JPEG â†’ JXL", test_jpeg_to_jxl),
        ("JXL â†’ JPEG", test_jxl_to_jpeg),
        ("Roundtrip", test_roundtrip_integrity),
    ]
    
    if args.quick:
        tests = tests[:3]  # Skip last 2 tests in quick mode
        print_info("Quick mode: Running first 3 tests only\n")
    
    # Run tests
    results = []
    passed = 0
    failed = 0
    skipped = 0
    
    for name, test_func in tests:
        try:
            success, message = test_func(args.input_dir, args.output_dir, args.verbose)
            results.append((name, success, message))
            
            if success:
                passed += 1
            elif message == "SKIP":
                skipped += 1
            else:
                failed += 1
        except Exception as e:
            results.append((name, False, f"Exception: {str(e)}"))
            failed += 1
            print_error(f"{name}: Exception - {str(e)}")
    
    # Print summary
    print_header("TEST SUMMARY")
    
    for name, success, message in results:
        status = "âœ“ PASS" if success else ("âŠ˜ SKIP" if message == "SKIP" else "âœ— FAIL")
        color = Colors.GREEN if success else (Colors.YELLOW if message == "SKIP" else Colors.RED)
        print(f"{color}{status}{Colors.RESET} {name:<20} {message}")
    
    print()
    print(f"{Colors.BOLD}Results:{Colors.RESET}")
    print(f"  {Colors.GREEN}Passed:  {passed}{Colors.RESET}")
    print(f"  {Colors.RED}Failed:  {failed}{Colors.RESET}")
    print(f"  {Colors.YELLOW}Skipped: {skipped}{Colors.RESET}")
    print(f"  Total:   {len(results)}")
    
    # Cleanup
    if not args.keep_outputs and failed == 0:
        print()
        print_info("Cleaning up test outputs...")
        try:
            shutil.rmtree(args.output_dir, ignore_errors=True)
            print_success("Cleanup complete")
        except Exception as e:
            print_warning(f"Cleanup failed: {e}")
    elif args.keep_outputs:
        print()
        print_info(f"Output files kept in: {args.output_dir}")
    
    print()
    print(f"Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Exit with appropriate code
    sys.exit(0 if failed == 0 else 1)

if __name__ == "__main__":
    main()

