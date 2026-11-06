"""What the page can change: saving uploads, importing them, adding a rule and re-running the rules.

dashboard.py calls these; they write files and the database, and compute nothing for display.
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from contextlib import closing
from datetime import datetime
from pathlib import Path

import categorize
import config
import db
import load
from load import normalize


def save_upload(name: str, data: bytes, now: datetime | None = None, exports_dir: Path | None = None) -> Path:
    """Keep an uploaded export as EXPORTS_DIR/YYYYMMDD-HHMMSS_<name>, so a bank's reused download
    name never overwrites last month's file. Only the file name from the browser is used."""
    folder = exports_dir or config.EXPORTS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    base = Path(name).name or "export.csv"
    target = folder / f"{stamp}_{base}"
    copy = 2
    while target.exists():
        target = folder / f"{stamp}_{copy}_{base}"
        copy += 1
    target.write_bytes(data)
    return target


def rerun_rules() -> Counter[str]:
    """Recategorize every row with the current rules.csv; return the count per category."""
    rules = categorize.read_rules(categorize.ensure_rules_file())
    with closing(db.connect()) as connection:
        return categorize.categorize(connection, rules)


def import_files(paths: list[Path]) -> list[dict]:
    """Load saved exports, then recategorize everything; return load's result for each file.

    If rules.csv can't be read, the files stay loaded (their new rows show as uncategorized) and
    the rules error is added as one more result.
    """
    results = load.load_files(paths)
    try:
        rerun_rules()
    except categorize.RulesError as error:
        results.append({"file": "rules.csv", "error": f"{error}. The files were loaded; fix it and click Re-run rules."})
    return results


def add_rule(pattern: str, category: str, match: str = "contains", rules_path: Path | None = None,
             examples: list[str] | None = None) -> None:
    """Append one rule to rules.csv. The whole file is checked first, so a bad rule raises
    categorize.RulesError and the file is left as it was. With examples (the descriptions the rule is
    meant for), a rule that matches none of them is refused the same way."""
    path = categorize.ensure_rules_file(rules_path)
    line = io.StringIO()
    csv.writer(line, lineterminator="\n").writerow([pattern.strip(), category, match])
    current = path.read_text(encoding="utf-8-sig")
    if current and not current.endswith("\n"):
        current += "\n"
    trial = path.with_name(path.name + ".new")
    trial.write_text(current + line.getvalue(), encoding="utf-8")
    try:
        new_rule = categorize.read_rules(trial)[-1]
    except categorize.RulesError as error:
        trial.unlink()
        raise categorize.RulesError(str(error).replace(trial.name, path.name)) from None
    if examples and not any(new_rule["regex"].search(normalize(text)) for text in examples):
        trial.unlink()
        raise categorize.RulesError(f"{pattern.strip()!r} doesn't match any of this merchant's rows, so nothing would change")
    trial.replace(path)
