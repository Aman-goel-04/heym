import unittest

from app.services.markdown_task_list import (
    has_task_items,
    remove_task_item,
    toggle_task_item,
    update_or_remove_task_item,
    update_task_item,
)


class MarkdownTaskListTests(unittest.TestCase):
    def test_has_task_items(self):
        self.assertFalse(has_task_items("plain text"))
        self.assertTrue(has_task_items("- [ ] todo\n- [x] done"))

    def test_toggle_unchecked_to_checked(self):
        md = "- [ ] Seçenek 2\n- [ ] Seçenek 3"
        result = toggle_task_item(md, 0)
        self.assertEqual(result, "- [x] Seçenek 2\n- [ ] Seçenek 3")

    def test_toggle_checked_to_unchecked(self):
        md = "- [x] Seçili seçenek\n- [ ] Seçenek 2"
        result = toggle_task_item(md, 0)
        self.assertEqual(result, "- [ ] Seçili seçenek\n- [ ] Seçenek 2")

    def test_toggle_uppercase_x(self):
        md = "- [X] done"
        result = toggle_task_item(md, 0)
        self.assertEqual(result, "- [ ] done")

    def test_toggle_asterisk_bullet(self):
        md = "* [ ] item"
        result = toggle_task_item(md, 0)
        self.assertEqual(result, "* [x] item")

    def test_toggle_invalid_index_raises(self):
        with self.assertRaises(ValueError):
            toggle_task_item("- [ ] ok", 5)

    def test_toggle_non_task_line_raises(self):
        with self.assertRaises(ValueError):
            toggle_task_item("- plain bullet", 0)

    def test_update_task_item_preserves_checked_state(self):
        md = "- [x] Done item\n- [ ] Todo item"
        result = update_task_item(md, 0, "Finished item")
        self.assertEqual(result, "- [x] Finished item\n- [ ] Todo item")

    def test_update_task_item_unchecked(self):
        md = "- [ ] Old label"
        result = update_task_item(md, 0, "New label")
        self.assertEqual(result, "- [ ] New label")

    def test_remove_task_item(self):
        md = "- [ ] One\n- [ ] Two\n- [ ] Three"
        result = remove_task_item(md, 1)
        self.assertEqual(result, "- [ ] One\n- [ ] Three")

    def test_update_or_remove_blank_text_removes_line(self):
        md = "- [ ] One\n- [ ] Two"
        result = update_or_remove_task_item(md, 0, "   ")
        self.assertEqual(result, "- [ ] Two")

    def test_update_or_remove_non_blank_updates(self):
        md = "- [ ] One"
        result = update_or_remove_task_item(md, 0, "Updated")
        self.assertEqual(result, "- [ ] Updated")

    def test_remove_invalid_index_raises(self):
        with self.assertRaises(ValueError):
            remove_task_item("- [ ] ok", 5)
