Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "c:\Users\swarn\Downloads\mark-xlviii-anyllm-main\mark-xlviii-anyllm-main\Mark-XLVIII-main"
WshShell.Run """.venv\Scripts\pythonw.exe"" main.py", 0, False
