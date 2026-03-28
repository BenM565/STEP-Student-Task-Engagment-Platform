# PowerShell helper to run the Flask app
# - Activates .venv if present
# - Runs via "python -m flask" so PATH to flask.exe is not required

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Move to this script's directory
Set-Location -Path $PSScriptRoot

# Activate virtual environment if it exists
$venvActivate = ".\.venv\Scripts\Activate.ps1"
if (Test-Path $venvActivate) {
    . $venvActivate
}

# Ensure app.py exists in current directory
if (-not (Test-Path ".\app.py")) {
    Write-Error "app.py not found in $((Get-Location).Path). Please 'cd' into your project folder that contains app.py."
    exit 1
}

# Run Flask using Python module (no need for flask.exe on PATH)
python -m flask --app app run --debug --host 0.0.0.0 --port 5000
