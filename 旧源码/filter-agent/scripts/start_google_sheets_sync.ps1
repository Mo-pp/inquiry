$ErrorActionPreference = "Stop"

$ProjectDirectory = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectDirectory

# Use the Python interpreter selected by PATH. A virtual-environment path can
# be supplied by replacing this with its absolute python.exe path.
python -m filter_agent.lead_sync
