# AGENTS.md

This document serves as the guide and instruction set for AI agents (such as Codex, Claude, Gemini, or local LLMs) interacting with and contributing to this Python repository.

---

## 1. Project Overview & Architecture
* **Primary Language:** Python 3.10+
* **Framework / Libraries:** [e.g., FastAPI, PyTorch, Pandas, Click] *(Update as applicable)*
* **Architecture Pattern:** Modular design separating core logic, CLI/API interfaces, and data models.

---

## 2. Environment & Dependency Management
When running, building, or testing code in this project, use the designated environment tools.

* **Package Manager:** `poetry` / `pipenv` / `uv` / `pip` *(Select your preferred tool)*
* **Virtual Environment Setup:**
  ```bash
  python -m venv .venv
  source .venv/bin/activate  # On Windows: .venv\Scripts\activate
  pip install -r requirements.txt
  ```

---

## 3. Code Standards & Style Guidelines

### Formatting & Linting
Agents **must** ensure generated or edited code adheres to the project's formatting requirements:
* **Style Guide:** [PEP 8](https://peps.python.org/pep-0008/)
* **Formatter:** `black` (line length: 88)
* **Imports Sorting:** `isort`
* **Linter:** `flake8` or `ruff`

Run formatting tools before submitting changes:
```bash
ruff check --fix .
black .
```

### Type Hints & Documentation
* **Type Annotations:** Python standard typing module (or native generics in Python 3.9+) is **mandatory** for all public function signatures and class attributes.
* **Docstrings:** Use Google-style docstrings for modules, classes, and public functions.

```python
def process_data(input_path: str, threshold: float = 0.5) -> list[dict]:
    """Processes input data files and filters based on a numeric threshold.

    Args:
        input_path: The file path to the raw data.
        threshold: Minimum confidence score to include in results.

    Returns:
        A list of dictionary objects meeting the criteria.

    Raises:
        FileNotFoundError: If `input_path` does not exist.
    """
    ...
```

---

## 4. Testing & Quality Assurance

* **Framework:** `pytest`
* **Coverage Requirements:** All new functions and bug fixes must include corresponding unit tests in the `tests/` directory.
* **Running Tests:**
  ```bash
  pytest tests/ --maxfail=1 --disable-warnings -q
  ```

---

## 5. Agent Workflow & Safety Rules

1. **Incremental Edits:** Avoid rewriting entire files unless necessary. Make isolated, single-purpose edits.
2. **Do Not Alter Dependencies Lightly:** Only add new dependencies to `pyproject.toml` or `requirements.txt` if required by the core task.
3. **Preserve Configuration:** Never expose API keys, credentials, or `.env` files. Ensure secrets remain in `.env.example` placeholders.
4. **Validation:** Always verify that proposed changes compile (`python -m py_compile <file>`) and pass tests before finalizing output.