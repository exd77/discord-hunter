#!/bin/bash
set -e

if [ ! -d "venv" ]; then
  python3 -m venv venv
fi

source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "Setup kelar. Copy .env.example jadi .env lalu isi token-token yang dibutuhin."
