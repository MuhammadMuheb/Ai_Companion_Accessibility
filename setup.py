#!/usr/bin/env python3
"""Setup script for AI Companion."""

import os
import sys
import subprocess
from pathlib import Path

def check_python():
    if sys.version_info < (3, 10):
        print("❌ Python 3.10+ required")
        sys.exit(1)
    print(f"✓ Python {sys.version_info.major}.{sys.version_info.minor}")

def create_directories():
    dirs = ["data", "data/screenshots", "data/conversations", "data/code_output", "data/logs", "logs"]
    for dir_name in dirs:
        Path(dir_name).mkdir(parents=True, exist_ok=True)
    print("✓ Directories created")

def install_dependencies():
    print("\n📦 Installing dependencies...")
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements-FULL.txt"], check=True)
        print("✓ Dependencies installed")
    except subprocess.CalledProcessError:
        print("❌ Failed to install dependencies")
        sys.exit(1)

def check_ollama():
    try:
        subprocess.run(["ollama", "--version"], capture_output=True, check=True, timeout=5)
        print("✓ Ollama found")
        return True
    except:
        print("⚠️  Ollama not installed. Install from https://ollama.ai")
        return False

def main():
    print("""
╔══════════════════════════════════════════╗
║   AI COMPANION - SETUP INSTALLER        ║
╚══════════════════════════════════════════╝
""")
    check_python()
    create_directories()

    if input("\n📦 Install Python dependencies? (y/n): ").lower() == "y":
        install_dependencies()

    if check_ollama():
        if input("\n📥 Download Ollama models? (y/n): ").lower() == "y":
            subprocess.run(["ollama", "pull", "qwen2.5:1.5b"])      # chat
            subprocess.run(["ollama", "pull", "nomic-embed-text"])  # memory search
            subprocess.run(["ollama", "pull", "gemma3:4b"])         # English -> Roman Urdu translation

    print("""
╔══════════════════════════════════════════╗
║      SETUP COMPLETE! 🎉                  ║
║                                          ║
║  Next steps:                             ║
║  1. Start Ollama: ollama serve           ║
║  2. Run: python main_accessibility.py    ║
╚══════════════════════════════════════════╝
""")

if __name__ == "__main__":
    main()
