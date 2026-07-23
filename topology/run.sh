#!/bin/bash
# IoT Topology Dashboard - Startup Script
# Usage: ./run.sh

echo ""
echo "============================================"
echo "  IoT Topology Dashboard"
echo "  Starting FastAPI on port 8001..."
echo "============================================"
echo ""

# Navigate to script directory
cd "$(dirname "$0")"

# Activate venv if it exists
if [ -f "venv/Scripts/activate" ]; then
    echo "[INFO] Activating virtual environment..."
    source venv/Scripts/activate
fi

# Check Python
if ! command -v py &> /dev/null; then
    echo "[ERROR] Python not found. Install Python 3.11+ from python.org"
    exit 1
fi

# Check dependencies
echo "[INFO] Checking dependencies..."
py -c "import fastapi" 2>/dev/null || py -m pip install fastapi uvicorn pyodbc

# Check database
echo "[INFO] Testing database connection..."
py -c "
import pyodbc
try:
    conn = pyodbc.connect('Driver={ODBC Driver 17 for SQL Server};Server=thtrdinfradb1;Database=InfrastructureMonitorDB;Trusted_Connection=yes;TrustServerCertificate=yes;', timeout=5)
    print('[OK] Database connected')
except Exception as e:
    print(f'[WARN] DB error: {e}')
" 2>/dev/null

echo ""
echo "============================================"
echo "  Server starting at http://127.0.0.1:8000"
echo "  API docs at:    http://127.0.0.1:8000/docs"
echo "  Press Ctrl+C to stop"
echo "============================================"
echo ""

# Start the server
py -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload