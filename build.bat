@echo off
rem UetiBot.exe 만들기 (결과: dist\UetiBot.exe)
rem   --collect-all selenium : 셀레니움은 필요한 모듈을 실행 중에 불러와서, 전부 포함하라고 알려줘야 함
cd /d "%~dp0"
py -m pip install selenium keyring pyinstaller
py -m PyInstaller --noconfirm --onefile --windowed --name UetiBot --collect-all selenium app.py
echo.
echo 완료: %~dp0dist\UetiBot.exe
pause
