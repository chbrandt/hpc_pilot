# Project Guidelines

## Documentation

- Update relevant docs when modifying features
- Keep README.md in sync with new capabilities
- Document public functions and classes with docstrings (Google or NumPy style)
- Keep `documentation/`  directories up to date when adding or changing API endpoints or architecture

## Code quality

- Maintain high code coverage (target ≥ 80%) with unit tests
- Write integration tests for every API endpoint
- Run the full test suite before merging: `.venv/bin/python -m pytest tests/`
- Avoid code duplication — extract shared logic into reusable helper functions or modules
- Keep functions small and focused on a single responsibility (Single Responsibility Principle)
- Handle exceptions explicitly; avoid bare `except:` clauses

## Code style

- Follow PEP 8 guidelines for all Python code
- Prefer functional programming patterns where possible (e.g., list comprehensions, `map`/`filter`, pure functions)
- Use type annotations for all public function signatures
- Keep imports organised: standard library → third-party → local, each group separated by a blank line
- Prefer f-strings over `%`-formatting or `.format()` for string interpolation
- Use `pathlib.Path` instead of `os.path` for file-system operations

## Graphical interface

- Keep the UI simple and intuitive; prioritise usability over flashy design
- Use consistent styling and layout across all pages
- Use the following colour palette for UI elements:
  - Primary Colours:
    - Blue: #005FAA
    - Orange: #EF8300
  - Secondary Colours:
    - Grey: #999999
    - Dark Blue: #08152E
  - typefaces:
    - Primary: DM Sans
    - Secondary: Roboto Mono
