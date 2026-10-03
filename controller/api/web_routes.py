import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

log = logging.getLogger(__name__)

web_router = APIRouter(tags=["web"])


@web_router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("dashboard.html", {"request": request, "page": "dashboard"})


@web_router.get("/music", response_class=HTMLResponse)
async def music(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("music.html", {"request": request, "page": "music"})


@web_router.get("/modes", response_class=HTMLResponse)
async def modes(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("modes.html", {"request": request, "page": "modes"})


@web_router.get("/direct", response_class=HTMLResponse)
async def direct(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("direct.html", {"request": request, "page": "direct"})


@web_router.get("/settings", response_class=HTMLResponse)
async def settings(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("settings.html", {"request": request, "page": "settings"})
