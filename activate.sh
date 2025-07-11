#!/bin/bash
# Activate virtual environment and install dependencies

source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "Virtual environment activated and dependencies installed!"
echo "Run 'deactivate' to exit the virtual environment"
echo ""
echo "To start the MCP adapter:"
echo "python main.py serve"
