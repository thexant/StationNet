"""Structured validation problems for world packages and dungeon themes."""

SEVERITIES = ("error", "warning", "info")
CATEGORIES = ("invalid", "missing_reference", "unsupported", "engine_reserved", "conflict", "legacy")
# Messages that read as a missing reference or unsupported feature get those categories.
_HINTS = (("unknown", "missing_reference"), ("not bundled or installed", "missing_reference"),
          ("not installed", "missing_reference"), ("must name", "missing_reference"),
          ("references unknown", "missing_reference"), ("unsupported", "unsupported"),
          ("conflicts with", "conflict"))


def categorize(message):
    folded = message.casefold()
    return next((category for hint, category in _HINTS if hint in folded), "invalid")


class PackageReport:
    def __init__(self):
        self.problems = []

    def add(self, severity, file, path, message, category=None):
        problem = {"severity": severity, "category": category or categorize(message),
                   "file": file, "path": path, "message": message}
        if problem not in self.problems:
            self.problems.append(problem)
        return problem

    def error(self, file, path, message, category=None):
        return self.add("error", file, path, message, category)

    def warning(self, file, path, message, category=None):
        return self.add("warning", file, path, message, category)

    def info(self, file, path, message, category=None):
        return self.add("info", file, path, message, category)

    def extend_paths(self, file, messages, severity="error"):
        """Adopt validator messages shaped like ``$.path: message``."""
        for message in messages:
            path, _, text = message.partition(": ")
            if not path.startswith("$"):
                path, text = "", message
            self.add(severity, file, path, text)

    @property
    def errors(self):
        return [problem for problem in self.problems if problem["severity"] == "error"]

    @property
    def warnings(self):
        return [problem for problem in self.problems if problem["severity"] == "warning"]

    def as_json(self):
        return list(self.problems)

    def summary(self, limit=10):
        lines = [format_problem(problem) for problem in self.errors[:limit]]
        if len(self.errors) > limit:
            lines.append(f"…and {len(self.errors) - limit} more.")
        return " ".join(lines)


def format_problem(problem):
    where = ":".join(part for part in (problem["file"], problem["path"]) if part)
    return f"{where}: {problem['message']}" if where else problem["message"]
