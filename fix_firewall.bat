@echo off
echo Requesting administrative privileges...
net session >nul 2>&1
if %errorLevel% == 0 (
    echo Success: Administrative privileges confirmed.
) else (
    echo Failure: Current permissions inadequate.
    echo Please right-click this file and select "Run as Administrator".
    pause
    exit /b
)

echo Adding Firewall Rule for Port 8000...
netsh advfirewall firewall add rule name="ArchViz Backend" dir=in action=allow protocol=TCP localport=8000 profile=private,public,domain

echo.
echo Rule added. You should now be able to access the system.
pause
