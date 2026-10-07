"""Unit tests for guardrails_service (LLM-based content safety classification).

check_guardrails calls out to an LLM via create_openai_client/
create_guarded_openai_client (both imported into app.services.guardrails_service's
own namespace) - patch them AT THAT IMPORT POINT
("app.services.guardrails_service.create_openai_client", not
"app.services.openai_client.create_openai_client"), same convention as this
repo's other LLM-call tests. The mock's return value needs a
.chat.completions.create(...) method returning something _extract_llm_content
can read - a MagicMock with .choices[0].message.content set, or a plain dict
shaped like a raw ChatCompletion JSON response both work (see
_extract_llm_content's own two branches).
"""

import unittest
from unittest.mock import MagicMock, patch

from app.services.guardrails_service import (
    CATEGORY_LABELS,
    GuardrailCategory,
    GuardrailConfig,
    GuardrailViolationError,
    _extract_llm_content,
    check_guardrails,
)


class ExtractLlmContentTests(unittest.TestCase):
    def test_extracts_from_chat_completion_object(self) -> None:
        chat_completion = MagicMock()
        chat_completion.choices = [MagicMock(message=MagicMock(content='{"violated": []}'))]
        chat_completion.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
        result = _extract_llm_content(chat_completion)
        self.assertEqual(result, ('{"violated": []}', 10, 5))

    def test_extracts_from_raw_dict_response(self) -> None:
        response = {
            "choices": [{"message": {"content": '{"violated": []}'}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        result = _extract_llm_content(response)
        self.assertEqual(result, ('{"violated": []}', 10, 5))

    def test_empty_choices_returns_none_tuple(self) -> None:
        chat_completion = MagicMock()
        chat_completion.choices = []
        self.assertEqual(_extract_llm_content(chat_completion), (None, None, None))
        self.assertEqual(_extract_llm_content({"choices": []}), (None, None, None))

    def test_json_string_response_is_parsed(self) -> None:
        response = (
            '{"choices": [{"message": {"content": "{\\"violated\\": []}"}}], '
            '"usage": {"prompt_tokens": 10, "completion_tokens": 5}}'
        )
        result = _extract_llm_content(response)
        self.assertEqual(result, ('{"violated": []}', 10, 5))

    def test_unparseable_string_returns_none_tuple(self) -> None:
        self.assertEqual(_extract_llm_content("not json at all"), (None, None, None))

    def test_none_response_returns_none_tuple(self) -> None:
        self.assertEqual(_extract_llm_content(None), (None, None, None))


def _mock_client(content: str) -> MagicMock:
    """A fake OpenAI-compatible client whose chat.completions.create(...)
    returns a ChatCompletion-shaped response _extract_llm_content can read."""
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=content))]
    response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
    client = MagicMock()
    client.chat.completions.create.return_value = response
    return client


class CheckGuardrailsTests(unittest.TestCase):
    def test_disabled_config_does_nothing(self) -> None:
        config = GuardrailConfig(enabled=False, categories=[GuardrailCategory.NSFW])
        with patch("app.services.guardrails_service.create_openai_client") as mock_create:
            result = check_guardrails("hello", config, "openai", "key", model="gpt-4o-mini")
        self.assertIsNone(result)
        mock_create.assert_not_called()

    def test_no_categories_configured_does_nothing(self) -> None:
        config = GuardrailConfig(enabled=True, categories=[])
        with patch("app.services.guardrails_service.create_openai_client") as mock_create:
            result = check_guardrails("hello", config, "openai", "key", model="gpt-4o-mini")
        self.assertIsNone(result)
        mock_create.assert_not_called()

    def test_blank_text_does_nothing(self) -> None:
        config = GuardrailConfig(enabled=True, categories=[GuardrailCategory.NSFW])
        with patch("app.services.guardrails_service.create_openai_client") as mock_create:
            result = check_guardrails("   ", config, "openai", "key", model="gpt-4o-mini")
        self.assertIsNone(result)
        mock_create.assert_not_called()

    def test_no_violation_does_not_raise(self) -> None:
        config = GuardrailConfig(enabled=True, categories=[GuardrailCategory.NSFW])
        client = _mock_client('{"violated": []}')
        with patch("app.services.guardrails_service.create_openai_client", return_value=client):
            result = check_guardrails("hello", config, "openai", "key", model="gpt-4o-mini")
        self.assertIsNone(result)

    def test_violation_raises_with_human_readable_category(self) -> None:
        config = GuardrailConfig(enabled=True, categories=[GuardrailCategory.NSFW])
        client = _mock_client('{"violated": ["nsfw"]}')
        with patch("app.services.guardrails_service.create_openai_client", return_value=client):
            with self.assertRaises(GuardrailViolationError) as ctx:
                check_guardrails("hello", config, "openai", "key", model="gpt-4o-mini")
        self.assertEqual(ctx.exception.categories, [CATEGORY_LABELS[GuardrailCategory.NSFW]])

    def test_missing_model_raises_guardrail_violation_not_value_error(self) -> None:
        config = GuardrailConfig(enabled=True, categories=[GuardrailCategory.NSFW])
        with patch("app.services.guardrails_service.create_openai_client") as mock_create:
            with self.assertRaises(GuardrailViolationError) as ctx:
                check_guardrails("hello", config, "openai", "key", model="   ")
        self.assertIn("Content safety check failed", str(ctx.exception))
        mock_create.assert_not_called()

    def test_unrecognized_response_format_raises(self) -> None:
        config = GuardrailConfig(enabled=True, categories=[GuardrailCategory.NSFW])
        client = MagicMock()
        client.chat.completions.create.return_value = MagicMock(spec=[])
        with patch("app.services.guardrails_service.create_openai_client", return_value=client):
            with self.assertRaises(GuardrailViolationError) as ctx:
                check_guardrails("hello", config, "openai", "key", model="gpt-4o-mini")
        self.assertIn("unexpected response format", str(ctx.exception))

    def test_empty_llm_response_raises(self) -> None:
        config = GuardrailConfig(enabled=True, categories=[GuardrailCategory.NSFW])
        client = _mock_client("   ")
        with patch("app.services.guardrails_service.create_openai_client", return_value=client):
            with self.assertRaises(GuardrailViolationError) as ctx:
                check_guardrails("hello", config, "openai", "key", model="gpt-4o-mini")
        self.assertIn("no classification", str(ctx.exception))

    def test_markdown_fenced_response_is_unwrapped(self) -> None:
        config = GuardrailConfig(enabled=True, categories=[GuardrailCategory.NSFW])
        client = _mock_client('```json\n{"violated": []}\n```')
        with patch("app.services.guardrails_service.create_openai_client", return_value=client):
            result = check_guardrails("hello", config, "openai", "key", model="gpt-4o-mini")
        self.assertIsNone(result)

    def test_invalid_json_response_raises(self) -> None:
        config = GuardrailConfig(enabled=True, categories=[GuardrailCategory.NSFW])
        client = _mock_client("not json at all")
        with patch("app.services.guardrails_service.create_openai_client", return_value=client):
            with self.assertRaises(GuardrailViolationError) as ctx:
                check_guardrails("hello", config, "openai", "key", model="gpt-4o-mini")
        self.assertIn("could not parse classification response", str(ctx.exception))

    def test_violated_categories_outside_the_requested_set_are_filtered(self) -> None:
        config = GuardrailConfig(enabled=True, categories=[GuardrailCategory.NSFW])
        client = _mock_client('{"violated": ["nsfw", "violence"]}')
        with patch("app.services.guardrails_service.create_openai_client", return_value=client):
            with self.assertRaises(GuardrailViolationError) as ctx:
                check_guardrails("hello", config, "openai", "key", model="gpt-4o-mini")
        self.assertEqual(ctx.exception.categories, [CATEGORY_LABELS[GuardrailCategory.NSFW]])

    def test_base_url_selects_the_guarded_client_factory(self) -> None:
        config = GuardrailConfig(enabled=True, categories=[GuardrailCategory.NSFW])
        client = _mock_client('{"violated": []}')
        with (
            patch(
                "app.services.guardrails_service.create_openai_client", return_value=client
            ) as mock_plain,
            patch(
                "app.services.guardrails_service.create_guarded_openai_client",
                return_value=client,
            ) as mock_guarded,
        ):
            check_guardrails("hello", config, "openai", "key", model="gpt-4o-mini")
            mock_plain.assert_called_once()
            mock_guarded.assert_not_called()

            mock_plain.reset_mock()
            mock_guarded.reset_mock()

            check_guardrails(
                "hello",
                config,
                "openai",
                "key",
                base_url="https://example.com",
                model="gpt-4o-mini",
            )
            mock_guarded.assert_called_once()
            mock_plain.assert_not_called()


if __name__ == "__main__":
    unittest.main()
