@echo off
set PIP_CONFIG_FILE=pip.ini

if not exist venv_diktat\Scripts\python.exe (
    echo Creating virtual environment...
    python -m venv venv_diktat
    echo Installing dependencies...
    venv_diktat\Scripts\pip install -r requirements.txt
)

venv_diktat\Scripts\python diktat.py
