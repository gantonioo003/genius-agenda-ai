"""Expose existing read-only preparation services through the panel's session boundary."""

from fastapi import APIRouter, Depends

from app.api_agenda_summary import register_agenda_summary_routes
from app.api_availability import register_availability_routes
from app.api_briefing import register_briefing_routes
from app.panel_security import require_panel_session


def register_panel_preparation_routes(application):
    """Same service contracts, with Origin/CSRF checked before body or providers."""
    router = APIRouter(prefix='/painel/api', dependencies=[Depends(require_panel_session)],
                       tags=['panel-preparation'])
    register_agenda_summary_routes(router, presentation='whatsapp')
    register_availability_routes(router)
    register_briefing_routes(router)
    # Preserve separate names for diagnostics and generated operation identifiers.
    # The existing /api/v1 routes keep their names and service-key authentication.
    for route in router.routes:
        route.name = route.name.replace('api_v1_', 'panel_', 1)
    application.include_router(router)
