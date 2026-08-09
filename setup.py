#!/usr/bin/env python
"""Setup script for InteractFormer."""

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as f:
    long_description = f.read()

setup(
    name="interactformer",
    version="0.1.0",
    author="InteractFormer Team",
    description=(
        "InteractFormer: A Real-Time Multimodal Interaction Framework "
        "with Dual-Model Architecture"
    ),
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/interactformer/interactformer",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Science/Research",
        "License :: OSI Approved :: Apache Software License",
        "Programming Language :: Python :: 3.10",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
    ],
    python_requires=">=3.10",
    install_requires=[
        "torch>=2.0.0",
        "numpy>=1.24.0",
        "pyyaml>=6.0",
        "tqdm>=4.65.0",
    ],
    extras_require={
        "audio": ["torchaudio>=2.0.0", "librosa>=0.10.0", "soundfile>=0.12.0"],
        "vision": ["pillow>=10.0.0", "opencv-python>=4.8.0"],
        "serve": ["fastapi>=0.100.0", "uvicorn>=0.23.0", "websockets>=11.0"],
        "dev": ["pytest>=7.0.0", "black>=23.0.0", "ruff>=0.1.0"],
    },
)
