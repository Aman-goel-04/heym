"""Tests for global variable numeric value validation and coercion.

Regression coverage for issue #685:
- Invalid numeric input must be rejected with HTTP 422 rather than escaping as HTTP 500.
- Non-finite numbers ("nan", "inf", "-inf", "1e999", etc.) must be rejected with HTTP 422.
- Valid input ("42", "3.14", "", "1e5", "7.7", "-5") preserves existing behavior.
- Both create_global_variable and update_global_variable are covered.
- Update with omitted value_type when the stored variable is already a number is covered.
"""

import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from app.api import global_variables as global_variables_api
from app.db.models import GlobalVariable
from app.models.schemas import GlobalVariableCreate, GlobalVariableUpdate


def _fake_user(user_id: uuid.UUID | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id or uuid.uuid4(),
        email="developer@example.com",
        name="Developer",
    )


def _make_stored_variable(
    name: str = "counter",
    value: object = 10,
    value_type: str = "number",
    owner_id: uuid.UUID | None = None,
) -> GlobalVariable:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    var = GlobalVariable(
        name=name,
        value={"v": value},
        value_type=value_type,
        owner_id=owner_id or uuid.uuid4(),
    )
    var.id = uuid.uuid4()
    var.created_at = now
    var.updated_at = now
    return var


class _SingleResult:
    def __init__(self, item: object | None) -> None:
        self._item = item

    def scalar_one_or_none(self) -> object | None:
        return self._item


class CoerceValueNumericValidationTests(unittest.TestCase):
    """Direct tests for _coerce_value numeric type handling."""

    def test_valid_numbers_preserve_existing_behavior(self) -> None:
        cases = [
            ("42", 42),
            ("3.14", 3.14),
            ("", 0),
            ("1e5", 100000.0),
            ("7.7", 7.7),
            ("-5", -5),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw, expected=expected):
                result = global_variables_api._coerce_value(raw, "number")
                self.assertEqual(result, expected)
                self.assertIs(type(result), type(expected))

    def test_valid_numeric_types_preserve_existing_behavior(self) -> None:
        self.assertEqual(global_variables_api._coerce_value(42, "number"), 42.0)
        self.assertEqual(global_variables_api._coerce_value(3.14, "number"), 3.14)
        self.assertEqual(global_variables_api._coerce_value(0, "number"), 0.0)
        self.assertEqual(global_variables_api._coerce_value(None, "number"), 0)

    def test_malformed_numeric_strings_raise_422(self) -> None:
        malformed = ["abc", "1.2.3", "hello world", "foo123", "--5", "1..0"]
        for raw in malformed:
            with self.subTest(raw=raw):
                with self.assertRaises(HTTPException) as ctx:
                    global_variables_api._coerce_value(raw, "number")
                self.assertEqual(ctx.exception.status_code, 422)
                self.assertEqual(ctx.exception.detail, "Invalid number value")

    def test_non_finite_strings_and_floats_raise_422(self) -> None:
        non_finite_inputs = [
            "nan",
            "NaN",
            "inf",
            "INF",
            "+inf",
            "-inf",
            "Infinity",
            "-Infinity",
            "1e999",
            "-1e999",
            float("nan"),
            float("inf"),
            float("-inf"),
        ]
        for raw in non_finite_inputs:
            with self.subTest(raw=raw):
                with self.assertRaises(HTTPException) as ctx:
                    global_variables_api._coerce_value(raw, "number")
                self.assertEqual(ctx.exception.status_code, 422)
                self.assertEqual(ctx.exception.detail, "Invalid number value")

    def test_non_number_types_are_unaffected(self) -> None:
        self.assertEqual(global_variables_api._coerce_value("true", "boolean"), True)
        self.assertEqual(global_variables_api._coerce_value("false", "boolean"), False)
        self.assertEqual(global_variables_api._coerce_value("test", "string"), "test")
        self.assertEqual(global_variables_api._coerce_value([1, 2], "array"), [1, 2])
        self.assertEqual(global_variables_api._coerce_value({"k": "v"}, "object"), {"k": "v"})
        self.assertEqual(global_variables_api._coerce_value("raw", "auto"), "raw")


class CreateGlobalVariableValidationTests(unittest.IsolatedAsyncioTestCase):
    """Tests for create_global_variable endpoint validation."""

    def _mock_db_for_create(self) -> AsyncMock:
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_SingleResult(None))
        db.add = MagicMock()
        db.flush = AsyncMock()

        async def _refresh(obj: GlobalVariable) -> None:
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            now = datetime(2026, 1, 1, tzinfo=timezone.utc)
            obj.created_at = now
            obj.updated_at = now

        db.refresh = AsyncMock(side_effect=_refresh)
        return db

    async def test_create_with_invalid_numeric_text_raises_422(self) -> None:
        db = self._mock_db_for_create()
        user = _fake_user()
        for invalid_val in ["abc", "1.2.3"]:
            with self.subTest(val=invalid_val):
                req = GlobalVariableCreate(name="num_var", value=invalid_val, value_type="number")
                with patch.object(global_variables_api, "audit") as mock_audit:
                    with self.assertRaises(HTTPException) as ctx:
                        await global_variables_api.create_global_variable(
                            data=req, current_user=user, db=db
                        )
                    self.assertEqual(ctx.exception.status_code, 422)
                    self.assertEqual(ctx.exception.detail, "Invalid number value")
                    db.add.assert_not_called()
                    mock_audit.assert_not_called()

    async def test_create_with_non_finite_numbers_raises_422(self) -> None:
        db = self._mock_db_for_create()
        user = _fake_user()
        for non_finite in ["nan", "inf", "-inf", "1e999"]:
            with self.subTest(val=non_finite):
                req = GlobalVariableCreate(name="num_var", value=non_finite, value_type="number")
                with patch.object(global_variables_api, "audit") as mock_audit:
                    with self.assertRaises(HTTPException) as ctx:
                        await global_variables_api.create_global_variable(
                            data=req, current_user=user, db=db
                        )
                    self.assertEqual(ctx.exception.status_code, 422)
                    self.assertEqual(ctx.exception.detail, "Invalid number value")
                    db.add.assert_not_called()
                    mock_audit.assert_not_called()

    async def test_create_with_valid_numbers_succeeds(self) -> None:
        db = self._mock_db_for_create()
        user = _fake_user()
        cases = [
            ("42", 42),
            ("3.14", 3.14),
            ("", 0),
            ("1e5", 100000.0),
        ]
        for val, expected in cases:
            with self.subTest(val=val):
                req = GlobalVariableCreate(
                    name=f"var_{val}",
                    value=val,
                    value_type="number",
                )
                with patch.object(global_variables_api, "audit"):
                    resp = await global_variables_api.create_global_variable(
                        data=req, current_user=user, db=db
                    )
                self.assertEqual(resp.value, expected)
                self.assertIs(type(resp.value), type(expected))
                self.assertEqual(resp.value_type, "number")


class UpdateGlobalVariableValidationTests(unittest.IsolatedAsyncioTestCase):
    """Tests for update_global_variable endpoint validation."""

    def _mock_db_for_update(self, variable: GlobalVariable) -> AsyncMock:
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_SingleResult(variable))
        db.flush = AsyncMock()
        db.refresh = AsyncMock()
        return db

    async def test_update_with_explicit_number_type_and_invalid_value_raises_422(self) -> None:
        user = _fake_user()
        variable = _make_stored_variable(
            name="my_var", value="hello", value_type="string", owner_id=user.id
        )
        db = self._mock_db_for_update(variable)

        for invalid_val in ["abc", "1.2.3", "nan", "inf", "-inf", "1e999"]:
            with self.subTest(val=invalid_val):
                req = GlobalVariableUpdate(value=invalid_val, value_type="number")
                with patch.object(global_variables_api, "audit") as mock_audit:
                    with self.assertRaises(HTTPException) as ctx:
                        await global_variables_api.update_global_variable(
                            variable_id=variable.id,
                            data=req,
                            current_user=user,
                            db=db,
                        )
                    self.assertEqual(ctx.exception.status_code, 422)
                    self.assertEqual(ctx.exception.detail, "Invalid number value")
                    mock_audit.assert_not_called()

    async def test_update_with_omitted_value_type_when_stored_is_number_raises_422(self) -> None:
        """When data.value_type is None and variable.value_type is 'number', validate with stored type."""
        user = _fake_user()
        variable = _make_stored_variable(
            name="counter", value=10, value_type="number", owner_id=user.id
        )
        db = self._mock_db_for_update(variable)

        for invalid_val in ["abc", "1.2.3", "nan", "inf", "-inf", "1e999"]:
            with self.subTest(val=invalid_val):
                req = GlobalVariableUpdate(value=invalid_val)
                self.assertIsNone(req.value_type)

                with patch.object(global_variables_api, "audit") as mock_audit:
                    with self.assertRaises(HTTPException) as ctx:
                        await global_variables_api.update_global_variable(
                            variable_id=variable.id,
                            data=req,
                            current_user=user,
                            db=db,
                        )
                    self.assertEqual(ctx.exception.status_code, 422)
                    self.assertEqual(ctx.exception.detail, "Invalid number value")
                    mock_audit.assert_not_called()

    async def test_update_with_valid_number_succeeds(self) -> None:
        user = _fake_user()
        variable = _make_stored_variable(
            name="counter", value=10, value_type="number", owner_id=user.id
        )
        db = self._mock_db_for_update(variable)

        # 1. Explicit value_type="number"
        req1 = GlobalVariableUpdate(value="42", value_type="number")
        with patch.object(global_variables_api, "audit"):
            resp1 = await global_variables_api.update_global_variable(
                variable_id=variable.id,
                data=req1,
                current_user=user,
                db=db,
            )
        self.assertEqual(resp1.value, 42)
        self.assertIs(type(resp1.value), int)

        # 2. Omitted value_type with stored value_type="number"
        req2 = GlobalVariableUpdate(value="3.14")
        with patch.object(global_variables_api, "audit"):
            resp2 = await global_variables_api.update_global_variable(
                variable_id=variable.id,
                data=req2,
                current_user=user,
                db=db,
            )
        self.assertEqual(resp2.value, 3.14)
        self.assertIs(type(resp2.value), float)

        # 3. Empty string coercing to 0
        req3 = GlobalVariableUpdate(value="")
        with patch.object(global_variables_api, "audit"):
            resp3 = await global_variables_api.update_global_variable(
                variable_id=variable.id,
                data=req3,
                current_user=user,
                db=db,
            )
        self.assertEqual(resp3.value, 0)
        self.assertIs(type(resp3.value), int)
