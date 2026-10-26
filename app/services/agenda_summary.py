"""Resumo factual: IA escolhe somente organização de fatos já validados."""
from app.llm import ModelError
from app.services.agent_preview import validate_preview_reply
from app.services.summary_context import (
    SummaryCollectionError, SummaryInputError, validate_input,
)
from app.summary_plan import (
    SUMMARY_INSTRUCTIONS, PreparedSummary, SummaryPlan,
    prepare_summary, parse_summary_plan, render_summary, render_summary_whatsapp,
)


_MODEL_REASONS = frozenset({
    "llm_not_configured", "llm_unavailable", "llm_invalid_response",
    "llm_credentials_rejected", "llm_rate_limited", "llm_timeout",
})
_SKIP_REASONS = frozenset({"empty_period", "context_budget_exceeded"})


def _model_reason(error):
    try:
        code = getattr(error, "code", None)
    except Exception:
        return "llm_unavailable"
    return code if type(code) is str and code in _MODEL_REASONS else "llm_unavailable"


class AgendaSummaryService:
    def __init__(self, context_service, model_factory, *, max_input_chars=4000, presentation='detailed'):
        if type(max_input_chars) is not int or not 1 <= max_input_chars <= 4000:
            raise ValueError("Limite inválido para organizar o resumo.")
        self._context_service = context_service
        self._model_factory = model_factory
        self._max_input_chars = max_input_chars
        if presentation not in {'detailed', 'whatsapp'}:
            raise ValueError('Apresentação do resumo inválida.')
        self._presentation = presentation

    def summarize(self, *, period: str, reference_date: str | None = None) -> dict:
        # Mesmo um serviço de coleta injetado não pode pular a entrada estrita.
        kind, reference = validate_input(period, reference_date)
        try:
            context = self._context_service.build(period=period, reference_date=reference_date)
            prepared = prepare_summary(context, max_input_chars=self._max_input_chars)
            if type(prepared) is not PreparedSummary:
                raise SummaryCollectionError()
            facts = prepared.context
            if (facts["period"]["kind"] != kind
                    or reference is not None and facts["period"]["reference_date"] != reference.isoformat()):
                raise SummaryCollectionError()
            reason = prepared.fallback_reason
            if (reason is not None and reason not in _SKIP_REASONS
                    or (reason is None) != (prepared.model_message is not None)):
                raise SummaryCollectionError()
        except (SummaryInputError, SummaryCollectionError):
            raise
        except Exception:
            # Falha de leitura/fonte não se transforma em resumo factual vazio.
            raise SummaryCollectionError() from None

        plan, model_name = None, None
        if reason is None:
            try:
                model = self._model_factory()
                reply = model.generate(instructions=SUMMARY_INSTRUCTIONS, message=prepared.model_message)
            except ModelError as error:
                reason = _model_reason(error)
            except Exception:
                reason = "llm_unavailable"
            else:
                try:
                    # Contrato comum do adaptador mais o plano editorial estrito.
                    reply = validate_preview_reply(reply)
                    plan = parse_summary_plan(reply, prepared)
                    if type(plan) is not SummaryPlan:
                        raise ValueError()
                    model_name = reply.model
                except Exception:
                    reason, plan, model_name = "llm_invalid_response", None, None

        try:
            # Selecionar o caminho antes de renderizar: nenhuma segunda chamada
            # ao modelo e nenhuma narrativa bruta é reaproveitada no fallback.
            text = (render_summary_whatsapp(prepared, plan) if self._presentation == 'whatsapp'
                    else render_summary(prepared, plan))
            if type(text) is not str or not text.strip() or len(text) > 3500:
                raise ValueError()
            text.encode("utf-8")
            facts = prepared.context  # Cópia do JSON canônico, não o dict original.
            return {
                "schema_version": 1, "period": facts["period"], "snapshot": facts["snapshot"],
                "totals": facts["totals"], "details": facts["details"],
                "summary": {"text": text, "mode": "ai_organized" if plan is not None else "factual",
                            "reason": reason},
                "generation": {"model": model_name}, "execution_allowed": False,
            }
        except SummaryCollectionError:
            raise
        except Exception:
            raise SummaryCollectionError() from None
