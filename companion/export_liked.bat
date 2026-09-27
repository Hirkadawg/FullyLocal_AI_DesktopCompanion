@echo off
rem Export every thumbs-up answer and remark as training examples, for review.
rem Writes to data\exports -- nothing is trained, nothing leaves this machine.
cd /d "%~dp0"
".venv\Scripts\python.exe" main.py --export-liked
pause
