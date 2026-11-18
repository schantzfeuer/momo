# Release and rollback

Use the repository's CI workflow to validate package artifacts and recover from a broken release.

> **Prerequisites:** A clean working branch, Python 3.10 or later, GitHub Actions access, and the native TA-Lib prerequisite for the selected platform.

## Validation performed by CI

The checked-in workflow at `.github/workflows/ci.yml`:

- Runs tests on Ubuntu, macOS, and Windows with Python 3.10 through 3.13.
- Installs the Linux or macOS TA-Lib native package where specified by the workflow.
- Installs dependencies using `requirements.lock` and the `analysis`, `cli`, and `test` extras.
- Compiles source files and runs pytest with coverage.
- Builds package artifacts, installs the wheel, runs `pip-audit`, generates an SBOM, and uploads CI artifacts.

## Release preparation

1. Update `version` in `pyproject.toml` and `__version__` in `src/momo/__init__.py`.
2. Review dependency declarations and regenerate `requirements.lock` using the project's verified dependency workflow. [TODO: document the lock-file generation command.]
3. Run the same validation used by CI:

   ```powershell
   py -m pip install -c requirements.lock -e ".[analysis,cli,test]"
   py -m compileall -q src interface main.py
   py -m pytest --cov=src/momo --cov=interface --cov-report=term-missing
   ```

4. Build package artifacts with `py -m build`. The `build` package is not declared as a project extra; install it in the build environment before running this command.
5. Verify the wheel in a clean environment. [TODO: define the project's release publication and clean-environment verification procedure.]

The repository workflow uploads build, SBOM, and coverage artifacts to the workflow run; it does not define publishing to a package index.

## Rollback

1. Stop promotion or publication of the affected release.
2. Select a previously verified release artifact and its matching dependency constraints.
3. Restore service or user access to that version using the project's distribution process. [TODO: document the publication and rollback mechanism.]
4. Re-run parser fixture and clean-environment checks.
5. Record the provider-contract or dependency change that caused the failure in an ADR or release note.

## Next Steps

- Read the [Runbook](runbook.md) for runtime failure triage.
- Review [Architecture](../architecture/overview.md) and [Provider contracts](../reference/provider-contracts.md) before changing parsing behavior.
