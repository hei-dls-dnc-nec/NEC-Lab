# Network Lab Template

Welcome to the NEC Lab. This repository contains the starter code and lab exercises for the networking semester.

This project uses [uv](https://astral.sh/uv/) for Python package management and PyCharm as the recommended IDE.

## Step 1: Create your repository

1. Click the green **Use this template** button at the top right of this page.
2. Select **Create a new repository**.
3. Name it `nec-lab-YOURNAME` and click **Create repository**.

## Step 2: Install UV
- **macOS / Linux:** `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Windows:** `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

## Step 3: Open in PyCharm & Sync
1. Clone your newly created repository in PyCharm.
2. Synchronize the virtual environment by running in the terminal:
  ```bash
  uv sync
  ```
  *(or click the **uv Sync** notification in PyCharm)*.

3. If prompted by PyCharm, accept using `.venv` as the project interpreter.

---

## Modules

### Dashboard (`dashboard/`)

A unified lab service emulating an industrial SCADA dashboard (Modbus TCP on port `1502`) and an HTTP traffic generator (port `8050`).

To run the dashboard:

```bash
python dashboard/main.py
```

Or from inside the folder:

```bash
cd dashboard
python main.py
```

Once running, access the web interface at **`http://localhost:8050`**.
