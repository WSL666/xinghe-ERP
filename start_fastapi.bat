@echo off
chcp 65001 >nul
cd /d "%~dp0backend"

REM ============================================================
REM PRODUCTION: https://wangshilin888.com  (port 8443, HTTPS)
REM ------------------------------------------------------------
REM 8443 is HTTPS. Two deployment options:
REM
REM   (A) uvicorn terminates TLS directly. Fill in your cert paths:
REM       set SSL_CERT=<path-to>\wangshilin888.com.crt
REM       set SSL_KEY=<path-to>\wangshilin888.com.key
REM
REM   (B) nginx/caddy reverse-proxies to uvicorn on 8443 (HTTP).
REM       Then drop the --ssl-* flags below.
REM ============================================================

REM >>> Option A: uvicorn with TLS (default). Edit SSL_CERT/SSL_KEY >>> 
if not defined SSL_CERT set SSL_CERT=E:\code_workplace\ssl\wangshilin888.com.crt
if not defined SSL_KEY  set SSL_KEY=E:\code_workplace\ssl\wangshilin888.com.key

"E:\code_workplace\Anaconda_envs\envs\Agens\python.exe" -B -m uvicorn main:app --host 0.0.0.0 --port 8443 --ssl-certfile "%SSL_CERT%" --ssl-keyfile "%SSL_KEY%"

REM >>> Option B: reverse proxy (no TLS here). Uncomment, comment out Option A >>>
REM "E:\code_workplace\Anaconda_envs\envs\Agens\python.exe" -B -m uvicorn main:app --host 127.0.0.1 --port 8443

pause
