import unittest
import uuid
from datetime import datetime, timedelta, timezone

from app.db.models import DataTable, DataTableRow, User
from app.db.session import SessionLocal
from app.services.workflow_executor import WorkflowExecutor


def _is_db_reachable() -> bool:
    try:
        from sqlalchemy import text

        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


class DataTablePostgreSqlSortIntegrationTests(unittest.TestCase):
    """PostgreSQL integration tests covering issue #648 sort criteria:

    - User schema columns sort correctly (string & number).
    - Numeric columns sort numerically (2, 10, 100), not lexicographically.
    - Non-numeric or empty values in number columns do not crash the query.
    - NULLS LAST is explicitly respected on both ASC and DESC.
    - Metadata columns (created_at, updated_at) map to real table columns.
    - id provides deterministic tie-breaking.
    - limit=1 returns the highest-score row rather than the newest row.
    - Both find and getAll operations honor the configured sort column.
    """

    def setUp(self) -> None:
        if not _is_db_reachable():
            self.skipTest("PostgreSQL database is not reachable")

        self.user_id = uuid.uuid4()
        self.table_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        with SessionLocal() as db:
            user = User(
                id=self.user_id,
                email=f"test-sort-{self.user_id}@example.com",
                hashed_password="hashed_pw",
                name="Sort Tester",
            )
            db.add(user)
            db.flush()

            table = DataTable(
                id=self.table_id,
                name=f"sort_test_table_{self.table_id}",
                owner_id=user.id,
                columns=[
                    {"name": "name", "type": "string"},
                    {"name": "score", "type": "number"},
                ],
            )
            db.add(table)
            db.flush()

            # Row 1: Alice, score 10, oldest created, middle updated
            self.row_alice = DataTableRow(
                id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
                table_id=table.id,
                data={"name": "Alice", "score": 10},
                created_at=now - timedelta(days=3),
                updated_at=now - timedelta(days=2),
            )
            # Row 2: Bob, score 100, middle created, oldest updated
            self.row_bob = DataTableRow(
                id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
                table_id=table.id,
                data={"name": "Bob", "score": 100},
                created_at=now - timedelta(days=2),
                updated_at=now - timedelta(days=3),
            )
            # Row 3: Charlie, score 2, newest created, newest updated
            self.row_charlie = DataTableRow(
                id=uuid.UUID("00000000-0000-0000-0000-000000000003"),
                table_id=table.id,
                data={"name": "Charlie", "score": 2},
                created_at=now - timedelta(days=1),
                updated_at=now - timedelta(days=1),
            )
            # Row 4: Dave, non-numeric score string
            self.row_dave = DataTableRow(
                id=uuid.UUID("00000000-0000-0000-0000-000000000004"),
                table_id=table.id,
                data={"name": "Dave", "score": "not-a-number"},
                created_at=now,
                updated_at=now,
            )
            # Row 5: Eve, empty string score
            self.row_eve = DataTableRow(
                id=uuid.UUID("00000000-0000-0000-0000-000000000005"),
                table_id=table.id,
                data={"name": "Eve", "score": ""},
                created_at=now,
                updated_at=now,
            )
            # Row 6: Frank, score key missing
            self.row_frank = DataTableRow(
                id=uuid.UUID("00000000-0000-0000-0000-000000000006"),
                table_id=table.id,
                data={"name": "Frank"},
                created_at=now,
                updated_at=now,
            )
            # Row 7: George, score 10 (tied with Alice, higher UUID than Alice)
            self.row_george = DataTableRow(
                id=uuid.UUID("00000000-0000-0000-0000-000000000007"),
                table_id=table.id,
                data={"name": "George", "score": 10},
                created_at=now,
                updated_at=now,
            )
            db.add_all(
                [
                    self.row_alice,
                    self.row_bob,
                    self.row_charlie,
                    self.row_dave,
                    self.row_eve,
                    self.row_frank,
                    self.row_george,
                ]
            )
            db.commit()

    def tearDown(self) -> None:
        if not _is_db_reachable():
            return
        with SessionLocal() as db:
            from sqlalchemy import delete

            db.execute(delete(DataTableRow).where(DataTableRow.table_id == self.table_id))
            db.execute(delete(DataTable).where(DataTable.id == self.table_id))
            db.execute(delete(User).where(User.id == self.user_id))
            db.commit()

    def _execute_dt(
        self,
        op: str,
        sort: str | None = None,
        limit: int | None = None,
        filter_str: str = "{}",
    ) -> list[dict]:
        node_data: dict = {
            "label": "dt",
            "dataTableId": str(self.table_id),
            "dataTableOperation": op,
            "dataTableFilter": filter_str,
        }
        if sort is not None:
            node_data["dataTableSort"] = sort
        if limit is not None:
            node_data["dataTableLimit"] = limit

        nodes = [{"id": "dt", "type": "dataTable", "data": node_data}]
        executor = WorkflowExecutor(nodes=nodes, edges=[], actor_user_id=self.user_id)
        result = executor.execute_node("dt", {})
        self.assertEqual(result.status, "success")
        return result.output["rows"]

    def test_dynamic_string_column_asc(self) -> None:
        rows = self._execute_dt("find", sort="name")
        names = [r["data"]["name"] for r in rows]
        self.assertEqual(names, ["Alice", "Bob", "Charlie", "Dave", "Eve", "Frank", "George"])

    def test_dynamic_string_column_desc(self) -> None:
        rows = self._execute_dt("find", sort="-name")
        names = [r["data"]["name"] for r in rows]
        self.assertEqual(names, ["George", "Frank", "Eve", "Dave", "Charlie", "Bob", "Alice"])

    def test_numeric_column_asc_orders_numerically_not_lexicographically(self) -> None:
        # 2, 10, 100 must come before unusable values (lexicographical would sort 10, 100, 2)
        rows = self._execute_dt("find", sort="score")
        valid_scores = [
            r["data"].get("score") for r in rows if isinstance(r["data"].get("score"), (int, float))
        ]
        self.assertEqual(valid_scores, [2, 10, 10, 100])

    def test_numeric_column_desc_orders_numerically(self) -> None:
        rows = self._execute_dt("find", sort="-score")
        valid_scores = [
            r["data"].get("score") for r in rows if isinstance(r["data"].get("score"), (int, float))
        ]
        self.assertEqual(valid_scores, [100, 10, 10, 2])

    def test_created_at_compatibility_uses_real_row_column(self) -> None:
        # created_at ASC: Alice (oldest), Bob, Charlie, then Dave/Eve/Frank/George
        rows_asc = self._execute_dt("find", sort="created_at")
        self.assertEqual(rows_asc[0]["data"]["name"], "Alice")
        self.assertEqual(rows_asc[1]["data"]["name"], "Bob")
        self.assertEqual(rows_asc[2]["data"]["name"], "Charlie")

        # created_at DESC: newest first, Alice last
        rows_desc = self._execute_dt("find", sort="-created_at")
        self.assertEqual(rows_desc[-1]["data"]["name"], "Alice")
        self.assertEqual(rows_desc[-2]["data"]["name"], "Bob")
        self.assertEqual(rows_desc[-3]["data"]["name"], "Charlie")

    def test_updated_at_compatibility_uses_real_row_column(self) -> None:
        # Bob has oldest updated_at (3 days ago), Alice (2 days ago), Charlie (1 day ago)
        rows_asc = self._execute_dt("find", sort="updated_at")
        self.assertEqual(rows_asc[0]["data"]["name"], "Bob")
        self.assertEqual(rows_asc[1]["data"]["name"], "Alice")
        self.assertEqual(rows_asc[2]["data"]["name"], "Charlie")

        rows_desc = self._execute_dt("find", sort="-updated_at")
        self.assertEqual(rows_desc[-1]["data"]["name"], "Bob")
        self.assertEqual(rows_desc[-2]["data"]["name"], "Alice")
        self.assertEqual(rows_desc[-3]["data"]["name"], "Charlie")

    def test_missing_empty_and_non_numeric_values_sort_nulls_last(self) -> None:
        # ASC: Valid numbers (Charlie 2, Alice 10, George 10, Bob 100), then Dave/Eve/Frank last
        rows_asc = self._execute_dt("find", sort="score")
        first_four = [r["data"]["name"] for r in rows_asc[:4]]
        last_three = {r["data"]["name"] for r in rows_asc[4:]}
        self.assertEqual(first_four, ["Charlie", "Alice", "George", "Bob"])
        self.assertEqual(last_three, {"Dave", "Eve", "Frank"})

    def test_descending_sort_places_nulls_last_never_first(self) -> None:
        # In PostgreSQL without NULLS LAST, NULLs sort first on DESC.
        # Verify that valid scores win DESC: Bob 100, Alice/George 10, Charlie 2, then Dave/Eve/Frank last.
        rows_desc = self._execute_dt("find", sort="-score")
        first_four = [r["data"]["name"] for r in rows_desc[:4]]
        last_three = {r["data"]["name"] for r in rows_desc[4:]}
        self.assertEqual(first_four, ["Bob", "Alice", "George", "Charlie"])
        self.assertEqual(last_three, {"Dave", "Eve", "Frank"})

    def test_limit_regression_highest_score_returned_instead_of_newest_row(self) -> None:
        # Issue #648 primary bug: -score + limit=1 returned newest row (Charlie/Dave/etc.)
        # With fix, Bob (score 100) must be returned!
        rows = self._execute_dt("find", sort="-score", limit=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["data"]["name"], "Bob")
        self.assertEqual(rows[0]["data"]["score"], 100)

    def test_deterministic_tie_breaking_by_id(self) -> None:
        # Alice (id ending in 0001) and George (id ending in 0007) both have score 10.
        # Order by score ASC or DESC must stably order Alice before George by id ASC.
        rows = self._execute_dt("find", sort="-score")
        tied_names = [r["data"]["name"] for r in rows if r["data"].get("score") == 10]
        self.assertEqual(tied_names, ["Alice", "George"])

    def test_get_all_operation_honors_sort_column(self) -> None:
        rows = self._execute_dt("getAll", sort="-score")
        self.assertEqual(rows[0]["data"]["name"], "Bob")
        self.assertEqual(rows[0]["data"]["score"], 100)


if __name__ == "__main__":
    unittest.main()
