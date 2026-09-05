' Run a .cmd file with no window and wait for it (harness/mesen_play.lua):
'   wscript.exe //B //Nologo run_hidden.vbs "C:\path\to\file.cmd"
' Mesen has no console, so a plain os.execute pops a console window per
' call; wscript is a GUI-subsystem host and Run(..., 0, True) is hidden.
Set sh = CreateObject("WScript.Shell")
WScript.Quit sh.Run("cmd.exe /c """ & WScript.Arguments(0) & """", 0, True)
