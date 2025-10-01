# Console Authentication

The console API and dashboard require authentication, configured via environment variables.

Set one (or both) of the following:

- `CONSOLE_BASIC_USERNAME` / `CONSOLE_BASIC_PASSWORD`: Enables HTTP Basic auth. Browsers will prompt for these credentials when visiting `/dashboard`.
- `CONSOLE_API_TOKEN`: Enables Bearer token auth for API clients (use the value as the `Authorization: Bearer ...` header).

If both Basic and token values are provided, either method will be accepted. When none are defined, the console falls back to open access (intended only for local development).

Remember to update `.env.local` (or your deployment secrets) before starting `run_console.py`.
