"""Selecionar o adaptador sem acoplar serviços de agenda ao provedor."""
from app.llm import ModelError,OpenAIResponsesModel


def configured_language_model(config,*,openai_factory=None):
    if config.llm_provider=='gemini':
        from app.gemini_llm import GeminiModel
        key=config.gemini_api_key
        factory=GeminiModel
        model=config.gemini_model
        budget=config.gemini_max_output_tokens
    elif config.llm_provider=='openai':
        key=config.openai_api_key
        factory=OpenAIResponsesModel if openai_factory is None else openai_factory
        model=config.llm_model
        budget=config.llm_max_output_tokens
    else:
        raise ModelError('llm_not_configured',http_status=503)
    if key is None or not key.get_secret_value().strip():
        raise ModelError('llm_not_configured',http_status=503)
    return factory(api_key=key.get_secret_value(),model=model,
        timeout_seconds=config.llm_timeout_seconds,max_output_tokens=budget)
