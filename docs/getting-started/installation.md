# Installation

Install Momo and choose the optional dependencies needed for your workflow.

> **Prerequisites:** Python 3.10 or later, pip and network access to install packages, and network access to the configured public data providers when fetching data.

## Install

1. Install the package and CLI dependencies:

   ```powershell
   py -m pip install -e ".[cli]"
   ```

2. To enable technical and integrated analysis, install the analysis extra as well:

   ```powershell
   py -m pip install -e ".[analysis,cli]"
   ```

3. Run a stock query to verify the CLI installation:

   ```powershell
   py -m momo AAPL
   ```

   The command requires provider network access. Use `py -m momo --help` to verify the installed CLI options.

## Optional dependency groups

| Extra | Includes |
| --- | --- |
| `cli` | Rich terminal output and Typer CLI. |
| `analysis` | NumPy, pandas, yfinance, and TA-Lib. |
| `test` | pytest, pytest-cov, pandas, and TA-Lib. |

The core package dependencies are `requests` and `beautifulsoup4`.

### TA-Lib

The analysis and test extras declare the `TA-Lib` Python package. Linux and macOS CI install the native prerequisite with the operating-system package manager before installing the Python extras. The repository does not document a Windows setup procedure.

- Linux: CI installs `libta-lib0` and `libta-lib-dev`.
- macOS: CI runs `brew install ta-lib`.
- Windows: [TODO: document a verified Windows TA-Lib installation procedure.]

To reproduce the CI dependency set, use the committed constraints file:

```powershell
py -m pip install -c requirements.lock -e ".[analysis,cli,test]"
```

## Verify the package

```powershell
py -m momo --help
py -m pytest -q
```

The test command requires the `test` extra. Tests are configured in `pyproject.toml`.

## Next Steps

- Follow the [CLI guide](../guides/cli.md) to choose a workflow.
- Review [Configuration](../reference/configuration.md) to adjust requests and caching.
