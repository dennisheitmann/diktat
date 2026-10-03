#!/usr/bin/env bash

export PIP_CONFIG_FILE="pip.ini"

if [ ! -f "venv_diktat/bin/python" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv_diktat
    echo "Installing dependencies..."
    venv_diktat/bin/pip install -r requirements.txt
fi

venv_diktat/bin/python diktat.py