# Project instructions

- Use the existing Conda environment named `boss-agent` for Python, package installation, and checks.
- Do not create this environment again. Do not install project dependencies into `base` or system Python.
- Use `conda run -n boss-agent ...` when shell activation is unavailable.
- Keep demo fixtures visibly synthetic. Do not report plan generation as a successful real application.
- Keep credentials, authenticated browser state, resumes, and runtime databases out of Git.
- Before implementing retries for external sends, distinguish confirmed success, confirmed failure, and unknown results. Never blindly repeat a send after a timeout.
