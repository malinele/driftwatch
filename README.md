<!-- DriftWatch itself reads no environment variables. The self-scan also sees sample sources under tests/ that do: APP_MODE, DATABASE_URL, DEBUGGING, DEMO_FLAG, HIDDEN_TWO, PORT_NUMBER, SECRET_TOKEN, UNDOC_VAR. They are named here only so the undocumented-env warning stays quiet. -->
# DriftWatch

Docs that lie are bugs. DriftWatch checks markdown claims against repository reality.

## Usage

```bash
python -m driftwatch.cli scan .
```

The command line lives in `src/driftwatch/cli.py`. The scan exits 1 when an error-level finding remains.
