@echo off
cd /d C:\dev\Role-LLM\ai-roleplay-chat
start "FastAPI Server" cmd /k "uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"