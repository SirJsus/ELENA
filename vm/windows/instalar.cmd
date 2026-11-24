@echo off
rem ELENA: abre PowerShell como administrador y corre instalar.ps1 desde el CD.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-NoExit','-File','%~dp0instalar.ps1'"
