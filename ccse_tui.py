#!/usr/bin/env python3
"""
CCSE TUI study tool

Uses:
  - ccse_2026_preguntas.txt
  - ccse_2026_respuestas.txt

Features:
  - 25-question simulated tests with the same category proportions as the
    supplied 300-question bank: 10 / 3 / 2 / 3 / 7.
  - Spaced-repetition review.
  - Random/category practice.
  - Persistent response history and per-question scheduling in SQLite.
  - Statistics by category and question.

Run:
    python3 ccse_tui.py ccse_2026_preguntas.txt ccse_2026_respuestas.txt

The database defaults to:
    ~/.local/share/ccse-tui/progress.sqlite3

Controls:
  Menus: Up/Down or j/k, Enter, q
  Questions: a/b/c or 1/2/3, Enter to continue, q to stop
"""

from __future__ import annotations

import argparse
import curses
import random
import re
import sqlite3
import textwrap
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence


QUESTION_RE = re.compile(r"^\s*(\d{4})\s+(.+?)\s*$")
OPTION_RE = re.compile(r"^\s*([abcABC])\.\s*(.+?)\s*$")
ANSWER_RE = re.compile(r"^\s*(\d{4})\s*:\s*([abcABC])\.\s*(.+?)\s*$")

CATEGORY_NAMES = {
    1: "Gobierno, legislación y participación ciudadana",
    2: "Derechos y deberes fundamentales",
    3: "Organización territorial y geografía",
    4: "Cultura e historia de España",
    5: "Sociedad española",
}

# The supplied bank has 120/36/24/36/84 questions.
# These counts preserve the same proportions in a 25-question test.
MOCK_DISTRIBUTION = {1: 10, 2: 3, 3: 2, 4: 3, 5: 7}


@dataclass(frozen=True)
class Question:
    qid: str
    text: str
    options: dict[str, str]
    answer: str

    @property
    def category(self) -> int:
        return int(self.qid[0])


@dataclass
class SessionResult:
    qid: str
    selected: str
    correct: bool
    elapsed_seconds: float


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


def parse_question_bank(questions_path: Path, answers_path: Path) -> list[Question]:
    answers: dict[str, str] = {}
    for line in read_text(answers_path).splitlines():
        match = ANSWER_RE.match(line)
        if match:
            qid, letter, _ = match.groups()
            answers[qid] = letter.lower()

    if not answers:
        raise ValueError(
            "No answers found. Expected lines such as "
            "'1001: a. una monarquía parlamentaria.'"
        )

    parsed: dict[str, dict[str, object]] = {}
    current_id: str | None = None

    for raw_line in read_text(questions_path).splitlines():
        line = raw_line.rstrip()

        qmatch = QUESTION_RE.match(line)
        if qmatch:
            qid, question_text = qmatch.groups()
            current_id = qid
            parsed[qid] = {"text": question_text, "options": {}}
            continue

        omatch = OPTION_RE.match(line)
        if omatch and current_id:
            letter, option_text = omatch.groups()
            options = parsed[current_id]["options"]
            assert isinstance(options, dict)
            options[letter.lower()] = option_text

    questions: list[Question] = []
    errors: list[str] = []

    for qid, item in parsed.items():
        options = item["options"]
        assert isinstance(options, dict)
        if qid not in answers:
            errors.append(f"{qid}: missing answer")
            continue
        option_letters = set(options)
        if option_letters not in ({"a", "b"}, {"a", "b", "c"}):
            errors.append(f"{qid}: expected options a/b or a/b/c")
            continue
        if answers[qid] not in option_letters:
            errors.append(f"{qid}: correct answer is not among its options")
            continue
        questions.append(
            Question(
                qid=qid,
                text=str(item["text"]),
                options={str(k): str(v) for k, v in options.items()},
                answer=answers[qid],
            )
        )

    answer_only = sorted(set(answers) - set(parsed))
    errors.extend(f"{qid}: present in answers but not questions" for qid in answer_only)

    if errors:
        preview = "\n".join(errors[:10])
        more = f"\n...and {len(errors) - 10} more" if len(errors) > 10 else ""
        raise ValueError(f"Question-bank validation failed:\n{preview}{more}")

    questions.sort(key=lambda q: q.qid)
    return questions


class ProgressDB:
    def __init__(self, path: Path, questions: Sequence[Question]) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self._create_schema()
        self._ensure_cards(questions)

    def close(self) -> None:
        self.conn.close()

    def _create_schema(self) -> None:
        self.conn.executescript(
            """
            PRAGMA foreign_keys = ON;
            PRAGMA journal_mode = WAL;

            CREATE TABLE IF NOT EXISTS cards (
                qid TEXT PRIMARY KEY,
                category INTEGER NOT NULL,
                due_at TEXT NOT NULL,
                interval_days REAL NOT NULL DEFAULT 0,
                ease REAL NOT NULL DEFAULT 2.5,
                repetitions INTEGER NOT NULL DEFAULT 0,
                lapses INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0,
                correct_count INTEGER NOT NULL DEFAULT 0,
                last_answered_at TEXT,
                last_correct INTEGER
            );

            CREATE TABLE IF NOT EXISTS responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                qid TEXT NOT NULL,
                answered_at TEXT NOT NULL,
                selected TEXT NOT NULL,
                correct_answer TEXT NOT NULL,
                was_correct INTEGER NOT NULL,
                elapsed_seconds REAL NOT NULL,
                mode TEXT NOT NULL,
                session_id TEXT NOT NULL,
                FOREIGN KEY (qid) REFERENCES cards(qid)
            );

            CREATE INDEX IF NOT EXISTS idx_cards_due
                ON cards(due_at);
            CREATE INDEX IF NOT EXISTS idx_responses_qid
                ON responses(qid);
            CREATE INDEX IF NOT EXISTS idx_responses_session
                ON responses(session_id);
            """
        )
        self.conn.commit()

    def _ensure_cards(self, questions: Sequence[Question]) -> None:
        due = iso(now_utc())
        self.conn.executemany(
            """
            INSERT OR IGNORE INTO cards (qid, category, due_at)
            VALUES (?, ?, ?)
            """,
            [(q.qid, q.category, due) for q in questions],
        )
        self.conn.commit()

    def due_ids(self, limit: int | None = None, category: int | None = None) -> list[str]:
        sql = "SELECT qid FROM cards WHERE due_at <= ?"
        params: list[object] = [iso(now_utc())]
        if category is not None:
            sql += " AND category = ?"
            params.append(category)
        sql += " ORDER BY due_at ASC, attempts ASC, qid ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [row["qid"] for row in self.conn.execute(sql, params)]

    def new_ids(self, category: int | None = None) -> list[str]:
        sql = "SELECT qid FROM cards WHERE attempts = 0"
        params: list[object] = []
        if category is not None:
            sql += " AND category = ?"
            params.append(category)
        sql += " ORDER BY qid"
        return [row["qid"] for row in self.conn.execute(sql, params)]

    def record(
        self,
        question: Question,
        selected: str,
        elapsed_seconds: float,
        mode: str,
        session_id: str,
    ) -> bool:
        correct = selected == question.answer
        row = self.conn.execute(
            "SELECT * FROM cards WHERE qid = ?", (question.qid,)
        ).fetchone()
        if row is None:
            raise RuntimeError(f"Missing card state for {question.qid}")

        ease = float(row["ease"])
        interval = float(row["interval_days"])
        repetitions = int(row["repetitions"])
        lapses = int(row["lapses"])

        # A compact SM-2-inspired schedule:
        #   first correct: 1 day
        #   second correct: 3 days
        #   later correct: previous interval × ease
        # Incorrect answers reset the success streak and return quickly.
        if correct:
            repetitions += 1
            if repetitions == 1:
                interval = 1.0
            elif repetitions == 2:
                interval = 3.0
            else:
                interval = max(1.0, interval * ease)
            ease = min(3.0, ease + 0.05)
        else:
            repetitions = 0
            lapses += 1
            interval = 0.25 if lapses <= 2 else 1.0
            ease = max(1.3, ease - 0.20)

        answered_at = now_utc()
        due_at = answered_at + timedelta(days=interval)

        with self.conn:
            self.conn.execute(
                """
                INSERT INTO responses (
                    qid, answered_at, selected, correct_answer, was_correct,
                    elapsed_seconds, mode, session_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    question.qid,
                    iso(answered_at),
                    selected,
                    question.answer,
                    int(correct),
                    elapsed_seconds,
                    mode,
                    session_id,
                ),
            )
            self.conn.execute(
                """
                UPDATE cards
                SET due_at = ?,
                    interval_days = ?,
                    ease = ?,
                    repetitions = ?,
                    lapses = ?,
                    attempts = attempts + 1,
                    correct_count = correct_count + ?,
                    last_answered_at = ?,
                    last_correct = ?
                WHERE qid = ?
                """,
                (
                    iso(due_at),
                    interval,
                    ease,
                    repetitions,
                    lapses,
                    int(correct),
                    iso(answered_at),
                    int(correct),
                    question.qid,
                ),
            )
        return correct

    def summary(self) -> dict[str, object]:
        total = self.conn.execute("SELECT COUNT(*) AS n FROM cards").fetchone()["n"]
        attempted = self.conn.execute(
            "SELECT COUNT(*) AS n FROM cards WHERE attempts > 0"
        ).fetchone()["n"]
        due = self.conn.execute(
            "SELECT COUNT(*) AS n FROM cards WHERE due_at <= ?", (iso(now_utc()),)
        ).fetchone()["n"]
        responses = self.conn.execute(
            """
            SELECT COUNT(*) AS n,
                   COALESCE(SUM(was_correct), 0) AS correct
            FROM responses
            """
        ).fetchone()
        per_category = self.conn.execute(
            """
            SELECT category,
                   COUNT(*) AS questions,
                   SUM(CASE WHEN attempts > 0 THEN 1 ELSE 0 END) AS attempted,
                   SUM(attempts) AS responses,
                   SUM(correct_count) AS correct
            FROM cards
            GROUP BY category
            ORDER BY category
            """
        ).fetchall()
        weakest = self.conn.execute(
            """
            SELECT qid, attempts, correct_count,
                   CASE WHEN attempts = 0 THEN NULL
                        ELSE 1.0 * correct_count / attempts END AS accuracy
            FROM cards
            WHERE attempts > 0
            ORDER BY accuracy ASC, attempts DESC, qid ASC
            LIMIT 15
            """
        ).fetchall()

        completed_mock_tests = self.conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM (
                SELECT session_id
                FROM responses
                WHERE mode = 'mock'
                GROUP BY session_id
                HAVING COUNT(*) = 25
            )
            """
        ).fetchone()["n"]

        return {
            "total": total,
            "attempted": attempted,
            "due": due,
            "responses": responses["n"],
            "correct": responses["correct"],
            "completed_mock_tests": completed_mock_tests,
            "per_category": per_category,
            "weakest": weakest,
        }


class TUI:
    def __init__(
        self,
        stdscr: curses.window,
        questions: Sequence[Question],
        db: ProgressDB,
    ) -> None:
        self.stdscr = stdscr
        self.questions = list(questions)
        self.by_id = {q.qid: q for q in questions}
        self.db = db
        curses.curs_set(0)
        self.stdscr.keypad(True)
        try:
            curses.use_default_colors()
        except curses.error:
            pass

    def run(self) -> None:
        while True:
            due_count = len(self.db.due_ids())
            choices = [
                f"Review due questions ({due_count})",
                "Simulated 25-question test",
                "Random practice",
                "Practice by category",
                "Statistics",
                "Quit",
            ]
            selected = self.menu("CCSE 2026 — Study Tool", choices)
            if selected is None or selected == 5:
                return
            if selected == 0:
                self.review_due()
            elif selected == 1:
                self.mock_test()
            elif selected == 2:
                self.random_practice()
            elif selected == 3:
                self.category_practice()
            elif selected == 4:
                self.show_stats()

    def menu(self, title: str, choices: Sequence[str]) -> int | None:
        index = 0
        while True:
            self.stdscr.erase()
            height, width = self.stdscr.getmaxyx()
            self.safe_addstr(1, 2, title[: max(1, width - 4)], curses.A_BOLD)
            self.safe_addstr(3, 2, "Use ↑/↓ or j/k; Enter selects; q goes back.")
            for i, choice in enumerate(choices):
                attr = curses.A_REVERSE if i == index else curses.A_NORMAL
                self.safe_addstr(5 + i, 4, choice[: max(1, width - 8)], attr)
            self.stdscr.refresh()

            key = self.stdscr.getch()
            if key in (ord("q"), 27):
                return None
            if key in (curses.KEY_UP, ord("k")):
                index = (index - 1) % len(choices)
            elif key in (curses.KEY_DOWN, ord("j")):
                index = (index + 1) % len(choices)
            elif key in (10, 13, curses.KEY_ENTER):
                return index

    def review_due(self) -> None:
        ids = self.db.due_ids()
        if not ids:
            self.message("Nothing is due right now.")
            return
        self.study_session([self.by_id[qid] for qid in ids], "review", immediate=True)

    def mock_test(self) -> None:
        selected: list[Question] = []
        for category, count in MOCK_DISTRIBUTION.items():
            pool = [q for q in self.questions if q.category == category]
            selected.extend(random.sample(pool, count))
        random.shuffle(selected)
        self.study_session(selected, "mock", immediate=False)

    def random_practice(self) -> None:
        sizes = ["10 questions", "25 questions", "50 questions", "All questions"]
        picked = self.menu("Random practice — number of questions", sizes)
        if picked is None:
            return
        count = [10, 25, 50, len(self.questions)][picked]
        count = min(count, len(self.questions))
        selected = random.sample(self.questions, count)
        self.study_session(selected, "random", immediate=True)

    def category_practice(self) -> None:
        choices = [
            f"{number}. {CATEGORY_NAMES[number]}"
            for number in sorted(CATEGORY_NAMES)
        ]
        picked = self.menu("Choose a category", choices)
        if picked is None:
            return
        category = picked + 1
        pool = [q for q in self.questions if q.category == category]
        random.shuffle(pool)
        self.study_session(pool, f"category-{category}", immediate=True)

    def study_session(
        self,
        questions: Sequence[Question],
        mode: str,
        immediate: bool,
    ) -> None:
        session_id = now_utc().strftime("%Y%m%dT%H%M%S.%fZ")
        results: list[SessionResult] = []
        deferred_feedback: list[tuple[Question, str, bool]] = []

        for index, question in enumerate(questions, start=1):
            selected, elapsed = self.ask_question(
                question, index, len(questions), mode
            )
            if selected is None:
                break

            correct = self.db.record(
                question, selected, elapsed, mode, session_id
            )
            results.append(
                SessionResult(question.qid, selected, correct, elapsed)
            )

            if immediate:
                if not self.feedback(question, selected, correct):
                    break
            else:
                deferred_feedback.append((question, selected, correct))

        if not results:
            return

        if not immediate:
            self.mock_summary(results, deferred_feedback)
        else:
            self.session_summary(results)

    def ask_question(
        self,
        question: Question,
        index: int,
        total: int,
        mode: str,
    ) -> tuple[str | None, float]:
        started = time.monotonic()
        while True:
            self.stdscr.erase()
            height, width = self.stdscr.getmaxyx()
            usable = max(20, width - 6)

            title = (
                f"{mode.upper()}  {index}/{total}  "
                f"ID {question.qid}  Category {question.category}"
            )
            self.safe_addstr(1, 2, title[: width - 4], curses.A_BOLD)

            y = 3
            y = self.draw_wrapped(y, 3, question.text, usable, curses.A_BOLD)
            y += 1
            option_letters = sorted(question.options)
            for letter in option_letters:
                y = self.draw_wrapped(
                    y, 5, f"{letter}. {question.options[letter]}", usable - 2
                )
                y += 1

            choices_label = "/".join(option_letters)
            number_label = "/".join(str(i + 1) for i in range(len(option_letters)))
            footer = (
                f"Choose {choices_label} or {number_label}. "
                "Press q to stop this session."
            )
            self.safe_addstr(height - 2, 2, footer[: width - 4], curses.A_DIM)
            self.stdscr.refresh()

            key = self.stdscr.getch()
            mapping = {
                ord("a"): "a", ord("A"): "a", ord("1"): "a",
                ord("b"): "b", ord("B"): "b", ord("2"): "b",
                ord("c"): "c", ord("C"): "c", ord("3"): "c",
            }
            if key in mapping and mapping[key] in question.options:
                return mapping[key], time.monotonic() - started
            if key in (ord("q"), 27):
                return None, time.monotonic() - started

    def feedback(self, question: Question, selected: str, correct: bool) -> bool:
        self.stdscr.erase()
        height, width = self.stdscr.getmaxyx()
        heading = "CORRECT" if correct else "INCORRECT"
        attr = curses.A_BOLD
        self.safe_addstr(1, 2, heading, attr)
        self.safe_addstr(3, 2, f"{question.qid}: {question.text}"[: width - 4])
        self.draw_wrapped(
            5, 4,
            f"Your answer: {selected}. {question.options[selected]}",
            max(20, width - 8),
        )
        self.draw_wrapped(
            8, 4,
            f"Correct answer: {question.answer}. "
            f"{question.options[question.answer]}",
            max(20, width - 8),
            curses.A_BOLD,
        )
        self.safe_addstr(
            height - 2, 2, "Enter: next question   q: stop session", curses.A_DIM
        )
        self.stdscr.refresh()
        while True:
            key = self.stdscr.getch()
            if key in (10, 13, curses.KEY_ENTER, ord(" ")):
                return True
            if key in (ord("q"), 27):
                return False

    def session_summary(self, results: Sequence[SessionResult]) -> None:
        correct = sum(r.correct for r in results)
        total = len(results)
        accuracy = 100.0 * correct / total
        lines = [
            f"Answered: {total}",
            f"Correct: {correct}",
            f"Incorrect: {total - correct}",
            f"Accuracy: {accuracy:.1f}%",
            "",
            "Incorrect IDs:",
            ", ".join(r.qid for r in results if not r.correct) or "None",
        ]
        self.scroll_text("Session summary", lines)

    def mock_summary(
        self,
        results: Sequence[SessionResult],
        feedback: Sequence[tuple[Question, str, bool]],
    ) -> None:
        correct = sum(r.correct for r in results)
        total = len(results)
        lines = [
            f"Score: {correct}/{total} ({100.0 * correct / total:.1f}%)",
            "",
            "Review:",
        ]
        for question, selected, was_correct in feedback:
            marker = "✓" if was_correct else "✗"
            lines.append(
                f"{marker} {question.qid}  yours: {selected}  "
                f"correct: {question.answer}"
            )
            if not was_correct:
                lines.append(f"    {question.text}")
                lines.append(
                    f"    {question.answer}. {question.options[question.answer]}"
                )
        self.scroll_text("Simulated test result", lines)

    def show_stats(self) -> None:
        stats = self.db.summary()
        responses = int(stats["responses"])
        correct = int(stats["correct"])
        overall = 100.0 * correct / responses if responses else 0.0

        lines = [
            f"Question bank: {stats['total']}",
            f"Completed simulated tests: {stats['completed_mock_tests']}",
            f"Questions attempted at least once: {stats['attempted']}",
            f"Questions currently due: {stats['due']}",
            f"Total saved responses: {responses}",
            f"Overall response accuracy: {overall:.1f}%",
            "",
            "By category:",
        ]

        for row in stats["per_category"]:
            count = int(row["responses"] or 0)
            cat_correct = int(row["correct"] or 0)
            accuracy = 100.0 * cat_correct / count if count else 0.0
            lines.append(
                f"{row['category']}. {CATEGORY_NAMES[row['category']]} — "
                f"{row['attempted']}/{row['questions']} questions attempted, "
                f"{accuracy:.1f}% accuracy across {count} responses"
            )

        lines.extend(["", "Weakest attempted questions:"])
        weakest = stats["weakest"]
        if not weakest:
            lines.append("No responses recorded yet.")
        else:
            for row in weakest:
                accuracy = 100.0 * float(row["accuracy"])
                lines.append(
                    f"{row['qid']}: {accuracy:.1f}% "
                    f"({row['correct_count']}/{row['attempts']})"
                )

        lines.extend(["", f"Database: {self.db.path}"])
        self.scroll_text("Statistics", lines)

    def message(self, text: str) -> None:
        self.stdscr.erase()
        height, width = self.stdscr.getmaxyx()
        self.draw_wrapped(2, 2, text, max(20, width - 4), curses.A_BOLD)
        self.safe_addstr(height - 2, 2, "Press any key.", curses.A_DIM)
        self.stdscr.refresh()
        self.stdscr.getch()

    def scroll_text(self, title: str, lines: Sequence[str]) -> None:
        offset = 0
        while True:
            self.stdscr.erase()
            height, width = self.stdscr.getmaxyx()
            self.safe_addstr(1, 2, title[: width - 4], curses.A_BOLD)
            visible_height = max(1, height - 5)
            wrapped: list[str] = []
            for line in lines:
                wrapped.extend(
                    textwrap.wrap(line, width=max(10, width - 6))
                    if line else [""]
                )
            max_offset = max(0, len(wrapped) - visible_height)
            offset = min(offset, max_offset)
            for i, line in enumerate(wrapped[offset: offset + visible_height]):
                self.safe_addstr(3 + i, 3, line[: width - 6])
            self.safe_addstr(
                height - 1,
                2,
                "↑/↓ or j/k scroll; PgUp/PgDn; q/Enter returns"[: width - 4],
                curses.A_DIM,
            )
            self.stdscr.refresh()
            key = self.stdscr.getch()
            if key in (ord("q"), 27, 10, 13, curses.KEY_ENTER):
                return
            if key in (curses.KEY_DOWN, ord("j")):
                offset = min(max_offset, offset + 1)
            elif key in (curses.KEY_UP, ord("k")):
                offset = max(0, offset - 1)
            elif key == curses.KEY_NPAGE:
                offset = min(max_offset, offset + visible_height)
            elif key == curses.KEY_PPAGE:
                offset = max(0, offset - visible_height)
            elif key == curses.KEY_HOME:
                offset = 0
            elif key == curses.KEY_END:
                offset = max_offset

    def draw_wrapped(
        self,
        y: int,
        x: int,
        text: str,
        width: int,
        attr: int = curses.A_NORMAL,
    ) -> int:
        height, screen_width = self.stdscr.getmaxyx()
        width = min(width, max(10, screen_width - x - 1))
        lines = textwrap.wrap(text, width=width) or [""]
        for line in lines:
            if y >= height - 2:
                break
            self.safe_addstr(y, x, line, attr)
            y += 1
        return y

    def safe_addstr(
        self,
        y: int,
        x: int,
        text: str,
        attr: int = curses.A_NORMAL,
    ) -> None:
        height, width = self.stdscr.getmaxyx()
        if y < 0 or y >= height or x < 0 or x >= width:
            return
        try:
            self.stdscr.addstr(y, x, text[: max(0, width - x - 1)], attr)
        except curses.error:
            pass


def default_db_path() -> Path:
    return Path.home() / ".local" / "share" / "ccse-tui" / "progress.sqlite3"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CCSE terminal study tool")
    parser.add_argument(
        "questions",
        nargs="?",
        type=Path,
        default=Path("ccse_2026_preguntas.txt"),
        help="Questions text file",
    )
    parser.add_argument(
        "answers",
        nargs="?",
        type=Path,
        default=Path("ccse_2026_respuestas.txt"),
        help="Answers text file",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=default_db_path(),
        help="SQLite progress database path",
    )
    parser.add_argument(
        "--reset-progress",
        action="store_true",
        help="Delete the progress database before starting",
    )
    parser.add_argument(
        "--check-files",
        action="store_true",
        help="Validate the two input files and exit",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    for path in (args.questions, args.answers):
        if not path.is_file():
            print(f"Error: file not found: {path}")
            return 2

    try:
        questions = parse_question_bank(args.questions, args.answers)
    except ValueError as error:
        print(f"Error: {error}")
        return 2

    if args.check_files:
        counts = {
            category: sum(q.category == category for q in questions)
            for category in CATEGORY_NAMES
        }
        print(f"Validated {len(questions)} questions.")
        for category, count in counts.items():
            print(f"  Category {category}: {count}")
        return 0

    if args.reset_progress and args.db.exists():
        args.db.unlink()

    db = ProgressDB(args.db, questions)
    try:
        curses.wrapper(lambda stdscr: TUI(stdscr, questions, db).run())
    except KeyboardInterrupt:
        pass
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
