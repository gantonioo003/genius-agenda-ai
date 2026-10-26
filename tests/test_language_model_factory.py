"""Seleção de provedor sem fallback pago e configuração privada fora do repr."""
from pathlib import Path
import unittest
from unittest.mock import patch
from pydantic import ValidationError
from app.language_model import configured_language_model
from app.settings import Settings
from app.llm import ModelError
from app.services.agent_preview import default_agent_preview_service
from app.services.agent_dialogue import default_agent_dialogue_service
from app.services.agent_queries import default_agent_query_service

class LanguageModelFactoryTests(unittest.TestCase):
    def config(self,**kwargs):return Settings(_env_file=None,**kwargs)
    def test_gemini_selected_for_all_default_services_without_openai(self):
        config=self.config(llm_provider='gemini',gemini_api_key='PRIVATE_GEMINI_FIXTURE',openai_api_key='PRIVATE_OPENAI_FIXTURE')
        with patch('app.gemini_llm.GeminiModel') as gemini,patch('app.language_model.OpenAIResponsesModel') as openai:
            default_agent_preview_service(config)
            default_agent_dialogue_service(config,lambda:None)
            default_agent_query_service(config,lambda:None)
            self.assertEqual(gemini.call_count,3)
            openai.assert_not_called()
            self.assertEqual(gemini.call_args.kwargs['model'],config.gemini_model)
            self.assertEqual(gemini.call_args.kwargs['max_output_tokens'],2048)
    def test_missing_gemini_key_never_falls_back_to_openai(self):
        config=self.config(llm_provider='gemini',gemini_api_key=None,openai_api_key='PRIVATE_OPENAI_FIXTURE')
        with patch('app.language_model.OpenAIResponsesModel') as openai:
            with self.assertRaises(ModelError) as error:configured_language_model(config)
            self.assertEqual(error.exception.code,'llm_not_configured');openai.assert_not_called()
    def test_openai_compatibility_keeps_injected_factory(self):
        config=self.config(llm_provider='openai',openai_api_key='PRIVATE_OPENAI_FIXTURE',gemini_api_key='PRIVATE_GEMINI_FIXTURE')
        with patch('app.services.agent_preview.OpenAIResponsesModel') as openai,patch('app.gemini_llm.GeminiModel') as gemini:
            default_agent_preview_service(config);openai.assert_called_once();gemini.assert_not_called()
    def test_provider_and_model_reject_urls_or_unknown_values(self):
        for kwargs in ({'llm_provider':'unknown'},{'gemini_model':'https://private.invalid/model'},{'gemini_model':'../bad'},{'gemini_api_key':'key\r\nheader'}):
            with self.assertRaises(ValidationError):self.config(**kwargs)
    def test_private_keys_not_in_settings_dump_or_repr(self):
        config=self.config(llm_provider='gemini',gemini_api_key='PRIVATE_GEMINI_FIXTURE',openai_api_key='PRIVATE_OPENAI_FIXTURE')
        for secret in ('PRIVATE_GEMINI_FIXTURE','PRIVATE_OPENAI_FIXTURE'):
            self.assertNotIn(secret,repr(config));self.assertNotIn(secret,str(config.model_dump()))
    def test_gemini_budget_parses_environment_text_but_rejects_bool_and_out_of_range(self):
        self.assertEqual(self.config(gemini_max_output_tokens='2048').gemini_max_output_tokens,2048)
        for value in (True,127,4097,'private'):
            with self.assertRaises(ValidationError):self.config(gemini_max_output_tokens=value)
    def test_default_stays_openai_for_legacy_and_example_allows_free_provider(self):
        self.assertEqual(self.config().llm_provider,'openai')
        self.assertEqual(self.config().gemini_model,'gemini-3.8-flash')
        self.assertIsNone(self.config(gemini_api_key='').gemini_api_key)

if __name__=='__main__':unittest.main()
