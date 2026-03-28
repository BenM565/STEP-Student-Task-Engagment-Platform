STEP Platform - Quick Start (Windows)

1) Open a terminal in your project folder
- Important: cd into the folder that actually contains app.py.
  Example:
  PS> cd "C:\Users\YOUR_USER\OneDrive\FYP\PyCharmProjects\YourProjectName"

2) Option A: Run with helper script (recommended)
- PowerShell:
  PS> .\run.ps1
- Command Prompt (cmd.exe):
  C:\> run.bat

These scripts:
- Activate .venv if it exists
- Start the app via "python -m flask --app app run --debug"
- Avoid needing 'flask.exe' on PATH

3) Option B: Run manually without helper script
- Ensure you are in the folder with app.py
- If you want to use a virtual environment, activate it first:
  PowerShell:
    PS> .\.venv\Scripts\Activate.ps1
  cmd.exe:
    C:\> .\venv\Scripts\activate.bat
- Start Flask without relying on PATH:
  PS/CMD> python -m flask --app app run --debug

Notes
- If you try "flask run" and get "flask not recognized", it means the script
  'flask.exe' is not on your PATH. Using "python -m flask" avoids this.
- If "python app.py" says "No such file or directory", you’re in the wrong folder.
  cd into the folder that contains app.py.
- This app tolerates missing flask-migrate; database migrations are optional for local runs.
