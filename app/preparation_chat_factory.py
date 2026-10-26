"""Conectar serviços já implementados à conversa, sem novos segredos ou schema."""

from app.availability_calendar import default_availability_gateway
from app.briefing_calendar import default_briefing_gateway
from app.language_model import configured_language_model
from app.services.agenda_summary import AgendaSummaryService
from app.services.availability import AvailabilityService
from app.services.interview_briefing import InterviewBriefingService
from app.services.preparation_chat import PreparationChatService
from app.services.summary_context import SummaryContextService
from app.services.reschedule_suggestions import RescheduleSuggestionsService
from app.summary_calendar import default_summary_calendar_gateway


def default_preparation_chat_service(application):
    state = application.state
    config = state.settings
    summary_gateway = getattr(state, 'summary_calendar_gateway_factory', None) or default_summary_calendar_gateway
    briefing_gateway = getattr(state, 'briefing_gateway_factory', None) or default_briefing_gateway
    availability_gateway = getattr(state, 'availability_gateway_factory', None) or default_availability_gateway
    model_factory = getattr(state, 'summary_model_factory', None) or (lambda: configured_language_model(config))

    def calendar_zone():
        with summary_gateway() as gateway:
            return gateway.binding().time_zone

    return PreparationChatService(
        summary_factory=lambda: AgendaSummaryService(SummaryContextService(summary_gateway), model_factory,
            max_input_chars=config.llm_max_input_chars, presentation='whatsapp'),
        briefing_factory=lambda: InterviewBriefingService(briefing_gateway),
        availability_factory=lambda: AvailabilityService(availability_gateway),
        time_zone_loader=calendar_zone,
        reschedule_factory=lambda: RescheduleSuggestionsService(
            briefing_factory=lambda: InterviewBriefingService(briefing_gateway),
            availability_factory=lambda: AvailabilityService(availability_gateway)),
    )
