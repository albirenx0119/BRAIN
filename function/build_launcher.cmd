@echo off
cd /d "%~dp0"
"%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo /target:winexe /out:..\BRAIN.exe /win32icon:BRAIN.ico /resource:assets\logo.png,BRAIN.Logo /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll /reference:System.IO.Compression.dll /reference:System.IO.Compression.FileSystem.dll launcher.cs updates.cs AssemblyInfo.cs
if errorlevel 1 exit /b 1
python release_manifest.py --version 1.0.1
