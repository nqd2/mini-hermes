:: This method is not recommended, and we recommend you use the `start.sh` file with WSL instead.
@echo off
SETLOCAL ENABLEDELAYEDEXPANSION

:: Get the directory of the current script
SET "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%" || exit /b

:: Add conditional Playwright browser installation
IF /I "%WEB_LOADER_ENGINE%" == "playwright" (
    IF "%PLAYWRIGHT_WS_URL%" == "" (
        echo Installing Playwright browsers...
        playwright install chromium
        playwright install-deps chromium
    )
)

SET "KEY_FILE=.weai_secret_key"
IF NOT "%WEAI_SECRET_KEY_FILE%" == "" (
    SET "KEY_FILE=%WEAI_SECRET_KEY_FILE%"
)

IF "%PORT%"=="" SET PORT=8080
IF "%HOST%"=="" SET HOST=0.0.0.0
IF "%FORWARDED_ALLOW_IPS%"=="" SET "FORWARDED_ALLOW_IPS='*'"
SET "WEAI_SECRET_KEY=%WEAI_SECRET_KEY%"
SET "WEAI_JWT_SECRET_KEY=%WEAI_JWT_SECRET_KEY%"
IF "%WEAI_SECRET_KEY_LENGTH%" == "" (
    SET "WEAI_SECRET_KEY_LENGTH=24"
)

:: Check if WEAI_SECRET_KEY and WEAI_JWT_SECRET_KEY are not set
IF "%WEAI_SECRET_KEY% %WEAI_JWT_SECRET_KEY%" == " " (
    echo Loading WEAI_SECRET_KEY from file, not provided as an environment variable.

    IF NOT EXIST "!KEY_FILE!" (
        echo Generating WEAI_SECRET_KEY
        :: Generate a random value to use as a WEAI_SECRET_KEY in case the user didn't provide one
        SET "CHARSET=0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        SET "WEAI_SECRET_KEY="
        FOR /L %%i IN (1,1,!WEAI_SECRET_KEY_LENGTH!) DO (
            SET /A "INDEX=!RANDOM! %% 62"
            FOR %%j IN (!INDEX!) DO SET "WEAI_SECRET_KEY=!WEAI_SECRET_KEY!!CHARSET:~%%j,1!"
        )
        <nul SET /p="!WEAI_SECRET_KEY!">"!KEY_FILE!"
        echo WEAI_SECRET_KEY generated
    )

    echo Loading WEAI_SECRET_KEY from !KEY_FILE!
    SET /p WEAI_SECRET_KEY=<"!KEY_FILE!"
)

:: Execute uvicorn
SET "WEAI_SECRET_KEY=%WEAI_SECRET_KEY%"
IF "%UVICORN_WORKERS%"=="" SET UVICORN_WORKERS=1
if "%UVICORN_WS_PER_MESSAGE_DEFLATE%" == "" set "UVICORN_WS_PER_MESSAGE_DEFLATE=true"
uvicorn weai.main:app --host "%HOST%" --port "%PORT%" --forwarded-allow-ips %FORWARDED_ALLOW_IPS% --workers %UVICORN_WORKERS% --ws auto --ws-per-message-deflate %UVICORN_WS_PER_MESSAGE_DEFLATE%
:: For ssl user uvicorn weai.main:app --host "%HOST%" --port "%PORT%" --forwarded-allow-ips '*' --ssl-keyfile "key.pem" --ssl-certfile "cert.pem" --ws auto
