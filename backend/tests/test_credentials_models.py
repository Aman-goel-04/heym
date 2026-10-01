import unittest

from app.models.schemas import LLMModel


class CredentialsModelsContextWindowTests(unittest.TestCase):
    def test_known_models_get_context_window_via_substring_match(self) -> None:
        from app.services.context_compressor import KNOWN_LIMITS

        models = [
            LLMModel(id="gpt-4o-mini", name="GPT-4o mini"),
            LLMModel(id="claude-3-5-sonnet-20241022", name="Claude 3.5 Sonnet"),
            LLMModel(id="unknown-model-xyz", name="Unknown"),
        ]
        for m in models:
            model_lower = m.id.lower()
            for key, limit in KNOWN_LIMITS.items():
                if key in model_lower:
                    m.context_window = limit
                    break

        self.assertEqual(models[0].context_window, 128_000)
        self.assertEqual(models[1].context_window, 200_000)
        self.assertIsNone(models[2].context_window)

    def test_popular_models_resolve_to_their_own_context_window(self) -> None:
        from app.services.context_compressor import KNOWN_LIMITS

        expected = {
            "gpt-6.1-sol": 1_050_000,
            "gpt-5.6-luna": 1_050_000,
            "claude-opus-5.5": 1_000_000,
            "claude-sonnet-5.5": 1_000_000,
            "claude-haiku-4.5": 200_000,
            "gemini-3.8-flash": 1_048_576,
            "gemini-3.1-pro": 1_048_576,
            "deepseek-v4-flash-vision-exp": 1_000_000,
            "glm-5.3-flash": 1_000_000,
            "mimo-v2.6-pro": 1_048_576,
            # Family with differing windows resolves to the lower one.
            "mimo-v2.5-pro": 1_000_000,
            "mimo-v2.5": 1_000_000,
            "muse-spark-1.3-contributor": 1_048_576,
            "qwen3.8-flash": 1_000_000,
            "longcat-2.5-preview-free": 1_000_000,
            "hy3": 256_000,
            "kimi-k3": 1_048_576,
            "kimi-k2.6": 262_144,
            "kimi-k2.7-code": 262_144,
            "minimax-m2.7": 204_800,
            "grok-4.20": 2_000_000,
            "grok-4.7": 500_000,
            "grok-4.5": 500_000,
            "llama-4-scout": 10_000_000,
            "space-bunny-alpha": 1_000_000,
            # Alpha is 1,000,000 and free is 1,048,576; the lower window wins for both.
            "space-bunny-free": 1_000_000,
            "solar-pro-4": 524_288,
        }
        for model_id, limit in expected.items():
            resolved = next(
                (value for key, value in KNOWN_LIMITS.items() if key in model_id.lower()),
                None,
            )
            self.assertEqual(resolved, limit, model_id)
