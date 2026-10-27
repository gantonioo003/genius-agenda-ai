"""API inicial com saúde pública e fronteira v1 autenticada."""

from contextlib import asynccontextmanager
from starlette.concurrency import run_in_threadpool

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api_confirmations import register_confirmation_routes
from app.api_whatsapp_agent import register_whatsapp_agent_route
from app.api_agent_updates import register_agent_update_routes
from app.api_agent_deletion import register_agent_deletion_route
from app.api_agent_creation import register_agent_creation_route
from app.api_agent_dialogue import register_agent_dialogue_routes
from app.api_conversations import register_conversation_routes
from app.api_agent_queries import register_agent_query_routes
from app.api_preparation import register_preparation_route
from app.api_agent import register_agent_routes
from app.api_creation import creation_http_error_handler, register_creation_route
from app.api_deletion import register_deletion_routes
from app.api_queries import default_calendar_gateway, query_validation_error_handler, register_query_routes
from app.api_updates import register_update_routes
from app.api_whatsapp import register_reply_route
from app.api_reminders import register_reminder_routes
from app.api_reminder_delivery import register_reminder_delivery_routes
from app.api_reminder_recovery import register_reminder_recovery_routes
from app.api_summary_context import register_summary_context_routes
from app.api_agenda_summary import register_agenda_summary_routes
from app.api_availability import register_availability_routes
from app.api_briefing import register_briefing_routes
from app.api_security import (
    ApiSecurityError, RequestCorrelationMiddleware, api_security_error_handler,
    create_api_router, security_envelope,
)
from app.database import Database, database
from app.settings import Settings, settings


def create_app(config: Settings | None = None, db: Database | None = None, *,
               calendar_gateway_factory=None, sandbox_reply_service_factory=None, agent_preview_service_factory=None, agent_query_service_factory=None, conversation_store_factory=None, agent_dialogue_service_factory=None, confirmation_service_factory=None, agent_creation_store_factory=None, agent_creation_service_factory=None, agent_update_store_factory=None, agent_update_service_factory=None, agent_deletion_store_factory=None, agent_deletion_service_factory=None, whatsapp_agent_store_factory=None, whatsapp_agent_worker_factory=None, whatsapp_agent_service_factory=None, reminder_store_factory=None, reminder_gateway_factory=None, reminder_recipient_binding_loader=None, reminder_delivery_recipient_loader=None, reminder_delivery_sender_factory=None, summary_calendar_gateway_factory=None, summary_model_factory=None, availability_gateway_factory=None, briefing_gateway_factory=None) -> FastAPI:
    config = settings if config is None else config
    selected_db = database if db is None else db

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        selected_db.initialize()
        worker = None
        if config.whatsapp_agent_enabled:
            try:
                from app.whatsapp_agent_worker import default_whatsapp_agent_worker
                worker = (default_whatsapp_agent_worker(application) if whatsapp_agent_worker_factory is None
                          else whatsapp_agent_worker_factory())
                worker.start()
            except Exception:
                raise RuntimeError("Não foi possível iniciar o processamento do agente WhatsApp.") from None
        try:
            yield
        finally:
            try:
                if worker is not None:
                    await run_in_threadpool(worker.stop)
            finally:
                from app.gemini_transport import close_gemini_transport
                await run_in_threadpool(close_gemini_transport)

    application = FastAPI(
        title=config.app_name,
        version="0.1.0",
        description="Assistente de agenda para entrevistas de emprego.",
        lifespan=lifespan,
    )
    application.state.settings = config
    application.state.database = selected_db
    application.state.sandbox_reply_service_factory = sandbox_reply_service_factory
    application.state.agent_preview_service_factory = agent_preview_service_factory
    application.state.agent_query_service_factory = agent_query_service_factory
    application.state.conversation_store_factory = conversation_store_factory
    application.state.agent_dialogue_service_factory = agent_dialogue_service_factory
    application.state.confirmation_service_factory = confirmation_service_factory
    application.state.agent_creation_store_factory = agent_creation_store_factory
    application.state.agent_creation_service_factory = agent_creation_service_factory
    application.state.agent_update_store_factory = agent_update_store_factory
    application.state.agent_update_service_factory = agent_update_service_factory
    application.state.agent_deletion_store_factory = agent_deletion_store_factory
    application.state.agent_deletion_service_factory = agent_deletion_service_factory
    application.state.whatsapp_agent_store_factory = whatsapp_agent_store_factory
    application.state.whatsapp_agent_worker_factory = whatsapp_agent_worker_factory
    application.state.whatsapp_agent_service_factory = whatsapp_agent_service_factory
    application.state.reminder_store_factory = reminder_store_factory
    application.state.reminder_gateway_factory = reminder_gateway_factory
    application.state.reminder_recipient_binding_loader = reminder_recipient_binding_loader
    application.state.reminder_delivery_recipient_loader = reminder_delivery_recipient_loader
    application.state.reminder_delivery_sender_factory = reminder_delivery_sender_factory
    application.state.summary_calendar_gateway_factory = summary_calendar_gateway_factory
    application.state.summary_model_factory = summary_model_factory
    application.state.availability_gateway_factory = availability_gateway_factory
    application.state.briefing_gateway_factory = briefing_gateway_factory
    application.state.calendar_gateway_factory = (
        default_calendar_gateway if calendar_gateway_factory is None else calendar_gateway_factory
    )
    application.add_middleware(RequestCorrelationMiddleware)
    application.add_exception_handler(ApiSecurityError, api_security_error_handler)
    application.add_exception_handler(RequestValidationError, query_validation_error_handler)
    application.add_exception_handler(StarletteHTTPException, creation_http_error_handler)

    @application.get("/health", tags=["health"], summary="Verificar se a API está respondendo")
    async def health() -> dict[str, str]:
        return {
            "status": "ok", "service": config.app_name, "environment": config.app_env,
        }

    router = create_api_router()

    @router.get("/status", tags=["api"], summary="Conferir o acesso autorizado à API")
    async def api_status(request: Request) -> dict:
        return security_envelope(
            request, status="succeeded", mutation_state="not_applicable",
            message="Acesso autorizado.",
            data={"authenticated": True, "service": config.app_name},
        )

    register_query_routes(router)
    register_creation_route(router)
    register_update_routes(router)
    register_deletion_routes(router)
    register_reply_route(router)
    register_whatsapp_agent_route(router)
    register_agent_routes(router)
    register_agent_query_routes(router)
    register_conversation_routes(router)
    register_agent_dialogue_routes(router)
    register_agent_creation_route(router)
    register_agent_update_routes(router)
    register_agent_deletion_route(router)
    register_confirmation_routes(router)
    register_preparation_route(router)
    register_reminder_routes(router)
    register_reminder_delivery_routes(router)
    register_reminder_recovery_routes(router)
    register_summary_context_routes(router)
    register_agenda_summary_routes(router)
    register_availability_routes(router)
    register_briefing_routes(router)
    application.include_router(router)
    return application


app = create_app()
