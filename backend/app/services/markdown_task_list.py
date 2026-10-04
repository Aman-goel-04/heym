"""GFM task list helpers for dashboard markdown text widgets."""

import re

TASK_ITEM_RE = re.compile(r"^(\s*)([-*+])\s+\[([ xX])\]\s+(.*)$")


def has_task_items(markdown: str) -> bool:
    """Return True when markdown contains at least one GFM task list line."""
    return any(TASK_ITEM_RE.match(line) for line in markdown.splitlines())


def toggle_task_item(markdown: str, line_index: int) -> str:
    """Flip `[ ]` ↔ `[x]` on the task list line at ``line_index``.

    Raises:
        ValueError: When the line index is out of range or not a task item.
    """
    lines = markdown.split("\n")
    if line_index < 0 or line_index >= len(lines):
        raise ValueError(f"Invalid line_index: {line_index}")
    match = TASK_ITEM_RE.match(lines[line_index])
    if match is None:
        raise ValueError(f"Line {line_index} is not a task list item")
    indent, bullet, check, rest = match.groups()
    new_check = " " if check.lower() == "x" else "x"
    lines[line_index] = f"{indent}{bullet} [{new_check}] {rest}"
    return "\n".join(lines)


def update_task_item(markdown: str, line_index: int, text: str) -> str:
    """Replace the label text on a task list line, preserving checked state.

    Raises:
        ValueError: When the line index is out of range or not a task item.
    """
    lines = markdown.split("\n")
    if line_index < 0 or line_index >= len(lines):
        raise ValueError(f"Invalid line_index: {line_index}")
    match = TASK_ITEM_RE.match(lines[line_index])
    if match is None:
        raise ValueError(f"Line {line_index} is not a task list item")
    indent, bullet, check, _rest = match.groups()
    lines[line_index] = f"{indent}{bullet} [{check}] {text}"
    return "\n".join(lines)


def remove_task_item(markdown: str, line_index: int) -> str:
    """Remove a task list line from markdown.

    Raises:
        ValueError: When the line index is out of range or not a task item.
    """
    lines = markdown.split("\n")
    if line_index < 0 or line_index >= len(lines):
        raise ValueError(f"Invalid line_index: {line_index}")
    if TASK_ITEM_RE.match(lines[line_index]) is None:
        raise ValueError(f"Line {line_index} is not a task list item")
    del lines[line_index]
    if (
        line_index < len(lines)
        and lines[line_index].strip() == ""
        and line_index > 0
        and lines[line_index - 1].strip() == ""
    ):
        del lines[line_index]
    return "\n".join(lines)


def update_or_remove_task_item(markdown: str, line_index: int, text: str) -> str:
    """Update task item label text, or remove the line when text is blank."""
    if text.strip() == "":
        return remove_task_item(markdown, line_index)
    return update_task_item(markdown, line_index, text)
