@echo off
rem ELENA: abre PowerShell como administrador y corre pulir.ps1 desde el CD.
rem Opciones (se pasan tal cual): -SinApagar  -SinLimpieza  -SinInicioAutomatico  -SinPuente
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-NoExit','-File','%~dp0pulir.ps1 %*'"
