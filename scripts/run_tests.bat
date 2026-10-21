@echo off
REM Usage: scripts\run_tests.bat [quick|phases|all]
set MODE=%1
if "%MODE%"=="" set MODE=quick
set ARGS=-q -p no:cacheprovider -W ignore
if "%MODE%"=="quick"  python -m pytest %ARGS% inspection\tests & goto :eof
if "%MODE%"=="phases" python -m pytest %ARGS% qa_core\tests institutions\tests accreditation\tests payments\tests inspection\tests & goto :eof
if "%MODE%"=="all"    python -m pytest %ARGS% & goto :eof
echo Usage: scripts\run_tests.bat [quick^|phases^|all]
